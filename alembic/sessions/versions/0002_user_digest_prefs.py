"""sessions.db: add user_digest_prefs (per-user digest section toggles).

First incremental migration past 0001_sessions_baseline. The baseline's
upgrade() is Base.metadata.create_all of LIVE metadata, so on a fresh DB the
table is already created by the baseline step; this migration's upgrade() is
therefore idempotent (create_all with checkfirst=True) — it only does work on
a DB that was baselined before UserDigestPrefs existed. downgrade() drops the
table explicitly. Convention recorded in ADR-026.

Revision ID: 0002_user_digest_prefs
Revises: 0001_sessions_baseline
Create Date: 2026-05-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from ai_steward_wiki.storage.sessions import models  # noqa: F401
from ai_steward_wiki.storage.sessions.engine import Base

revision: str = "0002_user_digest_prefs"
down_revision: str | None = "0001_sessions_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: creates user_digest_prefs if missing, no-op otherwise.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    op.drop_table("user_digest_prefs")
