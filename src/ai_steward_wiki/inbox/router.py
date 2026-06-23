# FILE: src/ai_steward_wiki/inbox/router.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Parse the Stage-1a Inbox-WIKI Router reply (a fenced ```router block) into a RouterDecision.
#   SCOPE: RouterIntent enum, RouterDecision model, RouterError, parse_router_reply (tolerant, fallback to CLARIFY),
#          format_chat_window (render the D-033 recent dialogue window),
#          build_router_input (prefix user input with the owner's existing <Domain>-WIKI list + recent history).
#   DEPENDS: pydantic, re, enum
#   LINKS: D-004, D-016, D-033, prompts/inbox.md (>=1.1.0), M-INBOX-ROUTER, aisw-dsg, aisw-2co, aisw-kml
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   RouterIntent - closed enum: route | create_wiki | clarify | reject
#   RouterDecision - frozen Pydantic model (intent, target_wiki, notes, raw, parsed_ok)
#   RouterError - raised by the runtime adapter when the Router CLI run fails unrecoverably
#   parse_router_reply - extract the last ```router block, parse key:value, normalise, fallback to CLARIFY
#   format_chat_window - render the D-033 recent dialogue window into a compact ru transcript
#   build_router_input - prefix the router user-input with existing <Domain>-WIKI names + recent history
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-kml: build_router_input folds in the D-033 recent chat window
#                (last-20/24h) so bare follow-ups route on history instead of cold-rejecting.
#   PREVIOUS:    v0.0.2 - aisw-2co: add build_router_input — surface the owner's existing
#                <Domain>-WIKI list to the Stage-1a router so it can route to an existing
#                WIKI (e.g. medical data -> Medical-WIKI) instead of always proposing a new one.
#   PREVIOUS:    v0.0.1 - initial Router reply model + parser (aisw-dsg, Inbox-WIKI Phase-A)
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ai_steward_wiki.storage.audit.chat_log import ChatTurn

__all__ = [
    "RouterDecision",
    "RouterError",
    "RouterIntent",
    "build_router_input",
    "format_chat_window",
    "parse_router_reply",
]

_MAX_FALLBACK_NOTES = 500
_GENERIC_CLARIFY_RU = "Уточни, пожалуйста, к какой теме это относится?"

# Last fenced ```router … ``` block in the reply (a model may quote the format first).
_BLOCK_RE = re.compile(r"```router[^\n]*\n(?P<body>.*?)\n?```", re.DOTALL)
_KEY_RE = re.compile(r"^(?P<key>target_wiki|intent|notes)\s*:\s?(?P<val>.*)$")
_NULLISH = {"", "null", "none", "~"}


class RouterError(Exception):
    """The Stage-1a Router CLI run failed unrecoverably (raised by the runtime adapter)."""


class RouterIntent(str, Enum):
    ROUTE = "route"  # belongs to an existing <Domain>-WIKI (target_wiki set)
    CREATE_WIKI = "create_wiki"  # no domain yet; proposes a new NL name in target_wiki
    CLARIFY = "clarify"  # needs a follow-up question; target_wiki is None
    REJECT = "reject"  # not actionable / out of scope; target_wiki is None


class RouterDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: RouterIntent
    target_wiki: str | None
    notes: str
    raw: str
    parsed_ok: bool


# START_CONTRACT: parse_router_reply
#   PURPOSE: Turn a free-text Stage-1a Router reply into a structured RouterDecision.
#   INPUTS: { text: str - assistant reply, expected to contain a ```router fenced block }
#   OUTPUTS: { RouterDecision - parsed_ok=False on missing/malformed block (intent forced to CLARIFY) }
#   SIDE_EFFECTS: none (pure)
#   LINKS: prompts/inbox.md §"Формат ответа", M-INBOX-ROUTER
# END_CONTRACT: parse_router_reply
def parse_router_reply(text: str) -> RouterDecision:
    raw = text if text is not None else ""

    # START_BLOCK_EXTRACT_FENCED
    matches = list(_BLOCK_RE.finditer(raw))
    if not matches:
        return _fallback(raw)
    body = matches[-1].group("body")
    # END_BLOCK_EXTRACT_FENCED

    # START_BLOCK_PARSE_FIELDS
    target_raw: str | None = None
    intent_raw: str | None = None
    notes_lines: list[str] | None = None
    for line in body.splitlines():
        if notes_lines is not None:
            notes_lines.append(line)
            continue
        m = _KEY_RE.match(line.strip()) or _KEY_RE.match(line)
        if not m:
            continue
        key, val = m.group("key"), m.group("val").strip()
        if key == "target_wiki":
            target_raw = val
        elif key == "intent":
            intent_raw = val
        elif key == "notes":
            notes_lines = [val]
    # END_BLOCK_PARSE_FIELDS

    # START_BLOCK_NORMALISE
    notes = "\n".join(notes_lines).strip() if notes_lines is not None else ""
    target = None if (target_raw or "").strip().lower() in _NULLISH else target_raw.strip()  # type: ignore[union-attr]
    try:
        intent = RouterIntent((intent_raw or "").strip().lower())
    except ValueError:
        return _fallback(raw, notes=notes)
    if intent in (RouterIntent.CLARIFY, RouterIntent.REJECT):
        target = None
    elif intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI) and not target:
        # The model picked a routing intent but gave no target — demote to CLARIFY.
        return _fallback(raw, notes=notes)
    if not notes:
        notes = _GENERIC_CLARIFY_RU if intent is RouterIntent.CLARIFY else "(без пояснения)"
    return RouterDecision(intent=intent, target_wiki=target, notes=notes, raw=raw, parsed_ok=True)
    # END_BLOCK_NORMALISE


