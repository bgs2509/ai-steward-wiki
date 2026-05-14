# FILE: src/ai_steward_wiki/classifier/recurrence.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: NL recurrence parser for the digest fast-path (aisw-oqq).
#   SCOPE: Recurrence value + to_cron(); RecurrenceParseResult; parse_recurrence
#          (conservative rule-based ru regex daily|weekly; escalate otherwise).
#   DEPENDS: re, pydantic v2, structlog
#   LINKS: M-CLASSIFIER-RECURRENCE, M-CLASSIFIER-STAGE0, M-FOUNDATION-SETTINGS,
#          D-002, tech-spec §3, aisw-oqq
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Recurrence - frozen value: kind daily|weekly|monthly, time_hhmm, weekdays, day_of_month, tz; to_cron()
#   RecurrenceParseResult - frozen result: recurrence | None + escalate + reason
#   parse_recurrence - ru NL → RecurrenceParseResult (rule-based daily/weekly/monthly; escalate otherwise)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-r2k: typed monthly (kind=monthly + day_of_month); parser + cron
#   PREVIOUS:    v0.0.2 - aisw-oqq: parse_recurrence rule-based ru parser + escalate
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# ruff: noqa: RUF001, RUF003 (Cyrillic literals/comments in the ru recurrence regexes are intentional)
__all__ = ["Recurrence", "RecurrenceParseResult", "parse_recurrence"]

_log = structlog.get_logger("classifier.recurrence")

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Recurrence(BaseModel):
    """A recurring schedule reduced to APScheduler CronTrigger primitives.

    ``weekdays`` is 0=Mon … 6=Sun; required (non-empty) in practice when
    ``kind == 'weekly'``. ``tz`` is an IANA name passed to CronTrigger(timezone=).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["daily", "weekly", "monthly"]
    time_hhmm: str
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    tz: str

    @field_validator("time_hhmm")
    @classmethod
    def _check_time(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError(f"time_hhmm must be HH:MM 24h, got {v!r}")
        return v

    @field_validator("weekdays")
    @classmethod
    def _check_weekdays(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        for d in v:
            if not 0 <= d <= 6:
                raise ValueError(f"weekday out of range (0..6): {d}")
        return v

    @field_validator("day_of_month")
    @classmethod
    def _check_dom(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 31:
            raise ValueError(f"day_of_month out of range (1..31): {v}")
        return v

    @model_validator(mode="after")
    def _check_kind_fields(self) -> Recurrence:
        if self.kind == "monthly":
            if self.day_of_month is None:
                raise ValueError("day_of_month is required when kind='monthly'")
            if self.weekdays:
                raise ValueError("weekdays must be empty when kind='monthly'")
        else:
            if self.day_of_month is not None:
                raise ValueError(f"day_of_month is forbidden when kind={self.kind!r}")
        return self

    def to_cron(self) -> dict[str, object]:
        """Return CronTrigger keyword args for this recurrence."""
        hh, mm = (int(p) for p in self.time_hhmm.split(":"))
        if self.kind == "daily":
            return {"hour": hh, "minute": mm}
        if self.kind == "monthly":
            assert self.day_of_month is not None  # validated above
            return {"day": self.day_of_month, "hour": hh, "minute": mm}
        days = ",".join(_WEEKDAY_NAMES[d] for d in sorted(set(self.weekdays)))
        return {"day_of_week": days, "hour": hh, "minute": mm}


class RecurrenceParseResult(BaseModel):
    """Outcome of parsing a recurrence phrasing: a Recurrence, or escalate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recurrence: Recurrence | None = None
    escalate: bool = False
    reason: str = ""


# Time-of-day: "в 9", "в 9 утра", "в 21:30", "в 8 вечера". Returns "HH:MM" or None.
_TIME_RE = re.compile(
    r"в\s+(?P<h>2[0-3]|[01]?\d)(?!\d)(?::(?P<m>[0-5]\d))?\s*(?P<part>утра|дня|вечера|ночи)?",
    re.IGNORECASE,
)
_DAILY_RE = re.compile(r"кажд\w*\s+(день|дня|утр\w*|вечер\w*)|ежедневн\w*", re.IGNORECASE)
_WEEKLY_WORD_RE = re.compile(r"по\s+будн\w*|еженедельн\w*|кажд\w*\s+недел\w*", re.IGNORECASE)
_WEEKEND_RE = re.compile(r"по\s+выходн\w*", re.IGNORECASE)
_MONTHLY_RE = re.compile(
    r"\bчисл[аео]\b|\bчислам\b|каждого\s+месяц|ежемесячн|каждого\s+\w+го|\d{1,2}-го",
    re.IGNORECASE,
)
# Numeric day-of-month: "5 числа", "1-го числа", "по 15 числам", "1-го каждого месяца",
# "каждого 5-го", "28 числа", "ежемесячно 5-го".
_DOM_NUM_RE = re.compile(
    r"(?:по\s+)?(\d{1,2})(?:-го)?\s*(?:числ\w*|каждого)|каждого\s+(\d{1,2})-го|ежемесячн\w*\s+(\d{1,2})-?го?",
    re.IGNORECASE,
)
_ORDINAL_WORDS: dict[str, int] = {
    "первого": 1,
    "второго": 2,
    "третьего": 3,
    "четвертого": 4,
    "четвёртого": 4,
    "пятого": 5,
    "шестого": 6,
    "седьмого": 7,
    "восьмого": 8,
    "девятого": 9,
    "десятого": 10,
}
_DOM_ORDINAL_RE = re.compile(
    r"каждого\s+(первого|второго|третьего|четвертого|четвёртого|пятого|шестого|седьмого|восьмого|девятого|десятого)",
    re.IGNORECASE,
)
_DAY_NAME_RE: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"понедельник", re.IGNORECASE), 0),
    (re.compile(r"вторник", re.IGNORECASE), 1),
    (re.compile(r"\bсред[аеуы]\b|средам", re.IGNORECASE), 2),
    (re.compile(r"четверг", re.IGNORECASE), 3),
    (re.compile(r"пятниц", re.IGNORECASE), 4),
    (re.compile(r"суббот", re.IGNORECASE), 5),
    (re.compile(r"воскресень|воскресен", re.IGNORECASE), 6),
)


