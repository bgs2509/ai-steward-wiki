---
feature: classifier-stage0-and-nl-time
bd_id: aisw-5
status: stable
date: 2026-05-10
stack:
  - python: "3.11+"
  - pydantic: "v2 (already in deps) — strict validation of CLI JSON output"
  - dateparser: "1.2.0 — rule-based NL time parsing, ru/en"
  - structlog: "already in deps"
  - asyncio: "stdlib — subprocess via asyncio.create_subprocess_exec"
  - sqlalchemy: "async (already in deps) — audit.prompt_versions writes"
  - claude_cli: "external binary, invoked via systemd-run --scope per call (chunk 16 wiring; chunk 5 stubs the wrapper)"
---

# Design: M-CLASSIFIER-STAGE0

## 1. Module decomposition

```
src/ai_steward_wiki/classifier/
├── __init__.py        # BARREL — public surface
├── schema.py          # Pydantic models + Intent enum
├── backend.py         # ClassifierBackend Protocol; ClaudeCliBackend; AnthropicApiBackend; FakeClaudeRunner
├── stage0.py          # classify() orchestrator + prompt loader + audit hook
└── time_parse.py      # parse_time() — dateparser → Haiku-fallback chain
```

## 2. Schema (schema.py)

```python
class Intent(str, Enum):
    REMINDER = "reminder"      # → fast-path reminder_job (no WIKI resolve)
    WIKI_INGEST = "wiki_ingest"
    WIKI_QUERY = "wiki_query"
    WIKI_LINT = "wiki_lint"
    DIGEST = "digest"
    ADMIN = "admin"
    UNKNOWN = "unknown"

class ClassifierResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    distilled_payload: dict[str, Any]    # opaque, schema enforced downstream
    backend: Literal["claude_cli", "anthropic_api", "fake"]
    model: str                           # e.g. "claude-haiku-4-5"
    prompt_semver: str                   # e.g. "1.0.0"
    prompt_sha256: str                   # full hash
    latency_ms: int

class TimeParseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    when_utc: datetime | None            # tz-aware UTC; None iff escalate=True
    source: Literal["dateparser", "haiku_fallback", "escalate"]
    escalate: bool                       # caller MUST route to Stage-1a if True
    raw: str                             # original input text (for audit)
    user_tz: str                         # IANA name, audit only
```

Intent enum is **closed**: extending it requires a new D-decision and Alembic migration of `audit.prompt_versions.intent_seen` (deferred — chunk 5 does not write per-intent counters).

## 3. Backend Protocol (backend.py)

```python
class ClassifierBackend(Protocol):
    async def call(
        self, *, text: str, prompt_path: Path, correlation_id: str
    ) -> dict[str, Any]: ...    # raw JSON dict; Stage-0 wraps into ClassifierResult
```

### 3.1 ClaudeCliBackend
- Builds command: `claude --model claude-haiku-4-5 --output-format json --json-schema <schema-path> --max-turns 1 --append-system-prompt @<prompt_path> --disallowedTools Bash Read Write Edit Glob Grep WebFetch --permission-mode dontAsk`
- Spawned via `asyncio.create_subprocess_exec` with `env={CLAUDE_CONFIG_DIR: settings.claude_config_dir, PATH: ...}` — minimal env, no leakage. `claude_config_dir` is an env-resolved property (`_local`/`_vps`); when `None` (VPS default) the variable is omitted and CLI falls back to `~/.claude/`.
- Timeout = `settings.classifier_stage0_timeout_s` (default 30s). Timeout → raises `ClassifierTimeoutError` (transient per chunk 4 taxonomy).
- stdin = `text`; stdout = JSON; stderr captured into structlog at WARN.
- **Note (chunk 16 hand-off):** in production the CLI is invoked via `systemd-run --scope --uid=aisw-stage0 ...`. For chunk 5 we abstract the spawn primitive behind `Spawner` Protocol so chunk 16 can inject the systemd-run prefix without re-touching this module.

### 3.2 AnthropicApiBackend (optional)
- Activated iff `settings.stage0_backend == "anthropic_api"`.
- Credential loaded from systemd-credentials path `settings.stage0_api_credential_path`; raises `ConfigError` if absent.
- Uses `anthropic` SDK; identical prompt + JSON-mode (`response_format={"type": "json_schema", ...}`).
- INV-6 enforced by config validation: backend == anthropic_api → credential_path is required and MUST NOT equal claude_config_dir.

