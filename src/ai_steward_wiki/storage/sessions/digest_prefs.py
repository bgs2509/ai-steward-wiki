# FILE: src/ai_steward_wiki/storage/sessions/digest_prefs.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Read/write a digest-job owner's per-section toggles (user_digest_prefs).
#   SCOPE: get_digest_prefs (read, defaults when absent), set_digest_section (upsert one section).
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.sessions.models.{User,UserDigestPrefs},
#            ai_steward_wiki.storage.sessions.users.resolve_user_id
#   LINKS: M-STORAGE-SESSIONS, ADR-025, ADR-026, D-024, aisw-pv8
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TOGGLEABLE_DIGEST_SECTIONS - the section keys a user may toggle ('trackers','wiki')
#   SECTION_DISPLAY_NAME - section key -> ru label with emoji (for buttons + the digest directive)
#   DigestPrefs - frozen view of one owner's toggles (+ disabled_keys)
#   get_digest_prefs - telegram_id -> DigestPrefs (both True when no row / no user)
#   set_digest_section - upsert one section's bool for telegram_id; no-op (returns defaults) if no users row
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-pv8: initial per-user digest section toggles repo
# END_CHANGE_SUMMARY

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import UserDigestPrefs
from ai_steward_wiki.storage.sessions.users import resolve_user_id

__all__ = [
    "SECTION_DISPLAY_NAME",
    "TOGGLEABLE_DIGEST_SECTIONS",
    "DigestPrefs",
    "get_digest_prefs",
    "set_digest_section",
]

# The two optional digest sections (ADR-025). Order is the canonical display order.
TOGGLEABLE_DIGEST_SECTIONS: tuple[str, ...] = ("trackers", "wiki")

# ru label with emoji — mirrors the <b>-headers in prompts/digest.md.
SECTION_DISPLAY_NAME: dict[str, str] = {
    "trackers": "📈 Трекеры",
    "wiki": "📝 Обновления WIKI",
}

# Column name on UserDigestPrefs for each key (DRY: one mapping, used by get/set).
_SECTION_COLUMN: dict[str, str] = {"trackers": "trackers_enabled", "wiki": "wiki_enabled"}


@dataclass(frozen=True, slots=True)
class DigestPrefs:
    trackers_enabled: bool = True
    wiki_enabled: bool = True

    @property
    def disabled_keys(self) -> tuple[str, ...]:
        return tuple(k for k in TOGGLEABLE_DIGEST_SECTIONS if not getattr(self, _SECTION_COLUMN[k]))


def _from_row(row: UserDigestPrefs | None) -> DigestPrefs:
    if row is None:
        return DigestPrefs()
    return DigestPrefs(
        trackers_enabled=bool(row.trackers_enabled), wiki_enabled=bool(row.wiki_enabled)
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# START_CONTRACT: get_digest_prefs
#   PURPOSE: Read a telegram_id's digest section toggles.
#   INPUTS: { session_maker: async_sessionmaker[AsyncSession], telegram_id: int }
#   OUTPUTS: { DigestPrefs - both True if no users row or no prefs row }
#   SIDE_EFFECTS: one read-only DB transaction
#   LINKS: M-STORAGE-SESSIONS, ADR-026
# END_CONTRACT: get_digest_prefs
async def get_digest_prefs(
    session_maker: async_sessionmaker[AsyncSession], telegram_id: int
) -> DigestPrefs:
    user_id = await resolve_user_id(session_maker, telegram_id)
    if user_id is None:
        return DigestPrefs()
    async with session_maker() as session:
        row = await session.get(UserDigestPrefs, user_id)
        return _from_row(row)


# START_CONTRACT: set_digest_section
#   PURPOSE: Set one section's on/off for a telegram_id (creating the row with defaults if absent).
#   INPUTS: { session_maker, telegram_id: int, section: str (must be in TOGGLEABLE_DIGEST_SECTIONS), enabled: bool }
#   OUTPUTS: { DigestPrefs - the new state; both True (no write) if telegram_id has no users row }
#   SIDE_EFFECTS: one read-write DB transaction (upsert)
#   LINKS: M-STORAGE-SESSIONS, ADR-026
# END_CONTRACT: set_digest_section
async def set_digest_section(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    section: str,
    enabled: bool,
) -> DigestPrefs:
    if section not in _SECTION_COLUMN:
        raise ValueError(f"unknown digest section: {section!r}")
    user_id = await resolve_user_id(session_maker, telegram_id)
    if user_id is None:
        return DigestPrefs()
    async with session_maker() as session:
        row = await session.get(UserDigestPrefs, user_id)
        if row is None:
            row = UserDigestPrefs(user_id=user_id, trackers_enabled=True, wiki_enabled=True)
            session.add(row)
        setattr(row, _SECTION_COLUMN[section], enabled)
        row.updated_at_utc = _now()
        await session.commit()
        return DigestPrefs(
            trackers_enabled=bool(row.trackers_enabled), wiki_enabled=bool(row.wiki_enabled)
        )
