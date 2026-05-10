# FILE: src/ai_steward_wiki/inbox/hint_cache.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: sessions.inbox_hint_cache repo + hot-path get_or_refresh helper (D-006/D-016).
#   SCOPE: InboxHintCacheRepo (get/upsert/invalidate); get_or_refresh_hint helper.
#   DEPENDS: SQLAlchemy.async, ai_steward_wiki.storage.sessions.models.InboxHintCache,
#            ai_steward_wiki.inbox.parser.extract_inbox_hint, ai_steward_wiki.logging_setup
#   LINKS: D-004, D-006 §"Структура", D-016 §"Кэш", M-INBOX, M-STORAGE-SESSIONS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   InboxHintCacheRepo - async wrapper over sessions.inbox_hint_cache (UPSERT/get/invalidate)
#   get_or_refresh_hint - hot-path helper; stat→cache-hit on (size,mtime) match else read+sha256
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial cache repo + hot-path refresh (chunk 6)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.inbox.parser import extract_inbox_hint
from ai_steward_wiki.logging_setup import get_logger
from ai_steward_wiki.storage.sessions.models import InboxHintCache

_log = get_logger(__name__)


class InboxHintCacheRepo:
    """Repository for the sessions.inbox_hint_cache table.

    SQLite UPSERT (`ON CONFLICT … DO UPDATE`) executes as a single statement and is
    atomic — there is no torn-write window on the (user_id, wiki_path) row.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = session_maker

    # START_CONTRACT: get
    #   PURPOSE: Fetch cache row by (user_id, wiki_path), or None.
    #   INPUTS: { user_id: int, wiki_path: str }
    #   OUTPUTS: { InboxHintCache | None }
    #   SIDE_EFFECTS: read-only DB transaction
    # END_CONTRACT: get
    async def get(self, user_id: int, wiki_path: str) -> InboxHintCache | None:
        async with self._sm() as session:
            stmt = select(InboxHintCache).where(
                InboxHintCache.user_id == user_id,
                InboxHintCache.wiki_path == wiki_path,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    # START_CONTRACT: upsert
    #   PURPOSE: Insert-or-update cache row atomically (single SQLite statement).
    #   INPUTS: { user_id, wiki_path, size_bytes, mtime_ns, ctime_ns,
    #             content_sha256, hint_text, refreshed_at_utc }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: writes one row in sessions.inbox_hint_cache
    # END_CONTRACT: upsert
    async def upsert(
        self,
        user_id: int,
        wiki_path: str,
        *,
        size_bytes: int,
        mtime_ns: int,
        ctime_ns: int,
        content_sha256: str,
        hint_text: str,
        refreshed_at_utc: datetime,
    ) -> None:
        stmt = sqlite_insert(InboxHintCache).values(
            user_id=user_id,
            wiki_path=wiki_path,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            ctime_ns=ctime_ns,
            content_sha256=content_sha256,
            hint_text=hint_text,
            refreshed_at_utc=refreshed_at_utc,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[InboxHintCache.user_id, InboxHintCache.wiki_path],
            set_={
                "size_bytes": stmt.excluded.size_bytes,
                "mtime_ns": stmt.excluded.mtime_ns,
                "ctime_ns": stmt.excluded.ctime_ns,
                "content_sha256": stmt.excluded.content_sha256,
                "hint_text": stmt.excluded.hint_text,
                "refreshed_at_utc": stmt.excluded.refreshed_at_utc,
            },
        )
        async with self._sm() as session, session.begin():
            await session.execute(stmt)

    # START_CONTRACT: invalidate
    #   PURPOSE: Delete cache row for (user_id, wiki_path); idempotent.
    #   INPUTS: { user_id: int, wiki_path: str }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: delete one row if exists
    # END_CONTRACT: invalidate
    async def invalidate(self, user_id: int, wiki_path: str) -> None:
        stmt = delete(InboxHintCache).where(
            InboxHintCache.user_id == user_id,
            InboxHintCache.wiki_path == wiki_path,
        )
        async with self._sm() as session, session.begin():
            await session.execute(stmt)


def _stat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _read_and_hash(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


# START_CONTRACT: get_or_refresh_hint
#   PURPOSE: Hot-path "## Inbox hint" lookup with metadata-guarded cache (D-006 §"Структура").
#   INPUTS: { repo: InboxHintCacheRepo, user_id: int, claude_md_path: Path }
#   OUTPUTS: { str | None - hint body or None if section absent / file missing }
#   SIDE_EFFECTS: may stat/read file; may upsert/invalidate cache row; emits structlog events.
#   LINKS: D-006 §"Структура inbox_hint_cache", D-016 §"Кэш"
# END_CONTRACT: get_or_refresh_hint
async def get_or_refresh_hint(
    repo: InboxHintCacheRepo,
    user_id: int,
    claude_md_path: Path,
) -> str | None:
    wiki_path = str(claude_md_path)
    # START_BLOCK_STAT_OR_INVALIDATE
    st = await asyncio.to_thread(_stat_or_none, claude_md_path)
    if st is None:
        await repo.invalidate(user_id, wiki_path)
        _log.info(
            "inbox.hint_cache.invalidate_missing",
            user_id=user_id,
            wiki_path=wiki_path,
        )
        return None
    # END_BLOCK_STAT_OR_INVALIDATE

    # START_BLOCK_CACHE_LOOKUP
    cached = await repo.get(user_id, wiki_path)
    if cached is not None and cached.size_bytes == st.st_size and cached.mtime_ns == st.st_mtime_ns:
        _log.debug(
            "inbox.hint_cache.hit",
            user_id=user_id,
            wiki_path=wiki_path,
        )
        return cached.hint_text
    # END_BLOCK_CACHE_LOOKUP

    # START_BLOCK_REFRESH
    data, sha256 = await asyncio.to_thread(_read_and_hash, claude_md_path)
    text = data.decode("utf-8", errors="replace")
    hint = extract_inbox_hint(text)
    hint_text = hint or ""
    await repo.upsert(
        user_id,
        wiki_path,
        size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        content_sha256=sha256,
        hint_text=hint_text,
        refreshed_at_utc=datetime.now(UTC).replace(tzinfo=None),
    )
    _log.info(
        "inbox.hint_cache.refresh",
        user_id=user_id,
        wiki_path=wiki_path,
        sha256=sha256,
        present=hint is not None,
    )
    return hint
    # END_BLOCK_REFRESH