### 3.3 FakeClaudeRunner (test double)
- Pure Python, no subprocess, no network.
- Constructor takes `responses: list[dict] | Callable[[str], dict]` — deterministic queue or fn.
- Records every call into `self.calls` for assertion.

## 4. Orchestrator (stage0.py)

```python
async def classify(
    text: str,
    *,
    correlation_id: str,
    backend: ClassifierBackend = _default_backend,
    audit_session: AsyncSession | None = None,
) -> ClassifierResult:
```

Flow:
1. Load + cache prompt: `_PromptCache.get(Path("prompts/classifier.md"))` → `(text, semver, sha256)`. Cache invalidated on SIGHUP (re-uses chunk 3 reloader signal channel).
2. Wall-clock start.
3. `raw = await backend.call(text=text, prompt_path=..., correlation_id=...)`.
4. Validate via `ClassifierResult.model_validate({**raw, backend, model, prompt_semver, prompt_sha256, latency_ms})`.
5. If `audit_session`: upsert `prompt_versions(sha256, semver, file_name, first_seen_utc)` (idempotent on sha256 PK).
6. structlog event `classifier.stage0.call` with all schema fields except `distilled_payload` (size-bounded).
7. Return `ClassifierResult`.

Errors:
- `ClassifierTimeoutError` (transient) → caller retries via chunk 4 retry policy.
- `ClassifierSchemaError` (permanent) → wraps Pydantic ValidationError with raw output truncated to 2KB.

## 5. NL time parser (time_parse.py)

```python
async def parse_time(
    text: str,
    *,
    user_tz: ZoneInfo,
    now_utc: datetime,
    haiku_backend: ClassifierBackend = _default_backend,
) -> TimeParseResult:
```

Algorithm (D-010):
1. **dateparser** — `dateparser.parse(text, settings={"TIMEZONE": str(user_tz), "RELATIVE_BASE": now_utc.astimezone(user_tz), "RETURN_AS_TIMEZONE_AWARE": True}, languages=["ru", "en"])`.
   - Hit → convert to UTC → return `(when_utc, source="dateparser", escalate=False)`.
   - p95 ≤ 50ms enforced via test fixture.
2. **Haiku-fallback** — narrow system prompt `prompts/time-parse.md` (created here, semver 1.0.0) instructs Haiku to return `{"when_iso": "...", "tz": "...", "ambiguous": bool}` or `{"ambiguous": true}`.
   - If `ambiguous=true` → `(None, source="escalate", escalate=True)`.
   - Else → parse ISO → convert to UTC → return.
3. structlog event `classifier.time.parse` with source, escalate, latency_ms_per_stage.

UTC invariant: every returned `when_utc` is `tz=ZoneInfo("UTC")`; assertion in unit tests.

## 6. Prompt files

### prompts/classifier.md (new, semver 1.0.0)
- Frontmatter: `semver: 1.0.0`, `purpose: Stage-0 classifier`.
- System body: instructs the model to emit JSON conforming to `ClassifierResult`-subset schema (intent, confidence, distilled_payload). RU+EN examples. Closed intent enum listed verbatim.

### prompts/time-parse.md (new, semver 1.0.0)
- Narrow prompt for Haiku-fallback in NL time parser. Returns ISO datetime + tz + ambiguous-flag.

Both files mounted RO at `/opt/ai-steward-wiki/prompts/` per §10.1.

## 7. Audit table (alembic/audit)

If `prompt_versions` table not yet created in chunk 2, add migration:

```sql
CREATE TABLE prompt_versions (
    sha256        TEXT PRIMARY KEY,    -- full hash
    file_name     TEXT NOT NULL,       -- e.g. "classifier.md"
    semver        TEXT NOT NULL,       -- e.g. "1.0.0"
    first_seen_utc TIMESTAMP NOT NULL  -- ISO 8601 UTC
);
CREATE INDEX ix_prompt_versions_file ON prompt_versions(file_name, semver);
```

