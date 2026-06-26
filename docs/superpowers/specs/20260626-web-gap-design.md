---
feature: web-gap
bd_id: aisw-dqz
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-26
risk: high
evidence: strong
open_questions:
  - OQ-1: SECURITY GATE — Approve enabling the WebSearch tool for web_task runs (Path B) with mitigations M-1..M-5 below? HIGH risk -> --auto-approve has no effect -> explicit human yes/no required.
stack:
  - library: claude-code CLI
    version: "2.1.139 (pinned in deploy)"
    used_for: --allowedTools WebSearch + --permission-mode dontAsk per-run tool scoping
  - library: pydantic v2
    version: pinned (uv.lock)
    used_for: Intent enum extension (frozen ClassifierResult unchanged)
  - library: structlog
    version: pinned (uv.lock)
    used_for: new wiki.run.web_search.* observability anchor (NFR-4)
  - library: pytest / pytest-asyncio
    version: pinned (uv.lock)
    used_for: RED->GREEN routing + tool-scoping unit tests
decisions:
  - D-local-1: Add Intent.WEB_TASK = "web_task" to the closed enum (classifier/schema.py:43) and a web_task section to prompts/classifier.md; bump prompt semver 1.1.0 -> 1.2.0. Because the new value is NOT in _ROUTABLE_INTENTS and is not reminder/digest/admin, a web_task message falls through to the generic answer runner (pipeline.py:1404+) with NO routing-predicate change — same control-flow the sibling query-gap (aisw-50z) relies on. Verified against the 4 Intent dispatch sites (pipeline.py:585,1116,1135,1395).
  - D-local-2 (PATH A, medium): "answer-in-chat web_task" works on the existing generic runner WITHOUT WebSearch. This alone fixes the "filed instead of answered" subset of failures (answers from model knowledge — adequate for stable knowledge like recipes). Path A is shippable independently and carries medium risk (new enum value = cross-cutting classifier-prompt change, behaviourally additive).
  - D-local-3 (PATH B, high): To answer queries needing LIVE web content, select a web_task-scoped _RunConfig that adds "WebSearch" to allowed_tools. Selection happens inside _WikiRunnerAdapter.run by branching on the already-threaded `intent` arg (pipeline.py:679) — NO WikiRunner/StreamingDelivery Protocol change, NO new module on the hot path. Path B is the security crux and is gated on OQ-1.
  - D-local-4 (security hardening, bundled with Path B): the web_task run config is READ-ONLY (allowed_tools = ["WebSearch"] only, NO WRITE_TOOLS) and is launched WITHOUT --add-dir on the user's WIKI tree (neutral cwd). This removes the prompt-injection -> write/exfiltrate-WIKI chain: even if a search result injects instructions, the run has no write tool and no access to WIKI files. WebFetch stays in disallowed_tools (no SSRF).
  - D-local-5: WebSearch is enabled ONLY for the web_task config. ingest/digest/librarian/router/query configs are untouched and keep WebSearch denied (FR-4). The change is intent-scoped, not a global policy lift.
  - D-local-6: Path B requires an ADR (security/permissions change to D-038) recording the WebSearch carve-out + mitigations, plus a structured log anchor wiki.run.web_search.enabled / .used for observability (NFR-4). This is an architectural fork => evidence stays weak for the gate => HIGH risk + weak evidence => fallback to ask (matrix).
---

# Design — web-gap: web_task mode + WebSearch policy

## Two-path split (recommended)

| Path | What | Risk | Gate |
|------|------|------|------|
| **A** answer-in-chat web_task | new `web_task` intent (enum + prompt) → falls through to generic answer runner; answers from model knowledge; WebSearch still denied | **medium** | matrix auto-approve eligible on its own |
| **B** enable WebSearch policy | web_task-scoped read-only run config with `WebSearch` allowed + no WIKI add-dir + ADR | **high** | **explicit human security approval (OQ-1)** |

