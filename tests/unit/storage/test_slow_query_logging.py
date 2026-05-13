"""Slow-query log listener — emits storage.slow_query above threshold, silent below."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from structlog.testing import capture_logs

from ai_steward_wiki.storage.slow_query import attach_slow_query_logging


async def _exec_once(engine, sql: str) -> None:
    async with engine.connect() as conn:
        await conn.execute(text(sql))


def test_above_threshold_emits_slow_query() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    # Threshold 0 → every query is slow.
    attach_slow_query_logging(engine, db_name="jobs", threshold_ms=-1)
    sql = "SELECT 1 AS one"
    with capture_logs() as logs:
        asyncio.run(_exec_once(engine, sql))
    rows = [r for r in logs if r["event"] == "storage.slow_query"]
    assert rows, f"expected storage.slow_query event, got {logs}"
    rec = rows[0]
    assert rec["log_level"] == "warning"
    assert rec["db_name"] == "jobs"
    assert isinstance(rec["duration_ms"], int)
    assert rec["duration_ms"] >= 0
    expected_sha8 = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:8]
    assert rec["statement_sha8"] == expected_sha8
    # Critical PII gate: raw SQL and parameters must NOT appear anywhere.
    serialized = repr(rec)
    assert "SELECT 1" not in serialized
    assert "AS one" not in serialized


def test_below_threshold_emits_nothing() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    attach_slow_query_logging(engine, db_name="audit", threshold_ms=10_000)
    with capture_logs() as logs:
        asyncio.run(_exec_once(engine, "SELECT 2"))
    assert [r for r in logs if r["event"] == "storage.slow_query"] == []


def test_per_db_label_propagates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    attach_slow_query_logging(engine, db_name="sessions", threshold_ms=-1)
    with capture_logs() as logs:
        asyncio.run(_exec_once(engine, "SELECT 3"))
    rows = [r for r in logs if r["event"] == "storage.slow_query"]
    assert rows
    assert rows[0]["db_name"] == "sessions"


@pytest.mark.parametrize(
    ("module_path", "expected_db_name"),
    [
        ("ai_steward_wiki.storage.jobs.engine", "jobs"),
        ("ai_steward_wiki.storage.audit.engine", "audit"),
        ("ai_steward_wiki.storage.sessions.engine", "sessions"),
    ],
)
def test_build_engine_attaches_slow_query_listener(
    module_path: str,
    expected_db_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-DB build_engine wires the listener with threshold 0 → query emits warning."""
    import importlib

    # Force a threshold of 0 via the settings singleton so every query is "slow".
    from ai_steward_wiki import settings as settings_mod

    monkeypatch.setattr(
        settings_mod.get_settings.__wrapped__,
        "__defaults__",
        settings_mod.get_settings.__wrapped__.__defaults__,
    )
    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("AISW_STORAGE_SLOW_QUERY_THRESHOLD_MS", "-1")

    mod = importlib.import_module(module_path)
    engine = mod.build_engine("sqlite+aiosqlite:///:memory:")
    with capture_logs() as logs:
        asyncio.run(_exec_once(engine, f"SELECT '{expected_db_name}'"))
    settings_mod.get_settings.cache_clear()  # restore default for other tests

    rows = [r for r in logs if r["event"] == "storage.slow_query"]
    assert rows, f"expected storage.slow_query from {module_path}, got {logs}"
    assert rows[0]["db_name"] == expected_db_name
