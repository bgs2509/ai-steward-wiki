---
title: Reminder fast-path fix — Implementation Plan
bd_id: aisw-5q5
status: approved
date: 2026-05-13
references:
  discovery: docs/superpowers/specs/20260513-reminder-fastpath-fix-discovery.md
  design: docs/superpowers/specs/20260513-reminder-fastpath-fix-design.md
ordering:
  - phase-1: aisw-4dr (RC-3, hotfix)
  - phase-2: aisw-2mg (RC-2, distill time_expr)
  - phase-3: aisw-ct9 (RC-1, NOW_ISO/USER_TZ header)
---

# Implementation plan — Reminder fast-path

## Phase 1 — aisw-4dr (hotfix RC-3)

**Goal:** Guard `_handle_reminder_intent` so no parser exception ever leaves the handler without a user-facing reply.

### Step 1.1 — RED tests

Add to `tests/unit/test_pipeline_reminder.py` (create if missing, else extend):

1. `test_reminder_intent_classifier_schema_error_emits_unparseable_ru` — stub TimeParser whose `parse_time` raises `ClassifierSchemaError("inner JSON parse failed: 'prose'")`. Assert: sender received exactly one message = `REMINDER_UNPARSEABLE_RU`; no exception leaves `_handle_reminder_intent`; log captured for `tg.pipeline.reminder.parser_failed` with `error_class="ClassifierSchemaError"`.
2. `test_reminder_intent_timeout_error_emits_unparseable_ru` — same shape, `parse_time` raises `ClassifierTimeoutError`.
3. `test_reminder_intent_happy_path_unchanged` — `parse_time` returns valid `TimeParseResult` → confirm_requested logged as before, no `parser_failed` anchor.

Run: `uv run pytest tests/unit/test_pipeline_reminder.py -x` → expect 2 fail (NameError on new anchor or no message) + 1 pass.

### Step 1.2 — GREEN

In `src/ai_steward_wiki/tg/pipeline.py` `_handle_reminder_intent` (around line 1169), replace:

```python
tp = await self._time_parser.parse_time(
    text,
    user_tz=user_tz,
    now_utc=now_utc,
    prefer_future=True,
    correlation_id=correlation_id,
)
```

with:

```python
try:
    tp = await self._time_parser.parse_time(
        text,
        user_tz=user_tz,
        now_utc=now_utc,
        prefer_future=True,
        correlation_id=correlation_id,
    )
except Exception as exc:  # parser is best-effort; never crash the handler
    _log.warning(
        "tg.pipeline.reminder.parser_failed",
        correlation_id=correlation_id,
        telegram_id=telegram_id,
        error_class=type(exc).__name__,
        error_msg=str(exc)[:200],
    )
    await self._sender.send_message(chat_id, REMINDER_UNPARSEABLE_RU)
    return
```

Run: `uv run pytest tests/unit/test_pipeline_reminder.py -x` → expect 3 pass.

### Step 1.3 — REFACTOR + lint

- `make lint` must remain green.
- Commit: `fix(M-TG-PIPELINE): guard reminder parser; user-facing fallback on exception (aisw-4dr)`

---

## Phase 2 — aisw-2mg (RC-2 distill `time_expr`)

**Goal:** Stage-0 emits `distilled_payload.time_expr`; reminder fast-path uses it; dateparser succeeds on clean expression; Haiku-fallback not invoked on happy path.

### Step 2.1 — Update `prompts/classifier.md`

Bump `semver: 1.0.0 → 1.1.0`. After the "Intent semantics" section add:

```markdown
## Per-intent distilled_payload contract

For `intent="reminder"`, `distilled_payload` MUST include:

- `time_expr` (string) — the natural-language time fragment verbatim, e.g.
  `"через 5 минут"`, `"в 18:00 завтра"`, `"в субботу в 9"`. NEVER include action
  words ("напомни", "пойти", etc.). NEVER resolve to ISO — that is Stage-1's job.
- `reminder_text` (string) — the action without the time, e.g. `"пойти гулять"`,
  `"позвонить маме"`. May be empty if the entire message is just a time hint.
```

