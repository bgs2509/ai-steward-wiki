# Inbox-WIKI Phase-D.b.1 — `digest_job` vertical slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create a recurring digest by natural language («каждый день в 9 утра сводка») via an inline-button confirm; on schedule, run Claude with `--add-dir` into the owner's `<Name>-WIKI`s and deliver the summary to Telegram.

**Architecture:** Mirrors aisw-kcz (`reminder_job`). New `classifier/recurrence.py` parses ru recurrence phrasings → `Recurrence` → APScheduler `CronTrigger`. `DigestPayload` widened. `scheduler/firing.py` gains `create_digest_job` / `fire_digest_job` — direct-fire (no `PriorityJobQueue` consumer): the firing callback runs `run_wiki_session` under the existing `WikiLockManager` semaphore + per-WIKI lock and delivers via `tg/output.deliver_output`; 3 consecutive failures auto-disable (D-019). `tg/pipeline.py`'s recurring-digest stub becomes a real `category='digest'` confirm flow.

**Tech Stack:** Python 3.11, Pydantic v2, APScheduler (`AsyncIOScheduler` + `SQLAlchemyJobStore` + `CronTrigger`), aiogram 3.x, structlog, `claude` CLI via `run_wiki_session`.

**bd_id:** aisw-oqq · **Design:** `docs/superpowers/specs/20260512-inbox-wiki-digest-job-design.md` · **GRACE:** `M-CLASSIFIER-RECURRENCE` (new), `V-M-CLASSIFIER-RECURRENCE`, extended `V-M-SCHEDULER-FIRING`.

**Conventions:** TDD (RED → GREEN → REFACTOR). All DB datetimes naive-UTC. Commit format `<type>(<MODULE_ID>): <desc>`. Run `make lint` (ruff check + ruff format --check + mypy src) before each commit; never `--no-verify`. After every code file change, update the file's `MODULE_MAP` + `VERSION` + `CHANGE_SUMMARY` header (GRACE rule). After all tasks: `grace lint --failOn errors`, `make inv-lint`, `uv run pytest tests/unit`.

---

## File structure

- **Create** `src/ai_steward_wiki/classifier/recurrence.py` — `Recurrence`, `RecurrenceParseResult`, `parse_recurrence` (`M-CLASSIFIER-RECURRENCE`).
- **Create** `tests/unit/classifier/test_recurrence.py`.
- **Modify** `src/ai_steward_wiki/storage/jobs/payloads.py` — widen `DigestPayload` (`M-STORAGE-JOBS`).
- **Modify** `tests/unit/storage/test_payloads.py` — add digest cases.
- **Modify** `src/ai_steward_wiki/wiki/runner.py` — `run_wiki_session(..., extra_add_dirs=...)`; `_build_argv` (`M-WIKI-RUNNER`).
- **Modify** `tests/unit/wiki/test_runner.py` — add `extra_add_dirs` argv test.
- **Modify** `src/ai_steward_wiki/scheduler/firing.py` — `set_digest_context`, `create_digest_job`, `fire_digest_job`, `DigestNotInitialisedError` (`M-SCHEDULER-FIRING`).
- **Modify** `tests/unit/scheduler/test_firing.py` — add digest cases.
- **Modify** `src/ai_steward_wiki/tg/pipeline.py` — `_handle_digest_intent`, `_handle_digest_confirm`, `build_digest_recap`, `RecurrenceParser` Protocol, `DIGEST_*` ru constants, `on_confirm_callback` `category=='digest'` dispatch, new `DefaultPipeline.__init__` kwarg (`M-TG-PIPELINE-CLASSIFIER`).
- **Create** `tests/unit/tg/test_pipeline_digest.py`.
- **Create** `tests/unit/tg/test_digest_e2e.py`.
- **Modify** `src/ai_steward_wiki/__main__.py` — `_RecurrenceParserAdapter`, `_resolve_owner_wikis`, `firing.set_digest_context(...)`, pass `recurrence_parser=` (`M-RUNTIME-WIRING`).
- **Create** `prompts/digest.md` (Stage-1 overlay; `semver:` line).
- **Create** `prompts/recurrence.md` (optional Haiku-fallback prompt; can be a 3-line skeleton).
- **Modify** GRACE artifacts — already done in commit e0c28ab; only re-check `grace lint` at the end. ADR-007 written at Finish.

---

## Task 1: `Recurrence` value + `to_cron()`

**Files:**
- Create: `src/ai_steward_wiki/classifier/recurrence.py`
- Test: `tests/unit/classifier/test_recurrence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/classifier/test_recurrence.py
from ai_steward_wiki.classifier.recurrence import Recurrence


def test_daily_to_cron():
    r = Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")
    assert r.to_cron() == {"hour": 9, "minute": 0}


def test_weekly_to_cron_orders_weekdays():
    r = Recurrence(kind="weekly", time_hhmm="19:05", weekdays=(4, 0), tz="Europe/Moscow")
    assert r.to_cron() == {"day_of_week": "mon,fri", "hour": 19, "minute": 5}


def test_recurrence_frozen_and_extra_forbidden():
    import pytest
    from pydantic import ValidationError

    r = Recurrence(kind="daily", time_hhmm="08:00", tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="08:00", tz="UTC", junk=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        r.time_hhmm = "07:00"  # type: ignore[misc]


def test_invalid_time_hhmm_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="9:00", tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="25:00", tz="UTC")
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/classifier/test_recurrence.py -q` → FAIL (module not found).

- [ ] **Step 3: Write the module skeleton (model only)**

```python
# FILE: src/ai_steward_wiki/classifier/recurrence.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: NL recurrence parser for the digest fast-path (aisw-oqq).
#   SCOPE: Recurrence value + to_cron(); RecurrenceParseResult; parse_recurrence
#          (rule-based ru regex daily|weekly + optional Haiku fallback → escalate).
#   DEPENDS: re, pydantic v2, structlog, ai_steward_wiki.classifier.backend (typing)
#   LINKS: M-CLASSIFIER-RECURRENCE, M-CLASSIFIER-STAGE0, D-002, tech-spec §3, aisw-oqq
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Recurrence - frozen value: kind daily|weekly, time_hhmm, weekdays tuple, tz; to_cron()
#   RecurrenceParseResult - frozen result: recurrence | None + escalate + reason
#   parse_recurrence - ru NL → RecurrenceParseResult (rule-based → optional Haiku → escalate)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-oqq: Recurrence model + parse_recurrence (daily/weekly MVP)
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["Recurrence", "RecurrenceParseResult", "parse_recurrence"]

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Recurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["daily", "weekly"]
    time_hhmm: str
    weekdays: tuple[int, ...] = ()  # 0=mon … 6=sun; required+non-empty when kind=='weekly'
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
                raise ValueError(f"weekday out of range: {d}")
        return v

    def to_cron(self) -> dict[str, object]:
        hh, mm = (int(p) for p in self.time_hhmm.split(":"))
        if self.kind == "daily":
            return {"hour": hh, "minute": mm}
        days = ",".join(_WEEKDAY_NAMES[d] for d in sorted(set(self.weekdays)))
        return {"day_of_week": days, "hour": hh, "minute": mm}
```

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/unit/classifier/test_recurrence.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add src/ai_steward_wiki/classifier/recurrence.py tests/unit/classifier/test_recurrence.py && git commit -m "feat(M-CLASSIFIER-RECURRENCE): Recurrence value + to_cron (aisw-oqq)"`

---

## Task 2: `parse_recurrence` — rule-based ru parser + escalate

**Files:**
- Modify: `src/ai_steward_wiki/classifier/recurrence.py`
- Test: `tests/unit/classifier/test_recurrence.py`

- [ ] **Step 1: Add failing tests**

```python
# append to tests/unit/classifier/test_recurrence.py
from ai_steward_wiki.classifier.recurrence import RecurrenceParseResult, parse_recurrence


