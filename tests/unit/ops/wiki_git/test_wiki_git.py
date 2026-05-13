"""Unit tests for ops.wiki_git: idempotent init + canonical commit format."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ai_steward_wiki.ops.wiki_git import (
    COMMIT_FMT,
    GITIGNORE_ENTRIES,
    WikiGitError,
    auto_commit,
    format_commit_message,
    init_wiki_git,
)

# Tests need real `git` binary on PATH. Skip gracefully if missing.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_init_wiki_git_creates_repo_and_gitignore(tmp_path: Path) -> None:
    created = init_wiki_git(tmp_path)
    assert created is True
    assert (tmp_path / ".git").is_dir()
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    for entry in GITIGNORE_ENTRIES:
        assert entry in gi


def test_init_wiki_git_idempotent(tmp_path: Path) -> None:
    init_wiki_git(tmp_path)
    created_again = init_wiki_git(tmp_path)
    assert created_again is False


def test_init_wiki_git_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(WikiGitError):
        init_wiki_git(tmp_path / "nope")


def test_format_commit_message_canonical() -> None:
    msg = format_commit_message(job_id="job-abc", category="medical", title="Add glucose log")
    assert msg == COMMIT_FMT.format(job_id="job-abc", category="medical", title="Add glucose log")
    assert msg == "job-abc(medical): Add glucose log"


def test_auto_commit_creates_commit_with_canonical_message(tmp_path: Path) -> None:
    init_wiki_git(tmp_path)
    (tmp_path / "page.md").write_text("# hello\n", encoding="utf-8")
    sha = auto_commit(tmp_path, job_id="j1", category="default", title="seed page")
    assert sha is not None
    assert len(sha) >= 7

    out = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "j1(default): seed page"


def test_auto_commit_noop_when_nothing_changed(tmp_path: Path) -> None:
    init_wiki_git(tmp_path)
    (tmp_path / "page.md").write_text("# hello\n", encoding="utf-8")
    auto_commit(tmp_path, job_id="j1", category="default", title="seed")
    sha2 = auto_commit(tmp_path, job_id="j2", category="default", title="seed-again")
    assert sha2 is None


def test_auto_commit_requires_init(tmp_path: Path) -> None:
    with pytest.raises(WikiGitError):
        auto_commit(tmp_path, job_id="j1", category="default", title="no-init")


def test_no_remote_configured(tmp_path: Path) -> None:
    """INV-3 / D-037: local-only, never set a remote."""
    init_wiki_git(tmp_path)
    out = subprocess.run(
        ["git", "remote"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == ""
