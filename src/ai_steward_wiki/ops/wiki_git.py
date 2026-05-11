# FILE: src/ai_steward_wiki/ops/wiki_git.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Per-WIKI git auto-commit helpers (D-037). Local-only repo per WIKI dir;
#            point-in-time recovery against bad Claude edits / accidental writes.
#   SCOPE: init_wiki_git, auto_commit, GITIGNORE_ENTRIES, COMMIT_FMT.
#            NEVER configures a remote (INV-3 / tech-spec §10.2 / D-037 §Remote push).
#   DEPENDS: subprocess.run (git CLI), pathlib
#   LINKS: M-OPS-BACKUP, D-037, tech-spec §10.5, INV-3
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   COMMIT_FMT - canonical commit-message template: "<job_id>(<category>): <title>"
#   GITIGNORE_ENTRIES - lines written into .gitignore inside each WIKI repo
#   WikiGitError - raised when the local git CLI call fails
#   init_wiki_git - idempotent git init + .gitignore writer
#   format_commit_message - render the canonical per-WIKI commit message
#   auto_commit - stage-all + commit; no-op when nothing staged
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 14: M-OPS-BACKUP per-WIKI git
# END_CHANGE_SUMMARY

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

__all__ = [
    "COMMIT_FMT",
    "GITIGNORE_ENTRIES",
    "WikiGitError",
    "auto_commit",
    "format_commit_message",
    "init_wiki_git",
]

COMMIT_FMT = "{job_id}({category}): {title}"
GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".wiki.lock",
    "data/runs/",
)

_AUTHOR_NAME = "ai-steward-wiki"
_AUTHOR_EMAIL = "aisw@localhost"

_log = structlog.get_logger(__name__)


class WikiGitError(RuntimeError):
    """Raised when the local git CLI call fails for a per-WIKI repo."""


def _run_git(wiki_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(wiki_root),
        check=False,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": _AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": _AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": _AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": _AUTHOR_EMAIL,
            "HOME": str(wiki_root),  # isolate from user-global git config
            "PATH": "/usr/bin:/usr/local/bin:/bin",
        },
    )


def init_wiki_git(wiki_root: Path) -> bool:
    """Idempotently bootstrap a local git repo inside ``wiki_root``.

    Returns True if a new repo was created, False if one already existed.
    Always (re)writes ``.gitignore`` to keep the entries in sync if they drift.
    """
    if not wiki_root.is_dir():
        raise WikiGitError(f"wiki_root does not exist or is not a directory: {wiki_root}")

    git_dir = wiki_root / ".git"
    created = not git_dir.exists()
    if created:
        result = _run_git(wiki_root, "init", "--quiet", "--initial-branch=main")
        if result.returncode != 0:
            raise WikiGitError(f"git init failed: {result.stderr.strip()}")

    gitignore = wiki_root / ".gitignore"
    gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n", encoding="utf-8")

    _log.info(
        "[ops.wiki_git][init_wiki_git][DONE] per-WIKI git ready",
        wiki_root=str(wiki_root),
        created=created,
    )
    return created


def format_commit_message(*, job_id: str, category: str, title: str) -> str:
    """Render the canonical per-WIKI commit message (D-037)."""
    return COMMIT_FMT.format(job_id=job_id, category=category, title=title)


def auto_commit(
    wiki_root: Path,
    *,
    job_id: str,
    category: str,
    title: str,
) -> str | None:
    """Stage-all changes in ``wiki_root`` and create a commit.

    Returns the new commit SHA, or ``None`` if there was nothing to commit
    (post-Stage-1b write may produce a byte-identical file — we don't want
    empty commits).
    """
    if not (wiki_root / ".git").exists():
        raise WikiGitError(f"git not initialised in {wiki_root}; call init_wiki_git first")

    _run_git(wiki_root, "add", "-A")
    status = _run_git(wiki_root, "status", "--porcelain")
    if not status.stdout.strip():
        return None

    msg = format_commit_message(job_id=job_id, category=category, title=title)
    result = _run_git(wiki_root, "commit", "--quiet", "-m", msg)
    if result.returncode != 0:
        raise WikiGitError(f"git commit failed: {result.stderr.strip() or result.stdout.strip()}")

    sha = _run_git(wiki_root, "rev-parse", "HEAD").stdout.strip()
    _log.info(
        "[ops.wiki_git][auto_commit][DONE] wiki commit",
        wiki_root=str(wiki_root),
        sha=sha,
        job_id=job_id,
        category=category,
    )
    return sha
