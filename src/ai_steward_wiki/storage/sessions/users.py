# FILE: src/ai_steward_wiki/storage/sessions/users.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Resolve the surrogate users.user_id for a canonical telegram_id (D-042) — the one mapping the rest of sessions.db rows key on.
#   SCOPE: resolve_user_id only (read-only lookup over the users table).
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.sessions.models.User
#   LINKS: D-042, M-STORAGE-SESSIONS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   resolve_user_id - telegram_id -> users.user_id (surrogate) or None if unknown
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial surrogate-id lookup (aisw-5sd — hint-catalog resolver needs it for inbox_hint_cache)
# END_CHANGE_SUMMARY

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import User

__all__ = ["resolve_user_id"]


# START_CONTRACT: resolve_user_id
#   PURPOSE: Map a canonical telegram_id to the surrogate users.user_id.
#   INPUTS: { session_maker: async_sessionmaker[AsyncSession], telegram_id: int }
#   OUTPUTS: { int | None - the surrogate id, or None if no users row exists for this telegram_id }
#   SIDE_EFFECTS: one read-only DB transaction
#   LINKS: D-042, M-STORAGE-SESSIONS, M-INBOX (hint cache keying)
# END_CONTRACT: resolve_user_id
async def resolve_user_id(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> int | None:
    async with session_maker() as session:
        return (
            await session.execute(select(User.user_id).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
