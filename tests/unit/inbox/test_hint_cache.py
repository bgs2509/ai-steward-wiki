"""Tests for InboxHintCacheRepo + get_or_refresh_hint (D-006/D-016)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.auth.allowlist import sync_to_sessions_db
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.inbox.hint_cache import InboxHintCacheRepo, get_or_refresh_hint

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
    # Seed one user (FK target).
    await sync_to_sessions_db(
        UsersConfig(schema_version=1, users=(UserRecord(telegram_id=1001),)),
        maker,
    )
    yield maker
    await engine.dispose()


async def _user_id(maker) -> int:
    from sqlalchemy import select

    from ai_steward_wiki.storage.sessions.models import User

    async with maker() as s:
        return (await s.execute(select(User.user_id))).scalar_one()


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


async def test_cache_miss_then_hit(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    md = tmp_path / "Health-WIKI" / "CLAUDE.md"
    md.parent.mkdir(parents=True)
    _write(md, "# Health\n\n## Inbox hint\n\nlab results, BP\n")

    # Miss → file read, cache populated.
    hint1 = await get_or_refresh_hint(repo, user_id, md)
    assert hint1 == "lab results, BP"
    cached = await repo.get(user_id, str(md))
    assert cached is not None
    assert cached.content_sha256 != ""
    sha_before = cached.content_sha256

    # Hit: even if we corrupt file content but preserve stat, cached is returned.
    # We do this by overwriting bytes in-place and restoring mtime/size.
    st = md.stat()
    new_body = "# Health\n\n## Inbox hint\n\nDIFFERENT\n"
    # Pad/truncate to identical size.
    new_bytes = new_body.encode("utf-8")
    if len(new_bytes) < st.st_size:
        new_bytes = new_bytes + b" " * (st.st_size - len(new_bytes))
    else:
        new_bytes = new_bytes[: st.st_size]
    md.write_bytes(new_bytes)
    os.utime(md, ns=(st.st_atime_ns, st.st_mtime_ns))

    hint2 = await get_or_refresh_hint(repo, user_id, md)
    # Cache hit returns OLD value because (size, mtime) matched.
    assert hint2 == "lab results, BP"
    cached2 = await repo.get(user_id, str(md))
    assert cached2 is not None
    assert cached2.content_sha256 == sha_before  # not refreshed


async def test_mtime_change_triggers_refresh(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    md = tmp_path / "CLAUDE.md"
    _write(md, "## Inbox hint\nfirst\n")
    hint1 = await get_or_refresh_hint(repo, user_id, md)
    assert hint1 == "first"
    cached1 = await repo.get(user_id, str(md))
    assert cached1 is not None

    # Modify file with new content + bump mtime.
    _write(md, "## Inbox hint\nsecond rev\n")
    st = md.stat()
    new_mtime = st.st_mtime_ns + 10_000_000_000  # +10s to be safe
    os.utime(md, ns=(new_mtime, new_mtime))

    hint2 = await get_or_refresh_hint(repo, user_id, md)
    assert hint2 == "second rev"
    cached2 = await repo.get(user_id, str(md))
    assert cached2 is not None
    assert cached2.content_sha256 != cached1.content_sha256


async def test_file_deletion_invalidates(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    md = tmp_path / "CLAUDE.md"
    _write(md, "## Inbox hint\nbody\n")
    await get_or_refresh_hint(repo, user_id, md)
    assert await repo.get(user_id, str(md)) is not None

    md.unlink()
    hint = await get_or_refresh_hint(repo, user_id, md)
    assert hint is None
    assert await repo.get(user_id, str(md)) is None


async def test_no_hint_section_caches_empty(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    md = tmp_path / "CLAUDE.md"
    _write(md, "# Title\n\nno hint here\n")
    hint = await get_or_refresh_hint(repo, user_id, md)
    assert hint is None
    cached = await repo.get(user_id, str(md))
    assert cached is not None
    assert cached.hint_text == ""


async def test_repo_invalidate_idempotent(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    await repo.invalidate(user_id, "/nonexistent/CLAUDE.md")  # no-op, no error


async def test_repo_upsert_overwrites(session_maker, tmp_path) -> None:
    user_id = await _user_id(session_maker)
    repo = InboxHintCacheRepo(session_maker)
    wiki_path = "/some/CLAUDE.md"
    now = datetime.now(UTC).replace(tzinfo=None)
    await repo.upsert(
        user_id,
        wiki_path,
        size_bytes=10,
        mtime_ns=1,
        ctime_ns=1,
        content_sha256="a" * 64,
        hint_text="v1",
        refreshed_at_utc=now,
    )
    await repo.upsert(
        user_id,
        wiki_path,
        size_bytes=20,
        mtime_ns=2,
        ctime_ns=2,
        content_sha256="b" * 64,
        hint_text="v2",
        refreshed_at_utc=now,
    )
    row = await repo.get(user_id, wiki_path)
    assert row is not None
    assert row.hint_text == "v2"
    assert row.size_bytes == 20


async def test_resolve_user_id_known_and_unknown(session_maker) -> None:
    # The fixture seeds exactly one user with telegram_id=1001.
    from ai_steward_wiki.storage.sessions.users import resolve_user_id

    expected = await _user_id(session_maker)
    assert await resolve_user_id(session_maker, 1001) == expected
    assert await resolve_user_id(session_maker, 999_999) is None
