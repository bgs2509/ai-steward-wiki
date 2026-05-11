"""audit.db: extend admin_events with target_telegram_id, outcome, reason (chunk 12).

Revision ID: 0005_admin_events_ext
Revises: 0001_audit_baseline
Create Date: 2026-05-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_admin_events_ext"
down_revision: str | None = "0001_audit_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Baseline (0001_audit_baseline) uses metadata.create_all() so on fresh
    # DBs the new columns are already present. Detect and skip to keep this
    # revision additive and re-runnable in mixed-state environments.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("admin_events")}
    to_add = [
        ("target_telegram_id", sa.BigInteger()),
        ("outcome", sa.String(length=32)),
        ("reason", sa.Text()),
    ]
    missing = [(n, t) for (n, t) in to_add if n not in existing_cols]
    if missing:
        with op.batch_alter_table("admin_events") as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))
    existing_indexes = {i["name"] for i in inspector.get_indexes("admin_events")}
    if "ix_admin_events_target_telegram_id" not in existing_indexes:
        op.create_index(
            "ix_admin_events_target_telegram_id",
            "admin_events",
            ["target_telegram_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_admin_events_target_telegram_id", table_name="admin_events")
    with op.batch_alter_table("admin_events") as batch:
        batch.drop_column("reason")
        batch.drop_column("outcome")
        batch.drop_column("target_telegram_id")
