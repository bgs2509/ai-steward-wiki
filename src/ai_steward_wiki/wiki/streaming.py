# FILE: src/ai_steward_wiki/wiki/streaming.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Parse `claude --output-format stream-json` line-delimited events.
#   SCOPE: StreamEvent Pydantic model + parse_stream_json async iterator.
#          Tolerant: empty/malformed lines skipped + structlog warn.
#   DEPENDS: asyncio, json, pydantic, structlog
#   LINKS: M-WIKI-RUNNER, tech-spec-draft.md §8 streaming
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   StreamEvent - frozen Pydantic v2 model with type+payload
#   StreamEventType - Literal type union of recognised event types
#   parse_stream_json - async iterator over an asyncio.StreamReader
#   classify_line - pure helper mapping a parsed dict to StreamEvent
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 7: stream-json parser
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

__all__ = [
    "StreamEvent",
    "StreamEventType",
    "classify_line",
    "parse_stream_json",
]

_log = structlog.get_logger("wiki.streaming")

StreamEventType = Literal["assistant_chunk", "tool_use", "final", "raw"]


class StreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: StreamEventType
    payload: dict[str, Any]


def classify_line(data: dict[str, Any]) -> StreamEvent:
    """Map a parsed JSON object to a typed StreamEvent."""
    raw_type = data.get("type")
    if raw_type == "assistant":
        return StreamEvent(type="assistant_chunk", payload=data)
    if raw_type == "tool_use":
        return StreamEvent(type="tool_use", payload=data)
    if raw_type == "result" or "stop_reason" in data:
        return StreamEvent(type="final", payload=data)
    return StreamEvent(type="raw", payload=data)


async def parse_stream_json(reader: asyncio.StreamReader) -> AsyncIterator[StreamEvent]:
    """Yield StreamEvent objects parsed from line-delimited JSON.

    Empty lines are skipped silently; malformed JSON lines are skipped with a
    structlog warn so the stream stays consumable even if the CLI emits noise.
    """
    while True:
        line = await reader.readline()
        if not line:
            return  # EOF
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            _log.warning("wiki.stream.malformed_line", preview=text[:120])
            continue
        if not isinstance(data, dict):
            _log.warning("wiki.stream.non_object", kind=type(data).__name__)
            continue
        yield classify_line(data)
