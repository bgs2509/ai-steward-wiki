# FILE: src/ai_steward_wiki/auth/allowlist.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: In-memory allowlist cache + sync to sessions.db.users (D-031, D-042).
#   SCOPE: Allowlist, replace_global, get_global, sync_to_sessions_db.
#   DEPENDS: ai_steward_wiki.auth.users_toml, SQLAlchemy.async, sessions.users model
#   LINKS: D-031, D-042, INV-10, M-AUTH-USERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Allowlist - frozen mapping telegram_id -> UserRecord (enabled only)
#   replace_global - atomic swap of module-level cache
#   get_global - accessor returning current cache
#   sync_to_sessions_db - upsert + soft-disable for users absent from config
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial in-memory allowlist + sessions.db sync (D-031/D-042)
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.storage.sessions.models import User

__all__ = [
    "Allowlist",
    "get_global",
    "replace_global",
    "sync_to_sessions_db",
]


class Allowlist:
    """Frozen view over telegram_id → UserRecord."""

    __slots__ = ("_by_tg",)

    def __init__(self, config: UsersConfig) -> None:
        self._by_tg: Mapping[int, UserRecord] = MappingProxyType(
            {u.telegram_id: u for u in config.users if u.enabled}
        )

    def is_allowed(self, telegram_id: int) -> bool:
        return telegram_id in self._by_tg

    def get_user(self, telegram_id: int) -> UserRecord | None:
        return self._by_tg.get(telegram_id)

    def all_users(self) -> tuple[UserRecord, ...]:
        return tuple(self._by_tg.values())


_GLOBAL: Allowlist = Allowlist(UsersConfig(schema_version=1, users=()))


def replace_global(config: UsersConfig) -> Allowlist:
    """Atomic swap of module-level allowlist."""
    global _GLOBAL
    new = Allowlist(config)
    _GLOBAL = new
    return new


def get_global() -> Allowlist:
    return _GLOBAL


async def sync_to_sessions_db(
    config: UsersConfig,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Upsert users from config into sessions.users; soft-disable users absent from config.

    enabled flag mirrors users.toml; role/display_name/tz updated on each reload.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    toml_by_tg: dict[int, UserRecord] = {u.telegram_id: u for u in config.users}

    async with session_maker() as session, session.begin():
        existing = (await session.execute(select(User))).scalars().all()
        existing_by_tg: dict[int, User] = {u.telegram_id: u for u in existing}

        for tg_id, rec in toml_by_tg.items():
            row = existing_by_tg.get(tg_id)
            if row is None:
                session.add(
                    User(
                        telegram_id=tg_id,
                        role=rec.role,
                        display_name=rec.display_name,
                        tz=rec.tz,
                        enabled=rec.enabled,
                        created_at_utc=now,
                        updated_at_utc=now,
                    )
                )
            else:
                row.role = rec.role
                row.display_name = rec.display_name
                row.tz = rec.tz
                row.enabled = rec.enabled
                row.updated_at_utc = now

        for tg_id, row in existing_by_tg.items():
            if tg_id not in toml_by_tg and row.enabled:
                row.enabled = False
                row.updated_at_utc = now