def _extract_day_of_month(text: str) -> int | None:
    """Return 1..31 day-of-month parsed from ru phrasings, or None."""
    m_ord = _DOM_ORDINAL_RE.search(text)
    if m_ord:
        word = m_ord.group(1).lower().replace("ё", "е")
        # _ORDINAL_WORDS keys include both "четвертого" and "четвёртого"; lookup direct first
        return _ORDINAL_WORDS.get(m_ord.group(1).lower()) or _ORDINAL_WORDS.get(word)
    for m in _DOM_NUM_RE.finditer(text):
        for g in m.groups():
            if g:
                try:
                    n = int(g)
                except ValueError:
                    continue
                if 1 <= n <= 31:
                    return n
                return None  # found a number but out of range → signal invalid
    return None


def _extract_time(text: str) -> str | None:
    m = _TIME_RE.search(text)
    if not m:
        return None
    h = int(m.group("h"))
    mm = int(m.group("m") or 0)
    part = (m.group("part") or "").lower()
    if part in {"вечера", "ночи", "дня"} and h < 12:
        h += 12
    if h > 23:
        return None
    return f"{h:02d}:{mm:02d}"


def parse_recurrence(
    text: str,
    *,
    user_tz: str,
    correlation_id: str = "",
) -> RecurrenceParseResult:
    """Conservative ru NL → recurrence. Escalates on interval/ambiguous phrasings."""
    if _MONTHLY_RE.search(text):
        time_hhmm = _extract_time(text)
        if time_hhmm is None:
            _log.info(
                "classifier.recurrence.parse",
                outcome="escalate",
                reason="monthly_no_time",
                correlation_id=correlation_id,
            )
            return RecurrenceParseResult(escalate=True, reason="no_time_of_day")
        dom = _extract_day_of_month(text)
        if dom is None:
            _log.info(
                "classifier.recurrence.parse",
                outcome="escalate",
                reason="monthly_no_day",
                correlation_id=correlation_id,
            )
            return RecurrenceParseResult(escalate=True, reason="monthly_day_unrecognized")
        rec = Recurrence(kind="monthly", time_hhmm=time_hhmm, day_of_month=dom, tz=user_tz)
        _log.info(
            "classifier.recurrence.parse",
            outcome="monthly",
            day_of_month=dom,
            time=time_hhmm,
            correlation_id=correlation_id,
        )
        return RecurrenceParseResult(recurrence=rec)
    time_hhmm = _extract_time(text)
    if time_hhmm is None:
        _log.info(
            "classifier.recurrence.parse",
            outcome="escalate",
            reason="no_time",
            correlation_id=correlation_id,
        )
        return RecurrenceParseResult(escalate=True, reason="no_time_of_day")

    named = tuple(d for rx, d in _DAY_NAME_RE if rx.search(text))
    if named or _WEEKLY_WORD_RE.search(text) or _WEEKEND_RE.search(text):
        if named:
            weekdays: tuple[int, ...] = tuple(sorted(set(named)))
        elif _WEEKEND_RE.search(text):
            weekdays = (5, 6)
        else:
            weekdays = (0, 1, 2, 3, 4)
        rec = Recurrence(kind="weekly", time_hhmm=time_hhmm, weekdays=weekdays, tz=user_tz)
        _log.info(
            "classifier.recurrence.parse",
            outcome="weekly",
            weekdays=weekdays,
            time=time_hhmm,
            correlation_id=correlation_id,
        )
        return RecurrenceParseResult(recurrence=rec)
    if _DAILY_RE.search(text):
        rec = Recurrence(kind="daily", time_hhmm=time_hhmm, tz=user_tz)
        _log.info(
            "classifier.recurrence.parse",
            outcome="daily",
            time=time_hhmm,
            correlation_id=correlation_id,
        )
        return RecurrenceParseResult(recurrence=rec)
    _log.info(
        "classifier.recurrence.parse",
        outcome="escalate",
        reason="ambiguous",
        correlation_id=correlation_id,
    )
    return RecurrenceParseResult(escalate=True, reason="ambiguous")