def test_parse_daily():
    r = parse_recurrence("каждый день в 9 утра сводка", user_tz="Europe/Moscow").recurrence
    assert r == Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


def test_parse_daily_explicit_minutes():
    r = parse_recurrence("присылай дайджест каждый день в 21:30", user_tz="UTC").recurrence
    assert r == Recurrence(kind="daily", time_hhmm="21:30", tz="UTC")


def test_parse_weekly_weekdays_word():
    r = parse_recurrence("сводка по будням в 19:00", user_tz="Europe/Moscow").recurrence
    assert r == Recurrence(kind="weekly", time_hhmm="19:00", weekdays=(0, 1, 2, 3, 4), tz="Europe/Moscow")


def test_parse_weekly_named_days():
    r = parse_recurrence("еженедельно по понедельникам и пятницам в 8", user_tz="UTC").recurrence
    assert r == Recurrence(kind="weekly", time_hhmm="08:00", weekdays=(0, 4), tz="UTC")


def test_parse_no_time_escalates():
    res = parse_recurrence("каждый день сводка", user_tz="UTC")
    assert res.recurrence is None and res.escalate is True


def test_parse_monthly_escalates():
    res = parse_recurrence("15 числа каждого месяца отчёт", user_tz="UTC")
    assert res.recurrence is None and res.escalate is True


def test_parse_unrelated_escalates():
    res = parse_recurrence("просто текст", user_tz="UTC")
    assert res.recurrence is None and res.escalate is True
```

- [ ] **Step 2: Run** — `uv run pytest tests/unit/classifier/test_recurrence.py -q` → FAIL.

- [ ] **Step 3: Implement `RecurrenceParseResult` + `parse_recurrence`**

Add to `recurrence.py` (and bump `VERSION` to `0.0.2`, update `CHANGE_SUMMARY`):

```python
import structlog
from pydantic import ConfigDict

_log = structlog.get_logger("classifier.recurrence")


class RecurrenceParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recurrence: Recurrence | None = None
    escalate: bool = False
    reason: str = ""


# Time-of-day: "в 9", "в 9 утра", "в 21:30", "в 8 вечера". Returns "HH:MM" or None.
_TIME_RE = re.compile(
    r"в\s+(?P<h>[01]?\d|2[0-3])(?::(?P<m>[0-5]\d))?\s*(?P<part>утра|дня|вечера|ночи)?",
    re.IGNORECASE,
)
_DAILY_RE = re.compile(r"кажд\w*\s+(день|утр\w*|вечер\w*)|ежедневн\w*", re.IGNORECASE)
_WEEKLY_WORD_RE = re.compile(r"по\s+будн\w*|еженедельн\w*|кажд\w*\s+недел\w*", re.IGNORECASE)
_WEEKEND_RE = re.compile(r"по\s+выходн\w*", re.IGNORECASE)
_MONTHLY_RE = re.compile(r"\bчисл[аео]\b|каждого\s+месяц|ежемесячн", re.IGNORECASE)
_DAY_NAME_RE: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"понедельник", re.IGNORECASE), 0),
    (re.compile(r"вторник", re.IGNORECASE), 1),
    (re.compile(r"\bсред[ауы]\b|средам", re.IGNORECASE), 2),
    (re.compile(r"четверг", re.IGNORECASE), 3),
    (re.compile(r"пятниц", re.IGNORECASE), 4),
    (re.compile(r"суббот", re.IGNORECASE), 5),
    (re.compile(r"воскресень|воскресен", re.IGNORECASE), 6),
)


def _extract_time(text: str) -> str | None:
    m = _TIME_RE.search(text)
    if not m:
        return None
    h = int(m.group("h"))
    mm = int(m.group("m") or 0)
    part = (m.group("part") or "").lower()
    if part in {"вечера", "ночи"} and h < 12:
        h += 12
    if part == "дня" and h < 12:
        h += 12
    if h > 23:
        return None
    return f"{h:02d}:{mm:02d}"


def parse_recurrence(
    text: str,
    *,
    user_tz: str,
    haiku_backend: object | None = None,  # reserved; not used in MVP rule path
    correlation_id: str = "",
) -> RecurrenceParseResult:
    """Conservative ru NL → recurrence. Escalates on monthly/interval/ambiguous."""
    if _MONTHLY_RE.search(text):
        _log.info("classifier.recurrence.parse", outcome="escalate", reason="monthly", correlation_id=correlation_id)
        return RecurrenceParseResult(escalate=True, reason="monthly_not_supported")
    time_hhmm = _extract_time(text)
    if time_hhmm is None:
        _log.info("classifier.recurrence.parse", outcome="escalate", reason="no_time", correlation_id=correlation_id)
        return RecurrenceParseResult(escalate=True, reason="no_time_of_day")

    named = tuple(d for rx, d in _DAY_NAME_RE if rx.search(text))
    if named or _WEEKLY_WORD_RE.search(text) or _WEEKEND_RE.search(text):
        if named:
            weekdays = tuple(sorted(set(named)))
        elif _WEEKEND_RE.search(text):
            weekdays = (5, 6)
        else:  # "по будням" / "еженедельно" with no named day → weekdays
            weekdays = (0, 1, 2, 3, 4)
        rec = Recurrence(kind="weekly", time_hhmm=time_hhmm, weekdays=weekdays, tz=user_tz)
        _log.info("classifier.recurrence.parse", outcome="weekly", weekdays=weekdays, time=time_hhmm, correlation_id=correlation_id)
        return RecurrenceParseResult(recurrence=rec)
    if _DAILY_RE.search(text):
        rec = Recurrence(kind="daily", time_hhmm=time_hhmm, tz=user_tz)
        _log.info("classifier.recurrence.parse", outcome="daily", time=time_hhmm, correlation_id=correlation_id)
        return RecurrenceParseResult(recurrence=rec)
    _log.info("classifier.recurrence.parse", outcome="escalate", reason="ambiguous", correlation_id=correlation_id)
    return RecurrenceParseResult(escalate=True, reason="ambiguous")
```

- [ ] **Step 4: Run** — `uv run pytest tests/unit/classifier/test_recurrence.py -q` → PASS. Then `make lint` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(M-CLASSIFIER-RECURRENCE): parse_recurrence rule-based ru parser + escalate (aisw-oqq)"`

