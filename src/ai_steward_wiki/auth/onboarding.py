# FILE: src/ai_steward_wiki/auth/onboarding.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Onboarding flow for unknown telegram_ids — pending_users CRUD + intro template.
#   SCOPE: PendingUserRepo, start_unknown_user, purge_expired_pending, format_intro_message,
#          OnboardingTemplateError, REQUIRED_SLUGS, PENDING_USER_TTL_DAYS.
#   DEPENDS: SQLAlchemy.async, ai_steward_wiki.storage.sessions.models.PendingUser,
#            scripts.lint_onboarding (REQUIRED_SLUGS shared via re-import), structlog
#   LINKS: D-030, D-031, D-042, M-ONBOARD-ADMIN
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PENDING_USER_TTL_DAYS - default TTL (14 days, D-030)
#   REQUIRED_SLUGS - re-export of mandatory slug names
#   OnboardingTemplateError - raised when template fails slug validation
#   PendingUserRecord - frozen dataclass: detached view of a pending row
#   PendingUserRepo - async CRUD facade over sessions.pending_users
#   start_unknown_user - idempotent upsert returning the (refreshed) row
#   purge_expired_pending - delete rows past TTL; returns deleted count
#   format_intro_message - slug-validated template formatter with {bot_name} placeholder
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 12: onboarding pending_users + intro template
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import PendingUser

__all__ = [
    "PENDING_USER_TTL_DAYS",
    "REQUIRED_SLUGS",
    "OnboardingTemplateError",
    "PendingUserRecord",
    "PendingUserRepo",
    "format_intro_message",
    "purge_expired_pending",
    "start_unknown_user",
]

_log = structlog.get_logger("auth.onboarding")

PENDING_USER_TTL_DAYS = 14

REQUIRED_SLUGS: tuple[str, ...] = (
    "greeting",
    "purpose",
    "capabilities",
    "privacy",
    "next-steps",
    "contact",
)


class OnboardingTemplateError(Exception):
    """Raised when the intro template is missing required slugs."""


@dataclass(frozen=True)
class PendingUserRecord:
    """Detached view of a pending_users row (TZ-naive UTC datetimes)."""

    telegram_id: int
    requested_at_utc: datetime
    expires_at_utc: datetime
    username: str | None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _payload_from_record(row: PendingUser) -> PendingUserRecord:
    payload_raw = row.candidate_payload_json or "{}"
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {}
    expires = payload.get("expires_at_utc")
    expires_dt = (
        datetime.fromisoformat(expires)
        if isinstance(expires, str)
        else row.requested_at_utc + timedelta(days=PENDING_USER_TTL_DAYS)
    )
    username = payload.get("username") if isinstance(payload.get("username"), str) else None
    return PendingUserRecord(
        telegram_id=row.telegram_id,
        requested_at_utc=row.requested_at_utc,
        expires_at_utc=expires_dt,
        username=username,
    )


