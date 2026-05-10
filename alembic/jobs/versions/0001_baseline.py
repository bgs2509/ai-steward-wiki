"""jobs.db baseline — jobs, tracker_answers, jobs_dlq.

Revision ID: 0001_jobs_baseline
Revises:
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from ai_steward_wiki.storage.jobs import models  # noqa: F401
from ai_steward_wiki.storage.jobs.engine import Base

revision: str = "0001_jobs_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
