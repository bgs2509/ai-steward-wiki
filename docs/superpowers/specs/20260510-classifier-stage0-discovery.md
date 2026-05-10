---
feature: classifier-stage0-and-nl-time
bd_id: aisw-5
epic: aisw-fm0
chunk: 5
module_id: M-CLASSIFIER-STAGE0
status: stable
date: 2026-05-10
fr:
  - FR-1: Stage-0 classifier — invoke `claude-haiku-4-5` headless CLI with `--output-format json`, `--json-schema <classifier-schema>`, `--max-turns 1`, `--disallowedTools Bash Read Write Edit Glob Grep WebFetch`, `--permission-mode dontAsk`
  - FR-2: Backend abstraction — `ClassifierBackend` Protocol with two implementations: `ClaudeCliBackend` (default, subscription auth) and `AnthropicApiBackend` (optional, gated by env `STAGE0_BACKEND=anthropic_api` + separate credential)
  - FR-3: Backend-independent prompt — `prompts/classifier.md` consumed by CLI via `--append-system-prompt @file` and by API via system instructions
  - FR-4: Output schema — `ClassifierResult{intent, confidence: float ∈ [0,1], distilled_payload: dict}` validated via Pydantic v2; intent enum drawn from spec §3 job-kinds taxonomy
  - FR-5: `FakeClaudeRunner` Protocol implementation for unit tests — deterministic, no subprocess
  - FR-6: Prompt versioning — semver + sha256 of every prompt file used in a call recorded into `audit.db.prompt_versions` per D-015
  - FR-7: NL time parser — two-stage: `dateparser` (rule-based, ru/en, user-TZ from users.toml per D-042) → on miss, Haiku-fallback with narrow system prompt → on still-ambiguous, escalate=True signal for caller (Stage-1a)
  - FR-8: All datetime persisted as **UTC** in jobs.db; user-TZ applied only at parse-input and render-output boundaries
  - FR-9: Public API — `classify(text: str, *, correlation_id: str) -> ClassifierResult` and `parse_time(text: str, *, user_tz: ZoneInfo, now_utc: datetime) -> TimeParseResult`
  - FR-10: structlog event on every call: `classifier.stage0.call`, `classifier.time.parse`, with backend, model, prompt_semver, prompt_sha8, latency_ms, intent, confidence
nfr:
  - NFR-1: Stage-0 p95 latency ≤ 1500ms (CLI subprocess including auth handshake); SLA enforced via timeout (D-019 transient class)
  - NFR-2: dateparser p95 ≤ 50ms; Haiku-fallback p95 ≤ 1500ms
  - NFR-3: Subprocess invocation MUST set `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code` (RO mount, D-013)
  - NFR-4: API backend credential MUST come from systemd-credentials (`LoadCredential=`) — never `.env`, never re-using OAuth token (INV-6)
  - NFR-5: Prompt-file read once at startup + on SIGHUP (cached with sha256); no per-call disk read
  - NFR-6: Pydantic strict-validation on CLI JSON output; on schema-violation → `ClassifierError` (Permanent class per failure taxonomy of chunk 4)
  - NFR-7: ≥80% line coverage on `classifier/` core via FakeClaudeRunner
constraints:
  - INV-6 — API backend credential is separate from Claude Code OAuth (audit trail)
  - D-009 confidence threshold = 0.85 (fast-path gate for reminder_job)
  - D-010 NL parser order is fixed: dateparser → Haiku-fallback → escalate
  - D-015 prompt semver+sha256 logged per call; prompt files RO mounted
  - D-042 user_tz lookup keyed by telegram_id only
  - No FK from audit.prompt_versions to users (audit isolation invariant)
  - intent enum closed list — extension requires schema migration + new D-decision
risks:
  - Claude CLI JSON-mode schema-violation → handled by Pydantic strict + Permanent classification (no auto-retry, surfaces to operator)
  - dateparser locale drift between versions → pinned via uv.lock; integration nightly catches regression
  - Haiku-fallback hallucinates time on ambiguous text → mitigated by `escalate=True` signal to Stage-1a (D-010), never auto-write to jobs.db
  - prompt_versions table unbounded growth → out of scope (retention §10.4 says indefinite — compact, full-replay)
scope_in:
  - src/ai_steward_wiki/classifier/__init__.py — barrel
  - src/ai_steward_wiki/classifier/schema.py — Pydantic intent enum + ClassifierResult + TimeParseResult
  - src/ai_steward_wiki/classifier/backend.py — ClassifierBackend Protocol; ClaudeCliBackend; AnthropicApiBackend; FakeClaudeRunner
  - src/ai_steward_wiki/classifier/stage0.py — public classify() orchestrator + prompt loader + audit write
  - src/ai_steward_wiki/classifier/time_parse.py — dateparser + Haiku-fallback chain
  - prompts/classifier.md — backend-independent system prompt (versioned, semver header)
  - alembic/audit/versions/<rev>_prompt_versions.py — table prompt_versions(id, sha256, semver, file_name, first_seen_utc) if not yet present
  - tests/unit/classifier/{test_schema.py,test_stage0.py,test_time_parse.py,test_fake_runner.py}
  - tests/integration/classifier/test_real_cli.py (gated by RUN_INTEGRATION=1)
scope_out:
  - Stage-1a / Stage-1b runners (chunk 7)
  - Inbox-WIKI hint cache consumption (chunk 6)
  - Fast-path reminder_job dispatch — chunk 5 only emits intent+confidence; scheduler consumption is in chunk 7
  - prompt_versions retention purge — out of MVP per §10.4
  - i18n beyond ru/en in dateparser (D-032)
---

# Discovery: M-CLASSIFIER-STAGE0 (Chunk 5)

Implements Stage-0 of the §6 classifier pipeline plus the NL time-parser. Single responsibility: turn raw user text into a typed `(intent, confidence, distilled_payload)` plus an optional parsed UTC datetime, without writing to any WIKI.

## Sources
- spec §6 «Classifier & NLP» (lines 376–441)
- spec §3 job kinds taxonomy (intent enum source)
- spec §10.1 RO prompts mount
- spec §10.4 prompt_versions retention
- D-009 — Stage-0 Haiku, threshold 0.85
- D-010 — dateparser → Haiku-fallback → Stage-1 escalate; UTC storage; user-TZ from users.toml
- D-013 — shared `CLAUDE_CONFIG_DIR` for CLI subscription auth
- D-015 — Hybrid prompts; backend-independent classifier; semver+sha256 audit
- D-042 — telegram_id canonical identity for users.toml lookup
- INV-6 — API backend credential separation

## Open Questions
None — D-009/D-010/D-013/D-015/INV-6 closed in Spec-WIKI. First integration run with real Claude CLI (chunk 5 acceptance) will confirm exact JSON-schema shape; if CLI rejects our schema we escalate via questions-answers per superautocoder §3.

## Acceptance signals
1. `uv run pytest tests/unit/classifier` — all green, ≥80% coverage on `classifier/`.
2. `make lint` — ruff + ruff format + mypy strict + grace lint clean.
3. `RUN_INTEGRATION=1 pytest tests/integration/classifier/test_real_cli.py` — single happy-path run against real Claude Haiku CLI returns valid `ClassifierResult` with confidence ≥ 0.5 on a fixture text.
4. `make total-test` exit 0 (per superautocoder Phase 5.5).
