# FILE: src/ai_steward_wiki/tg/callbacks.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Inline reminder-card callback handler (aisw-163 P4). Resolves
#            `r:<job_id>:{done|snz|skp}` taps into CAS state mutations on
#            jobs.user_state, with snooze rescheduling a new DateTrigger and
#            collapsing to 'skipped' after _SNOOZE_CAP attempts.
#   SCOPE: parse_reminder_callback (pure parser), CallbackContext (dataclass),
#          set_callback_context (DI registry), on_reminder_card (main handler),
#          REMINDER_CALLBACK_PREFIX, _SNOOZE_DELTA, _SNOOZE_CAP.
#   DEPENDS: aiogram.types (CallbackQuery), apscheduler.triggers.date.DateTrigger,
#            apscheduler.schedulers.asyncio.AsyncIOScheduler, sqlalchemy,
#            structlog, ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.scheduler.firing.fire_job (rescheduled callback).
#   LINKS: M-TG-CALLBACKS, M-STORAGE-JOBS, M-SCHEDULER-FIRING, ADR-026, aisw-163
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   REMINDER_CALLBACK_PREFIX - "r:" callback_data prefix (matches M-DIGEST-CARDS)
#   CallbackContext - dataclass(scheduler, jobs_session_maker) — injected once at startup
#   set_callback_context - install/clear the module-level CallbackContext
#   parse_reminder_callback - parse `r:<job_id>:<action>` → (job_id, action) | None
#   on_reminder_card - aiogram callback_query handler for reminder cards
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-163 P4: CAS done/skip + snooze reschedule with cap=3
# END_CHANGE_SUMMARY

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import update

from ai_steward_wiki.storage.jobs.models import Job

if TYPE_CHECKING:
    from aiogram.types import CallbackQuery
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "REMINDER_CALLBACK_PREFIX",
    "CallbackContext",
    "on_reminder_card",
    "parse_reminder_callback",
    "set_callback_context",
]

_log = structlog.get_logger("tg.callbacks")

REMINDER_CALLBACK_PREFIX = "r:"
_VALID_ACTIONS: frozenset[str] = frozenset({"done", "snz", "skp"})
_SNOOZE_DELTA = timedelta(minutes=30)
_SNOOZE_CAP = 3

# Russian acks — short, single-line (FR-8: ≤1 line).
_ACK_DONE = "Отмечено ✅"
_ACK_SKIP = "Пропущено ❌"
_ACK_SNZ = "Отложено на 30 мин ⏰"
_ACK_NOOP = "Уже обработано"
_ACK_BAD = ""  # silent ack — empty toast

CallbackAction = Literal["done", "snz", "skp"]


@dataclass(frozen=True)
class CallbackContext:
    """DI container for the reminder-card callback handler."""

    scheduler: AsyncIOScheduler
    jobs_session_maker: async_sessionmaker[AsyncSession]


_ctx: CallbackContext | None = None


def set_callback_context(ctx: CallbackContext | None) -> None:
    """Install (or clear) the module-level CallbackContext. Idempotent."""
    global _ctx
    _ctx = ctx


def parse_reminder_callback(data: str) -> tuple[int, CallbackAction] | None:
    """Parse ``r:<job_id>:<action>`` payload; return None on any malformed input."""
    if not data or not data.startswith(REMINDER_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        job_id = int(parts[1])
    except ValueError:
        return None
    action = parts[2]
    if action not in _VALID_ACTIONS:
        return None
    return job_id, action  # type: ignore[return-value]


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# START_CONTRACT: _cas_set_state
#   PURPOSE: Compare-and-swap user_state from `expected` to `target` for one Job row.
#   INPUTS: { session: AsyncSession, job_id: int, target_state: str, expected: str }
#   OUTPUTS: { int - rowcount: 1 if the swap landed, 0 if a concurrent press won }
#   SIDE_EFFECTS: One UPDATE; caller commits.
# END_CONTRACT: _cas_set_state
async def _cas_set_state(
    session: AsyncSession, *, job_id: int, target_state: str, expected: str = "pending"
) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.user_state == expected)
        .values(user_state=target_state)
    )
    return int(result.rowcount or 0)