Update `prompt_semver` source-of-truth — `classifier/stage0.py` reads it via parser; no code change needed if it just echoes whatever the file says, but verify.

### Step 2.2 — RED tests

In `tests/unit/test_classifier_stage0.py`:
1. `test_reminder_distilled_payload_contains_time_expr` — FakeClaudeRunner returns canned JSON `{"intent":"reminder","confidence":0.95,"distilled_payload":{"time_expr":"через 5 минут","reminder_text":"пойти гулять"}}` for input «напомни мне пойти гулять через 5 минут». Assert `ClassifierResult.distilled_payload["time_expr"] == "через 5 минут"`.

In `tests/unit/test_pipeline_reminder.py`:
2. `test_reminder_intent_passes_distilled_time_expr_to_parser` — assert `parse_time` was called with positional `text="через 5 минут"`, not the raw sentence, when `distilled_payload["time_expr"]` is present.
3. `test_reminder_intent_falls_back_to_raw_text_when_time_expr_missing` — `distilled_payload={}`; assert `parse_time` called with raw sentence (NFR-2 backward compat).

In `tests/unit/test_time_parse.py`:
4. `test_dateparser_resolves_через_5_минут` — call `parse_time(text="через 5 минут", …)` with `RELATIVE_BASE=2026-05-13T12:00:00 UTC`, `user_tz=Europe/Moscow`, `prefer_future=True`. Assert `source="dateparser"`, `when_utc ≈ 2026-05-13T12:05:00 UTC` (±5s).

Run → expect 4 fail.

### Step 2.3 — GREEN

In `src/ai_steward_wiki/tg/pipeline.py` `_handle_reminder_intent`, before the `parse_time` call:

```python
time_expr_raw = distilled_payload.get("time_expr")
time_expr = (
    time_expr_raw.strip()
    if isinstance(time_expr_raw, str) and time_expr_raw.strip()
    else text
)
_log.info(
    "tg.pipeline.reminder.distill_used",
    correlation_id=correlation_id,
    telegram_id=telegram_id,
    time_expr_present=time_expr is not text,
)
```

Change `parse_time(text, …)` → `parse_time(time_expr, …)`. Keep `raw_reminder_text = distilled_payload.get("reminder_text")` block as-is (already there).

Run tests → 4 pass.

### Step 2.4 — REFACTOR + lint

- `make lint` green.
- Stage-0 unit-test matrix still passes (regression check on other intents).
- Commit: `fix(M-CLASSIFIER-STAGE0,M-TG-PIPELINE): distil time_expr; reminder parser uses clean expression (aisw-2mg)`

---

## Phase 3 — aisw-ct9 (RC-1 inject NOW_ISO / USER_TZ into Haiku stdin)

**Goal:** Haiku-fallback receives now-reference and user TZ; returns valid JSON. No `ClassifierSchemaError` on common reminder expressions when fallback is invoked.

### Step 3.1 — Update `prompts/time-parse.md`

Bump `semver: 1.0.0 → 1.1.0`. Replace the «You receive…» section with:

```markdown
## Input format

Input on stdin is two blocks separated by a line containing only `---`:

1. **Header** — key/value lines:
   - `NOW_ISO: <UTC ISO 8601>` — current instant in UTC (e.g. `2026-05-13T12:25:30+00:00`).
   - `USER_TZ: <IANA timezone>` — user's timezone (e.g. `Europe/Moscow`).
2. **User message** — the natural-language time expression (already distilled by
   Stage-0, e.g. `"через 5 минут"`, `"в субботу в 9"`).

Resolve the expression relative to NOW_ISO interpreted in USER_TZ.
```

Replace the «Rules» section with:

```markdown
## Rules

1. Output a single JSON object. No prose. No code fences. No clarifying
   questions. If the time expression is genuinely ambiguous given the context,
   set `"ambiguous": true` and omit `when_iso`.
2. `when_iso` MUST be in user's timezone (e.g. `"2026-05-13T15:30:00+03:00"`).
3. Russian and English inputs are equally supported.
4. Never invent a date not implied by the input.
```

