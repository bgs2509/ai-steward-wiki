# FILE: src/ai_steward_wiki/classifier/schema.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Pydantic schemas + intent enum + error classes for Stage-0 classifier.
#   SCOPE: Intent enum (closed list), ClassifierResult, TimeParseResult, error hierarchy,
#          WikiSlots/JobSlots lenient boundary slot models + parse_slots (aisw-xi8).
#   DEPENDS: pydantic
#   LINKS: M-CLASSIFIER-STAGE0, D-009, D-010, D-015, aisw-xi8, ADR-036
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Intent - closed 6-member artifact-anchored enum (aisw-xi8: wiki|job|web|chat|admin|unknown)
#   WikiSlots - frozen slot model: action (ingest|query|lint|catalog|None)
#   JobSlots - frozen slot model: action/kind/time_expr/schedule_expr/text/needle
#   parse_slots - lenient boundary parser: unknown keys ignored, bad values -> default instance
#   ClassifierResult - frozen Pydantic v2 model carrying intent/confidence/audit fields
#   TimeParseResult - frozen Pydantic v2 model carrying when_utc + escalation flag
#   ClassifierError - base exception
#   ClassifierTimeoutError - transient subclass (chunk 4 taxonomy)
#   ClassifierSchemaError - permanent subclass when CLI JSON violates schema
#   unwrap_fenced_json - parse a JSON object from a model reply, stripping a ```json fence
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-xi8 (Phase-A, FR-1/DEC-4): Intent narrows from the 9-member
#                v1 enum (reminder/wiki_ingest/wiki_query/wiki_lint/digest/web_task/smalltalk/
#                admin/unknown) to the closed 6-member artifact-anchored taxonomy
#                wiki|job|web|chat|admin|unknown. New WikiSlots/JobSlots + parse_slots —
#                lenient boundary parsing over distilled_payload (unknown keys ignored,
#                ValidationError on a known-but-malformed field -> default instance +
#                classifier.slots.invalid, never a user-facing error). ClassifierResult
#                shape unchanged (distilled_payload stays dict[str, Any]).
#   PREVIOUS:    v0.0.4 - aisw-7j3: add unwrap_fenced_json() — strip a fenced ```json
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
from typing import Any, Literal, TypeVar

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "ClassifierError",
    "ClassifierResult",
    "ClassifierSchemaError",
    "ClassifierTimeoutError",
    "Intent",
    "JobSlots",
    "TimeParseResult",
    "WikiSlots",
    "parse_slots",
    "unwrap_fenced_json",
]

_log = structlog.get_logger("classifier.schema")


class Intent(str, Enum):
    """Closed 6-member artifact-anchored taxonomy (aisw-xi8). An intent exists only
    for a distinct artifact (wiki files, jobs.db rows, the live web, service state,
    nothing) — verbs/kinds live in WikiSlots/JobSlots, never in the enum itself."""

    WIKI = "wiki"
    JOB = "job"
    WEB = "web"
    CHAT = "chat"
    ADMIN = "admin"
    UNKNOWN = "unknown"


class WikiSlots(BaseModel):
    """distilled_payload slots for intent=wiki (DEC-4). action=None is a VALID
    default — it covers the measured "Покажи мои вики" miss (empty action still
    routes correctly downstream via the DEC-3 routable predicate)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["ingest", "query", "lint", "catalog"] | None = None


class JobSlots(BaseModel):
    """distilled_payload slots for intent=job (DEC-4). action defaults to "create"
    (the dominant case) and kind defaults to "once" — time/recurrence validators
    still gate before anything is scheduled, so a wrong default never mis-schedules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["create", "cancel", "list", "reschedule"] = "create"
    kind: Literal["once", "recurring", "check_in", "digest"] = "once"
    time_expr: str = ""
    schedule_expr: str = ""
    text: str = ""
    needle: str = ""


_SlotModelT = TypeVar("_SlotModelT", bound=BaseModel)


def parse_slots(model: type[_SlotModelT], distilled_payload: dict[str, Any]) -> _SlotModelT:
    """Lenient boundary parse of a classifier distilled_payload into a slot model.

    Unknown keys are dropped before validation (so the model's ``extra="forbid"``
    never fires on a key belonging to a DIFFERENT slot model, e.g. a stray
    "needle" key when parsing WikiSlots). A malformed value for a KNOWN field
    (e.g. action=123) raises ValidationError internally — caught here and
    downgraded to the model's default instance + a classifier.slots.invalid log
    anchor. This function never raises — Haiku output must never 500 the turn.
    """
    known_keys = model.model_fields.keys()
    filtered = {k: v for k, v in distilled_payload.items() if k in known_keys}
    try:
        return model.model_validate(filtered)
    except ValidationError:
        _log.warning("classifier.slots.invalid", model=model.__name__, keys=list(filtered))
        return model()


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
