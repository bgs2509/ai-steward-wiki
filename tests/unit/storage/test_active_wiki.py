"""Unit tests for the sticky active-WIKI pointer (aisw-0ym)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.sessions.active_wiki import (
    ACTIVE_WIKI_TTL_HOURS,
    ActiveWikiPointer,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def sessions_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _now() -> datetime:
    return datetime(2026, 6, 23, 12, 0, 0)


def test_ttl_matches_chat_log_window() -> None:
    assert ACTIVE_WIKI_TTL_HOURS == 24


@pytest.mark.asyncio
async def test_set_then_get_returns_wiki(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    now = _now()
    await p.set_active(7, "Medical-WIKI", now=now)
    assert await p.get_active(7, now=now + timedelta(minutes=1)) == "Medical-WIKI"


@pytest.mark.asyncio
async def test_get_unknown_user_is_none(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    assert await p.get_active(999, now=_now()) is None


@pytest.mark.asyncio
async def test_set_active_upserts(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    now = _now()
    await p.set_active(7, "Medical-WIKI", now=now)
    await p.set_active(7, "Budget-WIKI", now=now + timedelta(minutes=5))
    assert await p.get_active(7, now=now + timedelta(minutes=6)) == "Budget-WIKI"


@pytest.mark.asyncio
async def test_stale_pointer_returns_none(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    now = _now()
    await p.set_active(7, "Medical-WIKI", now=now - timedelta(hours=30))
    assert await p.get_active(7, now=now) is None


@pytest.mark.asyncio
async def test_fresh_within_ttl_returns_wiki(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    now = _now()
    await p.set_active(7, "Medical-WIKI", now=now - timedelta(hours=23))
    assert await p.get_active(7, now=now) == "Medical-WIKI"


@pytest.mark.asyncio
async def test_pointer_is_per_user(sessions_maker) -> None:
    p = ActiveWikiPointer(sessions_maker)
    now = _now()
    await p.set_active(1, "Medical-WIKI", now=now)
    await p.set_active(2, "Budget-WIKI", now=now)
    assert await p.get_active(1, now=now) == "Medical-WIKI"
    assert await p.get_active(2, now=now) == "Budget-WIKI"
