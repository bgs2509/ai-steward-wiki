---
feature: web-gap
bd_id: aisw-dqz
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-26
risk: high
evidence: strong
open_questions:
  - OQ-1: Approve enabling the WebSearch tool for web_task runs (Path B)? This is a security/permissions change that --auto-approve cannot waive (HIGH risk). Needs an explicit human yes/no.
fr:
  - FR-1: A "найди в интернете …" / "search online for …" message MUST be recognised by Stage-0 as a NEW intent web_task — today it has no mode and falls to unknown -> Inbox Router -> filed (verified cycle-1).
  - FR-2: A web_task message MUST be ANSWERED in chat (assistant text delivered), NOT filed into a WIKI and NOT shown a Confirm/Cancel-to-file keyboard.
  - FR-3 (Path B, gated): To answer questions that require live web content (current recipes/prices/news), the web_task run MUST be allowed to use the WebSearch tool, which is currently denied for ALL WIKI/CLI runs (verified — produces wiki.run.permission_denied denied_tools=["WebSearch"]).
  - FR-4: Enabling WebSearch MUST be scoped to web_task runs ONLY. The ingest / digest / librarian / router / query run configs MUST keep WebSearch denied.
  - FR-5: WebFetch MUST remain denied for ALL runs including web_task (it is the SSRF / arbitrary-URL vector; runner.py:451 disallowed_tools default).
  - FR-6: All other intents (reminder, wiki_ingest, wiki_query, wiki_lint, digest, admin, unknown) MUST keep their current behaviour — only the new web_task intent is added.
nfr:
  - NFR-1: mypy --strict + ruff + ruff format + grace lint clean; make total-test fully green (coverage >=80%).
  - NFR-2: Ru-only user-facing strings (D-032).
  - NFR-3: TDD — a failing unit test reproduces the gap (web_task reaches an answer runner with WebSearch allowed, not the router/filing path) before the fix.
  - NFR-4 (security): web_task runs MUST be observable — a structured log anchor recording WebSearch availability + any web egress, and permission_denials surfaced as today (runner.py:638-641).
  - NFR-5 (security): web_task answer runs SHOULD be read-only (no WRITE_TOOLS) and SHOULD NOT receive --add-dir on the user's WIKI tree, to neutralise the prompt-injection -> data-exfiltration / file-corruption chain from untrusted web content.
constraints:
  - WebSearch is denied today NOT by an explicit --disallowedTools entry but by the dontAsk allowlist model — verified path below.
  - Stage-0 intent enum is a CLOSED list (classifier/schema.py:43 Intent + prompts/classifier.md output schema, semver 1.1.0). Adding a value is a cross-cutting classifier-prompt change: every classification request now sees the new option, so it MUST be added in BOTH the Python enum and the prompt, and the prompt semver bumped (prompt_sha256 is recorded per result, so the change is auditable).
  - The Stage-1a Inbox Router (inbox/router.py RouterIntent) only has FILING outcomes (route/create_wiki/clarify/reject) — there is NO answer outcome there. Answering happens on the generic fall-through runner (pipeline.py:1404+), the same path the sibling query-gap (aisw-50z) uses.
  - The _WikiRunnerAdapter (__main__.py:413) holds ONE fixed _RunConfig (allowed_tools=WRITE_TOOLS, __main__.py:1240). The intent already threads through WikiRunner.run / StreamingDelivery.run_and_deliver (pipeline.py:679,702), so a per-intent (web_task) run config can be selected WITHOUT any Protocol signature change.
risks:
  - R-1 (HIGH, prompt injection): WebSearch results bring untrusted web text into the model context. On the current generic runner the run has WRITE_TOOLS + --add-dir on the user root, so an injected instruction in fetched content could try to corrupt/exfiltrate WIKI files. Mitigation — run web_task READ-ONLY with no WIKI --add-dir (NFR-5); treat search results as untrusted in the system prompt.
  - R-2 (MEDIUM, SSRF): mitigated by keeping WebFetch denied (FR-5) — WebSearch is Anthropic-mediated search, not an arbitrary-URL fetcher, so it cannot be pointed at internal IPs / cloud-metadata endpoints.
  - R-3 (MEDIUM, exfiltration): WebSearch query strings are the only outbound channel and go to Anthropic's search backend (not an attacker endpoint); the dangerous exfil tool (WebFetch POST/GET to arbitrary URL) stays denied.
  - R-4 (LOW/MEDIUM, cost / unbounded egress): each web_task run incurs search cost + latency. Mitigation — per-call timeout (existing), optional WebSearch max_uses cap, per-user rate-limit (LATER if abused).
  - R-5 (scope creep): enabling WebSearch globally instead of per-intent would widen the deny surface for every run. Mitigation — FR-4 scopes it to the web_task config only.
