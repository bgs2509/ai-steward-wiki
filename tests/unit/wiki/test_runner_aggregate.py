"""Unit tests for ai_steward_wiki.wiki.runner.aggregate_text (chunk 20)."""

from __future__ import annotations

from ai_steward_wiki.wiki.runner import aggregate_text
from ai_steward_wiki.wiki.streaming import StreamEvent


def _ev_assistant(payload: dict[str, object]) -> StreamEvent:
    return StreamEvent(type="assistant_chunk", payload=payload)


def test_aggregate_text_message_content_text_blocks() -> None:
    events = [
        _ev_assistant(
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            }
        ),
    ]
    assert aggregate_text(events) == "Hello world"


def test_aggregate_text_delta_shape() -> None:
    events = [
        _ev_assistant({"delta": {"text": "foo"}}),
        _ev_assistant({"delta": {"text": "bar"}}),
    ]
    assert aggregate_text(events) == "foobar"


def test_aggregate_text_flat_text_payload() -> None:
    events = [_ev_assistant({"text": "привет"})]
    assert aggregate_text(events) == "привет"


def test_aggregate_text_ignores_non_assistant_events() -> None:
    events = [
        StreamEvent(type="tool_use", payload={"text": "tool"}),
        StreamEvent(type="final", payload={"text": "final"}),
        _ev_assistant({"text": "yes"}),
    ]
    assert aggregate_text(events) == "yes"


def test_aggregate_text_empty_returns_empty_string() -> None:
    assert aggregate_text([]) == ""


def test_aggregate_text_unknown_shape_yields_empty() -> None:
    events = [_ev_assistant({"unexpected": 42})]
    assert aggregate_text(events) == ""
