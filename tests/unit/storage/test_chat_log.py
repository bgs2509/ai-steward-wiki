"""Unit tests for the D-033 chat_log writer/reader (aisw-kml)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.audit.chat_log import (
    CHAT_LOG_DENYLIST,
    ChatLogWriter,
    redact_chat_text,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def audit_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _now() -> datetime:
    return datetime(2026, 6, 23, 12, 0, 0)


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("мой ключ sk-ant-abc123XYZ вот", "sk-ant-abc123XYZ"),
        ("Authorization: Bearer tok_secret_42", "tok_secret_42"),
        ("логин: admin password=hunter2", "hunter2"),
    ],
)
def test_redact_drops_denylisted_secrets(raw: str, must_not_contain: str) -> None:
    out = redact_chat_text(raw)
    assert must_not_contain not in out
    assert "[redacted]" in out


def test_redact_passes_through_clean_text() -> None:
    assert redact_chat_text("давление 120 на 80") == "давление 120 на 80"


def test_denylist_matches_spec() -> None:
    assert CHAT_LOG_DENYLIST == ("sk-ant-", "Bearer ", "password=")


@pytest.mark.asyncio
async def test_write_in_and_out_persist_rows(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    now = _now()
    await w.write_in(telegram_id=7, chat_id=70, text="привет", now=now)
    await w.write_out(telegram_id=7, chat_id=70, text="здравствуй", now=now)

    window = await w.read_recent_window(7, now=now + timedelta(seconds=1))
    assert [t.direction for t in window] == ["in", "out"]
    assert [t.text for t in window] == ["привет", "здравствуй"]


@pytest.mark.asyncio
async def test_write_redacts_before_persist(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    now = _now()
    await w.write_in(telegram_id=1, chat_id=1, text="токен sk-ant-LEAKED1234", now=now)
    window = await w.read_recent_window(1, now=now + timedelta(seconds=1))
    assert "sk-ant-LEAKED1234" not in window[0].text
    assert "[redacted]" in window[0].text


@pytest.mark.asyncio
async def test_window_is_per_user(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    now = _now()
    await w.write_in(telegram_id=1, chat_id=1, text="u1", now=now)
    await w.write_in(telegram_id=2, chat_id=2, text="u2", now=now)
    window = await w.read_recent_window(1, now=now + timedelta(seconds=1))
    assert [t.text for t in window] == ["u1"]


@pytest.mark.asyncio
async def test_window_respects_24h_cutoff(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    now = _now()
    await w.write_in(telegram_id=1, chat_id=1, text="old", now=now - timedelta(hours=30))
    await w.write_in(telegram_id=1, chat_id=1, text="fresh", now=now - timedelta(hours=1))
    window = await w.read_recent_window(1, now=now)
    assert [t.text for t in window] == ["fresh"]


@pytest.mark.asyncio
async def test_window_limit_keeps_newest_n_chronological(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    base = _now()
    for i in range(25):
        await w.write_in(telegram_id=1, chat_id=1, text=f"m{i}", now=base + timedelta(minutes=i))
    window = await w.read_recent_window(1, limit=20, now=base + timedelta(hours=1))
    assert len(window) == 20
    # newest 20 (m5..m24), chronological (oldest-first)
    assert window[0].text == "m5"
    assert window[-1].text == "m24"


@pytest.mark.asyncio
async def test_created_at_is_utc_naive(audit_maker) -> None:
    w = ChatLogWriter(audit_maker)
    await w.write_in(telegram_id=1, chat_id=1, text="x")
    window = await w.read_recent_window(1, now=datetime.now(UTC).replace(tzinfo=None))
    assert window[0].created_at_utc.tzinfo is None
