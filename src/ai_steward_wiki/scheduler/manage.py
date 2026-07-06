# FILE: src/ai_steward_wiki/scheduler/manage.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Generic job-management surface — list/needle-match/cancel/reschedule
#            over the 5 user-facing job kinds (aisw-xi8, DEC-9). Every function
#            takes an explicit AsyncSession + AsyncIOScheduler (no module-level
#            context registry — called synchronously from the tg request path,
#            never from a picklable APScheduler callback).
#   SCOPE: OwnerJob, list_owner_jobs, match_jobs_by_needle, cancel_job,
#          reschedule_once, reschedule_recurring, _job_key.
#   DEPENDS: apscheduler, sqlalchemy(.ext.asyncio), structlog, pydantic,
#            ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads (JobPayload, parse_job_payload),
#            ai_steward_wiki.classifier.recurrence.Recurrence,
#            ai_steward_wiki.inbox.hint_match.tokens
#   LINKS: M-SCHEDULER-MANAGE, M-STORAGE-JOBS, M-SCHEDULER-FIRING,
#          M-SCHEDULER-CRON-USER, M-INBOX, aisw-xi8, DEC-9, DEC-10
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   OwnerJob - frozen row view: id, kind, payload, scheduled_at_utc, rendered (ru string)
#   list_owner_jobs - the owner's enabled user-facing jobs, rendered
#   match_jobs_by_needle - casefold whole-token overlap scoring over OwnerJob.rendered
#   cancel_job - scheduler.remove_job (tolerant of a missing entry) + status='cancelled'
#   reschedule_once - scheduler.reschedule_job(DateTrigger) + scheduled_at_utc update
#   reschedule_recurring - scheduler.reschedule_job(CronTrigger) + payload.recurrence rewrite
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-xi8 (Phase-B, DEC-9): initial job-management module.
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import structlog
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update

from ai_steward_wiki.inbox.hint_match import tokens
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import JobPayload, parse_job_payload

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession

    from ai_steward_wiki.classifier.recurrence import Recurrence

__all__ = [
    "OwnerJob",
    "cancel_job",
    "list_owner_jobs",
    "match_jobs_by_needle",
    "reschedule_once",
    "reschedule_recurring",
]

_log = structlog.get_logger("scheduler.manage")

# Kinds a user ever sees via "какие у меня напоминания"-style queries. purge and  # noqa: RUF003
# wiki_run are system-internal — never listed, never cancellable this way.
_USER_FACING_KINDS = frozenset(
    {"reminder_job", "recurring_reminder", "check_in", "digest", "cron_user"}
)

# SSoT job-id-string registry — MUST match the literal id= strings already used
# at firing.py:217 (reminder:), firing.py:564 (digest:), and cron_user.py:125
# (cron_user:). recurring_reminder/check_in are new in this feature (Phase B
# Tasks B4/B5 register their APScheduler jobs under these exact prefixes).
_JOB_KEY_PREFIX = {
    "reminder_job": "reminder",
    "recurring_reminder": "recurring",
    "check_in": "check_in",
    "digest": "digest",
    "cron_user": "cron_user",
}

_WEEKDAY_RU_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")  # noqa: RUF001


def _job_key(kind: str, job_id: int) -> str:
    """kind -> "<prefix>:<id>" — the exact APScheduler job id string for this row."""
    prefix = _JOB_KEY_PREFIX.get(kind, kind)
    return f"{prefix}:{job_id}"


def _humanize_recurrence(rec: Recurrence) -> str:
    """Local ru rendering of a Recurrence (scheduler-layer copy — tg/pipeline.py's
    humanize_recurrence is the tg-layer twin; this module must not import from
    tg/, so the small pure function is duplicated per house convention, same as
    tg/cron_add.py's private _humanize_recurrence)."""
    if rec.kind == "daily":
        return f"каждый день в {rec.time_hhmm}"
    if rec.kind == "monthly":
        return f"{rec.day_of_month} числа каждого месяца в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (0, 1, 2, 3, 4):
        return f"по будням в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (5, 6):
        return f"по выходным в {rec.time_hhmm}"
    days = ", ".join(_WEEKDAY_RU_SHORT[d] for d in sorted(set(rec.weekdays)))
    return f"по дням ({days}) в {rec.time_hhmm}"


@dataclass(frozen=True, slots=True)
class OwnerJob:
    """One rendered row for the job-management surface (DEC-9)."""

    id: int
    kind: str
    payload: JobPayload
    scheduled_at_utc: datetime | None
    rendered: str


def _render_job(row: Job, payload: JobPayload, zone: ZoneInfo) -> str:
    if row.kind == "reminder_job" and row.scheduled_at_utc is not None:
        local = row.scheduled_at_utc.replace(tzinfo=UTC).astimezone(zone)
        message = getattr(payload, "message", "")
        return f"{local:%d.%m %H:%M} — {message}"
    if row.kind == "recurring_reminder":
        message = getattr(payload, "message", "")
        rec = getattr(payload, "recurrence", None)
        schedule = _humanize_recurrence(rec) if rec is not None else "?"
        return f"{schedule} — {message}"
    if row.kind == "check_in":
        topic = getattr(payload, "question_topic", "")
        rec = getattr(payload, "recurrence", None)
        schedule = _humanize_recurrence(rec) if rec is not None else "?"
        return f"{schedule} — вопрос: {topic}"
    if row.kind == "digest":
        rec = getattr(payload, "recurrence", None)
        schedule = _humanize_recurrence(rec) if rec is not None else "?"
        return f"сводка {schedule}"
    if row.kind == "cron_user":
        rec = getattr(payload, "recurrence", None)
        command = getattr(payload, "command", "")
        schedule = _humanize_recurrence(rec) if rec is not None else "?"
        return f"{schedule} — {command}"
    return row.kind


