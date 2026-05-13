"""user_digest_prefs repo: defaults, toggle, unknown-user, CASCADE."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.sessions.digest_prefs import (
    SECTION_DISPLAY_NAME,
    TOGGLEABLE_DIGEST_SECTIONS,
    DigestPrefs,
    get_digest_prefs,
    set_cards_enabled,
    set_digest_section,
)
from ai_steward_wiki.storage.sessions.engine import Base
from ai_steward_wiki.storage.sessions.models import User, UserDigestPrefs


@pytest.fixture
async def session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _add_user(maker: async_sessionmaker, telegram_id: int) -> int:
    async with maker() as s:
        u = User(
            telegram_id=telegram_id,
            role="user",
            display_name="t",
            tz="Europe/Moscow",
            enabled=True,
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
            updated_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(u)
        await s.commit()
        return u.user_id


def test_vocab_subset_of_expand_keys():
    from ai_steward_wiki.tg.handlers import EXPAND_SECTION_KEYS

    assert set(TOGGLEABLE_DIGEST_SECTIONS) <= set(EXPAND_SECTION_KEYS)
    assert set(SECTION_DISPLAY_NAME) == set(TOGGLEABLE_DIGEST_SECTIONS)


async def test_get_defaults_when_no_row(session_maker):
    await _add_user(session_maker, 111)
    prefs = await get_digest_prefs(session_maker, 111)
    assert prefs == DigestPrefs(trackers_enabled=True, wiki_enabled=True)
    assert prefs.disabled_keys == ()


async def test_get_defaults_when_no_user(session_maker):
    prefs = await get_digest_prefs(session_maker, 999)
    assert prefs == DigestPrefs(trackers_enabled=True, wiki_enabled=True)


async def test_set_then_get_roundtrip(session_maker):
    await _add_user(session_maker, 222)
    after = await set_digest_section(session_maker, 222, section="trackers", enabled=False)
    assert after == DigestPrefs(trackers_enabled=False, wiki_enabled=True)
    assert after.disabled_keys == ("trackers",)
    assert await get_digest_prefs(session_maker, 222) == after
    again = await set_digest_section(session_maker, 222, section="wiki", enabled=False)
    assert again == DigestPrefs(trackers_enabled=False, wiki_enabled=False)
    assert again.disabled_keys == ("trackers", "wiki")
    back = await set_digest_section(session_maker, 222, section="trackers", enabled=True)
    assert back == DigestPrefs(trackers_enabled=True, wiki_enabled=False)


async def test_set_unknown_section_raises(session_maker):
    await _add_user(session_maker, 333)
    with pytest.raises(ValueError, match="unknown digest section"):
        await set_digest_section(session_maker, 333, section="bogus", enabled=False)


async def test_set_for_unknown_user_is_noop(session_maker):
    after = await set_digest_section(session_maker, 4040, section="wiki", enabled=False)
    assert after == DigestPrefs(trackers_enabled=True, wiki_enabled=True)
    async with session_maker() as s:
        from sqlalchemy import select

        rows = (await s.execute(select(UserDigestPrefs))).scalars().all()
    assert rows == []


async def test_cards_enabled_default_true(session_maker):
    """aisw-163 P2: absent row ⇒ cards_enabled=True."""
    await _add_user(session_maker, 666)
    prefs = await get_digest_prefs(session_maker, 666)
    assert prefs.cards_enabled is True


async def test_cards_enabled_default_when_no_user(session_maker):
    prefs = await get_digest_prefs(session_maker, 6661)
    assert prefs.cards_enabled is True


async def test_set_cards_enabled_round_trip(session_maker):
    """aisw-163 P2: toggle off then on preserves other section toggles."""
    await _add_user(session_maker, 777)
    # toggle a section first to ensure the row exists with non-defaults
    await set_digest_section(session_maker, 777, section="wiki", enabled=False)
    after = await set_cards_enabled(session_maker, 777, enabled=False)
    assert after == DigestPrefs(trackers_enabled=True, wiki_enabled=False, cards_enabled=False)
    assert await get_digest_prefs(session_maker, 777) == after
    back = await set_cards_enabled(session_maker, 777, enabled=True)
    assert back == DigestPrefs(trackers_enabled=True, wiki_enabled=False, cards_enabled=True)


async def test_set_cards_enabled_creates_row(session_maker):
    """aisw-163 P2: first toggle (no prior row) upserts with default sections + new cards value."""
    await _add_user(session_maker, 888)
    after = await set_cards_enabled(session_maker, 888, enabled=False)
    assert after == DigestPrefs(trackers_enabled=True, wiki_enabled=True, cards_enabled=False)


async def test_set_cards_enabled_unknown_user_noop(session_maker):
    after = await set_cards_enabled(session_maker, 9090, enabled=False)
    assert after == DigestPrefs(trackers_enabled=True, wiki_enabled=True, cards_enabled=True)
    async with session_maker() as s:
        from sqlalchemy import select

        rows = (await s.execute(select(UserDigestPrefs))).scalars().all()
    assert rows == []


async def test_cascade_delete(session_maker):
    uid = await _add_user(session_maker, 555)
    await set_digest_section(session_maker, 555, section="trackers", enabled=False)
    async with session_maker() as s:
        await s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
        u = await s.get(User, uid)
        await s.delete(u)
        await s.commit()
    assert await get_digest_prefs(session_maker, 555) == DigestPrefs(True, True)
