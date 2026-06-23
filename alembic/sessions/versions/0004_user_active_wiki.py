"""sessions.db: add user_active_wiki sticky pointer (aisw-0ym).

Third incremental migration past 0001_sessions_baseline. The baseline's
upgrade() is Base.metadata.create_all of LIVE metadata, so on a fresh DB the
table already exists after baseline; this migration's upgrade() is therefore
idempotent (CREATE TABLE only if missing). downgrade() drops it.

Convention recorded in ADR-026 (mirrors alembic/sessions/0003_cards_enabled).

Revision ID: 0004_user_active_wiki
Revises: 0003_cards_enabled
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ai_steward_wiki.storage.sessions import models  # noqa: F401

revision: str = "0004_user_active_wiki"
down_revision: str | None = "0003_cards_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def upgrade() -> None:
    if "user_active_wiki" not in _existing_tables():
        op.create_table(
            "user_active_wiki",
            sa.Column("telegram_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("wiki_name", sa.String(length=128), nullable=False),
            sa.Column("updated_at_utc", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    if "user_active_wiki" in _existing_tables():
        op.drop_table("user_active_wiki")
