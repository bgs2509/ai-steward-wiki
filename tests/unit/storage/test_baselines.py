"""Run alembic baselines for the three DBs and assert expected tables exist."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("db", "env_var", "expected_tables"),
    [
        (
            "jobs",
            "AISW_JOBS_DB_URL_SYNC",
            {"jobs", "tracker_answers", "jobs_dlq"},
        ),
        (
            "audit",
            "AISW_AUDIT_DB_URL_SYNC",
            {
                "chat_log",
                "audit_events",
                "admin_events",
                "tg_updates",
                "seen_files",
                "dedup_hits",
                "job_outputs",
                "run_outputs",
                "prompt_versions",
                "onboarding_events",
            },
        ),
        (
            "sessions",
            "AISW_SESSIONS_DB_URL_SYNC",
            {"users", "pending_users", "pending_confirms", "inbox_hint_cache", "fsm"},
        ),
    ],
)
def test_alembic_upgrade_creates_tables(db, env_var, expected_tables, tmp_path, monkeypatch):
    db_path = tmp_path / f"{db}.db"
    monkeypatch.setenv(env_var, f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / db / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / db))
    command.upgrade(cfg, "head")

    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    actual = {r[0] for r in rows}
    missing = expected_tables - actual
    assert not missing, f"missing tables in {db}.db: {missing}; got {actual}"
