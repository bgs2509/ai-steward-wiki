"""Tests for the Inbox-WIKI Stage-1a Router reply parser (aisw-dsg, Phase-A)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from ai_steward_wiki.inbox.router import (
    RouterDecision,
    RouterError,
    RouterIntent,
    build_router_input,
    format_chat_window,
    parse_router_reply,
)
from ai_steward_wiki.storage.audit.chat_log import ChatTurn


def test_build_router_input_lists_existing_wikis() -> None:
    out = build_router_input("давление 137 96 пульс 78", ["Medical-WIKI", "Budget-WIKI"])
    assert "Существующие WIKI" in out
    assert "Medical-WIKI" in out
    assert "Budget-WIKI" in out
    assert out.endswith("давление 137 96 пульс 78")


def test_build_router_input_empty_list() -> None:
    out = build_router_input("привет", [])
    assert "нет ни одной" in out
    assert out.endswith("привет")


def _turn(direction: str, text: str) -> ChatTurn:
    return ChatTurn(direction=direction, text=text, created_at_utc=datetime(2026, 6, 23, 12, 0))


def test_format_chat_window_empty_is_blank() -> None:
    assert format_chat_window([]) == ""


def test_format_chat_window_renders_speakers() -> None:
    out = format_chat_window([_turn("in", "давление 120"), _turn("out", "записал")])
    assert "Пользователь: давление 120" in out
    assert "Бот: записал" in out
    # user turn precedes bot turn (chronological, oldest-first)
    assert out.index("Пользователь") < out.index("Бот")


def test_build_router_input_includes_recent_window() -> None:
    window = [_turn("in", "какое было давление?"), _turn("out", "120 на 80")]
    out = build_router_input("повтори последний ответ", ["Medical-WIKI"], recent_window=window)
    assert "Недавняя история" in out
    assert "120 на 80" in out
    assert out.endswith("повтори последний ответ")


def test_build_router_input_no_window_unchanged() -> None:
    out = build_router_input("привет", ["Medical-WIKI"], recent_window=[])
    assert "Недавняя история" not in out
    assert out.endswith("привет")


def _block(target: str, intent: str, notes: str) -> str:
    return f"```router\ntarget_wiki: {target}\nintent: {intent}\nnotes: {notes}\n```"


def test_route_happy_path() -> None:
    d = parse_router_reply(_block("Travel-WIKI", "route", "Похоже на авиабилет SVO→IST"))
    assert d.intent is RouterIntent.ROUTE
    assert d.target_wiki == "Travel-WIKI"
    assert d.notes == "Похоже на авиабилет SVO→IST"
    assert d.parsed_ok is True
    assert d.raw  # original text preserved


def test_create_wiki_happy_path() -> None:
    d = parse_router_reply(_block("Garden-WIKI", "create_wiki", "Заведём вики про сад?"))
    assert d.intent is RouterIntent.CREATE_WIKI
    assert d.target_wiki == "Garden-WIKI"
    assert d.parsed_ok is True


def test_clarify_with_null_target() -> None:
    d = parse_router_reply(_block("null", "clarify", "Уточни, к какой теме это относится?"))
    assert d.intent is RouterIntent.CLARIFY
    assert d.target_wiki is None
    assert d.parsed_ok is True


def test_reject_with_empty_target() -> None:
    d = parse_router_reply("```router\ntarget_wiki:\nintent: reject\nnotes: Вне зоны бота.\n```")
    assert d.intent is RouterIntent.REJECT
    assert d.target_wiki is None
    assert d.parsed_ok is True


def test_multiline_notes() -> None:
    text = (
        "```router\n"
        "target_wiki: Health-WIKI\n"
        "intent: route\n"
        "notes: Первая строка.\nВторая строка.\nТретья.\n"
        "```"
    )
    d = parse_router_reply(text)
    assert d.intent is RouterIntent.ROUTE
    assert d.notes == "Первая строка.\nВторая строка.\nТретья."


def test_preamble_and_quoted_example_last_block_wins() -> None:
    text = (
        "Конечно! Формат такой:\n"
        "```router\ntarget_wiki: <имя>\nintent: <route|...>\nnotes: <текст>\n```\n"
        "А вот мой ответ:\n" + _block("Career-WIKI", "route", "Резюме — сюда.")
    )
    d = parse_router_reply(text)
    assert d.target_wiki == "Career-WIKI"
    assert d.notes == "Резюме — сюда."
    assert d.parsed_ok is True


def test_no_block_falls_back_to_clarify() -> None:
    text = "Извини, я не уверен, к чему это отнести. Можешь уточнить?"
    d = parse_router_reply(text)
    assert d.intent is RouterIntent.CLARIFY
    assert d.target_wiki is None
    assert d.parsed_ok is False
    assert d.notes  # non-empty: trimmed original
    assert "уточнить" in d.notes


def test_empty_text_falls_back_with_generic_notes() -> None:
    d = parse_router_reply("   \n  ")
    assert d.intent is RouterIntent.CLARIFY
    assert d.parsed_ok is False
    assert d.notes.strip()  # generic ru prompt, not empty


def test_unknown_intent_value_falls_back() -> None:
    d = parse_router_reply(_block("X-WIKI", "frobnicate", "что-то"))
    assert d.intent is RouterIntent.CLARIFY
    assert d.parsed_ok is False


def test_route_without_target_demoted_to_clarify() -> None:
    d = parse_router_reply(_block("null", "route", "..."))
    assert d.intent is RouterIntent.CLARIFY
    assert d.target_wiki is None
    assert d.parsed_ok is False
    assert d.notes.strip()


def test_create_wiki_without_target_demoted_to_clarify() -> None:
    d = parse_router_reply(_block("", "create_wiki", "..."))
    assert d.intent is RouterIntent.CLARIFY
    assert d.parsed_ok is False


def test_decision_is_frozen_and_forbids_extra() -> None:
    d = parse_router_reply(_block("A-WIKI", "route", "x"))
    with pytest.raises(ValidationError):
        d.intent = RouterIntent.REJECT  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RouterDecision(  # type: ignore[call-arg]
            intent=RouterIntent.ROUTE, target_wiki="A", notes="n", raw="r", parsed_ok=True, x=1
        )


def test_router_error_is_exception() -> None:
    assert issubclass(RouterError, Exception)
