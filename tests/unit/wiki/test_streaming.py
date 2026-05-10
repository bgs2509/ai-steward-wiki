from __future__ import annotations

import asyncio
import json

import pytest

from ai_steward_wiki.wiki.streaming import StreamEvent, classify_line, parse_stream_json


def _reader_with(payload: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(payload)
    r.feed_eof()
    return r


async def test_parse_three_lines_yields_three_events() -> None:
    lines = [
        json.dumps({"type": "assistant", "text": "hello"}).encode() + b"\n",
        json.dumps({"type": "tool_use", "name": "Read"}).encode() + b"\n",
        json.dumps({"type": "result", "stop_reason": "end_turn"}).encode() + b"\n",
    ]
    reader = _reader_with(b"".join(lines))
    events: list[StreamEvent] = [ev async for ev in parse_stream_json(reader)]
    assert [e.type for e in events] == ["assistant_chunk", "tool_use", "final"]


async def test_partial_line_buffered() -> None:
    """readline buffers across feed_data chunks until newline."""
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"type": "assi')
    reader.feed_data(b'stant", "text": "x"}\n')
    reader.feed_eof()
    events = [ev async for ev in parse_stream_json(reader)]
    assert len(events) == 1
    assert events[0].type == "assistant_chunk"


async def test_malformed_line_skipped() -> None:
    payload = b"this is not json\n" + b'{"type": "result"}\n'
    reader = _reader_with(payload)
    events = [ev async for ev in parse_stream_json(reader)]
    assert len(events) == 1
    assert events[0].type == "final"


async def test_empty_lines_skipped() -> None:
    reader = _reader_with(b"\n\n\n")
    events = [ev async for ev in parse_stream_json(reader)]
    assert events == []


def test_classify_line_unknown_falls_to_raw() -> None:
    ev = classify_line({"type": "weird", "x": 1})
    assert ev.type == "raw"
    assert ev.payload["x"] == 1


def test_stream_event_is_frozen() -> None:
    ev = StreamEvent(type="raw", payload={})
    with pytest.raises(Exception):  # noqa: B017,PT011 — pydantic frozen raises ValidationError
        ev.type = "final"  # type: ignore[misc]
