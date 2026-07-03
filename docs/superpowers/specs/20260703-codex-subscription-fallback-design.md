---
feature: codex-subscription-fallback
bd_id: aisw-8gw
module_id: M-LLM-FAILOVER
status: stable
date: 2026-07-03
risk: high
evidence: strong
open_questions: []
stack:
  - Python 3.11 stdlib asyncio, dataclasses, enum, pathlib, and typing for provider state, concurrency, and typed results.
  - Existing Pydantic v2 schemas remain the validation boundary for Stage-0 structured output.
  - Existing structlog JSON logging remains the observability boundary.
  - Claude Code CLI remains the primary subscription provider through the existing saved authentication.
  - Codex CLI 0.142.5 is the pinned fallback runtime, authenticated through ChatGPT subscription login in a dedicated CODEX_HOME.
  - gpt-5.4-mini with low reasoning serves Stage-0 classification and lightweight structured parsers.
  - gpt-5.5 with medium reasoning serves WIKI, router, digest, web, schema-generation, and cron fallback flows.
  - codex exec uses JSON Schema output for structured mode and JSONL output for agent mode.
  - No new Python dependency and no database migration are required by the selected design.
decisions:
  - DEC-1 Use a shared provider-neutral failover policy with separate Claude and Codex adapters. Do not duplicate fallback logic in each call site and do not add a sidecar service.
  - DEC-2 Keep mode-specific adapters for structured, agent, and text execution. Each adapter preserves the existing consumer contract.
  - DEC-3 The circuit states are named claude, codex, and probe. Conventional closed/open/half-open names are intentionally not used.
  - DEC-4 Only a typed Claude subscription-limit error may move the circuit from claude to codex. Timeout, malformed output, permission denial, and generic process failures keep their current handling.
  - DEC-5 Parse the provider reset time when available. Otherwise use a 15-minute codex interval followed by one single-flight probe.
  - DEC-6 A successful probe moves the circuit to claude. Another subscription limit moves it back to codex.
  - DEC-7 Hold the existing WIKI lock across the Claude attempt and any safe Codex fallback attempt.
  - DEC-8 Replay is allowed only before mutation evidence. Write, Edit, file-change events, mutation-capable shell commands, and unknown actions block replay.
  - DEC-9 Structured mode maps Claude Haiku work to gpt-5.4-mini with low reasoning and validates the existing Pydantic result schema.
  - DEC-10 Agent and text modes map Claude Sonnet work to gpt-5.5 with medium reasoning.
  - DEC-11 Codex write flows use workspace-write restricted to the selected WIKI. Read-only, web, structured, and cron flows use narrower capability profiles.
  - DEC-12 Web fallback uses a neutral empty working directory, read-only sandbox, enabled web search, and no WIKI directory access.
  - DEC-13 Production Codex authentication uses ChatGPT subscription login in a dedicated CODEX_HOME under the existing service user. API-key billing is excluded.
  - DEC-14 Every Codex invocation uses explicit model, reasoning, sandbox, working directory, ignore-user-config, and ephemeral settings.
  - DEC-15 Codex JSONL is normalized into provider-neutral events before existing WIKI aggregation, transcript, and delivery logic consumes it.
  - DEC-16 If both providers are unavailable, preserve the source operation and return one Russian diagnostic. Do not schedule automatic replay.
  - DEC-17 Provider state is process-local for this feature. Durable state across restarts remains later scope.
  - DEC-18 Startup preflight checks binary, pinned version, login status, and CODEX_HOME readability without invoking a model.
  - DEC-19 Deployment smoke tests verify both models, structured output, JSONL, read-only isolation, and workspace-write containment.
  - DEC-20 Emit provider selection, failover, circuit transition, failure, recovery, and replay-blocked events without prompts or credentials.
---

# Design — Codex subscription fallback for Claude limits

## 1. Selected architecture

The selected design adds one provider-neutral policy layer inside the existing process.
Claude remains primary and Codex becomes a subscription-backed fallback.

```text
Existing pipeline
      |
      v
Mode adapter
      |
      v
Failover policy -------- Provider circuit
   |          |
   v          v
Claude       Codex
adapter      adapter
```

The policy owns selection, state transitions, replay safety, and failover telemetry.
It does not own Telegram routing, WIKI semantics, or business validation.

## 2. Proposed component boundaries

### Shared failover layer

A proposed `ai_steward_wiki.llm` package contains two focused modules:

1. `failover.py` — provider state, typed errors, attempt evidence, and policy orchestration.
2. `codex.py` — restricted environment, Codex argv builders, process execution, and result parsing.

The exact MODULE_CONTRACT boundaries are finalized during GRACE Plan.

### Mode adapters

1. Structured mode extends the classifier backend boundary.
2. Agent mode extends the WIKI runner execution boundary.
3. Text mode extends schema generation and cron execution.

Existing Telegram pipeline protocols remain unchanged.

## 3. Provider state machine

```text
claude --subscription limit--> codex
codex  --cooldown elapsed----> probe
probe  --Claude success------> claude
probe  --subscription limit--> codex
```

Only one request may execute the probe.
Other concurrent requests continue through Codex until the probe completes.

The reset timestamp comes from structured Claude output when available.
Without a timestamp, the codex interval lasts 15 minutes.

State is intentionally process-local.
A service restart begins in claude state.

## 4. Request flow

