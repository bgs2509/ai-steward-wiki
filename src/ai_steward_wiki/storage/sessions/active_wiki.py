# FILE: src/ai_steward_wiki/storage/sessions/active_wiki.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Read/write the per-user sticky last-active-<Domain>-WIKI pointer in sessions.db (aisw-0ym).
#   SCOPE: ACTIVE_WIKI_TTL_HOURS, ActiveWikiPointer (set_active / get_active with TTL guard).
#   DEPENDS: sqlalchemy.async, ai_steward_wiki.storage.sessions.models.UserActiveWiki
#   LINKS: D-042, M-STORAGE-SESSIONS, aisw-0ym
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ACTIVE_WIKI_TTL_HOURS - default freshness window (24h, == chat_log D-033 window)
#   ActiveWikiPointer - sessions.db adapter: set_active(upsert) / get_active(TTL-guarded read)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0ym: initial sticky active-WIKI pointer adapter.
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import UserActiveWiki

__all__ = [
    "ACTIVE_WIKI_TTL_HOURS",
    "ActiveWikiPointer",
]

# Same window as the D-033 chat_log buffer — a stale pointer (older than this)
# must NOT force-route a genuinely new topic.
ACTIVE_WIKI_TTL_HOURS = 24


def _utcnow_naive() -> datetime:
    # D-006 invariant: sessions.db datetimes are UTC, stored tz-naive.
    return datetime.now(UTC).replace(tzinfo=None)


class ActiveWikiPointer:
    """sessions.db adapter for the per-user sticky last-active-WIKI pointer.

    The dispatcher stays stateless — it reads the pointer each turn rather than
    holding it in memory.
    """

    def __init__(self, sessions_maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = sessions_maker

    # START_CONTRACT: set_active
    #   PURPOSE: Upsert the user's last-active <Domain>-WIKI on a successful route/ingest.
    #   INPUTS: { telegram_id, wiki_name, now: datetime|None }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: INSERT or UPDATE one user_active_wiki row.
    #   LINKS: aisw-0ym
    # END_CONTRACT: set_active
    async def set_active(
        self,
        telegram_id: int,
        wiki_name: str,
        *,
        now: datetime | None = None,
    ) -> None:
        ts = now or _utcnow_naive()
        async with self._maker() as session, session.begin():
            row = await session.get(UserActiveWiki, telegram_id)
            if row is None:
                session.add(
                    UserActiveWiki(
                        telegram_id=telegram_id,
                        wiki_name=wiki_name,
                        updated_at_utc=ts,
                    )
                )
            else:
                row.wiki_name = wiki_name
                row.updated_at_utc = ts

    # START_CONTRACT: get_active
    #   PURPOSE: Return the fresh (within TTL) last-active WIKI for a user, else None.
    #   INPUTS: { telegram_id, ttl_hours=ACTIVE_WIKI_TTL_HOURS, now: datetime|None }
    #   OUTPUTS: { str | None - wiki_name when a row exists AND is within ttl_hours; else None }
    #   SIDE_EFFECTS: one read-only DB transaction.
    #   LINKS: aisw-0ym
    # END_CONTRACT: get_active
    async def get_active(
        self,
        telegram_id: int,
        *,
        ttl_hours: int = ACTIVE_WIKI_TTL_HOURS,
        now: datetime | None = None,
    ) -> str | None:
        ts = now or _utcnow_naive()
        cutoff = ts - timedelta(hours=ttl_hours)
        async with self._maker() as session:
            row = (
                await session.execute(
                    select(UserActiveWiki.wiki_name, UserActiveWiki.updated_at_utc).where(
                        UserActiveWiki.telegram_id == telegram_id,
                    )
                )
            ).first()
        if row is None:
            return None
        wiki_name, updated_at = row
        if updated_at <= cutoff:
            return None
        return str(wiki_name)