Path A fixes the "filed instead of answered" failures. Path B fixes the
`permission_denied denied_tools=["WebSearch"]` failures and is the only way to serve
genuinely live web content. **Is B truly required?** For stable knowledge (recipes,
general facts) Path A alone answers correctly from training data. For current/volatile
info (today's prices, news, "что сейчас") B is required. Recommendation: ship A first,
land B only after OQ-1 approval. A safer-than-B alternative does **not** exist within
the CLI permission model — WebSearch is the only first-party live-web tool, and WebFetch
(the riskier one) must stay denied; there is no "read-only proxy" middle option without
building a new egress component (out of scope, much larger).

## Proposed design for web_task (enum / prompt / dispatch)

1. **Enum** — `classifier/schema.py:43`: add `WEB_TASK = "web_task"` to `Intent`.
2. **Prompt** — `prompts/classifier.md`: add `"web_task"` to the output-schema enum list
   and an intent-semantics bullet ("user asks to find/search something on the internet
   and get an answer back — NOT file it"); bump frontmatter `semver: 1.1.0 -> 1.2.0`.
   `prompt_sha256` is recorded per `ClassifierResult`, so the change is auditable.
3. **Routing** — NO change to `_ROUTABLE_INTENTS` (pipeline.py:585). web_task is absent
   from it and from reminder/digest/admin, so it naturally reaches the generic runner
   (pipeline.py:1404+) → `streaming.run_and_deliver(... intent=web_task)` / `runner.run(...)`
   → assistant answer delivered to chat. (Same fall-through the query-gap fix uses.)
4. **Dispatch / tool scoping (Path B)** — `_WikiRunnerAdapter.run` (__main__.py:413) branches
   on `intent`: when `intent is Intent.WEB_TASK`, use a `web_task` `_RunConfig` with
   `allowed_tools=["WebSearch"]` (read-only, no WRITE_TOOLS) and suppress `--add-dir` on the
   WIKI tree (neutral cwd). All other intents keep the current `WRITE_TOOLS` config.
5. **Runner** — `wiki/runner.py`: add `WEB_SEARCH_TOOLS = ["WebSearch"]`; keep
   `disallowed_tools=["WebFetch"]` for web_task too; add the `wiki.run.web_search.*` log anchor.
6. **Observability** — surface WebSearch availability + `permission_denials` (already at
   runner.py:638-641); new anchor `wiki.run.web_search.enabled`/`.used`.
7. **System prompt** — instruct the web_task run to treat search results as untrusted data,
   never follow instructions embedded in fetched content (defence-in-depth for R-1).
8. **ADR + tests** — ADR for the WebSearch carve-out to D-038; RED→GREEN unit tests that
   (a) web_task reaches the answer runner not the router, (b) the web_task config exposes
   WebSearch while ingest/query configs do not.

## Security analysis (enabling WebSearch in the per-CLI sandboxed scope)

Threat surface and mitigations (full risk table in discovery.md R-1..R-5):

- **M-1 (prompt injection, R-1):** run web_task READ-ONLY (no Write/Edit/MultiEdit) and
  with NO `--add-dir` on the WIKI tree → injected web instructions have no write tool and
  no WIKI files to read/exfiltrate. Strongest mitigation; also architecturally clean (web_task
  = answer, like query).
- **M-2 (SSRF, R-2):** keep WebFetch DENIED. WebSearch is Anthropic-mediated search, not an
  arbitrary-URL fetcher — cannot be aimed at internal IPs / cloud-metadata.
- **M-3 (exfiltration, R-3):** only WebSearch query strings leave, to Anthropic's backend;
  no attacker-controlled sink. The exfil-capable tool (WebFetch) stays denied.
- **M-4 (cost / unbounded egress, R-4):** existing per-call timeout; optional WebSearch
  `max_uses` cap; per-user rate-limit deferred (LATER, only if abused).
- **M-5 (blast radius, R-5):** WebSearch enabled ONLY in the web_task config (intent-scoped);
  ingest/digest/librarian/router/query keep it denied.

The per-CLI systemd-run scope (dedicated `aisw-*` UID, slice) already permits Anthropic-API
egress (the CLI must reach the API). WebSearch adds Anthropic-mediated web egress, NOT a new
raw-socket egress hole — so no new network boundary is opened; the net new capability is
"untrusted web text enters context", fully addressed by M-1.

## Risk classification

**HIGH** — Path B re-enables a tool class (`WebSearch`) that is currently denied for all
runs (a security/permissions change to the D-038 profile) and the feature also adds a new
Stage-0 enum value that every classification now sees (cross-cutting). Per the do-feature
risk×evidence matrix, HIGH risk → `--auto-approve` has no effect → fall back to ask. The
ADR-candidate (D-local-6) independently forces evidence=weak. Stopping at the design gate.

## Verification (TDD, for the eventual implementation run)

- RED: web_task message with router wired currently classifies as unknown→router (filed);
  after enum add it must reach `runner.run`/`run_and_deliver`, not `router.route`.
- web_task `_RunConfig` exposes `WebSearch` in allowed_tools; ingest/query configs do not.
- classifier prompt parses the new enum value; `make total-test` fully green.

## Out of scope

Global WebSearch lift; WebFetch re-enable; per-user web quota; single-theme cwd-scoping.