### Step 3.2 — RED tests

In `tests/unit/test_time_parse.py`:
1. `test_haiku_fallback_prepends_now_iso_and_user_tz_header` — fake `ClassifierBackend.call` captures the `text` arg. Trigger fallback by feeding an expression dateparser cannot resolve (e.g. `"через две черепахи"`). Assert captured text starts with `"NOW_ISO: 2026-05-13T12:00:00+00:00\nUSER_TZ: Europe/Moscow\n---\n"`.
2. `test_haiku_fallback_passes_raw_expression_after_separator` — same, assert text ends with `"---\nчерез две черепахи"`.

Run → expect 2 fail.

### Step 3.3 — GREEN

In `src/ai_steward_wiki/classifier/time_parse.py`, replace the `haiku_backend.call(...)` call (line ~111):

```python
payload = (
    f"NOW_ISO: {now_utc.astimezone(UTC).isoformat()}\n"
    f"USER_TZ: {user_tz}\n"
    f"---\n"
    f"{text}"
)
started_h = time.monotonic()
raw = await haiku_backend.call(
    text=payload,
    prompt_path=haiku_prompt_path,
    correlation_id=correlation_id,
)
```

Run tests → 2 pass.

### Step 3.4 — Integration test (optional, gated)

In `tests/integration/test_time_parse_haiku.py` (create if missing) — gated on `RUN_INTEGRATION=1` env var:

```python
@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="real Haiku")
async def test_haiku_fallback_resolves_через_5_минут_with_real_cli():
    # real ClaudeCliBackend, real prompts/time-parse.md
    result = await parse_time(
        "через 5 минут",
        user_tz=ZoneInfo("Europe/Moscow"),
        now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        prefer_future=True,
        haiku_backend=real_backend,
        haiku_prompt_path=Path("prompts/time-parse.md"),
    )
    assert result.escalate is False
    assert result.source in ("dateparser", "haiku_fallback")
    assert result.when_utc is not None
```

Not run in CI; documented in plan + runbook.

### Step 3.5 — REFACTOR + lint

- `make lint` green.
- Commit: `fix(M-CLASSIFIER-TIME,prompts): inject NOW_ISO/USER_TZ into Haiku-fallback; semver 1.1.0 (aisw-ct9)`

---

## End-to-end verification (post Phase 3)

1. `uv run pytest tests/unit -x` — all green
2. `make lint` — all green
3. Restart bot with stderr→log, send «напомни мне пойти гулять через 5 минут» from TG, expect:
   - log: `classifier.time.parse source="dateparser" escalate=false`
   - log: `tg.pipeline.reminder.confirm_requested`
   - TG: recap card with confirm/cancel buttons.
4. Send «через две черепахи» (deliberately ambiguous) — expect:
   - log: `classifier.time.parse source="escalate" escalate=true`
   - TG: `REMINDER_UNPARSEABLE_RU`.
5. Send «напомни в 3:00» (relative-future heuristics) — expect dateparser success.

## Self-review checklist

- [x] Every MODULE_CONTRACT covered: M-TG-PIPELINE, M-CLASSIFIER-STAGE0, M-CLASSIFIER-TIME
- [x] FR-1 (no silent failures) → Phase 1 try/except
- [x] FR-2 (clean dateparser) → Phase 2 distill
- [x] FR-3 (Haiku gets context) → Phase 3 header
- [x] NFR-1 (latency) → no new sub-calls; Phase 2 actually *removes* Haiku call from happy path
- [x] NFR-2 (backward-compat) → Phase 2 falls back to raw text
- [x] NFR-3 (log anchor) → Phase 1 `tg.pipeline.reminder.parser_failed`
- [x] NFR-4 (test coverage) → 9 unit tests + 1 optional integration
- [x] No placeholders in plan; every code block is final
- [x] Task order respects DEPENDS (Phase 1 → 2 → 3 enforced via bd dep)
