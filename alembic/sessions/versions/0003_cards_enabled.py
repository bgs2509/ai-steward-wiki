"""sessions.db: add user_digest_prefs.cards_enabled (digest cards opt-out, aisw-163).

Second incremental migration past 0001_sessions_baseline. The baseline's
upgrade() is Base.metadata.create_all of LIVE metadata, so on a fresh DB the
column already exists after baseline; this migration's upgrade() is therefore
idempotent (ADD COLUMN only if missing). downgrade() drops it.

Convention recorded in ADR-026 (mirrors alembic/jobs/0002_reminder_user_state).

Revision ID: 0003_cards_enabled
Revises: 0002_user_digest_prefs
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ai_steward_wiki.storage.sessions import models  # noqa: F401

revision: str = "0003_cards_enabled"
down_revision: str | None = "0002_user_digest_prefs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("user_digest_prefs")
    if "cards_enabled" not in cols:
        op.add_column(
            "user_digest_prefs",
            sa.Column(
                "cards_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )


def downgrade() -> None:
    cols = _existing_columns("user_digest_prefs")
    if "cards_enabled" in cols:
        op.drop_column("user_digest_prefs", "cards_enabled")
