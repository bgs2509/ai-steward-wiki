# FILE: src/ai_steward_wiki/classifier/time_parse.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: NL time parser — dateparser → Haiku-fallback → escalate (D-010).
#   SCOPE: parse_time() public API; UTC invariant; user-TZ from caller.
#   DEPENDS: dateparser, structlog, ai_steward_wiki.classifier.{schema,backend}
#   LINKS: M-CLASSIFIER-STAGE0, D-010, aisw-kcz
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   parse_time - async; tries dateparser, then Haiku-fallback, then escalates; prefer_future rolls bare past times forward
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-kcz: add prefer_future kwarg (PREFER_DATES_FROM='future') for reminders
#   PREVIOUS:    v0.0.1 - initial 3-step parser with UTC invariant
# END_CHANGE_SUMMARY

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import dateparser
import structlog

from ai_steward_wiki.classifier.backend import ClassifierBackend
from ai_steward_wiki.classifier.schema import TimeParseResult

__all__ = [
    "parse_time",
]

_log = structlog.get_logger("classifier.time")


def _to_utc(dt: datetime, fallback_tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=fallback_tz)
    return dt.astimezone(UTC)


async def parse_time(
    text: str,
    *,
    user_tz: ZoneInfo,
    now_utc: datetime,
    prefer_future: bool = False,
    haiku_backend: ClassifierBackend | None = None,
    haiku_prompt_path: Path | None = None,
    correlation_id: str = "",
) -> TimeParseResult:
    """Parse NL time per D-010. Returns UTC-only when_utc, or escalate=True.

    `prefer_future=True` makes a bare past wall-clock time («в 6» at 21:00) roll
    forward to the next future occurrence (PREFER_DATES_FROM='future') — used by
    the reminder fast-path (aisw-kcz).
    """
    started_dp = time.monotonic()
    relative_base = now_utc.astimezone(user_tz)
    settings: dict[str, object] = {
        "TIMEZONE": str(user_tz),
        "RELATIVE_BASE": relative_base.replace(tzinfo=None),
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    if prefer_future:
        settings["PREFER_DATES_FROM"] = "future"
    parsed = dateparser.parse(
        text,
        settings=settings,  # type: ignore[arg-type]
        languages=["ru", "en"],
    )
    dp_ms = int((time.monotonic() - started_dp) * 1000)

    if parsed is not None:
        when_utc = _to_utc(parsed, user_tz)
        _log.info(
            "classifier.time.parse",
            correlation_id=correlation_id,
            source="dateparser",
            escalate=False,
            dateparser_ms=dp_ms,
        )
        return TimeParseResult(
            when_utc=when_utc,
            source="dateparser",
            escalate=False,
            raw=text,
            user_tz=str(user_tz),
        )

    # dateparser miss → Haiku-fallback (only if backend wired)
    if haiku_backend is None or haiku_prompt_path is None:
        _log.info(
            "classifier.time.parse",
            correlation_id=correlation_id,
            source="escalate",
            escalate=True,
            dateparser_ms=dp_ms,
            reason="no_haiku_backend",
        )
        return TimeParseResult(
            when_utc=None, source="escalate", escalate=True, raw=text, user_tz=str(user_tz)
        )

    started_h = time.monotonic()
    raw = await haiku_backend.call(
        text=text, prompt_path=haiku_prompt_path, correlation_id=correlation_id
    )
    haiku_ms = int((time.monotonic() - started_h) * 1000)

    if raw.get("ambiguous"):
        _log.info(
            "classifier.time.parse",
            correlation_id=correlation_id,
            source="escalate",
            escalate=True,
            dateparser_ms=dp_ms,
            haiku_ms=haiku_ms,
        )
        return TimeParseResult(
            when_utc=None, source="escalate", escalate=True, raw=text, user_tz=str(user_tz)
        )

    when_iso = raw.get("when_iso")
    if not isinstance(when_iso, str):
        _log.warning(
            "classifier.time.parse",
            correlation_id=correlation_id,
            source="escalate",
            escalate=True,
            reason="haiku_no_when_iso",
        )
        return TimeParseResult(
            when_utc=None, source="escalate", escalate=True, raw=text, user_tz=str(user_tz)
        )

    parsed_h = datetime.fromisoformat(when_iso)
    when_utc = _to_utc(parsed_h, user_tz)
    _log.info(
        "classifier.time.parse",
        correlation_id=correlation_id,
        source="haiku_fallback",
        escalate=False,
        dateparser_ms=dp_ms,
        haiku_ms=haiku_ms,
    )
    return TimeParseResult(
        when_utc=when_utc,
        source="haiku_fallback",
        escalate=False,
        raw=text,
        user_tz=str(user_tz),
    )
