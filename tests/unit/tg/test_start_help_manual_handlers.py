"""Slash commands /start, /help, /manual (aisw-s5i Phase C.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from ai_steward_wiki.tg.handlers import build_router

# ---- minimal FakeMessage fixture, same shape as test_commands.py ---------


@dataclass
class _Chat:
    id: int


@dataclass
class _User:
    id: int
    username: str | None = None


@dataclass
class _Msg:
    chat: _Chat
    from_user: _User
    text: str | None = None
    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str, **kw: Any) -> None:
        self.answers.append(text)


def _handler(router: Any, name: str) -> Any:
    for h in router.message.handlers:
        if getattr(h.callback, "__name__", "") == name:
            return h.callback
    raise AssertionError(f"no message handler named {name!r}")


def _templates_dir() -> Path:
    # Live templates/ at repo root — used by the real loader.
    return Path(__file__).resolve().parents[3] / "templates"


# ---- /start --------------------------------------------------------------


async def test_start_known_renders_greeting_template() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_start")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=42))
    await h(msg, is_pending=False)
    assert len(msg.answers) == 1
    body = msg.answers[0]
    # markers from start-known.ru.md
    assert "WIKI-ассистент" in body
    assert "/help" in body
    assert "/manual" in body


async def test_start_unknown_calls_onboarding_and_renders_intro() -> None:
    pipeline = MagicMock()
    on_start_unknown = AsyncMock()
    router = build_router(
        pipeline,
        templates_dir=_templates_dir(),
        on_start_unknown=on_start_unknown,
    )
    h = _handler(router, "_on_start")
    msg = _Msg(chat=_Chat(id=11), from_user=_User(id=999, username="newbie"))
    await h(msg, is_pending=True)
    on_start_unknown.assert_awaited_once_with(telegram_id=999, username="newbie")
    assert len(msg.answers) == 1
    body = msg.answers[0]
    # markers from onboarding-intro.ru.md (existing)
    assert "WIKI-ассистент" in body
    assert "администратора" in body or "админ" in body.lower()


async def test_start_unknown_without_callback_still_answers() -> None:
    # Graceful degradation: handler MUST not crash if no on_start_unknown wired.
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_start")
    msg = _Msg(chat=_Chat(id=11), from_user=_User(id=999))
    await h(msg, is_pending=True)
    assert len(msg.answers) == 1
    assert "WIKI-ассистент" in msg.answers[0]


# ---- /help ---------------------------------------------------------------


async def test_help_renders_d041_paragraph_and_command_list() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_help")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=42))
    await h(msg, is_pending=False)
    assert len(msg.answers) == 1
    body = msg.answers[0]
    # D-041 mandatory paragraph (verbatim substring)
    assert "WIKI — это твоя персональная AI-библиотека" in body
    # cheat-sheet of all 6 commands
    for cmd in ("/start", "/help", "/manual", "/digest_now", "/expand", "/digest_sections"):
        assert cmd in body, cmd


async def test_help_works_for_unknown_user() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_help")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=999))
    await h(msg, is_pending=True)
    assert len(msg.answers) == 1
    assert "WIKI — это твоя персональная AI-библиотека" in msg.answers[0]


# ---- /manual -------------------------------------------------------------


async def test_manual_renders_all_scenarios() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_manual")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=42))
    await h(msg, is_pending=False)
    assert len(msg.answers) == 1
    body = msg.answers[0]
    # Markers from each major scenario block (text content).
    for marker in (
        "Сохранить заметку",
        "Создать WIKI",
        "Поставить напоминание",
        "ежедневную сводку",
        "/digest_now",
        "/expand",
        "/digest_sections",
        "Голос и фото",
        "Приватность",
    ):
        assert marker in body, marker


async def test_manual_works_for_unknown_user() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline, templates_dir=_templates_dir())
    h = _handler(router, "_on_manual")
    msg = _Msg(chat=_Chat(id=10), from_user=_User(id=999))
    await h(msg, is_pending=True)
    assert len(msg.answers) == 1
    assert "Приватность" in msg.answers[0]


# ---- build_router back-compat default ------------------------------------


def test_build_router_accepts_pipeline_only_default_templates_dir() -> None:
    # Existing callers (tests + storage layer) construct via positional pipeline.
    router = build_router(MagicMock())
    # Three new handlers + the existing ones are wired.
    names = {getattr(h.callback, "__name__", "") for h in router.message.handlers}
    assert {"_on_start", "_on_help", "_on_manual"}.issubset(names)
