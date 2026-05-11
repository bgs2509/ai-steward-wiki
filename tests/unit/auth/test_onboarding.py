"""Onboarding: pending_users CRUD + intro template formatter."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.auth.onboarding import (
    PENDING_USER_TTL_DAYS,
    OnboardingTemplateError,
    PendingUserRepo,
    format_intro_message,
    purge_expired_pending,
    start_unknown_user,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture
def repo(session_maker):
    return PendingUserRepo(session_maker)


async def test_start_unknown_user_creates_pending(repo) -> None:
    now = datetime(2026, 5, 11, 12, 0, 0)
    rec = await start_unknown_user(repo, telegram_id=777, username="alice", now=now)
    assert rec.telegram_id == 777
    assert rec.username == "alice"
    assert rec.expires_at_utc == now + timedelta(days=PENDING_USER_TTL_DAYS)


async def test_start_unknown_user_idempotent_refreshes_ttl(repo) -> None:
    t0 = datetime(2026, 5, 1, 12, 0, 0)
    t1 = datetime(2026, 5, 3, 12, 0, 0)
    await start_unknown_user(repo, telegram_id=42, username=None, now=t0)
    rec = await start_unknown_user(repo, telegram_id=42, username="bob", now=t1)
    assert rec.expires_at_utc == t1 + timedelta(days=PENDING_USER_TTL_DAYS)
    # No duplicate row
    row = await repo.get(42)
    assert row is not None
    assert row.requested_at_utc == t1


async def test_purge_expired_pending_deletes_only_old(session_maker, repo) -> None:
    now = datetime(2026, 5, 20, 12, 0, 0)
    old = now - timedelta(days=15)
    fresh = now - timedelta(days=1)
    await start_unknown_user(repo, telegram_id=1, now=old)
    await start_unknown_user(repo, telegram_id=2, now=fresh)
    n = await purge_expired_pending(session_maker, now=now)
    assert n == 1
    assert await repo.get(1) is None
    assert await repo.get(2) is not None


async def test_purge_expired_pending_noop_when_all_fresh(session_maker, repo) -> None:
    now = datetime(2026, 5, 20, 12, 0, 0)
    await start_unknown_user(repo, telegram_id=10, now=now - timedelta(days=1))
    assert await purge_expired_pending(session_maker, now=now) == 0


def test_format_intro_message_renders_bot_name(tmp_path: Path) -> None:
    tpl = tmp_path / "intro.md"
    tpl.write_text(
        "<!-- slug:greeting -->\nПривет от {bot_name}.\n"
        "<!-- slug:purpose -->\nЦель.\n"
        "<!-- slug:capabilities -->\nУмения.\n"
        "<!-- slug:privacy -->\nПриватность.\n"
        "<!-- slug:next-steps -->\nДальше.\n"
        "<!-- slug:contact -->\nКонтакт.\n",
        encoding="utf-8",
    )
    out = format_intro_message(tpl, bot_name="MyBot")
    assert "MyBot" in out
    assert "<!-- slug:greeting -->" in out


def test_format_intro_message_raises_on_missing_slug(tmp_path: Path) -> None:
    tpl = tmp_path / "intro.md"
    # Missing 'contact'
    tpl.write_text(
        "<!-- slug:greeting -->\nx\n"
        "<!-- slug:purpose -->\nx\n"
        "<!-- slug:capabilities -->\nx\n"
        "<!-- slug:privacy -->\nx\n"
        "<!-- slug:next-steps -->\nx\n",
        encoding="utf-8",
    )
    with pytest.raises(OnboardingTemplateError, match="contact"):
        format_intro_message(tpl)


def test_format_intro_message_rejects_non_ru_locale(tmp_path: Path) -> None:
    tpl = tmp_path / "intro.md"
    tpl.write_text("x", encoding="utf-8")
    with pytest.raises(OnboardingTemplateError, match="locale"):
        format_intro_message(tpl, locale="en")


def test_real_template_passes(tmp_path: Path) -> None:
    real = REPO_ROOT / "templates" / "onboarding-intro.ru.md"
    out = format_intro_message(real, bot_name="aisw")
    assert "aisw" in out
