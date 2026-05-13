"""Smoke test for alembic 0007: seen_files composite PK swap (aisw-5hy / ADR-028).

Verifies upgrade path on a tmp audit.db: legacy single-column PK rows are wiped,
post-upgrade schema has composite PK (owner_telegram_id, content_sha256).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="integration: set RUN_INTEGRATION=1",
)


def _alembic_cfg(db_path: Path) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    os.environ["AISW_AUDIT_DB_URL_SYNC"] = f"sqlite:///{db_path}"
    return cfg


def test_migration_0007_swaps_to_composite_pk(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    cfg = _alembic_cfg(db)

    # 1) Upgrade to 0006 first so we have the legacy single-column PK schema.
    command.upgrade(cfg, "0006_retention_columns")

    # 2) Seed three legacy rows (content_sha256 PK only).
    conn = sqlite3.connect(db)
    try:
        conn.executemany(
            "INSERT INTO seen_files (content_sha256, kind, owner_telegram_id, first_seen_at_utc) "
            "VALUES (?, ?, ?, ?)",
            [
                ("a" * 64, "text", 1001, "2026-05-01 00:00:00"),
                ("b" * 64, "voice", 1001, "2026-05-01 00:00:00"),
                ("c" * 64, "photo", 2002, "2026-05-01 00:00:00"),
            ],
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM seen_files").fetchone()[0] == 3
    finally:
        conn.close()

    # 3) Upgrade to 0007 (PK swap, wipes data per D-local-4).
    command.upgrade(cfg, "head")

    # 4) Assert: composite PK + table is empty.
    conn = sqlite3.connect(db)
    try:
        cols = conn.execute("PRAGMA table_info('seen_files')").fetchall()
        pk_cols = sorted(c[1] for c in cols if c[5] > 0)  # pk-flagged columns
        assert pk_cols == ["content_sha256", "owner_telegram_id"]

        count = conn.execute("SELECT COUNT(*) FROM seen_files").fetchone()[0]
        assert count == 0

        # 5) New composite PK enforces per-owner uniqueness — same sha for different
        # owners is OK; same (owner, sha) duplicate is rejected.
        conn.execute(
            "INSERT INTO seen_files VALUES (?, ?, ?, ?)",
            (1001, "a" * 64, "text", "2026-05-13 12:00:00"),
        )
        conn.execute(
            "INSERT INTO seen_files VALUES (?, ?, ?, ?)",
            (2002, "a" * 64, "text", "2026-05-13 12:00:00"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO seen_files VALUES (?, ?, ?, ?)",
                (1001, "a" * 64, "text", "2026-05-13 12:01:00"),
            )
    finally:
        conn.close()