async def list_owner_jobs(
    session: AsyncSession, owner_telegram_id: int, *, user_tz: str = "Europe/Moscow"
) -> list[OwnerJob]:
    """The owner's enabled user-facing jobs, rendered (DEC-9).

    'Enabled' means Job.status=='pending' for the once-shaped reminder_job kind,
    or Job.status=='scheduled' for every cron-shaped kind. purge/wiki_run rows
    are excluded even for a matching owner (system-internal, never user-visible).
    """
    rows = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.owner_telegram_id == owner_telegram_id,
                    Job.kind.in_(_USER_FACING_KINDS),
                    or_(
                        and_(Job.kind == "reminder_job", Job.status == "pending"),
                        and_(Job.kind != "reminder_job", Job.status == "scheduled"),
                    ),
                )
                .order_by(Job.id)
            )
        )
        .scalars()
        .all()
    )
    zone = ZoneInfo(user_tz)
    out: list[OwnerJob] = []
    for row in rows:
        try:
            payload = parse_job_payload(row.payload)
        except ValidationError:
            _log.warning("scheduler.manage.bad_payload_skipped", job_id=row.id, kind=row.kind)
            continue
        out.append(
            OwnerJob(
                id=row.id,
                kind=row.kind,
                payload=payload,
                scheduled_at_utc=row.scheduled_at_utc,
                rendered=_render_job(row, payload, zone),
            )
        )
    return out


def match_jobs_by_needle(jobs: Sequence[OwnerJob], needle: str) -> list[OwnerJob]:
    """Casefold whole-token overlap over OwnerJob.rendered (DEC-9, needle disambiguation).

    An empty/whitespace-only needle matches nothing (fail-safe — never "everything").
    A single strict top scorer -> [that job]. A tie at the top, or several jobs
    scoring >0 with no strict winner, -> all matched jobs, ranked by score desc.
    """
    needle_tokens = tokens(needle)
    if not needle_tokens:
        return []
    scored = [(job, len(needle_tokens & tokens(job.rendered))) for job in jobs]
    scored = [(job, score) for job, score in scored if score > 0]
    if not scored:
        return []
    scored.sort(key=lambda kv: -kv[1])
    top_score = scored[0][1]
    winners = [job for job, score in scored if score == top_score]
    if len(winners) == 1:
        return winners
    return [job for job, _ in scored]


async def cancel_job(scheduler: AsyncIOScheduler, session: AsyncSession, job: OwnerJob) -> None:
    """Remove the APScheduler trigger (tolerant of a missing entry) and mark the
    row cancelled. Idempotent on retry (DEC-9)."""
    key = _job_key(job.kind, job.id)
    with contextlib.suppress(JobLookupError):
        scheduler.remove_job(key)
    await session.execute(update(Job).where(Job.id == job.id).values(status="cancelled"))
    await session.commit()
    _log.info("scheduler.manage.cancelled", job_id=job.id, kind=job.kind, job_key=key)


async def reschedule_once(
    scheduler: AsyncIOScheduler,
    session: AsyncSession,
    job: OwnerJob,
    new_when_utc: datetime,
) -> None:
    """Move a once-shaped job's DateTrigger + update Job.scheduled_at_utc (DEC-9).

    Callers validate new_when_utc is in the future BEFORE calling this (the same
    parse_time validator the create-path uses) — this function does not re-check.
    """
    key = _job_key(job.kind, job.id)
    scheduler.reschedule_job(key, trigger=DateTrigger(run_date=new_when_utc))
    await session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(scheduled_at_utc=new_when_utc.astimezone(UTC).replace(tzinfo=None))
    )
    await session.commit()
    _log.info(
        "scheduler.manage.rescheduled_once",
        job_id=job.id,
        kind=job.kind,
        when_utc=new_when_utc.astimezone(UTC).isoformat(),
    )


def _with_recurrence(payload: JobPayload, new_recurrence: Recurrence) -> JobPayload:
    """Rebuild payload with its recurrence field replaced, re-validated end-to-end."""
    data = payload.model_dump(mode="json")
    data["recurrence"] = new_recurrence.model_dump(mode="json")
    return parse_job_payload(data)


async def reschedule_recurring(
    scheduler: AsyncIOScheduler,
    session: AsyncSession,
    job: OwnerJob,
    new_recurrence: Recurrence,
) -> None:
    """Move a cron-shaped job's CronTrigger + rewrite payload.recurrence (DEC-9,
    resolved Q3). Closes the measured #35/#91/#99 digest-control defect cluster —
    digest reschedule now works generically, alongside recurring_reminder/cron_user."""
    key = _job_key(job.kind, job.id)
    scheduler.reschedule_job(
        key, trigger=CronTrigger(timezone=new_recurrence.tz, **new_recurrence.to_cron())
    )
    new_payload = _with_recurrence(job.payload, new_recurrence)
    await session.execute(
        update(Job).where(Job.id == job.id).values(payload=new_payload.model_dump(mode="json"))
    )
    await session.commit()
    _log.info(
        "scheduler.manage.rescheduled_recurring",
        job_id=job.id,
        kind=job.kind,
        recurrence=new_recurrence.model_dump(mode="json"),
    )