# START_BLOCK_HANDLE_REMINDER_CARD
async def on_reminder_card(callback: CallbackQuery) -> None:
    """aiogram callback_query handler for `r:<id>:{done|snz|skp}` taps."""
    if _ctx is None:  # mis-wired runtime — fail loud once in logs, ack silently.
        _log.error("tg.callback.reminder_card.no_context")
        await _safe_ack(callback, "")
        return

    if callback.from_user is None or callback.data is None:
        _log.debug("tg.callback.reminder_card.skip_missing_fields")
        await _safe_ack(callback, "")
        return

    parsed = parse_reminder_callback(callback.data)
    if parsed is None:
        _log.info("tg.callback.reminder_card.bad_data", data=callback.data)
        await _safe_ack(callback, _ACK_BAD)
        return
    job_id, action = parsed
    user_id = callback.from_user.id

    async with _ctx.jobs_session_maker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            _log.info("tg.callback.reminder_card.unknown_job", job_id=job_id, user_id=user_id)
            await _safe_ack(callback, _ACK_BAD)
            return
        if job.owner_telegram_id != user_id:
            _log.warning(
                "tg.callback.reminder_card.owner_mismatch",
                job_id=job_id,
                user_id=user_id,
                owner=job.owner_telegram_id,
            )
            await _safe_ack(callback, _ACK_BAD)
            return

        if action == "done":
            changed = await _cas_set_state(session, job_id=job_id, target_state="done")
            await session.commit()
            anchor = "done" if changed else "idempotent_noop"
            _log.info(
                f"tg.callback.reminder_card.{anchor}",
                job_id=job_id,
                user_id=user_id,
            )
            await _safe_ack(callback, _ACK_DONE if changed else _ACK_NOOP)
            return

        if action == "skp":
            changed = await _cas_set_state(session, job_id=job_id, target_state="skipped")
            await session.commit()
            anchor = "skp" if changed else "idempotent_noop"
            _log.info(
                f"tg.callback.reminder_card.{anchor}",
                job_id=job_id,
                user_id=user_id,
            )
            await _safe_ack(callback, _ACK_SKIP if changed else _ACK_NOOP)
            return

        # action == "snz"
        if job.user_state != "pending":
            _log.info(
                "tg.callback.reminder_card.idempotent_noop",
                job_id=job_id,
                user_id=user_id,
                action="snz",
            )
            await _safe_ack(callback, _ACK_NOOP)
            return

        if job.snooze_count >= _SNOOZE_CAP:
            # Cap hit → collapse to skip (no reschedule).
            await _cas_set_state(session, job_id=job_id, target_state="skipped")
            await session.commit()
            _log.info(
                "tg.callback.reminder_card.snooze_cap_hit",
                job_id=job_id,
                user_id=user_id,
                snooze_count=job.snooze_count,
            )
            await _safe_ack(callback, _ACK_SKIP)
            return

        new_when_utc = _now_naive_utc() + _SNOOZE_DELTA
        new_count = job.snooze_count + 1
        # CAS-style: only bump if still pending (guards against done/skip race).
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.user_state == "pending")
            .values(scheduled_at_utc=new_when_utc, snooze_count=new_count)
        )
        if (result.rowcount or 0) == 0:
            await session.rollback()
            _log.info(
                "tg.callback.reminder_card.idempotent_noop",
                job_id=job_id,
                user_id=user_id,
                action="snz",
            )
            await _safe_ack(callback, _ACK_NOOP)
            return
        await session.commit()

    # Reschedule outside the DB session — APScheduler call is independent.
    _register_snooze_trigger(job_id=job_id, when_utc=new_when_utc, snooze_count=new_count)
    _log.info(
        "tg.callback.reminder_card.snz",
        job_id=job_id,
        user_id=user_id,
        snooze_count=new_count,
        when_utc=new_when_utc.isoformat(),
    )
    await _safe_ack(callback, _ACK_SNZ)


# END_BLOCK_HANDLE_REMINDER_CARD


def _register_snooze_trigger(*, job_id: int, when_utc: datetime, snooze_count: int) -> None:
    """Add a new APScheduler DateTrigger for the snoozed reminder.

    Job id pattern: `reminder:<id>:snz<n>` — keeps each snooze trigger uniquely
    addressable without colliding with the original `reminder:<id>` (already
    fired) or with prior snoozes.
    """
    assert _ctx is not None  # checked by caller
    # Lazy import to keep module import light + match firing.py convention.
    from apscheduler.triggers.date import DateTrigger

    from ai_steward_wiki.scheduler.firing import fire_job

    _ctx.scheduler.add_job(
        fire_job,
        trigger=DateTrigger(run_date=when_utc.replace(tzinfo=UTC)),
        args=[job_id],
        id=f"reminder:{job_id}:snz{snooze_count}",
        misfire_grace_time=None,
    )


async def _safe_ack(callback: CallbackQuery, text: str) -> None:
    """answer() never blocks the handler — TG quirks must not surface as 500."""
    try:
        await callback.answer(text or None)
    except Exception:  # pragma: no cover — defensive
        _log.debug("tg.callback.reminder_card.ack_failed")
