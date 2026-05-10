# FILE: src/ai_steward_wiki/inbox/materialize.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Materialise per-user Inbox-WIKI/ on first contact (D-004, D-016).
#   SCOPE: ensure_inbox_wiki(user_id, wiki_root, template_path); idempotent atomic write.
#   DEPENDS: asyncio (stdlib), hashlib, ai_steward_wiki.logging_setup
#   LINKS: D-004, D-016, M-INBOX
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   INBOX_WIKI_DIRNAME - canonical Inbox-WIKI directory name
#   ensure_inbox_wiki - idempotent first-contact materialise; returns Path to Inbox-WIKI dir
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial materialise (chunk 6)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from pathlib import Path

from ai_steward_wiki.logging_setup import get_logger

_log = get_logger(__name__)

INBOX_WIKI_DIRNAME = "Inbox-WIKI"
_CLAUDE_MD = "CLAUDE.md"


def _materialise_sync(user_dir: Path, template_path: Path) -> tuple[Path, str, bool]:
    """Synchronous core: returns (inbox_dir, template_sha256, created)."""
    inbox_dir = user_dir / INBOX_WIKI_DIRNAME
    target = inbox_dir / _CLAUDE_MD
    template_bytes = template_path.read_bytes()
    sha256 = hashlib.sha256(template_bytes).hexdigest()
    if target.exists():
        return inbox_dir, sha256, False

    inbox_dir.mkdir(parents=True, exist_ok=True)
    # START_BLOCK_ATOMIC_WRITE
    tmp = inbox_dir / f"{_CLAUDE_MD}.tmp"
    try:
        tmp.write_bytes(template_bytes)
        os.replace(tmp, target)
    finally:
        # Cleanup leftover tmp on any failure between write_bytes and replace.
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
    # END_BLOCK_ATOMIC_WRITE
    return inbox_dir, sha256, True


# START_CONTRACT: ensure_inbox_wiki
#   PURPOSE: Idempotently create <wiki_root>/<user_id>/Inbox-WIKI/CLAUDE.md from template.
#   INPUTS: { user_id: int, wiki_root: Path, template_path: Path }
#   OUTPUTS: { Path - absolute path to Inbox-WIKI/ directory }
#   SIDE_EFFECTS: creates directories + atomic-writes CLAUDE.md if absent;
#                 emits inbox.materialized log when newly created.
#   LINKS: D-004 §"Inbox-WIKI", D-016 §"Layout"
# END_CONTRACT: ensure_inbox_wiki
async def ensure_inbox_wiki(
    user_id: int,
    *,
    wiki_root: Path,
    template_path: Path,
) -> Path:
    user_dir = wiki_root / str(user_id)
    inbox_dir, sha256, created = await asyncio.to_thread(_materialise_sync, user_dir, template_path)
    if created:
        _log.info(
            "inbox.materialized",
            user_id=user_id,
            path=str(inbox_dir),
            template_sha256=sha256,
        )
    else:
        _log.debug(
            "inbox.materialize_idempotent",
            user_id=user_id,
            path=str(inbox_dir),
        )
    return inbox_dir
