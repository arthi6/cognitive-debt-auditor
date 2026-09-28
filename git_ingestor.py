"""
Git History Ingestor
-----------------------
Replaces/supplements the curated dataset.json with REAL engineering
history: this reads a repository's actual `git log` (commit messages,
authors, dates, changed files, and PR-merge commits) and converts each
commit into the same event schema used by dataset.json, so it flows
through the existing retain pipeline (format_event_for_retain) unchanged.

This module shells out to the system `git` binary directly (no GitPython
dependency) via subprocess, so it works with any locally cloned repo that
has git history — no network access, no GitHub API token required for the
local case. `repo_path` just needs to be a real git working tree.

Usage:
    python git_ingestor.py --repo-path /path/to/repo --dry-run
    python git_ingestor.py --repo-path /path/to/repo --max-commits 300
"""

import re
import subprocess
import threading
import time
from pathlib import Path

RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"
GIT_LOG_FORMAT = f"%H{FIELD_SEP}%an{FIELD_SEP}%ad{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}"

MERGE_PR_RE = re.compile(r"Merge pull request #(\d+) from (\S+)")
REVERT_RE = re.compile(r"^Revert\b", re.IGNORECASE)


class GitIngestError(Exception):
    pass


def _run_git(args: list[str], repo_path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + args,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise GitIngestError(f"git executable not found: {e}")
    except subprocess.CalledProcessError as e:
        raise GitIngestError(f"git {' '.join(args)} failed: {e.stderr.strip()}")
    return result.stdout


def is_git_repo(repo_path) -> bool:
    try:
        out = _run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
        return out.strip() == "true"
    except GitIngestError:
        return False


def parse_git_log(repo_path, max_commits: int = 200, since: str | None = None) -> list[dict]:
    """Parse real commit history into raw commit dicts (hash/author/date/subject/body)."""
    args = ["log", f"--pretty=format:{GIT_LOG_FORMAT}", "--date=short", f"-n{max_commits}"]
    if since:
        args.append(f"--since={since}")
    raw = _run_git(args, repo_path)

    commits = []
    for entry in raw.split(RECORD_SEP):
        entry = entry.strip("\n")
        if not entry.strip():
            continue
        parts = entry.split(FIELD_SEP)
        if len(parts) < 5:
            continue
        commit_hash, author, date, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        commits.append({
            "hash": commit_hash.strip(),
            "author": author.strip(),
            "date": date.strip(),
            "subject": subject.strip(),
            "body": body.strip(),
        })
    return commits


def _changed_files(repo_path, commit_hash: str, limit: int = 20) -> list[str]:
    try:
        out = _run_git(["show", "--name-only", "--pretty=format:", commit_hash], repo_path)
    except GitIngestError:
        return []
    files = [f for f in out.splitlines() if f.strip()]
    return files[:limit]


def classify_commit(subject: str, body: str) -> str:
    if MERGE_PR_RE.search(subject) or MERGE_PR_RE.search(body):
        return "pr_merge"
    if REVERT_RE.match(subject):
        return "revert"
    return "commit"


def commit_to_event(commit: dict, repo_path, repo_label: str, include_files: bool = True) -> dict:
    """Map a raw git commit to the same event schema dataset.json events use,
    so format_event_for_retain() in context_ingestor.py works unchanged."""
    event_type = classify_commit(commit["subject"], commit["body"])
    related_files = _changed_files(repo_path, commit["hash"]) if include_files else []

    pr_match = MERGE_PR_RE.search(commit["subject"]) or MERGE_PR_RE.search(commit["body"])
    description = commit["body"] or commit["subject"]
    if pr_match:
        description = f"PR #{pr_match.group(1)} merged from {pr_match.group(2)}. {description}"

    return {
        "id": f"git-{commit['hash'][:10]}",
        "date": commit["date"],
        "type": event_type,
        "title": commit["subject"],
        "repository": repo_label,
        "author": commit["author"],
        "description": description,
        "decision": None,
        "constraints": None,
        "outcome": None,
        "related_files": related_files,
        "tags": ["episodic", "git_history", event_type],
    }


def ingest_git_history(
    repo_path: str = ".",
    repo_label: str | None = None,
    max_commits: int = 200,
    since: str | None = None,
    include_files: bool = True,
) -> list[dict]:
    """Read real git history from repo_path and return it as retain-ready events."""
    resolved = Path(repo_path).resolve()
    if not is_git_repo(resolved):
        raise GitIngestError(
            f"'{resolved}' is not a git repository (or git is not installed/available)."
        )
    if repo_label is None:
        repo_label = resolved.name

    raw_commits = parse_git_log(resolved, max_commits=max_commits, since=since)
    if not raw_commits:
        raise GitIngestError(
            f"No commits found in '{resolved}' (empty history, or --since filtered everything out)."
        )

    return [commit_to_event(c, resolved, repo_label, include_files) for c in raw_commits]


def seed_git_history_into_hindsight(
    repo_path: str = ".",
    repo_label: str | None = None,
    max_commits: int = 200,
    since: str | None = None,
    progress_callback=None,
) -> dict:
    """Same background-thread retain pipeline as context_ingestor.seed_into_hindsight,
    but sourced from real git history instead of the synthetic dataset.json."""
    from config import HINDSIGHT_BASE_URL, BANK_ID
    from context_ingestor import format_event_for_retain

    events = ingest_git_history(repo_path, repo_label, max_commits, since)
    status = {"done": 0, "total": len(events), "errors": [], "finished": False}

    def _worker():
        try:
            from hindsight_client import Hindsight
            client = Hindsight(base_url=HINDSIGHT_BASE_URL)
        except Exception as e:
            status["errors"].append(f"Could not initialize Hindsight client: {e}")
            status["finished"] = True
            return

        for event in events:
            content = format_event_for_retain(event)
            try:
                client.retain(bank_id=BANK_ID, content=content)
            except Exception as e:
                status["errors"].append(f"{event.get('id', '?')}: {e}")
            status["done"] += 1
            if progress_callback:
                try:
                    progress_callback(status["done"], status["total"], event.get("title", ""))
                except Exception:
                    pass  # never let a UI callback crash ingestion

        status["finished"] = True

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return {"thread": thread, "status": status}


if __name__ == "__main__":
    import argparse
    import json as _json
    import sys

    parser = argparse.ArgumentParser(description="Ingest real git commit history as engineering-history events.")
    parser.add_argument("--repo-path", default=".", help="Path to a local git repository.")
    parser.add_argument("--repo-label", default=None, help="Label stored on each event (defaults to repo dir name).")
    parser.add_argument("--max-commits", type=int, default=200)
    parser.add_argument("--since", default=None, help="e.g. '6 months ago' or '2024-01-01'")
    parser.add_argument("--no-files", action="store_true", help="Skip per-commit changed-file lookup (faster).")
    parser.add_argument("--dry-run", action="store_true", help="Print parsed events as JSON instead of retaining them.")
    args = parser.parse_args()

    try:
        events = ingest_git_history(
            args.repo_path, args.repo_label, args.max_commits, args.since,
            include_files=not args.no_files,
        )
    except GitIngestError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

    if args.dry_run:
        print(_json.dumps(events, indent=2))
        sys.exit(0)

    result = seed_git_history_into_hindsight(
        args.repo_path, args.repo_label, args.max_commits, args.since,
    )
    while not result["status"]["finished"]:
        time.sleep(0.5)
    s = result["status"]
    print(f"Ingested {s['done']}/{s['total']} real git events into Hindsight. Errors: {len(s['errors'])}")
    for e in s["errors"]:
        print(f"  - {e}")
    sys.exit(1 if s["errors"] else 0)
