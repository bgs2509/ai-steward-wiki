# step-05-plan.md — Chunk 5 / M-CLASSIFIER-STAGE0

**bd_id:** aisw-5
**Module:** M-CLASSIFIER-STAGE0
**Window estimate:** 0.50

## Goal
Implement Stage-0 classifier (Haiku CLI default + optional API backend) and the two-stage NL time parser per discovery+design 2026-05-10.

## Steps

1. **Schema (TDD)** — write `tests/unit/classifier/test_schema.py` (RED), then `src/ai_steward_wiki/classifier/schema.py`:
   - `Intent` Enum (closed list).
   - `ClassifierResult`, `TimeParseResult` Pydantic v2 frozen, extra="forbid".
   - Errors: `ClassifierError`, `ClassifierTimeoutError`, `ClassifierSchemaError`.

2. **Backend Protocol + Fake** — `tests/unit/classifier/test_fake_runner.py` (RED), then `src/ai_steward_wiki/classifier/backend.py`:
   - `ClassifierBackend` Protocol with `async call(text, prompt_path, correlation_id)`.
   - `FakeClaudeRunner` — deterministic responses queue / callable.
   - `ClaudeCliBackend` skeleton (subprocess command builder + JSON parse + timeout). Uses `Spawner` Protocol seam for chunk 16.
   - `AnthropicApiBackend` skeleton (raises `ConfigError` if credential missing — INV-6 enforced upstream in Settings).

3. **Settings extension** — `tests/unit/test_settings.py` add INV-6 model_validator cases. Extend `Settings` with `stage0_backend`, `stage0_api_credential_path`, `classifier_stage0_timeout_s`, `classifier_haiku_fallback_timeout_s`, `prompts_dir`.

4. **Stage-0 orchestrator** — `tests/unit/classifier/test_stage0.py` (RED), then `src/ai_steward_wiki/classifier/stage0.py`:
   - `_PromptCache` reads `prompts/classifier.md` once, stores `(text, semver, sha256)`. Frontmatter parser: `semver: X.Y.Z` line.
   - `classify(text, *, correlation_id, backend, audit_session=None) -> ClassifierResult`.
   - Idempotent upsert into `audit.prompt_versions` (skip-if-exists by name+semver+sha256).
   - structlog event `classifier.stage0.call` with backend, model, prompt_semver, prompt_sha8, latency_ms, intent, confidence.

5. **NL time parser** — `tests/unit/classifier/test_time_parse.py` (RED), then `src/ai_steward_wiki/classifier/time_parse.py`:
   - `parse_time(text, *, user_tz, now_utc, haiku_backend) -> TimeParseResult`.
   - dateparser → Haiku-fallback → escalate. UTC invariant. structlog event.

6. **Prompt files** — `prompts/classifier.md` and `prompts/time-parse.md`, each with `semver: 1.0.0` frontmatter and minimal RU+EN body.

7. **Barrel** — `src/ai_steward_wiki/classifier/__init__.py` with MODULE_CONTRACT + MODULE_MAP per project convention.

8. **Quality gate** — `make lint` (ruff/format/mypy/grace) + `pytest tests/unit/classifier`. Coverage ≥ 80% for `classifier/`.

9. **Commit** — `feat(M-CLASSIFIER-STAGE0): stage-0 classifier + NL time parser` with `bd_id: aisw-5` trailer.

## Verification commands

```bash
uv run pytest tests/unit/classifier -q
uv run ruff check src/ai_steward_wiki/classifier tests/unit/classifier
uv run ruff format --check src/ai_steward_wiki/classifier tests/unit/classifier
uv run mypy src/ai_steward_wiki/classifier
make lint
```

## Out of scope (this chunk)
- Real CLI integration test wiring (skeleton only; nightly only).
- prompt_versions GC (indefinite per §10.4).
- systemd-run wrapping (chunk 16 hand-off via `Spawner` Protocol).