> **Note (executor):** The Haiku-fallback path (`haiku_backend`) is intentionally a no-op stub in the MVP — `prompts/recurrence.md` (Task 11) exists for a later wiring; do not add a real backend call in this phase. If a phrasing the rule path can't handle becomes common, that's a follow-up.

---

## Task 3: Widen `DigestPayload`

**Files:**
- Modify: `src/ai_steward_wiki/storage/jobs/payloads.py`
- Test: `tests/unit/storage/test_payloads.py`

- [ ] **Step 1: Add failing tests**

```python
# append to tests/unit/storage/test_payloads.py
from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.storage.jobs.payloads import DigestPayload, parse_job_payload


def _rec() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


def test_digest_payload_roundtrip():
    p = DigestPayload(recurrence=_rec())
    d = p.model_dump(mode="json")
    assert d["kind"] == "digest" and d["wiki_scope"] == "all" and d["window_hours"] == 24
    parsed = parse_job_payload(d)
    assert isinstance(parsed, DigestPayload) and parsed.recurrence == _rec()


def test_digest_payload_extra_forbidden():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), junk=1)  # type: ignore[call-arg]


def test_digest_payload_window_bounds():
    import pytest
    from pydantic import ValidationError

    DigestPayload(recurrence=_rec(), window_hours=168)
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), window_hours=0)
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), window_hours=169)


def test_digest_payload_frozen():
    import pytest
    from pydantic import ValidationError

    p = DigestPayload(recurrence=_rec())
    with pytest.raises(ValidationError):
        p.window_hours = 12  # type: ignore[misc]
```

- [ ] **Step 2: Run** — `uv run pytest tests/unit/storage/test_payloads.py -q` → FAIL.

- [ ] **Step 3: Edit `payloads.py`**

Replace the existing `DigestPayload` class:

```python
from ai_steward_wiki.classifier.recurrence import Recurrence  # add to imports


class DigestPayload(_PayloadBase):
    kind: Literal["digest"] = "digest"
    wiki_scope: Literal["all"] = "all"
    recurrence: Recurrence
    window_hours: int = Field(default=24, ge=1, le=24 * 7)
    prompt_hint: str | None = None
```

Update the module header: `VERSION` 0.0.3 → 0.0.4; `MODULE_MAP` line for `DigestPayload` → `"DigestPayload - recurring digest job (wiki_scope, recurrence, window_hours, prompt_hint) (aisw-oqq)"`; `CHANGE_SUMMARY` → `v0.0.4 - aisw-oqq: widen DigestPayload (wiki_scope/recurrence/window_hours/prompt_hint)`; add `DEPENDS` mention of `M-CLASSIFIER-RECURRENCE`.

> **mypy note:** `payloads.py` now imports from `classifier/recurrence.py`. `classifier/__init__.py` does NOT need to re-export it (import the module path directly), but check there's no import cycle: `recurrence.py` must NOT import from `storage`. It doesn't.

- [ ] **Step 4: Run** — `uv run pytest tests/unit/storage/test_payloads.py -q` → PASS. `make lint` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(M-STORAGE-JOBS): widen DigestPayload — wiki_scope/recurrence/window_hours/prompt_hint (aisw-oqq)"`

---

## Task 4: `run_wiki_session` — `extra_add_dirs`

**Files:**
- Modify: `src/ai_steward_wiki/wiki/runner.py` (`_build_argv` ~line 268, `run_wiki_session` ~line 332)
- Test: `tests/unit/wiki/test_runner.py`

- [ ] **Step 1: Add failing test**

```python
# append to tests/unit/wiki/test_runner.py
from pathlib import Path

from ai_steward_wiki.wiki.runner import _build_argv  # if private import is awkward, test via run_wiki_session w/ a fake spawner


def test_build_argv_extra_add_dirs_after_primary():
    argv = _build_argv(
        binary="claude", model="m", wiki_path=Path("/w/Health-WIKI"),
        prompt_path=Path("/tmp/p.md"), allowed_tools=None, disallowed_tools=None,
        media_dirs=None, extra_add_dirs=[Path("/w/Finance-WIKI"), Path("/w/Home-WIKI")],
    )
    i = argv.index("--add-dir")
    assert argv[i + 1] == "/w/Health-WIKI"
    assert "/w/Finance-WIKI" in argv and "/w/Home-WIKI" in argv
    # extra dirs appear after the primary --add-dir target
    assert argv.index("/w/Finance-WIKI") > i + 1


def test_build_argv_extra_add_dirs_none_unchanged():
    base = _build_argv(binary="c", model="m", wiki_path=Path("/w/A-WIKI"), prompt_path=Path("/p"),
                       allowed_tools=None, disallowed_tools=None, media_dirs=None)
    with_none = _build_argv(binary="c", model="m", wiki_path=Path("/w/A-WIKI"), prompt_path=Path("/p"),
                            allowed_tools=None, disallowed_tools=None, media_dirs=None, extra_add_dirs=None)
    assert base == with_none
```

- [ ] **Step 2: Run** — `uv run pytest tests/unit/wiki/test_runner.py -q -k extra_add_dirs` → FAIL (unexpected kwarg).

- [ ] **Step 3: Edit `runner.py`**

In `_build_argv` add a parameter `extra_add_dirs: list[Path] | None = None` and extend `extra_dirs`:

```python
def _build_argv(
    *,
    binary: str,
    model: str,
    wiki_path: Path,
    prompt_path: Path,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    media_dirs: list[Path] | None = None,
    extra_add_dirs: list[Path] | None = None,
) -> list[str]:
    extra_dirs = [str(d) for d in (extra_add_dirs or [])] + [str(d) for d in (media_dirs or [])]
    argv: list[str] = [
        binary, "-p", "--model", model,
        "--add-dir", str(wiki_path), *extra_dirs,
        *system_prompt_argv(prompt_path),
        "--setting-sources", "", "--disable-slash-commands", "--verbose",
        "--output-format", "stream-json", "--permission-mode", "dontAsk",
    ]
    if allowed_tools:
        argv.extend(["--allowedTools", *allowed_tools])
    if disallowed_tools:
        argv.extend(["--disallowedTools", *disallowed_tools])
    return argv
```

In `run_wiki_session` add `extra_add_dirs: list[Path] | None = None` to the signature (next to `media_paths`) and thread it into the `_build_argv(...)` call inside (find the existing `_build_argv(` invocation and add `extra_add_dirs=extra_add_dirs`).

Update the runner header: `VERSION` 0.0.8 → 0.0.9; `MODULE_MAP` `run_wiki_session` line append `; extra_add_dirs for read-only multi-WIKI --add-dir (aisw-oqq)`; `CHANGE_SUMMARY` new top entry `v0.0.9 - aisw-oqq: run_wiki_session/_build_argv accept extra_add_dirs (digest multi-WIKI --add-dir)`.

- [ ] **Step 4: Run** — `uv run pytest tests/unit/wiki/test_runner.py -q` → PASS. `make lint` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(M-WIKI-RUNNER): run_wiki_session extra_add_dirs for multi-WIKI digest --add-dir (aisw-oqq)"`

