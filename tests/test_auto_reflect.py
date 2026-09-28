"""
Tests for scripts/auto_reflect.py.

The git-reading half (latest_commit_summary) is exercised against a real
temp repo, same approach as test_git_ingestor.py. The Hindsight/LLM half
is mocked, consistent with test_core.py, since it needs a live server.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import auto_reflect  # noqa: E402
import reflection_engine  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                    capture_output=True, text=True)


def _make_repo(tmp_path) -> Path:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test Author")
    (repo / "auth.py").write_text("# adapter\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Remove legacy authentication adapter",
         "-m", "Replaces it with the standard OAuth flow.")
    (repo / "checkout.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Merge pull request #99 from team/oauth-cutover")
    return repo


def test_latest_commit_summary_reads_real_git_state(tmp_path):
    repo = _make_repo(tmp_path)
    info = auto_reflect.latest_commit_summary(str(repo))
    assert "Merge pull request #99" in info["subject"]
    assert info["branch"] in ("main", "master")
    assert "checkout.py" in info["files"]


def test_run_auto_reflect_stores_lesson_with_merge_outcome(tmp_path):
    repo = _make_repo(tmp_path)

    fake_result = reflection_engine.ReflectionResult(status="stored", lesson="Auto-generated lesson.")

    with patch("auto_reflect.retrieve_relevant_memories") as mock_retrieve, \
         patch("auto_reflect.reflect_and_store", return_value=fake_result) as mock_reflect:
        from memory_retriever import RetrievalResult
        mock_retrieve.return_value = RetrievalResult(status="ok", evidence=["ADR-014: keep adapter until verified"])

        exit_code = auto_reflect.run_auto_reflect("post-merge", str(repo))

    assert exit_code == 0
    mock_reflect.assert_called_once()
    call_kwargs = mock_reflect.call_args.kwargs
    assert "Merge pull request #99" in call_kwargs["proposal"] or "Merge pull request #99" in str(mock_reflect.call_args)
    assert "automatically detected" in call_kwargs["outcome"]
    assert call_kwargs["evidence"] == ["ADR-014: keep adapter until verified"]


def test_run_auto_reflect_handles_non_repo_gracefully(tmp_path):
    exit_code = auto_reflect.run_auto_reflect("post-merge", str(tmp_path / "not-a-repo"))
    assert exit_code == 1
