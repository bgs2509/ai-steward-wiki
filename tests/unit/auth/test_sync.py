"""sync_to_sessions_db upserts users and soft-disables removed ones."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.auth.allowlist import sync_to_sessions_db
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.storage.sessions.models import User

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _cfg(*recs: UserRecord) -> UsersConfig:
    return UsersConfig(schema_version=1, users=recs)


async def _users(maker) -> list[User]:
    async with maker() as s:
        return list((await s.execute(select(User).order_by(User.telegram_id))).scalars())


async def test_initial_insert(session_maker) -> None:
    cfg = _cfg(
        UserRecord(telegram_id=1, role="admin", display_name="A"),
        UserRecord(telegram_id=2, tz="Europe/Moscow"),
    )
    await sync_to_sessions_db(cfg, session_maker)
    rows = await _users(session_maker)
    assert [u.telegram_id for u in rows] == [1, 2]
    assert rows[0].role == "admin"
    assert rows[0].enabled is True
    assert rows[1].tz == "Europe/Moscow"


async def test_update_existing(session_maker) -> None:
    await sync_to_sessions_db(_cfg(UserRecord(telegram_id=1, role="user")), session_maker)
    await sync_to_sessions_db(
        _cfg(UserRecord(telegram_id=1, role="admin", display_name="X")),
        session_maker,
    )
    rows = await _users(session_maker)
    assert len(rows) == 1
    assert rows[0].role == "admin"
    assert rows[0].display_name == "X"


async def test_soft_disable_removed(session_maker) -> None:
    await sync_to_sessions_db(
        _cfg(UserRecord(telegram_id=1), UserRecord(telegram_id=2)),
        session_maker,
    )
    await sync_to_sessions_db(_cfg(UserRecord(telegram_id=1)), session_maker)
    rows = await _users(session_maker)
    by_tg = {u.telegram_id: u for u in rows}
    assert by_tg[1].enabled is True
    assert by_tg[2].enabled is False  # soft-disabled, not deleted


async def test_disabled_in_toml_propagates(session_maker) -> None:
    await sync_to_sessions_db(
        _cfg(UserRecord(telegram_id=1, enabled=False)),
        session_maker,
    )
    rows = await _users(session_maker)
    assert rows[0].enabled is False
