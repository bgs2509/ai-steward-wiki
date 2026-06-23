# Implementation Plan: escape outbound HTML (aisw-azu)

> SSoT for execution. TDD: RED → GREEN → REFACTOR. Module: M-TG-OUTPUT.

## T1 — sanitize_html() — `tg/output.py`
- RED: `tests/unit/tg/test_output_sanitize.py`:
  - `"норма <120/80, пульс"` → `<` escaped to `&lt;`, no raw `<` before a digit.
  - `"<b>жирный</b>"` → preserved verbatim (allowed tag kept).
  - `"<div>x</div>"` → escaped (`&lt;div&gt;`, not in whitelist).
  - `"a < b & c > d"` → `a &lt; b &amp; c &gt; d`.
  - `'<a href="u?a=1&b=2">ok</a>'` → tag kept, bare `&` → `&amp;`, existing `&amp;` not double-escaped.
  - idempotence on already-valid `"<b>x</b>"` (allowed tags unchanged).
- GREEN: add `_escape_text`, `_ALLOWED_TAG_RE` (built from ALLOWED_TAGS), `_BARE_AMP_RE`,
  `sanitize_html(text)`; export in `__all__`.

## T2 — apply in deliver_output — `tg/output.py`
- RED: extend `test_output.py` — a `deliver_output` call with text `"АД <120/80"` makes
  `FakeSender` receive text with `&lt;` (no raw `<…>` that isn't a whitelist tag).
- GREEN: `text = sanitize_html(text)` at top of `deliver_output` (before `_persist_to_disk`),
  so inline/chain/summary all send valid HTML.

## T3 — parse_mode=None fallback — `tg/bot.py` + `logging_events.py`
- RED: `test_bot_anchors.py` — fake bot raising `TelegramBadRequest("...can't parse entities...")`
  on first send, succeeding on second → assert `send_message` returns, second call used
  `parse_mode=None`, and `tg.io.send_message.parse_fallback` WARNING logged. A `TelegramBadRequest`
  WITHOUT "can't parse entities" → re-raised (no retry).
- GREEN: add `IO_SEND_PARSE_FALLBACK` to logging_events; wrap the `_bot.send_message` call in
  `send_message` with the targeted except + single retry (inside the existing `anchored` block).

## T4 — Full gate
- `make lint` (ruff+format+mypy), `make grace-lint`, `make inv-lint`.
- `uv run pytest tests/unit` green; new tests pass; `test_output.py` regressions pass.

## Self-review vs FR/NFR
- FR-1/2 → T1+T2 (sanitize). FR-3 → T1 (_BARE_AMP_RE). FR-4 → T2 (top of deliver_output).
- FR-5 → T3 (fallback). FR-6 → T1/T2/T3 tests. NFR-1/2 → T1 idempotence + whitelist-preserve tests.
- NFR-3 → T4. NFR-4 → T3 (parse_fallback warning event).
- Order: T1 → T2 → T3 → T4 (T2 depends on T1; T3 independent of T1/T2).
