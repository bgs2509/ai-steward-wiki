---
feature: codex-subscription-fallback
bd_id: aisw-8gw
module_id: M-LLM-FAILOVER
status: stable
date: 2026-07-03
risk: high
evidence: strong
open_questions: []
UseCases:
  UC-LLM-FAILOVER-1:
    Actor: Telegram user
    Action: Sends an operation while Claude reports a confirmed subscription limit
    Goal: Continue the same safe operation through the mapped Codex subscription model
    Preconditions: Claude is primary, Codex fallback is ready, and no mutation evidence exists
    AcceptanceCriteria: One Telegram operation produces one final response without requiring resubmission
    Priority: high
    RelatedFlows: DF-LLM-SAFE-FAILOVER
  UC-LLM-FAILOVER-2:
    Actor: Telegram user
    Action: Sends operations while the circuit is in codex or probe state
    Goal: Avoid repeated Claude failures and automatically restore Claude after a successful probe
    Preconditions: A prior typed Claude subscription-limit error established the codex interval
    AcceptanceCriteria: Requests use Codex until one single-flight Claude probe succeeds
    Priority: high
    RelatedFlows: DF-LLM-CIRCUIT-RECOVERY
  UC-LLM-FAILOVER-3:
    Actor: Runtime system
    Action: Detects a Claude subscription limit after possible agent actions
    Goal: Prevent duplicate writes or external effects during failover
    Preconditions: The primary attempt produced provider events or side-effect evidence
    AcceptanceCriteria: Codex replay occurs only when all observed evidence is proven read-only
    Priority: high
    RelatedFlows: DF-LLM-REPLAY-GUARD
  UC-LLM-FAILOVER-4:
    Actor: Operator
    Action: Provisions and verifies the Codex subscription fallback
    Goal: Enable fallback without API-key billing or interactive runtime login
    Preconditions: Codex CLI is installed and a dedicated CODEX_HOME exists
    AcceptanceCriteria: Startup preflight verifies binary, version, and ChatGPT login; deployment smoke verifies both configured models
    Priority: high
    RelatedFlows: DF-LLM-READINESS
fr:
  - FR-1 — Every Claude subscription-backed model call MUST use one provider chain. Claude remains primary. Codex becomes the fallback only after a confirmed Claude subscription-limit error.
  - FR-2 — The system MUST recognize Claude subscription exhaustion from structured CLI output. Detection MUST support classifier JSON envelopes and WIKI stream events carrying HTTP 429.
  - FR-3 — Generic process failures, timeouts, malformed output, permission denials, and unrelated HTTP errors MUST NOT trigger Codex automatically.
  - FR-4 — The first safe request that discovers Claude exhaustion MUST continue through Codex within the same Telegram operation. The user MUST NOT resend the message.
  - FR-5 — Stage-0 classification and Haiku-based parsers MUST fall back to gpt-5.4-mini. Their existing structured output contracts MUST remain unchanged.
  - FR-6 — Stage-1 routing, query, ingest, lint, digest, web tasks, schema generation, and cron-user execution MUST fall back to gpt-5.5 with medium reasoning.
  - FR-7 — After confirmed exhaustion, the system MUST mark Claude unavailable until its reported reset time. Subsequent requests MUST skip the known-unavailable Claude call.
  - FR-8 — When the reset time arrives, the next eligible request MUST probe Claude. A successful probe MUST restore Claude as primary.
  - FR-9 — Concurrent requests MUST share one provider-health state. They MUST NOT create a retry stampede after the first limit response.
  - FR-10 — Automatic replay through Codex MUST occur only before any external side effect. A run with file writes, tool actions, or delivered output MUST NOT replay automatically.
  - FR-11 — Codex classifier responses MUST be validated against the existing classifier schema before the pipeline accepts them.
  - FR-12 — Codex JSONL events MUST be normalized into the runner's provider-neutral event contract. Final text, failures, usage, and transcripts MUST remain available.
  - FR-13 — Codex fallback MUST preserve each run's capability boundary. Read-only, WIKI-write, media-read, and web-search runs MUST keep their current isolation.
  - FR-14 — Existing timeout, cancellation, locking, deduplication, and partial-result behavior MUST work identically under both providers.
  - FR-15 — Startup readiness MUST verify the Codex binary, pinned version, saved ChatGPT authentication, and non-interactive execution support without invoking a model. Deployment smoke tests MUST verify availability of both configured Codex models.
  - FR-16 — Missing or expired Codex authentication MUST NOT stop Claude-backed startup. The fallback MUST become unavailable with an operator-visible diagnostic.
  - FR-17 — If Claude and Codex are both unavailable, the bot MUST return a clear Russian message. The source message MUST remain recoverable for retry.
  - FR-18 — Operators MUST be able to configure provider enablement, binary paths, model identifiers, reasoning effort, and fallback cooldown without changing code.