1. The mode adapter builds a provider-neutral execution request.
2. The failover policy reads the current state.
3. In claude state, the Claude adapter runs first.
4. Success returns immediately without a Codex call.
5. A typed subscription limit moves the state to codex.
6. The policy checks mutation evidence.
7. A safe request continues through the matching Codex adapter.
8. An unsafe request returns the existing partial-failure behavior.

Successful fallback is transparent to the Telegram user.
Provider details remain operational telemetry.

## 5. Replay safety

Replay is safe for classification, read-only file access, web search, and pure text generation.

Replay is blocked after any of these signals:

1. Claude Write or Edit tool use.
2. A provider file-change event.
3. A shell command capable of mutation.
4. An unknown action without a proven read-only classification.
5. External result delivery.

Unknown evidence fails closed.

For WIKI runs, the existing lock spans both attempts.
This prevents another request from observing an intermediate state.

## 6. Model and mode mapping

### Structured mode

1. Model: `gpt-5.4-mini`.
2. Reasoning: `low`.
3. Sandbox: `read-only`.
4. Output: JSON Schema.
5. Consumer validation: existing Pydantic schema.

### Agent read-only mode

1. Model: `gpt-5.5`.
2. Reasoning: `medium`.
3. Sandbox: `read-only`.
4. Working directory: selected WIKI.
5. Output: normalized JSONL events.

### Agent write mode

1. Model: `gpt-5.5`.
2. Reasoning: `medium`.
3. Sandbox: `workspace-write`.
4. Writable boundary: selected WIKI only.
5. Output: normalized JSONL events.

### Web mode

1. Model: `gpt-5.5`.
2. Reasoning: `medium`.
3. Sandbox: `read-only`.
4. Working directory: neutral empty directory.
5. Web search: enabled.
6. WIKI access: absent.

### Text and cron mode

1. Model: `gpt-5.5`.
2. Reasoning: `medium`.
3. Sandbox: `read-only`.
4. Output: final text only.

## 7. Codex invocation isolation

Every production Codex invocation uses:

1. A dedicated production `CODEX_HOME`.
2. ChatGPT subscription authentication created by operator login.
3. `--ignore-user-config`.
4. `--ephemeral`.
5. An explicit model and reasoning value.
6. An explicit sandbox and working directory.
7. A restricted environment allowlist.

Runtime never installs Codex and never performs login.
Personal plugins, MCP configuration, and operator history are not reused.

Authentication files are treated as passwords.
They are excluded from logs, project backups, and repository content.

## 8. Output normalization

Structured mode validates Codex output directly against the existing classifier schema.

Agent mode maps Codex JSONL into provider-neutral events:

1. Agent messages become assistant text events.
2. File changes become mutation evidence.
3. Command executions become tool evidence.
4. Turn completion carries usage and success state.
5. Turn failure becomes a typed provider error.

The existing aggregation, transcript, and Telegram delivery layers consume normalized events.

## 9. Full-provider failure

When Codex also fails:

1. The operation is not replayed automatically.
2. The source material remains recoverable.
3. The user receives one concise Russian message.
4. Logs record the provider categories and correlation identifier.
5. The user starts a later retry explicitly.

## 10. Observability

Proposed structured events:

1. `llm.provider.selected`.
2. `llm.failover.triggered`.
3. `llm.circuit.changed`.
4. `llm.provider.failed`.
5. `llm.provider.recovered`.
6. `llm.replay.blocked`.

Stable fields include provider, model, run kind, state transition, reason category, probe time, latency, and mutation evidence.

Prompts, credentials, user content, and provider session identifiers are forbidden log fields.

Metrics distinguish primary success, fallback success, dual failure, blocked replay, and Claude recovery.

## 11. Startup and deployment

Startup preflight verifies:

1. Codex binary existence.
2. Codex CLI version `0.142.5`.
3. Saved ChatGPT login status.
4. Dedicated `CODEX_HOME` readability.

Preflight does not invoke a model.
Failure disables fallback without blocking Claude-backed startup.

Deployment smoke tests verify both model profiles and both output modes.
They also verify read-only isolation and workspace-write containment.

The production host requires Codex installation and one operator login before activation.

## 12. Verification design

Unit tests cover:

1. Subscription-limit recognition for classifier JSON and WIKI stream events.
2. Non-limit errors that must not trigger Codex.
3. Safe and blocked replay paths.
4. The claude to codex to probe to claude state sequence.
5. Single-flight probe under concurrent requests.
6. Model and reasoning mappings.
7. Codex JSON Schema and JSONL normalization.
8. Dual-provider failure.

Integration tests use deterministic fake CLI processes for all failure shapes.

Real subscription tests remain manual because they consume provider quota.

End-to-end acceptance proves one Telegram input produces one final response and at most one WIKI mutation.

## 13. Documentation impact

The implementation updates:

1. D-009-derived architecture through a new ADR.
2. MODULE_CONTRACT and MODULE_MAP headers for touched modules.
3. `knowledge-graph.xml` and `verification-plan.xml`.
4. Deployment and operations runbooks.
5. Completion report after verified implementation.

## 14. Official references

1. [Codex models](https://developers.openai.com/codex/models)
2. [Codex authentication](https://developers.openai.com/codex/auth)
3. [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
4. [Codex configuration reference](https://developers.openai.com/codex/config-reference)
