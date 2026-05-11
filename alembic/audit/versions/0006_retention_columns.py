"""audit.db: ensure created_at_utc / equivalent ts columns on all retention targets.

Chunk 13 recon: all tables targeted by §10.4 retention purges already carry a
timestamp column from baseline (created_at_utc, received_at_utc, first_seen_at_utc,
fired_at_utc, started_at_utc, shown_at_utc). This revision is therefore
**defensive-additive**: it inspects each table and adds the column only when
missing. On fresh dev DBs upgraded via baseline.create_all(), nothing changes.

Revision ID: 0006_retention_columns
Revises: 0005_admin_events_ext
Create Date: 2026-05-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_retention_columns"
down_revision: str | None = "0005_admin_events_ext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, sqltype) — only tables whose retention key is `created_at_utc`.
_EXPECTED: list[tuple[str, str, sa.types.TypeEngine[object]]] = [
    ("chat_log", "created_at_utc", sa.DateTime()),
    ("audit_events", "created_at_utc", sa.DateTime()),
    ("admin_events", "created_at_utc", sa.DateTime()),
    ("dedup_hits", "created_at_utc", sa.DateTime()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table, column, type_ in _EXPECTED:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column in cols:
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(column, type_, nullable=True))


def downgrade() -> None:
    # Defensive-additive only — downgrade is intentionally a no-op.
    pass
