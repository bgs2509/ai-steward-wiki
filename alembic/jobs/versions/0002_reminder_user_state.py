"""jobs.db: add jobs.user_state + jobs.snooze_count (digest cards, aisw-163).

First incremental migration past 0001_jobs_baseline. The baseline's upgrade()
is Base.metadata.create_all of LIVE metadata, so on a fresh DB the columns
already exist after baseline; this migration's upgrade() is therefore
idempotent (it ADDs each column only if missing) — it only does work on a DB
that was baselined before the columns existed. downgrade() drops them.

Convention recorded in ADR-026 (mirrors alembic/sessions/0002_user_digest_prefs).

Revision ID: 0002_reminder_user_state
Revises: 0001_jobs_baseline
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ai_steward_wiki.storage.jobs import models  # noqa: F401

revision: str = "0002_reminder_user_state"
down_revision: str | None = "0001_jobs_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table)}


def _existing_indexes(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    # Idempotent: ALTER each column only if missing (fresh baseline already
    # has them via Base.metadata.create_all).
    cols = _existing_columns("jobs")
    if "user_state" not in cols:
        op.add_column(
            "jobs",
            sa.Column(
                "user_state",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            ),
        )
    if "snooze_count" not in cols:
        op.add_column(
            "jobs",
            sa.Column(
                "snooze_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    indexes = _existing_indexes("jobs")
    if "ix_jobs_reminder_pending_window" not in indexes:
        op.create_index(
            "ix_jobs_reminder_pending_window",
            "jobs",
            ["user_state", "scheduled_at_utc"],
            sqlite_where=sa.text("kind = 'reminder_job'"),
        )


def downgrade() -> None:
    indexes = _existing_indexes("jobs")
    if "ix_jobs_reminder_pending_window" in indexes:
        op.drop_index("ix_jobs_reminder_pending_window", table_name="jobs")
    cols = _existing_columns("jobs")
    if "snooze_count" in cols:
        op.drop_column("jobs", "snooze_count")
    if "user_state" in cols:
        op.drop_column("jobs", "user_state")
