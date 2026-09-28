"""
Automatic Reflection After Real Git/PR Events
--------------------------------------------------
Closes the reflection loop WITHOUT a human clicking "Reflect & Remember"
in the UI. This script is meant to be invoked automatically:

  - locally, by a git `post-merge` hook (see install_git_hooks.py), or
  - in CI, as a step that runs after a PR merges (e.g. a GitHub Actions
    job on `pull_request: closed` with `merged == true`, or any CI system
    that runs a shell step post-merge).

It treats the merge landing at all as the ground-truth outcome signal —
"merged into <branch>" — which is more honest than a developer's manual
after-the-fact recollection, then recalls relevant prior memory for that
change and stores a lesson as procedural memory via the same
reflect_and_store() used by the manual UI flow.

Usage:
    python scripts/auto_reflect.py --event post-merge
    python scripts/auto_reflect.py --event manual --repo-path /path/to/repo
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_agent import AuditResult  # noqa: E402
from memory_retriever import retrieve_relevant_memories  # noqa: E402
from reflection_engine import reflect_and_store  # noqa: E402


def _run_git(args: list[str], repo_path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def latest_commit_summary(repo_path: str = ".") -> dict:
    subject = _run_git(["log", "-1", "--pretty=%s"], repo_path)
    body = _run_git(["log", "-1", "--pretty=%b"], repo_path)
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    files_raw = _run_git(["show", "--name-only", "--pretty=format:", "HEAD"], repo_path)
    files = [f for f in files_raw.splitlines() if f.strip()]
    return {"subject": subject, "body": body, "branch": branch, "files": files}


def run_auto_reflect(event: str, repo_path: str = ".") -> int:
    try:
        info = latest_commit_summary(repo_path)
    except Exception as e:
        print(f"[auto-reflect] Could not read git history at '{repo_path}': {e}")
        return 1

    proposal = f"{info['subject']} (files: {', '.join(info['files'][:10]) or 'n/a'})"

    print(f"[auto-reflect] Triggered by '{event}' on branch '{info['branch']}'.")
    print(f"[auto-reflect] Proposal: {proposal}")

    retrieval = retrieve_relevant_memories(proposal)
    evidence = retrieval.evidence if retrieval.status == "ok" else []
    print(f"[auto-reflect] Recalled {len(evidence)} relevant memory item(s) "
          f"(status: {retrieval.status}).")

    outcome = (
        f"Merged into '{info['branch']}' — automatically detected via {event} "
        f"git hook, no manual outcome entry required."
    )

    synthetic_audit = AuditResult(
        proposed_change=proposal,
        risk_level="UNKNOWN",
        historical_why="Automatically triggered by a real merge event — no pre-merge audit was run.",
        evidence=evidence,
        reasoning_mode="auto_reflect",
    )

    result = reflect_and_store(
        proposal=proposal,
        evidence=evidence,
        audit_result=synthetic_audit,
        outcome=outcome,
        notes=info["body"] or "",
    )

    print(f"[auto-reflect] {result.status}: {result.message}")
    if result.lesson:
        print(f"[auto-reflect] Lesson stored: {result.lesson}")

    return 0 if result.status == "stored" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatically reflect on the most recent git merge/commit event.")
    parser.add_argument("--event", default="post-merge", choices=["post-merge", "post-commit", "manual"])
    parser.add_argument("--repo-path", default=".")
    args = parser.parse_args()
    return run_auto_reflect(args.event, args.repo_path)


if __name__ == "__main__":
    sys.exit(main())
