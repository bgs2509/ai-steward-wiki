"""AllowlistReloader: sha-noop, validate-before-swap, admin_alert, debounce."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.auth.allowlist import get_global, replace_global
from ai_steward_wiki.auth.sighup import DEBOUNCE_SECONDS, AllowlistReloader
from ai_steward_wiki.auth.users_toml import UsersConfig

REPO_ROOT = Path(__file__).resolve().parents[3]

VALID = """
schema_version = 1
[[users]]
telegram_id = 42
role = "admin"
"""

VALID_V2 = """
schema_version = 1
[[users]]
telegram_id = 42
role = "admin"
[[users]]
telegram_id = 7
"""

INVALID = "schema_version = 1\n[[users]]\ntelegram_id = 0\n"


@pytest.fixture
async def maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_global():
    replace_global(UsersConfig(schema_version=1, users=()))
    yield
    replace_global(UsersConfig(schema_version=1, users=()))


async def test_first_reload_replaces_cache(tmp_path, maker) -> None:
    p = tmp_path / "users.toml"
    p.write_text(VALID, encoding="utf-8")
    r = AllowlistReloader(p, maker)
    assert await r.reload() is True
    assert get_global().is_allowed(42)


async def test_sha_noop_on_unchanged_file(tmp_path, maker) -> None:
    p = tmp_path / "users.toml"
    p.write_text(VALID, encoding="utf-8")
    r = AllowlistReloader(p, maker)
    assert await r.reload() is True
    assert await r.reload() is False  # noop


async def test_validate_before_swap_keeps_prior(tmp_path, maker) -> None:
    p = tmp_path / "users.toml"
    p.write_text(VALID, encoding="utf-8")
    alert = AsyncMock()
    r = AllowlistReloader(p, maker, admin_alert=alert)
    await r.reload()
    assert get_global().is_allowed(42)

    p.write_text(INVALID, encoding="utf-8")
    assert await r.reload() is False
    assert get_global().is_allowed(42)  # unchanged
    alert.assert_awaited_once()
    assert "invalid" in alert.await_args.args[0]


async def test_alert_on_missing_file(tmp_path, maker) -> None:
    alert = AsyncMock()
    r = AllowlistReloader(tmp_path / "absent.toml", maker, admin_alert=alert)
    assert await r.reload() is False
    alert.assert_awaited_once()


async def test_debounce_collapses_burst(tmp_path, maker) -> None:
    p = tmp_path / "users.toml"
    p.write_text(VALID, encoding="utf-8")
    r = AllowlistReloader(p, maker)

    for _ in range(5):
        r.schedule_debounced()

    await asyncio.sleep(DEBOUNCE_SECONDS + 0.2)
    assert get_global().is_allowed(42)


async def test_reload_picks_up_changes(tmp_path, maker) -> None:
    p = tmp_path / "users.toml"
    p.write_text(VALID, encoding="utf-8")
    r = AllowlistReloader(p, maker)
    await r.reload()
    assert not get_global().is_allowed(7)

    p.write_text(VALID_V2, encoding="utf-8")
    assert await r.reload() is True
    assert get_global().is_allowed(7)
    assert get_global().is_allowed(42)
