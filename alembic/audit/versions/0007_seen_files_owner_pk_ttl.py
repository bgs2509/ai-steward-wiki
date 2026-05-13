"""audit.db: seen_files composite PK (owner_telegram_id, content_sha256).

D-018 amended 2026-05-13 (ADR-028): per-owner L2 dedup scope + per-kind TTL.
Legacy rows are dropped — seen_files is forensic-only, 30d retention, no business loss.
SQLite PK swap requires full table rebuild; we drop+recreate explicitly.

Revision ID: 0007_seen_files_owner_pk_ttl
Revises: 0006_retention_columns
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_seen_files_owner_pk_ttl"
down_revision: str | None = "0006_retention_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Audit-only data; D-018 §retention allows wipe on schema change.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "seen_files" in inspector.get_table_names():
        op.drop_table("seen_files")
    op.create_table(
        "seen_files",
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at_utc", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("owner_telegram_id", "content_sha256", name="pk_seen_files"),
    )
    op.create_index(
        "ix_seen_files_first_seen_at_utc",
        "seen_files",
        ["first_seen_at_utc"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "seen_files" in inspector.get_table_names():
        op.drop_table("seen_files")
    op.create_table(
        "seen_files",
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at_utc", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("content_sha256", name="pk_seen_files"),
    )
    op.create_index(
        "ix_seen_files_owner_telegram_id",
        "seen_files",
        ["owner_telegram_id"],
    )
    op.create_index(
        "ix_seen_files_first_seen_at_utc",
        "seen_files",
        ["first_seen_at_utc"],
    )
