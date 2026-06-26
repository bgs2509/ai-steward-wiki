# FILE: src/ai_steward_wiki/classifier/schema.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Pydantic schemas + intent enum + error classes for Stage-0 classifier.
#   SCOPE: Intent enum (closed list), ClassifierResult, TimeParseResult, error hierarchy.
#   DEPENDS: pydantic
#   LINKS: M-CLASSIFIER-STAGE0, D-009, D-010, D-015
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Intent - closed enum of Stage-0 intents (D-009, mapped to spec §3 job kinds)
#   ClassifierResult - frozen Pydantic v2 model carrying intent/confidence/audit fields
#   TimeParseResult - frozen Pydantic v2 model carrying when_utc + escalation flag
#   ClassifierError - base exception
#   ClassifierTimeoutError - transient subclass (chunk 4 taxonomy)
#   ClassifierSchemaError - permanent subclass when CLI JSON violates schema
#   unwrap_fenced_json - parse a JSON object from a model reply, stripping a ```json fence
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - aisw-7j3: add unwrap_fenced_json() — strip a fenced ```json
#                envelope (even with surrounding prose) before json.loads so a reminder
#                time reply wrapped in a code fence parses instead of being lost.
#   PREVIOUS:    v0.0.3 - aisw-df4: add Intent.SMALLTALK ("smalltalk") — conversational
#                chitchat fallback (greetings/banter). Dispatched to a short ru reply in
#                tg.pipeline; never filed, scheduled, or run through a WIKI.
#   PREVIOUS:    v0.0.2 - aisw-dqz: add Intent.WEB_TASK ("web_task") — find-on-internet
#                answers-in-chat. Stays out of _ROUTABLE_INTENTS so it reaches the generic
#                answer runner; the runner enables WebSearch only for this intent (Path B).
#   PREVIOUS:    v0.0.1 - initial intent enum + result schemas + error hierarchy
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ClassifierError",
    "ClassifierResult",
    "ClassifierSchemaError",
    "ClassifierTimeoutError",
    "Intent",
    "TimeParseResult",
    "unwrap_fenced_json",
]


class Intent(str, Enum):
    REMINDER = "reminder"
    WIKI_INGEST = "wiki_ingest"
    WIKI_QUERY = "wiki_query"
    WIKI_LINT = "wiki_lint"
    DIGEST = "digest"
    WEB_TASK = "web_task"
    SMALLTALK = "smalltalk"
    ADMIN = "admin"
    UNKNOWN = "unknown"


class ClassifierResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    distilled_payload: dict[str, Any]
    backend: Literal["claude_cli", "anthropic_api", "fake"]
    model: str
    prompt_semver: str
    prompt_sha256: str
    latency_ms: int = Field(ge=0)


class TimeParseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    when_utc: datetime | None
    source: Literal["dateparser", "haiku_fallback", "escalate"]
    escalate: bool
    raw: str
    user_tz: str


# aisw-7j3: a fenced ```json … ``` block anywhere in a model reply. Uses search
# (not a full-string anchor) so a reply with surrounding prose still unwraps — the
# real failure shape where the envelope was not stripped before json.loads.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def unwrap_fenced_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model reply, tolerating a fenced ```json envelope.

    Strips a fenced code block (even with surrounding prose) before ``json.loads`` so a
    reply the model wrapped in a code fence still parses (aisw-7j3). Raises
    ClassifierSchemaError when the result is not parseable as a JSON object.
    """
    candidate = text.strip()
    fence_match = _JSON_FENCE_RE.search(candidate)
    if fence_match is not None:
        candidate = fence_match.group(1).strip()
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ClassifierSchemaError(
            f"could not parse JSON object from model reply: {text[:256]!r}"
        ) from e
    if not isinstance(obj, dict):
        raise ClassifierSchemaError(f"model reply JSON is not an object: {type(obj).__name__}")
    return obj


class ClassifierError(Exception):
    """Base error for the M-CLASSIFIER-STAGE0 module."""


class ClassifierTimeoutError(ClassifierError):
    """Subprocess / API call exceeded the configured timeout (transient)."""


class ClassifierSchemaError(ClassifierError):
    """Backend returned output that violates the ClassifierResult schema (permanent)."""