---

## Task 5: `scheduler/firing.py` — `set_digest_context` + `create_digest_job`

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py`
- Test: `tests/unit/scheduler/test_firing.py`

> Study the existing `create_reminder_job` / `fire_job` / `set_firing_context` in this file first — the digest functions mirror their structure (module-level context registry, picklable int callback, commit-before-add_job, log anchors).

- [ ] **Step 1: Add failing tests** (use the existing fake-scheduler fixtures in `test_firing.py`; the fake scheduler must record `add_job` calls and support `remove_job`)

```python
# append to tests/unit/scheduler/test_firing.py — adapt to the file's existing fixtures
import pytest
from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.firing import (
    DigestNotInitialisedError, create_digest_job, fire_digest_job, set_digest_context,
)
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import parse_job_payload, DigestPayload


@pytest.mark.asyncio
async def test_create_digest_job_writes_row_and_cron(jobs_session, fake_scheduler):  # reuse fixtures
    rec = Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")
    job_id = await create_digest_job(
        jobs_session, fake_scheduler, owner_telegram_id=7, chat_id=7,
        recurrence=rec, wiki_scope="all", window_hours=24, correlation_id="cid",
    )
    row = await jobs_session.get(Job, job_id)
    assert row.kind == "digest_job" and row.status == "scheduled"
    parsed = parse_job_payload(row.payload)
    assert isinstance(parsed, DigestPayload) and parsed.recurrence == rec
    call = fake_scheduler.added[-1]
    assert call.id == f"digest:{job_id}" and call.args == [job_id]
    assert call.replace_existing is True
    assert call.trigger_kwargs == {"hour": 9, "minute": 0}
    assert call.timezone == "Europe/Moscow"
```

(Adjust `fake_scheduler` to capture `trigger`, `timezone`, `id`, `args`, `replace_existing` — if the existing fake only captures DateTrigger calls, extend it. The real call is `scheduler.add_job(fire_digest_job, trigger=CronTrigger(**rec.to_cron(), timezone=rec.tz), args=[job_id], id=f"digest:{job_id}", replace_existing=True)`.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — append to `firing.py`:

```python
from apscheduler.triggers.cron import CronTrigger  # add to imports
from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.storage.jobs.payloads import DigestPayload  # add

# --- digest context registry (parallel to _ctx) ---
# fire_digest_job needs a runner adapter + sem + lock manager + owner-WIKI resolver +
# jobs sessionmaker + sender — all reached from here (picklable-int callback).
class DigestNotInitialisedError(RuntimeError):
    """Raised when fire_digest_job runs before set_digest_context was called."""


# Type of the runner adapter: an async callable that runs one Stage-1 session and
# returns the aggregated assistant text. Wired in __main__ over wiki.runner.run_wiki_session.
# signature: async def runner(*, wiki_id, wiki_path, extra_add_dirs, overlay_text, correlation_id) -> str
_digest_ctx: tuple[object, ...] | None = None  # (runner, sem, lock_mgr, resolve_wikis, jobs_maker, sender)


def set_digest_context(
    *, runner, semaphore, lock_manager, resolve_owner_wikis, jobs_session_maker, sender
) -> None:
    """Install the digest firing registry. Call once at startup (after scheduler/sender/runner)."""
    global _digest_ctx
    _digest_ctx = (runner, semaphore, lock_manager, resolve_owner_wikis, jobs_session_maker, sender)