Idempotent upsert on `sha256` (INSERT … ON CONFLICT DO NOTHING). Retention: indefinite (§10.4 — compact, full-replay).

## 8. Settings additions (settings.py)

```python
class Settings(BaseSettings):
    ...
    stage0_backend: Literal["claude_cli", "anthropic_api"] = "claude_cli"
    stage0_api_credential_path: Path | None = None
    classifier_stage0_timeout_s: float = 30.0
    classifier_haiku_fallback_timeout_s: float = 15.0
    claude_config_dir_local: Path | None = Path("/var/lib/ai-steward-wiki/claude-code")
    claude_config_dir_vps: Path | None = None  # None → CLI uses ~/.claude/
    prompts_dir: Path = Path("/opt/ai-steward-wiki/prompts")

    @property
    def claude_config_dir(self) -> Path | None:
        return self.claude_config_dir_vps if self.env == "vps" else self.claude_config_dir_local

    @model_validator(mode="after")
    def _check_api_backend_credential(self):
        if self.stage0_backend == "anthropic_api":
            if self.stage0_api_credential_path is None:
                raise ValueError("STAGE0_API_CREDENTIAL_PATH required for anthropic_api backend (INV-6)")
            if (
                self.claude_config_dir is not None
                and self.stage0_api_credential_path == self.claude_config_dir
            ):
                raise ValueError("API credential MUST NOT equal CLAUDE_CONFIG_DIR (INV-6)")
        return self
```

## 9. Tests

| File | Coverage |
|------|----------|
| `tests/unit/classifier/test_schema.py` | Intent enum closed; ClassifierResult strict validation; confidence bounds; TimeParseResult invariants |
| `tests/unit/classifier/test_fake_runner.py` | FakeClaudeRunner determinism; call recording |
| `tests/unit/classifier/test_stage0.py` | Orchestrator with FakeClaudeRunner — happy path, timeout, schema-violation, prompt cache hit/miss, audit upsert idempotency, structlog event shape |
| `tests/unit/classifier/test_time_parse.py` | dateparser hit (ru + en fixtures, ≥10 cases); fallback to Haiku via FakeRunner; escalate path; UTC invariant; user_tz application |
| `tests/unit/classifier/test_settings.py` | INV-6 model_validator: API backend without credential → ValueError; equal paths → ValueError |
| `tests/integration/classifier/test_real_cli.py` (RUN_INTEGRATION=1) | One happy-path call against real `claude-haiku-4-5`; returns ClassifierResult with confidence ≥ 0.5 on RU fixture |

## 10. Public surface (__init__.py)

```python
# START_MODULE_CONTRACT
#   PURPOSE: Stage-0 classifier (Haiku CLI) + NL time parser (dateparser → Haiku-fallback → escalate).
#   SCOPE: Re-export schema, backend Protocol, classify(), parse_time(), errors.
#   DEPENDS: ai_steward_wiki.classifier.{schema,backend,stage0,time_parse}
#   LINKS: M-CLASSIFIER-STAGE0
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
```

Exports: `Intent`, `ClassifierResult`, `TimeParseResult`, `ClassifierBackend`, `ClaudeCliBackend`, `AnthropicApiBackend`, `FakeClaudeRunner`, `classify`, `parse_time`, `ClassifierError`, `ClassifierTimeoutError`, `ClassifierSchemaError`.

## 11. Hand-offs to subsequent chunks

- **Chunk 6 (Inbox materialize):** consumes `Intent` enum to decide hint-cache invalidation triggers — no API change here.
- **Chunk 7 (Wiki runner):** receives `ClassifierResult` via scheduler dispatch; reads `confidence ≥ 0.85 ∧ intent=REMINDER` for fast-path. The fast-path branch itself lives in chunk 7.
- **Chunk 16 (Deployment):** wraps `ClaudeCliBackend.spawn` with `systemd-run --scope --uid=aisw-stage0`. The `Spawner` Protocol seam is provided here so chunk 16 is one-line wiring.

## 12. Out of scope (deferred)

- Per-intent confidence calibration / accuracy metrics — chunk 17 verification.
- Anthropic API SDK pinning beyond first-import — full credential rotation runbook is chunk 16.
- prompt_versions GC — indefinite per §10.4.
