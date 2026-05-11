# FILE: src/ai_steward_wiki/scheduler/dlq.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Insert a row into jobs.db.jobs_dlq for a permanently-failed job (D-019, INV-11).
#   SCOPE: Single helper move_to_dlq. No retry orchestration here.
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.jobs.models.JobDLQ
#   LINKS: M-SCHEDULER, M-STORAGE-JOBS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   move_to_dlq - persist (job_id, reason, error_class, last_error) into jobs_dlq
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - chunk 4: DLQ writer for jobs.db.jobs_dlq (D-019, INV-11)
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ai_steward_wiki.scheduler.failure import FailureClass
from ai_steward_wiki.storage.jobs.models import JobDLQ

__all__ = [
    "move_to_dlq",
]


async def move_to_dlq(
    session: AsyncSession,
    *,
    job_id: int,
    reason: str,
    error_class: FailureClass | str | None,
    last_error: str | None,
) -> JobDLQ:
    cls_value = error_class.value if isinstance(error_class, FailureClass) else error_class
    row = JobDLQ(
        job_id=job_id,
        reason=reason,
        error_class=cls_value,
        last_error=last_error,
        moved_at_utc=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    await session.flush()
    return row
