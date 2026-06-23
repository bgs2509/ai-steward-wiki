"""Unit tests for ai_steward_wiki.wiki.runner.final_turn_text (aisw-2n2).

final_turn_text returns only the trailing assistant prose (the answer), discarding
inter-tool narration that the agentic loop emits before each tool call.
"""

from __future__ import annotations

from ai_steward_wiki.wiki.runner import final_turn_text
from ai_steward_wiki.wiki.streaming import StreamEvent


def _assistant(*content: dict[str, object]) -> StreamEvent:
    return StreamEvent(type="assistant_chunk", payload={"message": {"content": list(content)}})


def _text(s: str) -> dict[str, object]:
    return {"type": "text", "text": s}


def _tool() -> dict[str, object]:
    return {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}


def test_strips_inter_tool_narration() -> None:
    """Narration emitted before each tool_use is dropped; only the final answer remains."""
    events = [
        _assistant(_text("Прочитаю сырьё…"), _tool()),
        _assistant(_text("Вижу, что вопрос query…"), _tool()),
        _assistant(_text("Теперь понятно…"), _tool()),
        _assistant(_text("Резюме состояния: давление 131/92.")),
    ]
    assert final_turn_text(events) == "Резюме состояния: давление 131/92."


def test_no_tool_calls_passes_through_all_text() -> None:
    """With no tool invocation the whole answer is returned (== aggregate_text)."""
    events = [
        _assistant(_text("Привет. ")),
        _assistant(_text("Как дела?")),
    ]
    assert final_turn_text(events) == "Привет. Как дела?"


def test_empty_after_last_tool_falls_back_to_aggregate() -> None:
    """If nothing follows the last tool call, fall back to all assistant text (never empty)."""
    events = [
        _assistant(_text("Думаю над ответом…"), _tool()),
    ]
    assert final_turn_text(events) == "Думаю над ответом…"


def test_keeps_multiple_trailing_text_turns() -> None:
    """Several text-only assistant turns after the last tool are all kept (risk R-1)."""
    events = [
        _assistant(_text("narration"), _tool()),
        _assistant(_text("Часть 1. ")),
        _assistant(_text("Часть 2.")),
    ]
    assert final_turn_text(events) == "Часть 1. Часть 2."


def test_separate_tool_use_event_is_a_boundary() -> None:
    """A standalone tool_use StreamEvent also resets the trailing-answer window."""
    events = [
        _assistant(_text("narration")),
        StreamEvent(type="tool_use", payload={"id": "t1"}),
        _assistant(_text("Финальный ответ")),
    ]
    assert final_turn_text(events) == "Финальный ответ"


def test_empty_events_returns_empty_string() -> None:
    assert final_turn_text([]) == ""