nfr:
  - NFR-1 — Provider selection overhead MUST stay below 100 ms at p95, excluding CLI startup and model latency.
  - NFR-2 — Once Claude is marked unavailable, no new Claude process may start until the probe window opens.
  - NFR-3 — Failover decisions MUST be atomic inside one service process. Concurrent requests MUST observe a consistent provider state.
  - NFR-4 — Fallback MUST be idempotent. One Telegram operation may produce at most one committed WIKI mutation and one final delivery.
  - NFR-5 — Codex MUST run with the least filesystem and tool privileges required by the current run type.
  - NFR-6 — Codex authentication data MUST be treated as a password. Tokens, session identifiers, prompts, and user content MUST NOT enter logs.
  - NFR-7 — ChatGPT-managed automation MUST run only on the private trusted VPS. Public or untrusted execution is outside the supported security boundary.
  - NFR-8 — Existing Telegram responses, classifier schemas, WIKI layouts, database schemas, and job payloads MUST remain backward compatible.
  - NFR-9 — Logs MUST expose provider, model, fallback reason, state transition, latency, and outcome through structured fields.
  - NFR-10 — Metrics MUST distinguish primary success, fallback success, both-provider failure, blocked replay, and automatic failback.
  - NFR-11 — The Codex CLI production version MUST be pinned. Model identifiers MUST remain configurable for controlled upgrades.
  - NFR-12 — No new Python runtime dependency or database migration SHOULD be added unless the approved design proves it necessary.
  - NFR-13 — Unit tests MUST cover error classification, model mapping, circuit state, concurrency, and replay safety. Integration tests MUST cover both CLIs.
  - NFR-14 — The full project quality gate MUST pass. GRACE contracts, graph, verification plan, deployment documentation, and runbooks MUST be synchronized.
constraints:
  - Claude remains the primary provider. This feature is failover, not load balancing or round-robin routing.
  - Codex access uses ChatGPT subscription authentication. OpenAI API-key billing is outside this feature.
  - The production host vpn-2 currently has no codex executable. Deployment work is mandatory before fallback can become ready.
  - Official Codex documentation currently recommends gpt-5.4-mini for lighter tasks and gpt-5.5 for complex work.
  - gpt-5.5 uses medium reasoning for Stage-1 fallback. The gpt-5.4-mini reasoning effort remains a design decision.
  - Current Claude integrations use three output shapes: classifier JSON, WIKI stream JSON, and plain text.
  - Current direct Claude call sites include classifier backend, WIKI runner, schema generator, and cron consumer.
  - Existing web-task isolation remains unchanged. Codex web search may only be enabled for the existing web-task path.
  - Provider credentials are provisioned manually by an operator. Runtime code MUST NOT perform interactive login.
risks:
  - R-1 (HIGH) — A multi-user Telegram service consumes one person's ChatGPT-managed Codex entitlement. Mitigation — trusted-user boundary, explicit operator acceptance, and entitlement review before deployment.
  - R-2 (HIGH) — Replaying a partially executed WIKI run can duplicate or corrupt writes. Mitigation — FR-10 and NFR-4 prohibit unsafe replay.
  - R-3 (HIGH) — Claude and Codex expose different event and tool models. Mitigation — provider adapters and contract-level compatibility tests.
  - R-4 (MEDIUM) — Codex may load user configuration or repository instructions unexpectedly. Mitigation — isolated configuration, explicit prompt assembly, and least-privilege execution.
  - R-5 (MEDIUM) — The fallback subscription can also exhaust its own quota. Mitigation — distinct provider state, clear user message, and observable recovery.
  - R-6 (MEDIUM) — Saved Codex authentication can expire or become unreadable under systemd. Mitigation — startup preflight and operator-visible readiness.
  - R-7 (MEDIUM) — Reset-time parsing can be absent or ambiguous. Mitigation — bounded default cooldown followed by a single probe.
  - R-8 (LOW) — The first failover request has extra latency from the failed Claude attempt. Mitigation — immediate transition to codex prevents repeated cost.
