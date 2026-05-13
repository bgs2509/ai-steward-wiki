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
            {
                "users",
                "pending_users",
                "pending_confirms",
                "inbox_hint_cache",
                "fsm",
                "user_digest_prefs",
            },
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


def test_jobs_baseline_has_user_state_columns(tmp_path, monkeypatch):
    """aisw-163: a fresh baseline (`create_all`) must include user_state + snooze_count."""
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("AISW_JOBS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "jobs" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "jobs"))
    command.upgrade(cfg, "head")

    import sqlite3

    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    assert "user_state" in cols, f"missing user_state in jobs; got {cols}"
    assert "snooze_count" in cols, f"missing snooze_count in jobs; got {cols}"


def test_jobs_stepwise_upgrade_adds_user_state(tmp_path, monkeypatch):
    """aisw-163: stamping 0001 then upgrading to head → 0002 ALTERs the existing baseline."""
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("AISW_JOBS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "jobs" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "jobs"))
    # Simulate an old baseline by stamping it (no metadata create) — but the
    # baseline IS create_all of LIVE metadata, so even at 0001 the columns
    # exist. We instead drop them after baseline to simulate a pre-feature DB.
    command.upgrade(cfg, "0001_jobs_baseline")
    import sqlite3

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Drop the two columns + the partial index that references one of them to
    # simulate an older schema. SQLite supports DROP COLUMN as of 3.35.
    cur.execute("DROP INDEX IF EXISTS ix_jobs_reminder_pending_window")
    cur.execute("ALTER TABLE jobs DROP COLUMN user_state")
    cur.execute("ALTER TABLE jobs DROP COLUMN snooze_count")
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    assert "user_state" in cols
    assert "snooze_count" in cols


def test_sessions_stepwise_upgrade_creates_user_digest_prefs(tmp_path, monkeypatch):
    """Stamp 0001 then upgrade to head → 0002 brings user_digest_prefs onto an already-baselined DB."""
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "0001_sessions_baseline")
    command.upgrade(cfg, "head")

    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert "user_digest_prefs" in rows