async def create_digest_job(
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    *,
    owner_telegram_id: int,
    chat_id: int,
    recurrence: Recurrence,
    wiki_scope: str = "all",
    window_hours: int = 24,
    correlation_id: str = "",
) -> int:
    payload = DigestPayload(
        wiki_scope=wiki_scope, recurrence=recurrence, window_hours=window_hours
    ).model_dump(mode="json")
    job = Job(
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        kind="digest_job",
        status="scheduled",
        priority=int(Lane.DIGEST),
        scheduled_at_utc=None,
        payload=payload,
        created_at_utc=_now_naive_utc(),
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    await session.commit()

    scheduler.add_job(
        fire_digest_job,
        trigger=CronTrigger(**recurrence.to_cron(), timezone=recurrence.tz),
        args=[job_id],
        id=f"digest:{job_id}",
        replace_existing=True,
    )
    _log.info(
        "scheduler.digest.scheduled",
        correlation_id=correlation_id, job_id=job_id, owner_telegram_id=owner_telegram_id,
        recurrence=recurrence.model_dump(mode="json"),
    )
    return job_id
```

Update header: `VERSION` 0.1.0 → 0.2.0; `MODULE_MAP` add `set_digest_context`, `create_digest_job`, `fire_digest_job` (next task), `DigestNotInitialisedError`; `DEPENDS` add `M-WIKI-RUNNER`, `M-WIKI-LIFECYCLE`; `CHANGE_SUMMARY` new top entry; `LINKS` add D-019/D-024/D-025/ADR-007/aisw-oqq. (`fire_digest_job` is added in Task 6 — for now stub `async def fire_digest_job(job_id: int) -> None: raise NotImplementedError` so `add_job` references resolve, OR write Task 6 before committing Task 5; recommended: do Tasks 5+6 together and commit once.)

- [ ] **Step 4: Run** — Task-5 tests pass once Task 6 is in. Hold the commit until Task 6.

---

## Task 6: `scheduler/firing.py` — `fire_digest_job`

**Files:**
- Modify: `src/ai_steward_wiki/scheduler/firing.py`
- Test: `tests/unit/scheduler/test_firing.py`

- [ ] **Step 1: Add failing tests** (fake runner adapter returning canned text; fake sender; fake sem/lock as async context managers; in-process jobs DB)

```python
@pytest.mark.asyncio
async def test_fire_digest_job_runs_and_delivers(jobs_session_maker, fake_scheduler):
    sent: list[str] = []
    class FakeRunner:
        async def __call__(self, **kw): return "TL;DR: ничего особенного.\n📅 Сегодня: —"
    class FakeSender:
        async def send_message(self, chat_id, text, **kw): sent.append(text); return type("M", (), {"message_id": 1})()
        async def send_document(self, *a, **k): ...
    class _Null:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    async def resolve_wikis(owner_id):  # returns [(wiki_id, Path), ...]
        from pathlib import Path
        return [("health", Path("/w/u/Health-WIKI")), ("finance", Path("/w/u/Finance-WIKI"))]
    # seed a job
    async with jobs_session_maker() as s:
        rec = Recurrence(kind="daily", time_hhmm="09:00", tz="UTC")
        job_id = await create_digest_job(s, fake_scheduler, owner_telegram_id=7, chat_id=7, recurrence=rec)
    set_digest_context(runner=FakeRunner(), semaphore=_Null(), lock_manager=type("L",(),{"acquire":lambda self,*a,**k:_Null()})(),
                       resolve_owner_wikis=resolve_wikis, jobs_session_maker=jobs_session_maker, sender=FakeSender())
    await fire_digest_job(job_id)
    assert len(sent) == 1 and "TL;DR" in sent[0]
    async with jobs_session_maker() as s:
        row = await s.get(Job, job_id)
        assert row.status == "scheduled" and row.retry_count == 0 and row.finished_at_utc is not None


@pytest.mark.asyncio
async def test_fire_digest_job_no_context_raises():
    # reset module state if a fixture doesn't — set_digest_context never called
    with pytest.raises(DigestNotInitialisedError):
        await fire_digest_job(999)


@pytest.mark.asyncio
async def test_fire_digest_job_third_failure_disables(jobs_session_maker, fake_scheduler):
    class FailRunner:
        async def __call__(self, **kw):
            from ai_steward_wiki.wiki.runner import WikiRunnerError
            raise WikiRunnerError("boom")
    # ... seed job, set_digest_context with FailRunner, call fire_digest_job 3×
    # after 3rd: row.status == "disabled"; fake_scheduler.removed contains f"digest:{job_id}";
    # a jobs_dlq row exists for job_id.
```

(Also: empty-WIKI-set → `resolve_owner_wikis` returns `[]` → one `send_message` with the ru "no WIKI" line, `row.status` not disabled, no strike. Bad payload → seed a job with `payload={"kind": "digest", "bogus": 1}` → `fire_digest_job` → `row.status == "disabled"` + DLQ row.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement `fire_digest_job`**

```python
from pathlib import Path
from pydantic import ValidationError
from ai_steward_wiki.scheduler.dlq import move_to_dlq  # verify the name/signature in scheduler/dlq.py

_DIGEST_EMPTY_RU = "Сегодня дел нет 🌿"
_DIGEST_NO_WIKI_RU = "У тебя пока нет ни одной WIKI для сводки."
_MAX_DIGEST_STRIKES = 3


async def fire_digest_job(job_id: int) -> None:
    """APScheduler callback for a recurring digest. Picklable int arg only."""
    if _digest_ctx is None:
        raise DigestNotInitialisedError("digest context not initialised — call set_digest_context() at startup")
    runner, semaphore, lock_manager, resolve_owner_wikis, maker, sender = _digest_ctx
    async with maker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != "scheduled":
            _log.info("scheduler.digest.skipped", job_id=job_id, status=(job.status if job else "missing"))
            return
        try:
            payload = parse_job_payload(job.payload)
        except ValidationError:
            job.status = "disabled"
            job.last_error = "bad payload"
            await session.commit()
            await move_to_dlq(session, job_id, reason="bad_payload", error_class="ValidationError", last_error="bad payload")
            _log.warning("scheduler.digest.failed", job_id=job_id, error_class="ValidationError", disabled=True)
            return
        if not isinstance(payload, DigestPayload):
            job.status = "disabled"
            await session.commit()
            _log.warning("scheduler.digest.failed", job_id=job_id, error_class="WrongPayloadKind", disabled=True)
            return
        owner_id, chat_id = job.owner_telegram_id, job.chat_id
        job.started_at_utc = _now_naive_utc()
        await session.commit()
        _log.info("scheduler.digest.fired", job_id=job_id, owner_telegram_id=owner_id)

        wikis = list(await resolve_owner_wikis(owner_id))  # [(wiki_id, Path), ...], Inbox-WIKI already excluded
        if not wikis:
            await sender.send_message(chat_id, _DIGEST_NO_WIKI_RU)
            job.finished_at_utc = _now_naive_utc()
            await session.commit()
            _log.info("scheduler.digest.delivered", job_id=job_id, empty="no_wiki")
            return
        (primary_id, primary_path), *rest = wikis
        extra_dirs = [p for _, p in rest]
        # planner-semantics window context: keep MVP-minimal — a one-line note; richer query is aisw-w3k.
        planner_context = f"Окно сводки: ближайшие {payload.window_hours} ч."

        try:
            async with semaphore, lock_manager.acquire(primary_id):
                text = await runner(
                    wiki_id=primary_id, wiki_path=primary_path, extra_add_dirs=extra_dirs,
                    planner_context=planner_context, correlation_id=f"digest:{job_id}",
                )
        except Exception as exc:  # noqa: BLE001 — strike-and-maybe-disable, never propagate
            job.retry_count = (job.retry_count or 0) + 1
            job.finished_at_utc = _now_naive_utc()
            job.last_error = f"{type(exc).__name__}: {exc}"
            disabled = job.retry_count >= _MAX_DIGEST_STRIKES
            if disabled:
                job.status = "disabled"
            await session.commit()
            if disabled:
                from ai_steward_wiki.scheduler.firing import _remove_scheduler_job  # see note
                # remove the cron trigger so it stops firing
                # NOTE: fire_digest_job has no scheduler handle; store it in the registry OR
                # have set_digest_context also accept the scheduler. Recommended: add `scheduler`
                # to set_digest_context and capture it here.
                ...
                await move_to_dlq(session, job_id, reason="auto_disable", error_class=type(exc).__name__, last_error=job.last_error)
                _log.warning("scheduler.digest.disabled", job_id=job_id, error_class=type(exc).__name__, retry_count=job.retry_count)
            _log.warning("scheduler.digest.failed", job_id=job_id, error_class=type(exc).__name__, retry_count=job.retry_count, disabled=disabled)
            return

        body = text.strip() or _DIGEST_EMPTY_RU
        try:
            await sender.send_message(chat_id, body)  # MVP: plain send; D-025 ChainSplitter/send_document is aisw-w3k via deliver_output
        except Exception as exc:  # noqa: BLE001
            job.retry_count = (job.retry_count or 0) + 1
            job.finished_at_utc = _now_naive_utc()
            job.last_error = f"{type(exc).__name__}: {exc}"
            disabled = job.retry_count >= _MAX_DIGEST_STRIKES
            if disabled:
                job.status = "disabled"
            await session.commit()
            if disabled:
                await move_to_dlq(session, job_id, reason="auto_disable", error_class=type(exc).__name__, last_error=job.last_error)
                _log.warning("scheduler.digest.disabled", job_id=job_id, error_class=type(exc).__name__, retry_count=job.retry_count)
            _log.warning("scheduler.digest.failed", job_id=job_id, error_class=type(exc).__name__, retry_count=job.retry_count, disabled=disabled)
            return

        job.retry_count = 0
        job.finished_at_utc = _now_naive_utc()
        # status stays 'scheduled' — recurring
        await session.commit()
        _log.info("scheduler.digest.delivered", job_id=job_id)
```

> **Decision for the executor:** add a `scheduler` parameter to `set_digest_context(...)` (so `fire_digest_job` can `scheduler.remove_job(f"digest:{job_id}")` on auto-disable). Update Task 5's `set_digest_context` signature and the `_digest_ctx` tuple accordingly, and Task 7's `__main__` call. Then replace the `...`/NOTE block above with `scheduler.remove_job(f"digest:{job_id}")` inside a `with contextlib.suppress(JobLookupError):` (import `from apscheduler.jobstores.base import JobLookupError`).
>
> **Decision (delivery):** the design says deliver via `tg/output.deliver_output` (ChainSplitter/send_document + audit run_outputs row). For the MVP this plan uses a plain `sender.send_message` to avoid threading `runs_dir` + `audit_session_maker` into the digest registry; if you prefer fidelity to the design, instead store `runs_dir` + `audit_session_maker` in `set_digest_context` and call `deliver_output(..., kind="digest", job_id=job_id, run_id=..., tg_send=True)` — the runner adapter must then return both the text and a `run_id`. Either is acceptable; pick one and note it in the completion report. (Recommended: use `deliver_output` — it's the documented path and the audit `run_outputs` row matters.)

- [ ] **Step 4: Run** — `uv run pytest tests/unit/scheduler/test_firing.py -q` → PASS. `make lint` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(M-SCHEDULER-FIRING): create_digest_job + fire_digest_job — direct-fire recurring digest under Semaphore+per-WIKI lock, 3-strike auto-disable (aisw-oqq)"`

---

## Task 7: `tg/pipeline.py` — `_handle_digest_intent` + `_handle_digest_confirm`

**Files:**
- Modify: `src/ai_steward_wiki/tg/pipeline.py`
- Test: `tests/unit/tg/test_pipeline_digest.py` (new)

> Study `_handle_reminder_intent` / `_handle_reminder_confirm` / `build_reminder_recap` / the `on_confirm_callback` dispatch and the `REMINDER_*` constants in `pipeline.py` first — the digest versions mirror them. The recurring-digest keyword set (`«кажд»/«ежедневн»/«еженедельн»/«сводк»/«дайджест»`) is currently `_RECURRING_KEYWORDS` (private) used in `_handle_reminder_intent` to return `REMINDER_RECURRING_RU` — that branch is what you replace.

- [ ] **Step 1: Add ru constants + `RecurrenceParser` Protocol + `build_digest_recap`**

In `pipeline.py` add (near the `REMINDER_*` block):

```python
DIGEST_RECAP_RU = "Буду присылать сводку {schedule_human} по WIKI: {wikis}. Подтверждаешь?"
DIGEST_ACK_RU = "Готово — буду присылать сводку {schedule_human}."
DIGEST_UNPARSEABLE_RU = (
    "Не понял расписание сводки. Скажи, например: «каждый день в 9» или «по будням в 19:00»."
)
DIGEST_CONFIRM_CANCELLED_RU = "Хорошо, сводку настраивать не буду."
DIGEST_CONFIRM_STALE_RU = "Это подтверждение уже неактуально."


class RecurrenceParser(Protocol):
    def __call__(self, text: str, *, user_tz: str, correlation_id: str = "") -> "RecurrenceParseResult": ...


def _humanize_recurrence(rec: "Recurrence") -> str:
    if rec.kind == "daily":
        return f"каждый день в {rec.time_hhmm}"
    names_ru = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
    if tuple(sorted(rec.weekdays)) == (0, 1, 2, 3, 4):
        return f"по будням в {rec.time_hhmm}"
    days = ", ".join(names_ru[d] for d in sorted(set(rec.weekdays)))
    return f"по дням ({days}) в {rec.time_hhmm}"


def build_digest_recap(rec: "Recurrence", wiki_names: list[str]) -> str:
    wikis = ", ".join(wiki_names) if wiki_names else "все твои WIKI"
    return DIGEST_RECAP_RU.format(schedule_human=_humanize_recurrence(rec), wikis=wikis)
```

Add `DIGEST_*`, `RecurrenceParser`, `build_digest_recap` to `__all__` and `MODULE_MAP`. Import `Recurrence`, `RecurrenceParseResult` from `ai_steward_wiki.classifier.recurrence` (TYPE_CHECKING is fine for the Protocol return; runtime import needed where `parse_recurrence` result is used).

- [ ] **Step 2: Add a `recurrence_parser` kwarg to `DefaultPipeline.__init__`**

Find the reminder kwargs block (`time_parser`, `jobs_session_maker`, `scheduler`, `user_tz_lookup`, `default_user_tz`, `clock`) and add `recurrence_parser: RecurrenceParser | None = None` → `self._recurrence_parser = recurrence_parser`. Bump `DefaultPipeline`'s relevance in `VERSION` (module `VERSION` 0.7.0 → 0.8.0).

- [ ] **Step 3: Replace the recurring-digest stub in `_handle_reminder_intent`**

The current branch:
```python
if any(kw in lowered for kw in _RECURRING_KEYWORDS):
    # send REMINDER_RECURRING_RU
```
becomes a delegation:
```python
if any(kw in lowered for kw in _RECURRING_KEYWORDS):
    return await self._handle_digest_intent(text, telegram_id, chat_id, correlation_id, distilled_payload)
```

> **Important ordering note:** the reminder fast-path triggers on `intent is Intent.REMINDER`. A «каждый день в 9 сводка» message must classify as `REMINDER` for this to fire (it did in aisw-kcz — the recurring-keyword branch existed inside the reminder handler). Keep that. If Stage-0 classifies digest phrasings as a different intent, that's out of scope here — the keyword branch inside the reminder handler is the entry point per the design.

- [ ] **Step 4: Add `_handle_digest_intent`**

```python
async def _handle_digest_intent(self, text, telegram_id, chat_id, correlation_id, distilled_payload):
    if self._recurrence_parser is None or self._confirmation is None:
        # no parser wired (e.g. unit pipeline without digest deps) → fall back to the old "not yet" line
        await self._send(chat_id, REMINDER_RECURRING_RU)
        return
    user_tz = self._resolve_user_tz(telegram_id)
    res = self._recurrence_parser(text, user_tz=user_tz, correlation_id=correlation_id)
    if res.recurrence is None:
        _log.info("tg.pipeline.digest.unparseable", correlation_id=correlation_id, reason=res.reason)
        await self._send(chat_id, DIGEST_UNPARSEABLE_RU)
        return
    rec = res.recurrence
    _log.info("tg.pipeline.digest.detected", correlation_id=correlation_id, recurrence=rec.model_dump(mode="json"))
    # MVP: wiki_scope='all'; don't resolve names here (avoids a DB hit in the pipeline) — recap says "все твои WIKI".
    draft = PendingConfirmDraft(
        telegram_id=telegram_id, chat_id=chat_id, category="digest",
        draft={
            "recurrence": rec.model_dump(mode="json"),
            "wiki_scope": "all", "window_hours": 24,
            "user_tz": user_tz, "correlation_id": correlation_id,
        },
        recap_text=build_digest_recap(rec, []),
    )
    await self._confirmation.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)
    _log.info("tg.pipeline.digest.confirm_requested", correlation_id=correlation_id)
```

(Use the same `self._send` / `self._confirmation` / `self._resolve_user_tz` helpers the reminder path uses — match their exact names in the file.)

- [ ] **Step 5: Add `_handle_digest_confirm` + wire `on_confirm_callback`**

In `on_confirm_callback`, where it currently dispatches `category == "reminder"` → `_handle_reminder_confirm` and `category == "route_ingest"` → `_handle_route_confirm`, add `category == "digest"` → `_handle_digest_confirm`.

```python
async def _handle_digest_confirm(self, pending, action, telegram_id, chat_id):
    status = await self._confirmation.resolve(telegram_id, pending.id, action)
    _log.info("tg.pipeline.confirm.digest_dispatched", pending_id=pending.id, status=status)
    if status is None:
        await self._send(chat_id, DIGEST_CONFIRM_STALE_RU); return
    if status in {"cancelled", "corrected"}:
        await self._send(chat_id, DIGEST_CONFIRM_CANCELLED_RU)
        _log.info("tg.pipeline.digest.confirm_cancelled", pending_id=pending.id); return
    # confirmed
    draft = json.loads(pending.draft_json)  # match how _handle_reminder_confirm reads the draft
    rec = Recurrence(**draft["recurrence"])
    if self._jobs_session_maker is None or self._scheduler is None:
        await self._send(chat_id, ACK_RUNNER_ERR_RU)
        _log.warning("tg.pipeline.digest.confirm_misconfigured", pending_id=pending.id); return
    from ai_steward_wiki.scheduler.firing import create_digest_job
    async with self._jobs_session_maker() as s:
        await create_digest_job(
            s, self._scheduler, owner_telegram_id=telegram_id, chat_id=chat_id,
            recurrence=rec, wiki_scope=draft.get("wiki_scope", "all"),
            window_hours=int(draft.get("window_hours", 24)), correlation_id=draft.get("correlation_id", ""),
        )
    await self._send(chat_id, DIGEST_ACK_RU.format(schedule_human=_humanize_recurrence(rec)))
    _log.info("tg.pipeline.digest.confirm_created", pending_id=pending.id)
```

- [ ] **Step 6: Write `tests/unit/tg/test_pipeline_digest.py`** — mirror `test_pipeline_reminder.py`. Cases:
  1. recurring phrasing + a fake `recurrence_parser` returning a `Recurrence` → `request_explicit` called once with `category="digest"` and the right `draft`, no `create_digest_job`.
  2. parser returns `escalate` → `DIGEST_UNPARSEABLE_RU` sent, no `request_explicit`.
  3. `recurrence_parser=None` → falls back to `REMINDER_RECURRING_RU` (the old "not yet" line).
  4. confirm callback (`action="confirm"`) on a `category="digest"` pending row → `create_digest_job` called once + `DIGEST_ACK_RU`.
  5. cancel → `DIGEST_CONFIRM_CANCELLED_RU`, no job.
  6. `resolve`→`None` → `DIGEST_CONFIRM_STALE_RU`.
  7. non-digest category callback → existing generic path untouched.

- [ ] **Step 7: Run** — `uv run pytest tests/unit/tg/test_pipeline_digest.py tests/unit/tg/test_pipeline_reminder.py -q` → PASS. `make lint` → PASS.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat(M-TG-PIPELINE-CLASSIFIER): digest fast-path — _handle_digest_intent/_handle_digest_confirm + ru copy, replaces the recurring stub (aisw-oqq)"`

---

## Task 8: `prompts/digest.md` (+ `prompts/recurrence.md`)

**Files:** Create `prompts/digest.md`, `prompts/recurrence.md`.

- [ ] **Step 1: Write `prompts/digest.md`**

```markdown
semver: 0.0.1

Ты — персональный ассистент. Тебе дали read-доступ (`--add-dir`) к одной или нескольким папкам `<Имя>-WIKI/` пользователя и краткое описание окна сводки. Сформируй компактную сводку на русском для чтения с телефона:

1. Начни с TL;DR — 3–5 строк самого важного.
2. Затем сгруппируй по разделам, если есть содержимое: «📅 Сегодня», «💊 Лекарства», «📈 Трекеры», «📝 Обновления WIKI».
3. Каждый пункт — одна короткая строка. Без воды, без markdown-таблиц.
4. Можно использовать простой HTML (`<b>`, `<i>`). Не используй MarkdownV2.
5. Если делать нечего — ответь одной строкой: «Сегодня дел нет 🌿».

Не редактируй файлы. Только читай и суммируй.
```

- [ ] **Step 2: Write `prompts/recurrence.md`** (skeleton — not wired in this phase, but the file is referenced by `__main__` if present)

```markdown
semver: 0.0.1

Извлеки из сообщения расписание повторения. Ответь строго JSON:
{"kind":"daily"|"weekly","time_hhmm":"HH:MM","weekdays":[0..6]}
weekdays — только для weekly (0=пн … 6=вс). Если расписание не распознаётся — {"escalate":true}.
```

- [ ] **Step 3: Commit** — `git add prompts/digest.md prompts/recurrence.md && git commit -m "feat(M-WIKI-RUNNER): prompts/digest.md + prompts/recurrence.md for the digest job (aisw-oqq)"`

---

## Task 9: `__main__.py` wiring

**Files:** Modify `src/ai_steward_wiki/__main__.py`.

> Study how `_TimeParserAdapter`, `firing.set_firing_context(...)`, `_user_tz_lookup`, and the `DefaultPipeline(...)` construction are wired for aisw-kcz — the digest wiring is the parallel of that.

- [ ] **Step 1: Add `_RecurrenceParserAdapter`** — a small class wrapping `classifier.recurrence.parse_recurrence`, binding the Stage-0 backend + `prompts/recurrence.md` content (if the file exists, else `None`). `__call__(self, text, *, user_tz, correlation_id="") -> RecurrenceParseResult` → `return parse_recurrence(text, user_tz=user_tz, haiku_backend=self._backend if self._prompt else None, correlation_id=correlation_id)`. (Since the Haiku path is a stub, this can be even simpler — just call `parse_recurrence(text, user_tz=user_tz, correlation_id=correlation_id)`.)

- [ ] **Step 2: Add `_resolve_owner_wikis(owner_telegram_id) -> list[tuple[str, Path]]`** — use `wiki/lifecycle.py`'s WIKI listing (the method that returns the owner's `WikiName` list — `WikiLifecycleManager.list_wikis` or equivalent; check the actual name at `lifecycle.py:129`) + the workspace-root resolver (`lifecycle.py:288`'s method, or the existing helper that builds `<wiki_root>/<owner>/<Name>-WIKI`), filter out `Inbox-WIKI`, return `[(normalized_id, path), ...]`. If no helper composes name→path, build it from `settings.wiki_root` + owner + `f"{name}-WIKI"`.

- [ ] **Step 3: Build the digest runner adapter** — an async callable `async def _digest_runner(*, wiki_id, wiki_path, extra_add_dirs, planner_context, correlation_id) -> str` (or `-> tuple[str, str]` text+run_id if you went with `deliver_output`): assemble the overlay = `prompts/digest.md` content + `\n\n` + `planner_context` (write to a temp file via the same mechanism `run_wiki_session`/`assemble_prompt` expects, or pass `prompts/digest.md` as `overlay_prompt_path` and prepend the planner context into `user_input`), then `result = await run_wiki_session(wiki_id=wiki_id, wiki_path=wiki_path, base_prompt_path=<prompts/wiki.md>, overlay_prompt_path=<prompts/digest.md>, run_id=<new uuid>, correlation_id=correlation_id, runtime_dir=<...>, acquirer=<the LockAcquirer>, spawner=<AsyncioSpawner>, config=_RunConfig(claude_config_dir=settings.claude_config_dir, model=..., timeout_s=600.0), extra_add_dirs=list(extra_add_dirs), user_input=planner_context)` then `return aggregate_text(result.events)`. **Note:** `run_wiki_session` already acquires the per-WIKI lock + semaphore via its `acquirer` — so passing the same `WikiLockManager`-backed acquirer means the `async with semaphore, lock_manager.acquire(...)` in `fire_digest_job` would double-lock. **Resolve:** either (a) have `fire_digest_job` NOT take the sem/lock itself and rely on `run_wiki_session`'s acquirer (simplest — drop sem/lock from `set_digest_context`), or (b) pass a no-op acquirer to `run_wiki_session` and lock in `fire_digest_job`. **Recommended: (a)** — `set_digest_context(runner=..., resolve_owner_wikis=..., jobs_session_maker=..., sender=..., scheduler=...)`, and `fire_digest_job` just calls `await runner(...)` without its own sem/lock. Update Tasks 5/6 accordingly.

- [ ] **Step 4: Call `firing.set_digest_context(...)`** after `sender = AiogramSender(bot)` and after the runner adapter / scheduler exist (alongside the existing `firing.set_firing_context(...)` call).

- [ ] **Step 5: Pass `recurrence_parser=_RecurrenceParserAdapter(...)` into `DefaultPipeline(...)`** (alongside the existing `time_parser=`, `jobs_session_maker=`, `scheduler=` kwargs).

- [ ] **Step 6: Update the `__main__` header** — `VERSION` 0.4.0 → 0.5.0; `CHANGE_SUMMARY` new top entry describing the digest wiring; `LINKS` add `M-CLASSIFIER-RECURRENCE`; `DEPENDS` already updated in the KG.

- [ ] **Step 7: Smoke-check** — `python -c "import ai_steward_wiki.__main__"` succeeds; `make lint` → PASS.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat(M-RUNTIME-WIRING): wire the digest job — _RecurrenceParserAdapter, _resolve_owner_wikis, _digest_runner, firing.set_digest_context (aisw-oqq)"`

---

## Task 10: end-to-end test

**Files:** Create `tests/unit/tg/test_digest_e2e.py`.

> Mirror `tests/unit/tg/test_reminder_e2e.py`.

- [ ] **Step 1: Write the e2e test** — over an alembic-migrated sessions DB + a real jobs DB (via the existing test fixtures) + a fake scheduler (records `add_job`) + a fake digest runner adapter (returns canned `"TL;DR: всё спокойно.\n📅 Сегодня: —"`) + a fake sender + a fake `resolve_owner_wikis` returning two `(id, Path)` pairs:
  1. construct `DefaultPipeline` with a real `ConfirmationService` over the sessions DB + a fake `recurrence_parser` (returns `Recurrence(kind="daily", time_hhmm="09:00", tz="UTC")`) + `jobs_session_maker` + the fake `scheduler`.
  2. `await pipeline.on_text(...)` with «каждый день в 9 сводка» → assert a `pending_confirms` row with `category="digest"` exists.
  3. `await pipeline.on_confirm_callback(...)` simulating the `confirm:<pid>:confirm` callback → assert a `jobs.jobs` row `kind="digest_job"`, `status="scheduled"` + `scheduler.add_job` called once with a `CronTrigger` (`{"hour":9,"minute":0}`, `timezone="UTC"`), `id=f"digest:{job_id}"` + a `DIGEST_ACK_RU` send.
  4. `set_digest_context(runner=fake_runner, resolve_owner_wikis=fake_resolve, jobs_session_maker=..., sender=fake_sender, scheduler=fake_scheduler)` then `await fire_digest_job(job_id)` → assert one `send_message` containing `"TL;DR"`; reload the `Job` row → `status="scheduled"`, `retry_count==0`, `finished_at_utc` set.

- [ ] **Step 2: Run** — `uv run pytest tests/unit/tg/test_digest_e2e.py -q` → PASS.

- [ ] **Step 3: Commit** — `git add tests/unit/tg/test_digest_e2e.py && git commit -m "test(M-SCHEDULER-FIRING): in-process e2e for the digest job — on_text → confirm → jobs row + cron → fire → deliver (aisw-oqq)"`

---

## Task 11: final verification gates

- [ ] **Step 1:** `make lint` → ruff check ✅, ruff format --check ✅, mypy src ✅.
- [ ] **Step 2:** `grace lint --failOn errors` → 0 issues. (If `M-CLASSIFIER-RECURRENCE` lint complains about the new `recurrence.py` header — fix the header to match the GRACE markup reference.)
- [ ] **Step 3:** `make inv-lint` → all invariant checks pass.
- [ ] **Step 4:** `uv run pytest tests/unit` → all pass (≈ 543 + new ≈ 30).
- [ ] **Step 5:** If anything is red — fix root cause, no `--no-verify`.

---

## Self-review notes

- **Spec coverage:** FR-1 → Tasks 1–2; FR-2 → Task 3; FR-3/FR-4 → Tasks 5–6; FR-5 → Task 4; FR-6 → Task 7; FR-7 → Task 9; NFR-1 (log anchors) → embedded in Tasks 2/5/6/7; NFR-2 (concurrency) → Task 9 Step 3 resolves the double-lock by relying on `run_wiki_session`'s acquirer; NFR-3 (failure isolation) → `fire_digest_job`'s broad `except` + strike logic in Task 6; NFR-4 (TZ) → `Recurrence.tz` from `_resolve_user_tz`; NFR-5 (ordering) → commit-before-add_job in Task 5; NFR-6 (idempotency) → `replace_existing=True` + `ConfirmationService.resolve` race-safety. `prompts/digest.md` → Task 8. GRACE artifacts → done in commit e0c28ab; ADR-007 → Finish step.
- **Open executor decisions flagged inline** (pick one, note in the completion report): (1) `set_digest_context` should carry the `scheduler` handle so `fire_digest_job` can `remove_job` on auto-disable; (2) drop the sem/lock from `set_digest_context`/`fire_digest_job` and rely on `run_wiki_session`'s acquirer (avoids double-locking) — **recommended**; (3) delivery via `deliver_output` (documented, writes the audit `run_outputs` row) vs plain `sender.send_message` (simpler) — **recommended: `deliver_output`**, which means the runner adapter returns `(text, run_id)` and `set_digest_context` also carries `runs_dir` + `audit_session_maker`.
- **Types are consistent:** `Recurrence` (Task 1) used in Tasks 2/3/5/6/7/9/10; `RecurrenceParseResult` in 2/7/9; `DigestPayload` in 3/5/6; `fire_digest_job`/`create_digest_job`/`set_digest_context`/`DigestNotInitialisedError` in 5/6/7/9/10.
