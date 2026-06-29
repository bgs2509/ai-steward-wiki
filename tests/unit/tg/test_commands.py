"""Slash commands /digest_now + /expand (aisw-269, Phase-D.b.2b)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import ai_steward_wiki.tg.handlers as handlers_mod
from ai_steward_wiki.tg.handlers import build_router


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
    answers: list[str] = field(default_factory=list)
    answer_kwargs: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, **kw: Any) -> None:
        self.answers.append(text)
        self.answer_kwargs.append(kw)


def _handler(router: Any, name: str) -> Any:
    for h in router.message.handlers:
        if getattr(h.callback, "__name__", "") == name:
            return h.callback
    raise AssertionError(f"no message handler named {name!r}")


def _router() -> Any:
    return build_router(MagicMock())


# --- /digest_now -----------------------------------------------------------


async def test_digest_now_runs_each_owner_job(monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[int] = []
    monkeypatch.setattr(
        handlers_mod.firing, "list_owner_digest_job_ids", AsyncMock(return_value=[11, 22])
    )
    monkeypatch.setattr(
        handlers_mod.firing, "fire_digest_job", AsyncMock(side_effect=lambda jid: fired.append(jid))
    )
    h = _handler(_router(), "_on_digest_now")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    assert fired == [11, 22]
    assert msg.answers == []


async def test_digest_now_no_jobs_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing, "list_owner_digest_job_ids", AsyncMock(return_value=[])
    )
    fire = AsyncMock()
    monkeypatch.setattr(handlers_mod.firing, "fire_digest_job", fire)
    h = _handler(_router(), "_on_digest_now")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    assert len(msg.answers) == 1
    assert "сводк" in msg.answers[0].lower()
    fire.assert_not_awaited()


async def test_digest_now_one_job_fails_others_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[int] = []

    async def _fire(jid: int) -> None:
        if jid == 11:
            raise RuntimeError("boom")
        fired.append(jid)

    monkeypatch.setattr(
        handlers_mod.firing, "list_owner_digest_job_ids", AsyncMock(return_value=[11, 22])
    )
    monkeypatch.setattr(handlers_mod.firing, "fire_digest_job", _fire)
    h = _handler(_router(), "_on_digest_now")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)  # must not raise
    assert fired == [22]


async def test_digest_now_unavailable_when_ctx_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing,
        "list_owner_digest_job_ids",
        AsyncMock(side_effect=handlers_mod.firing.DigestNotInitialisedError("nope")),
    )
    h = _handler(_router(), "_on_digest_now")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7))
    await h(msg)
    assert len(msg.answers) == 1


# --- /expand <section> -----------------------------------------------------


async def test_expand_known_section_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    async def _run(owner: int, section: str) -> str:
        called.update(owner=owner, section=section)
        return "DETAIL"

    monkeypatch.setattr(handlers_mod.firing, "run_section_expand", _run)
    h = _handler(_router(), "_on_expand")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7), text="/expand trackers")
    await h(msg)
    assert called == {"owner": 7, "section": "trackers"}
    assert msg.answers == ["DETAIL"]


@pytest.mark.parametrize("text", ["/expand", "/expand wat", "/expand   "])
async def test_expand_bad_section_usage(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    run = AsyncMock()
    monkeypatch.setattr(handlers_mod.firing, "run_section_expand", run)
    h = _handler(_router(), "_on_expand")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7), text=text)
    await h(msg)
    assert len(msg.answers) == 1
    assert "today" in msg.answers[0]
    assert "trackers" in msg.answers[0]
    # _EXPAND_USAGE_RU has a literal <раздел>; must go out plain text or HTML mode
    # drops it (aisw-woc).
    assert msg.answer_kwargs[0].get("parse_mode") is None
    run.assert_not_awaited()


async def test_expand_no_wiki_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers_mod.firing, "run_section_expand", AsyncMock(return_value=None))
    h = _handler(_router(), "_on_expand")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7), text="/expand wiki")
    await h(msg)
    assert len(msg.answers) == 1
    assert "WIKI" in msg.answers[0]


async def test_expand_empty_text_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers_mod.firing, "run_section_expand", AsyncMock(return_value="   "))
    h = _handler(_router(), "_on_expand")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7), text="/expand meds")
    await h(msg)
    assert msg.answers == ["По этому разделу за период ничего нет."]


async def test_expand_runner_error_generic_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handlers_mod.firing, "run_section_expand", AsyncMock(side_effect=RuntimeError("x"))
    )
    h = _handler(_router(), "_on_expand")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=7), text="/expand today")
    await h(msg)  # must not raise
    assert len(msg.answers) == 1


# --- router order: a plain text message still reaches the pipeline ----------


async def test_plain_text_still_reaches_pipeline() -> None:
    pipeline = MagicMock()
    pipeline.on_text = AsyncMock()
    router = build_router(pipeline)
    # The text handler is registered with ~F.text.startswith("/"); a non-command
    # message must dispatch to it (the command handlers must not swallow text).
    text_handler = router.message.handlers[0].callback

    @dataclass
    class _M:
        message_id: int
        chat: _Chat
        from_user: _User
        text: str

    await text_handler(_M(message_id=1, chat=_Chat(id=10), from_user=_User(id=7), text="привет"))
    pipeline.on_text.assert_awaited_once_with(telegram_id=7, chat_id=10, update_id=1, text="привет")
