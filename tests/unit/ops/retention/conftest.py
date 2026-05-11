"""Shared fixtures for retention tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[4]


def _migrate(db_kind: str, db_path: Path, env_var: str, monkeypatch) -> None:
    monkeypatch.setenv(env_var, f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / db_kind / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / db_kind))
    command.upgrade(cfg, "head")


@pytest.fixture
async def audit_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    _migrate("audit", db_path, "AISW_AUDIT_DB_URL_SYNC", monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def jobs_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.db"
    _migrate("jobs", db_path, "AISW_JOBS_DB_URL_SYNC", monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def sessions_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    _migrate("sessions", db_path, "AISW_SESSIONS_DB_URL_SYNC", monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
