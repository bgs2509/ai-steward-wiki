# FILE: src/ai_steward_wiki/storage/jobs/models.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: ORM models for jobs.db — operational hot store (D-002, D-014, D-019).
#   SCOPE: jobs (flat + JSON payload), tracker_answers, jobs_dlq.
#          APScheduler tables are owned by SQLAlchemyJobStore — created at runtime, not here.
#   DEPENDS: SQLAlchemy.orm, ai_steward_wiki.storage.jobs.engine.Base
#   LINKS: M-STORAGE-JOBS, M-SCHEDULER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Job - row in jobs.jobs (flat columns + JSON payload)
#   TrackerAnswer - append-only row for top-3 prediction window (D-014)
#   JobDLQ - dead-letter row for permanently-failed jobs (D-019)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - initial jobs.db ORM models
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_steward_wiki.storage.jobs.engine import Base

__all__ = [
    "Job",
    "JobDLQ",
    "TrackerAnswer",
]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    scheduled_at_utc: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    started_at_utc: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at_utc: Mapped[datetime | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False)


class TrackerAnswer(Base):
    """Append-only tracker memory (D-014). Retention 90d via APScheduler purge."""

    __tablename__ = "tracker_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_key: Mapped[str] = mapped_column(String(128), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False)


Index(
    "ix_tracker_answers_owner_question_created",
    TrackerAnswer.owner_telegram_id,
    TrackerAnswer.question_key,
    TrackerAnswer.created_at_utc,
)


class JobDLQ(Base):
    """Dead-letter queue for jobs that exceeded retries or hit Permanent failure (D-019)."""

    __tablename__ = "jobs_dlq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    moved_at_utc: Mapped[datetime] = mapped_column(nullable=False)