_CHAT_WINDOW_HEADER = "[Недавняя история диалога (от старых к новым)]"
_CHAT_DIRECTION_RU = {"in": "Пользователь", "out": "Бот"}


# START_CONTRACT: format_chat_window
#   PURPOSE: Render the D-033 recent chat window into a compact ru transcript block.
#   INPUTS: { window: Sequence[ChatTurn] - oldest-first recent turns (in/out) }
#   OUTPUTS: { str - a header + "Пользователь: …/Бот: …" lines, or "" when window is empty }
#   SIDE_EFFECTS: none (pure)
#   LINKS: D-033 §"Access pattern", aisw-kml
# END_CONTRACT: format_chat_window
def format_chat_window(window: Sequence[ChatTurn]) -> str:
    if not window:
        return ""
    lines = [_CHAT_WINDOW_HEADER]
    for turn in window:
        speaker = _CHAT_DIRECTION_RU.get(turn.direction, turn.direction)
        body = (turn.text or "").strip().replace("\n", " ")
        lines.append(f"{speaker}: {body}")
    return "\n".join(lines)


# START_CONTRACT: build_router_input
#   PURPOSE: Prefix the Stage-1a router user-input with the owner's existing <Domain>-WIKI
#            list and (optionally) the D-033 recent dialogue window.
#   INPUTS: { text: str - the raw message, existing_wikis: Sequence[str] - dir names like
#             "Medical-WIKI", recent_window: Sequence[ChatTurn]|None - oldest-first recent turns }
#   OUTPUTS: { str - the WIKI-list header, an optional recent-history block, a blank line, then the text }
#   SIDE_EFFECTS: none (pure)
#   LINKS: prompts/inbox.md, M-INBOX-ROUTER, aisw-2co, D-033, aisw-kml
# END_CONTRACT: build_router_input
def build_router_input(
    text: str,
    existing_wikis: Sequence[str],
    *,
    recent_window: Sequence[ChatTurn] | None = None,
) -> str:
    """Surface the owner's existing <Domain>-WIKI names (and recent dialogue) to the router.

    The Stage-1a router runs scoped to Inbox-WIKI and cannot otherwise see sibling
    WIKIs, so without this list it can only ever propose ``create_wiki``. Listing
    the existing WIKIs lets it return ``intent=route`` to a matching one
    (e.g. blood-pressure text -> Medical-WIKI) instead of duplicating a domain.

    When ``recent_window`` is supplied (D-033 last-20/24h buffer) a compact ru
    transcript is interleaved so a bare follow-up ("повтори последний ответ") is
    routed using history instead of cold-rejected.
    """
    if existing_wikis:
        listing = ", ".join(existing_wikis)
        header = f"[Существующие WIKI пользователя: {listing}]"
    else:
        header = "[У пользователя пока нет ни одной <Domain>-WIKI]"  # noqa: RUF001
    history = format_chat_window(recent_window or [])
    prefix = f"{header}\n\n{history}" if history else header
    return f"{prefix}\n\n{text}"


def _fallback(raw: str, *, notes: str = "") -> RouterDecision:
    candidate = (notes or raw).strip()
    notes_out = candidate[:_MAX_FALLBACK_NOTES].strip() or _GENERIC_CLARIFY_RU
    return RouterDecision(
        intent=RouterIntent.CLARIFY,
        target_wiki=None,
        notes=notes_out,
        raw=raw,
        parsed_ok=False,
    )