scope_in:
  - src/ai_steward_wiki/classifier/schema.py — add Intent.WEB_TASK to the closed enum.
  - prompts/classifier.md — add web_task semantics + bump semver (prompt_sha256 auditable).
  - src/ai_steward_wiki/__main__.py — a web_task-scoped _RunConfig (WebSearch allowed, read-only, no WIKI add-dir) selected by intent in _WikiRunnerAdapter.run.
  - src/ai_steward_wiki/wiki/runner.py — likely a WEB_SEARCH_TOOLS constant + (optional) plumbing for the read-only / add-dir-suppressed web_task run.
  - tests/unit — RED->GREEN test that web_task reaches an answer runner with WebSearch allowed, not the router/filing path; classifier-prompt enum coverage.
scope_out:
  - Lifting WebSearch for any run other than web_task.
  - Re-enabling WebFetch (stays denied).
  - Per-user web rate-limiting / quota (LATER, only if abused).
  - Single-theme cwd-scoping (same deferral as query-gap).
scope_later:
  - Path A can ship alone (web_task answers from model knowledge, WebSearch still denied) to fix the "filed instead of answered" failures; Path B (WebSearch enabled) lands only after explicit human security approval (OQ-1).
---

# Discovery — web-gap: web_task mode + WebSearch policy

## The verified gap

"найди в интернете рецепт …" has **no Stage-0 mode**. Stage-0's intent enum
(`classifier/schema.py:43`, `prompts/classifier.md` semver 1.1.0) is a closed list of
seven values — `reminder, wiki_ingest, wiki_query, wiki_lint, digest, admin, unknown` —
none of which means "search the web and answer". The message therefore classifies as
`unknown`, which is in `_ROUTABLE_INTENTS` (`pipeline.py:585`) and is funnelled to the
Inbox Router → confirm-to-file. Cycle-1: 5/7 `web_task__*` scenarios FAIL — either
`wiki.run.permission_denied denied_tools=["WebSearch"]` or filed instead of answered.

## Where WebSearch is denied today (file:line, verified)

There is **no explicit `--disallowedTools WebSearch`**. The denial is the combination of:

1. `wiki/runner.py:395-396` — every run is launched with `--permission-mode dontAsk`.
2. `wiki/runner.py:398-399` — when `allowed_tools` is set, the runner passes
   `--allowedTools <list>`. For the generic answer runner that list is
   `WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]` (`wiki/runner.py:151`), wired at
   `__main__.py:1240` (`allowed_tools=WRITE_TOOLS`). Under `dontAsk`, read-only tools
   (Read/Glob/Grep) are auto-granted, but **WebSearch is not auto-granted and is not in
   the allowlist → the permission layer refuses it**, emitting `permission_denials`
   with `tool_name="WebSearch"` (surfaced at `wiki/runner.py:638-641`).
3. `wiki/runner.py:451` — `disallowed_tools` defaults to `["WebFetch"]`, an explicit
   belt-and-suspenders deny of WebFetch (the arbitrary-URL fetcher).

Spec basis: D-038 / D-013 (`docs/Spec-WIKI/decisions/`) — "allow file tools, deny
Bash/WebFetch/Read(auth-dir), permission-mode dontAsk".

**To enable WebSearch for web_task:** add `"WebSearch"` to the `allowed_tools` of a
web_task-scoped run config. No other change unlocks it.

## Preflight

Design-only run (STOP at the design gate per HIGH-risk matrix). Full `make lint` /
Sentrux baseline deferred to the implementation run; no code touched this session.
