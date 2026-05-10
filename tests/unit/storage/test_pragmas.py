"""Verify PRAGMAs are applied to every connection of an engine built via build_engine."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from ai_steward_wiki.storage.audit.engine import build_engine as build_audit
from ai_steward_wiki.storage.jobs.engine import build_engine as build_jobs
from ai_steward_wiki.storage.sessions.engine import build_engine as build_sessions


@pytest.mark.parametrize(
    "factory",
    [build_jobs, build_audit, build_sessions],
)
async def test_pragmas_applied(factory, tmp_path):
    db_path = tmp_path / "t.db"
    engine = factory(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.connect() as conn:
            jm = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            sync = (await conn.execute(text("PRAGMA synchronous"))).scalar_one()
            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            bt = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
        assert str(jm).lower() == "wal"
        assert int(sync) == 1  # NORMAL
        assert int(fk) == 1
        assert int(bt) == 5000
    finally:
        await engine.dispose()
