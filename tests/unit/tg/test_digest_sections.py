"""/digest_sections command + digestsec: callback (aisw-pv8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import ai_steward_wiki.tg.handlers as handlers_mod
from ai_steward_wiki.storage.sessions.digest_prefs import DigestPrefs
from ai_steward_wiki.tg.handlers import (
    DIGESTSEC_CALLBACK_PREFIX,
    build_router,
    parse_digestsec_callback,
)

# --- parse_digestsec_callback ---------------------------------------------


def test_parse_digestsec_callback_ok() -> None:
    assert parse_digestsec_callback("digestsec:trackers:0") == ("trackers", False)
    assert parse_digestsec_callback("digestsec:wiki:1") == ("wiki", True)


@pytest.mark.parametrize(
    "bad",
    [
        "digestsec:trackers",
        "digestsec:trackers:2",
        "digestsec:bogus:0",
        "confirm:1:cancel",
        "digestsec:trackers:0:extra",
        "",
        "digestsec::0",
    ],
)
def test_parse_digestsec_callback_rejects(bad: str) -> None:
    assert parse_digestsec_callback(bad) is None


# --- harness --------------------------------------------------------------


@dataclass
class _Chat:
    id: int


@dataclass
class _User:
    id: int


@dataclass
class _Msg:
    chat: _Chat
    from_user: _User
    text: str | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, **kw: Any) -> None:
        self.answers.append({"text": text, **kw})


@dataclass
class _CbMsg:
    chat: _Chat
    edited_markups: list[Any] = field(default_factory=list)

    async def edit_reply_markup(self, *, reply_markup: Any = None) -> None:
        self.edited_markups.append(reply_markup)


@dataclass
class _Cb:
    data: str
    from_user: _User
    message: _CbMsg | None
    answers: list[tuple[Any, ...]] = field(default_factory=list)

    async def answer(self, *a: Any, **kw: Any) -> None:
        self.answers.append((a, kw))


def _handler(router: Any, name: str) -> Any:
    for h in router.message.handlers:
        if getattr(h.callback, "__name__", "") == name:
            return h.callback
    for h in router.callback_query.handlers:
        if getattr(h.callback, "__name__", "") == name:
            return h.callback
    raise AssertionError(f"no handler named {name!r}")


def _router() -> Any:
    return build_router(MagicMock())


# --- /digest_sections -----------------------------------------------------


async def test_digest_sections_shows_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "get_owner_digest_prefs",
        AsyncMock(return_value=DigestPrefs(trackers_enabled=True, wiki_enabled=True)),
    )
    h = _handler(_router(), "_on_digest_sections")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    assert len(msg.answers) == 1
    kb = msg.answers[0]["reply_markup"]
    rows = kb.inline_keyboard
    assert len(rows) == 2
    assert [b.callback_data for row in rows for b in row] == [
        "digestsec:trackers:0",
        "digestsec:wiki:0",
    ]
    assert rows[0][0].text == "📈 Трекеры: вкл ✅"
    assert rows[1][0].text == "📝 Обновления WIKI: вкл ✅"


async def test_digest_sections_reflects_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "get_owner_digest_prefs",
        AsyncMock(return_value=DigestPrefs(trackers_enabled=False, wiki_enabled=True)),
    )
    h = _handler(_router(), "_on_digest_sections")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    kb = msg.answers[0]["reply_markup"]
    rows = kb.inline_keyboard
    assert rows[0][0].text == "📈 Трекеры: выкл ⬜"
    assert rows[0][0].callback_data == "digestsec:trackers:1"
    assert rows[1][0].callback_data == "digestsec:wiki:0"


async def test_digest_sections_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "get_owner_digest_prefs",
        AsyncMock(side_effect=handlers_mod.firing.DigestNotInitialisedError("nope")),
    )
    h = _handler(_router(), "_on_digest_sections")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    assert len(msg.answers) == 1
    assert "reply_markup" not in msg.answers[0] or msg.answers[0]["reply_markup"] is None


async def test_digest_sections_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "get_owner_digest_prefs",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    h = _handler(_router(), "_on_digest_sections")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)  # must not raise
    assert len(msg.answers) == 1


# --- digestsec: callback --------------------------------------------------


async def test_digestsec_callback_toggles_and_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _set(owner: int, *, section: str, enabled: bool) -> DigestPrefs:
        seen.update(owner=owner, section=section, enabled=enabled)
        return DigestPrefs(trackers_enabled=enabled, wiki_enabled=True)

    monkeypatch.setattr(handlers_mod.firing, "set_owner_digest_section", _set)
    h = _handler(_router(), "_on_digestsec_callback")
    cb = _Cb(data="digestsec:trackers:0", from_user=_User(id=7), message=_CbMsg(chat=_Chat(id=11)))
    await h(cb)
    assert seen == {"owner": 7, "section": "trackers", "enabled": False}
    assert cb.answers  # acknowledged
    assert cb.message is not None
    assert len(cb.message.edited_markups) == 1
    rows = cb.message.edited_markups[0].inline_keyboard
    assert rows[0][0].text == "📈 Трекеры: выкл ⬜"
    assert rows[0][0].callback_data == "digestsec:trackers:1"


async def test_digestsec_callback_bad_data_no_write(monkeypatch: pytest.MonkeyPatch) -> None:
    setter = AsyncMock()
    monkeypatch.setattr(handlers_mod.firing, "set_owner_digest_section", setter)
    h = _handler(_router(), "_on_digestsec_callback")
    cb = _Cb(data="digestsec:bogus:9", from_user=_User(id=7), message=_CbMsg(chat=_Chat(id=11)))
    await h(cb)
    setter.assert_not_awaited()
    assert cb.answers


async def test_digestsec_callback_idempotent_when_already_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _set(owner: int, *, section: str, enabled: bool) -> DigestPrefs:
        return DigestPrefs(trackers_enabled=False, wiki_enabled=True)

    monkeypatch.setattr(handlers_mod.firing, "set_owner_digest_section", _set)
    h = _handler(_router(), "_on_digestsec_callback")
    cb = _Cb(data="digestsec:trackers:0", from_user=_User(id=7), message=_CbMsg(chat=_Chat(id=11)))
    await h(cb)  # must not raise
    assert cb.answers
    assert cb.message is not None
    rows = cb.message.edited_markups[0].inline_keyboard
    assert rows[0][0].callback_data == "digestsec:trackers:1"


async def test_digestsec_callback_setter_error_generic_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "set_owner_digest_section",
        AsyncMock(side_effect=RuntimeError("x")),
    )
    h = _handler(_router(), "_on_digestsec_callback")
    cb = _Cb(data="digestsec:wiki:0", from_user=_User(id=7), message=_CbMsg(chat=_Chat(id=11)))
    await h(cb)  # must not raise
    assert cb.answers


async def test_digestsec_callback_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _set(owner: int, *, section: str, enabled: bool) -> DigestPrefs:
        return DigestPrefs(trackers_enabled=False, wiki_enabled=True)

    monkeypatch.setattr(handlers_mod.firing, "set_owner_digest_section", _set)
    h = _handler(_router(), "_on_digestsec_callback")
    cb = _Cb(data="digestsec:trackers:0", from_user=_User(id=7), message=None)
    await h(cb)  # must not raise
    assert cb.answers


def test_digestsec_prefix_constant() -> None:
    assert DIGESTSEC_CALLBACK_PREFIX == "digestsec:"