scope_in:
  - Provider-neutral error taxonomy and provider-health state for Claude-limit detection and automatic failback.
  - Codex CLI adapter for classifier structured output using gpt-5.4-mini.
  - Codex CLI adapter for WIKI, router, digest, web, schema-generation, and cron flows using gpt-5.5 with medium reasoning.
  - Safe event normalization, transcript persistence, timeout, cancellation, and replay guards.
  - Settings, startup readiness, structured logs, metrics, deployment steps, and operational runbook changes.
  - Updates to D-009-derived architecture through a new ADR after design approval.
scope_out:
  - Proactive load balancing while Claude is healthy.
  - OpenAI API-key billing, Responses API integration, or direct SDK integration.
  - Third providers beyond Claude and Codex.
  - Changes to classifier intents, WIKI schemas, Telegram commands, or job payload formats.
  - Automatic installation or interactive authentication performed by the running bot.
scope_later:
  - Durable provider-health state across service restarts.
  - Per-user provider credentials or independent subscription routing.
  - Proactive quota telemetry before a provider returns an exhaustion error.
  - A generic multi-provider policy engine with cost-aware or latency-aware routing.
---

# Discovery — Codex subscription fallback for Claude limits

## Problem

The production service currently depends on one Claude subscription for every model call.
When that subscription reaches its session limit, all model-backed paths fail together.
The bot process remains healthy, but classification, routing, and WIKI execution stop.

The incident on 2026-07-03 showed six failed Claude CLI launches across two user operations.
The structured result contained HTTP 429 and an explicit subscription reset time.
Higher layers lost that reason because they primarily reported empty stderr.

## Real goal

Keep the bot useful when Claude subscription capacity is temporarily unavailable.
Codex must provide a safe secondary execution path without widening access or duplicating writes.

## Verified current state

1. Stage-0 uses `ClaudeCliBackend` with `claude-haiku-4-5`.
2. The time parser reuses the same Stage-0 backend.
3. WIKI, router, digest, and web runs use `_RunConfig` with `claude-sonnet-4-5`.
4. Schema generation invokes Claude separately and returns plain text.
5. Cron-user execution builds another direct Claude CLI command.
6. The production host `vpn-2` does not currently have `codex` installed.
7. Local Codex CLI supports non-interactive JSONL and JSON Schema output.

## Required user-visible behavior

1. Claude remains primary while its subscription has capacity.
2. A confirmed subscription-limit response moves the circuit from claude to codex.
3. A safe request continues immediately through the mapped Codex model.
4. Later requests use Codex directly until the Claude probe window opens.
5. Claude becomes primary again after a successful probe.
6. Unsafe partial runs are never replayed automatically.
7. Failure of both subscriptions produces one clear Russian response.

## Model mapping

1. `claude-haiku-4-5` maps to `gpt-5.4-mini` for lightweight structured tasks.
2. `claude-sonnet-4-5` maps to `gpt-5.5` with medium reasoning.
3. The mapping is configuration, not scattered constants.

## Research evidence

1. OpenAI documents `gpt-5.4-mini` as the current fast Codex model.
2. OpenAI documents `gpt-5.5` as the recommended model for complex Codex work.
3. Codex CLI supports ChatGPT sign-in for subscription access.
4. `codex exec` supports JSONL events, JSON Schema output, and ephemeral runs.
5. OpenAI treats ChatGPT-managed automation as an advanced trusted-runner workflow.

Sources:

1. [Codex models](https://developers.openai.com/codex/models)
2. [Codex authentication](https://developers.openai.com/codex/auth)
3. [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
4. [Codex configuration reference](https://developers.openai.com/codex/config-reference)

## Approval gate

Discovery stops here. Design, contracts, planning, and implementation require explicit approval.