class PendingUserRepo:
    """Async CRUD over sessions.pending_users (D-030)."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = session_maker

    async def get(self, telegram_id: int) -> PendingUser | None:
        async with self._sm() as s:
            row = (
                (await s.execute(select(PendingUser).where(PendingUser.telegram_id == telegram_id)))
                .scalars()
                .first()
            )
            if row is not None:
                s.expunge(row)
            return row

    async def upsert(
        self,
        telegram_id: int,
        *,
        username: str | None,
        now: datetime,
        ttl_days: int = PENDING_USER_TTL_DAYS,
    ) -> PendingUserRecord:
        expires = now + timedelta(days=ttl_days)
        payload = json.dumps(
            {"username": username, "expires_at_utc": expires.isoformat()},
            ensure_ascii=False,
            sort_keys=True,
        )
        async with self._sm() as s, s.begin():
            row = (
                (await s.execute(select(PendingUser).where(PendingUser.telegram_id == telegram_id)))
                .scalars()
                .first()
            )
            if row is None:
                row = PendingUser(
                    telegram_id=telegram_id,
                    requested_at_utc=now,
                    candidate_payload_json=payload,
                )
                s.add(row)
            else:
                row.requested_at_utc = now
                row.candidate_payload_json = payload
            await s.flush()
            s.expunge(row)
        return PendingUserRecord(
            telegram_id=telegram_id,
            requested_at_utc=now,
            expires_at_utc=expires,
            username=username,
        )

    async def delete(self, telegram_id: int) -> bool:
        async with self._sm() as s, s.begin():
            result = await s.execute(
                delete(PendingUser).where(PendingUser.telegram_id == telegram_id)
            )
            return (result.rowcount or 0) > 0


# START_CONTRACT: start_unknown_user
#   PURPOSE: Idempotent record-or-refresh of a /start applicant.
#   INPUTS: { repo: PendingUserRepo, telegram_id: int, username: str|None, now: datetime|None }
#   OUTPUTS: { PendingUserRecord - new or refreshed row }
#   SIDE_EFFECTS: writes/updates sessions.pending_users; emits auth.onboarding.pending_created
#   LINKS: D-030
# END_CONTRACT: start_unknown_user
async def start_unknown_user(
    repo: PendingUserRepo,
    telegram_id: int,
    *,
    username: str | None = None,
    now: datetime | None = None,
    ttl_days: int = PENDING_USER_TTL_DAYS,
) -> PendingUserRecord:
    ts = (
        (now or _utcnow_naive()).replace(tzinfo=None)
        if now and now.tzinfo
        else (now or _utcnow_naive())
    )
    existing = await repo.get(telegram_id)
    rec = await repo.upsert(telegram_id, username=username, now=ts, ttl_days=ttl_days)
    _log.info(
        "auth.onboarding.pending_created",
        telegram_id=telegram_id,
        was_existing=existing is not None,
        expires_at_utc=rec.expires_at_utc.isoformat(),
    )
    return rec


# START_CONTRACT: purge_expired_pending
#   PURPOSE: Maintenance — delete pending_users rows past TTL.
#   INPUTS: { session_maker: async_sessionmaker, now: datetime|None, ttl_days: int }
#   OUTPUTS: { int - rows deleted }
#   SIDE_EFFECTS: deletes from sessions.pending_users
#   LINKS: D-030
# END_CONTRACT: purge_expired_pending
async def purge_expired_pending(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    ttl_days: int = PENDING_USER_TTL_DAYS,
) -> int:
    ts = now or _utcnow_naive()
    cutoff = ts - timedelta(days=ttl_days)
    async with session_maker() as s, s.begin():
        result = await s.execute(delete(PendingUser).where(PendingUser.requested_at_utc < cutoff))
        n = result.rowcount or 0
    if n:
        _log.info("auth.onboarding.pending_expired", count=n, cutoff=cutoff.isoformat())
    return n


# START_CONTRACT: format_intro_message
#   PURPOSE: Read intro template, validate required slugs, substitute {bot_name}.
#   INPUTS: { template_path: Path, bot_name: str, locale: str (ru only in MVP) }
#   OUTPUTS: { str - rendered message body }
#   SIDE_EFFECTS: none (read-only file)
#   LINKS: D-030, D-032
# END_CONTRACT: format_intro_message
def format_intro_message(
    template_path: Path,
    *,
    bot_name: str = "ai-steward-wiki",
    locale: str = "ru",
) -> str:
    # Back-compat adapter over ai_steward_wiki.templates.render_template
    # (aisw-s5i Phase A: shared loader for onboarding + /start + /help + /manual).
    if locale != "ru":
        raise OnboardingTemplateError(f"unsupported locale {locale!r} (D-032: ru-only MVP)")
    from ai_steward_wiki.templates import TemplateError, render_template

    try:
        return render_template(
            template_path,
            required_slugs=frozenset(REQUIRED_SLUGS),
            bot_name=bot_name,
        )
    except FileNotFoundError as exc:
        raise OnboardingTemplateError(f"cannot read template {template_path}: {exc}") from exc
    except TemplateError as exc:
        # Preserve the original "missing required slugs" wording for tests that
        # match on it; otherwise expose the upstream message.
        msg = str(exc)
        if "missing=" in msg:
            # Extract sorted list for the legacy assertion shape.
            raise OnboardingTemplateError(
                f"missing required slugs in {template_path}: {msg}"
            ) from exc
        raise OnboardingTemplateError(msg) from exc
