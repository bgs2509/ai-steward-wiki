---
feature: classifier-envelope-fix
bd_id: aisw-p5b
date: 2026-05-12
status: stable
module_ids: [M-CLASSIFIER-STAGE0]
functional_requirements:
  - id: FR-1
    text: Stage-0 backend MUST return the inner classifier JSON object ({intent, confidence, distilled_payload}), not the Claude CLI envelope.
  - id: FR-2
    text: The classifier system prompt MUST actually be loaded into the CLI invocation (current literal '@/path' string is not expanded).
  - id: FR-3
    text: Backend MUST tolerate models that wrap JSON in ```json code fences (defensive parsing, even though prompt forbids them).
  - id: FR-4
    text: When CLI returns non-JSON or wrong-schema text in payload['result'], raise ClassifierSchemaError with the offending head of text (truncated) — not a 20-line pydantic dump.
non_functional_requirements:
  - id: NFR-1
    text: Unit test pins the real envelope shape captured from production logs to prevent regression.
  - id: NFR-2
    text: No change to ClassifierResult schema; no change to stage0.classify orchestrator beyond what's required for the unwrap.
risks:
  - id: R-1
    text: --append-system-prompt-file flag name/availability varies by Claude CLI version. Mitigation -- verify via `claude --help`; confirmed exists (--bare help string lists '--append-system-prompt[-file]').
  - id: R-2
    text: --bare mode might change other behavior. Mitigation -- evaluate but do not require for this fix; minimum change is switch '@path' to '-file <path>' variant.
scope_in:
  - src/ai_steward_wiki/classifier/backend.py (ClaudeCliBackend.call + _argv)
  - tests/unit/classifier/ (new envelope-shape regression test)
scope_out:
  - Stage-1a/1b backends, prompt template changes, schema changes, AnthropicApiBackend wiring.
---

# Discovery — Classifier envelope + prompt loading fix

## Symptom

Every Telegram message produces `ClassifierSchemaError: 20 validation errors for ClassifierResult` in `tg.pipeline.classify.error`. Three traces in the user's session (Привет / who-are-you / what-can-you-do) all fail identically.

## Root causes (verified)

1. **Envelope not unwrapped.** `backend.py:154` `json.loads(stdout)` returns the CLI envelope:
   ```json
   {"type":"result","subtype":"success","result":"...","session_id":"...","usage":{...},...}
   ```
   The orchestrator `stage0.classify` merges meta and feeds this to `ClassifierResult.model_validate` — which uses `extra="forbid"` and demands `{intent, confidence, distilled_payload}`. Hence 3 missing + 17 extra_forbidden = 20 errors.

2. **Classifier prompt never loaded.** `_argv` passes `--append-system-prompt @{prompt_path}` (literal "@/abs/path"). Claude CLI's `--append-system-prompt` takes the *prompt text*, not a `@file` reference. So the system prompt is "@<path>" — junk to the model, which then ignores it and answers as the default Claude Code assistant ("Привет! 👋 Я могу помочь..."). The `result` field of every envelope contains free-form Russian prose, not JSON.

`claude --help` confirms `--append-system-prompt-file` variant exists (listed in `--bare` help string).

## Fix shape (full design in Brainstorming)

1. In `_argv`: use `--append-system-prompt-file` with the absolute path (no `@`), OR pre-read the file in Python and pass its text via `--append-system-prompt`. File-flag preferred (no UTF-8 / shell-escaping risk on long prompts).
2. In `call`: after `json.loads(stdout)`, extract `data["result"]` (str); strip optional ```json ... ``` fences; `json.loads` the inner JSON; return that dict (still typed `dict[str, Any]` per Protocol).
3. Surface useful diagnostics: when inner parsing fails, raise `ClassifierSchemaError` with first 256 chars of `result` plus envelope `subtype`/`stop_reason` for context.

## Verification

1. New unit test feeds a real captured envelope (from logs) through a fake spawner and asserts `ClassifierError` with the expected message head, **then** swaps the envelope's `result` for a valid classifier JSON and asserts a clean `dict` is returned to the orchestrator.
2. Existing `FakeClaudeRunner`-based tests stay green (they bypass the wire format entirely).
3. Smoke test: run the bot, send "напомни мне завтра", confirm `tg.pipeline.classify.begin` → no `classify.error` → `classify.done` with `intent="reminder"`.

## Open questions

None — both root causes verified against source + CLI help in this session.
