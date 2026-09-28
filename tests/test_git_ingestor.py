"""
Tests for git_ingestor.py.

Unlike test_core.py, these build a REAL temporary git repository (via git
init + git commit against a local temp dir — no network access required)
and run the actual `git log` parsing against it. This proves the parser
works against real git output, not a mocked shape of it.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import git_ingestor  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                    capture_output=True, text=True)


def _make_repo(tmp_path) -> Path:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test Author")

    (repo / "auth.py").write_text("# legacy adapter\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m",
         "Introduce legacy authentication adapter",
         "-m", "Bridges internal session auth with early SSO tenants.")

    (repo / "auth.py").write_text("# legacy adapter\n# TODO remove\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m",
         "Merge pull request #42 from team/remove-adapter-ticket",
         "-m", "Flags adapter as temporary pending tenant migration.")

    (repo / "auth.py").write_text("# legacy adapter\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Revert \"Merge pull request #42\"",
         "-m", "Reverted — broke enterprise tenant SSO in staging.")

    return repo


def test_is_git_repo_true_and_false(tmp_path):
    repo = _make_repo(tmp_path)
    assert git_ingestor.is_git_repo(repo) is True
    assert git_ingestor.is_git_repo(tmp_path / "not-a-repo") is False


def test_parse_git_log_returns_real_commits(tmp_path):
    repo = _make_repo(tmp_path)
    commits = git_ingestor.parse_git_log(repo, max_commits=50)

    assert len(commits) == 3
    subjects = [c["subject"] for c in commits]
    assert "Introduce legacy authentication adapter" in subjects
    assert any("Merge pull request #42" in s for s in subjects)
    assert any(s.startswith("Revert") for s in subjects)
    for c in commits:
        assert c["hash"] and c["author"] == "Test Author" and c["date"]


def test_classify_commit_types():
    assert git_ingestor.classify_commit("Merge pull request #7 from x/y", "") == "pr_merge"
    assert git_ingestor.classify_commit("Revert \"bad change\"", "") == "revert"
    assert git_ingestor.classify_commit("Fix typo", "") == "commit"


def test_ingest_git_history_produces_retain_ready_events(tmp_path):
    repo = _make_repo(tmp_path)
    events = git_ingestor.ingest_git_history(str(repo), repo_label="demo-repo", include_files=True)

    assert len(events) == 3
    for e in events:
        assert e["id"].startswith("git-")
        assert e["repository"] == "demo-repo"
        assert e["author"] == "Test Author"
        assert "related_files" in e and isinstance(e["related_files"], list)
        # must be shaped so the existing formatter (dataset.json path) works unchanged
        from context_ingestor import format_event_for_retain
        content = format_event_for_retain(e)
        assert e["title"] in content

    pr_events = [e for e in events if e["type"] == "pr_merge"]
    assert len(pr_events) == 1
    assert "PR #42" in pr_events[0]["description"]

    revert_events = [e for e in events if e["type"] == "revert"]
    assert len(revert_events) == 1


def test_ingest_git_history_rejects_non_repo(tmp_path):
    import pytest
    with pytest.raises(git_ingestor.GitIngestError):
        git_ingestor.ingest_git_history(str(tmp_path / "nope"))


def test_ingest_git_history_since_filter_narrows_results(tmp_path):
    """--since is passed straight through to `git log`; verify it actually
    narrows results using two commits with explicit, far-apart dates
    (GIT_AUTHOR_DATE/GIT_COMMITTER_DATE), rather than relying on git's
    handling of an out-of-range future date, which some git versions
    parse inconsistently."""
    import os

    repo = tmp_path / "since-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test Author")

    old_env = os.environ.copy()
    try:
        os.environ["GIT_AUTHOR_DATE"] = "2015-01-01T00:00:00"
        os.environ["GIT_COMMITTER_DATE"] = "2015-01-01T00:00:00"
        (repo / "old.txt").write_text("old\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "Old commit from 2015")

        os.environ["GIT_AUTHOR_DATE"] = "2024-06-01T00:00:00"
        os.environ["GIT_COMMITTER_DATE"] = "2024-06-01T00:00:00"
        (repo / "new.txt").write_text("new\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "Recent commit from 2024")
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    all_events = git_ingestor.ingest_git_history(str(repo), include_files=False)
    assert len(all_events) == 2

    recent_events = git_ingestor.ingest_git_history(str(repo), since="2020-01-01", include_files=False)
    assert len(recent_events) == 1
    assert recent_events[0]["title"] == "Recent commit from 2024"
