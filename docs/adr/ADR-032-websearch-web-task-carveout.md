# ADR-032: WebSearch carve-out for the `web_task` intent (amends D-038)

- Status: Accepted
- Date: 2026-06-26
- Deciders: @bgs (explicit security approval, Path B)
- bd_id: aisw-dqz
- Amends: [D-038](../Spec-WIKI/decisions/D-038-per-user-systemd.md) (runtime permission profile), [D-013](../Spec-WIKI/decisions/D-013-claude-cli-auth.md)

## Context

D-038 / D-013 lock every Claude CLI run (Stage-1a/1b WIKI runner, router, classifier,
librarian, digest) to a `--permission-mode dontAsk` profile that allows file tools and
**denies `Bash`, `WebFetch`, and `Read(auth-dir)`**. Under that profile WebSearch is not
auto-granted and is absent from the per-run `--allowedTools` list, so the permission layer
refuses it — surfacing as `wiki.run.permission_denied denied_tools=["WebSearch"]`
(`wiki/runner.py` permission-denial path).

Cycle-1 e2e: "найди в интернете рецепт …" had **no Stage-0 mode**, classified as `unknown`,
was funnelled to the Inbox Router and **filed** instead of answered; the subset that did
reach a run hit the WebSearch denial. 5/7 `web_task__*` scenarios FAIL.

To answer find-on-internet requests with live web content, a run must be allowed to use the
WebSearch tool — a re-enable of a currently-denied tool class, i.e. a security/permissions
change. Per the do-feature risk×evidence matrix this is HIGH risk; `--auto-approve` has no
effect and the change requires explicit human security approval.

## Decision

Enable the **WebSearch** tool for the new `Intent.WEB_TASK` runs **only**, behind these
locked mitigations (human-approved 2026-06-26, "Path B"):

1. **M-5 intent-scoped, never global.** A dedicated `web_run_config` (`__main__.py`
   `_WikiRunnerAdapter`) with `allowed_tools=WEB_SEARCH_TOOLS` (`["WebSearch"]`) is selected
   only when `intent is Intent.WEB_TASK`. Ingest / digest / librarian / router / query
   configs are unchanged and keep WebSearch denied.
2. **M-1 read-only + no WIKI access.** The web_task config carries **no `WRITE_TOOLS`**
   (no Write/Edit/MultiEdit) and `web_search=True`, which makes the runner OMIT `--add-dir`
   on the WIKI tree and run in a dedicated **empty neutral cwd** (`runtime/web_task_cwd`).
   Untrusted web content therefore has no write tool and no WIKI files to read or exfiltrate.
3. **M-2 SSRF guard.** `WebFetch` stays in `disallowed_tools` for the web_task config too,
   so the model cannot fetch arbitrary URLs (internal IPs / cloud-metadata). WebSearch is
   Anthropic-mediated search, not an arbitrary-URL fetcher.
4. **M-3 exfiltration.** The only outbound channel is the WebSearch query string to
   Anthropic's search backend — no attacker-controlled sink; the exfil-capable tool
   (WebFetch) stays denied.
5. **M-4 cost / egress.** Per-call timeout (existing `_RunConfig.timeout_s`) bounds each run;
   a per-user web rate-limit / `max_uses` cap is deferred (LATER, only if abused).

Routing: `Intent.WEB_TASK` is intentionally **not** in `_ROUTABLE_INTENTS` and is not a
reminder/digest/admin fast-path, so it falls through to the generic answer runner and is
answered in chat (never filed).

Observability: a `wiki.run.web_search.enabled` structured log anchor records that a run was
launched with the WebSearch carve-out; the existing `wiki.run.permission_denied` surfacing
is unchanged.

## Alternatives considered

1. **Path A only — web_task answers from model knowledge, WebSearch still denied.** Rejected
   as the sole solution: fixes only the "filed instead of answered" subset; cannot serve
   live/volatile content (prices, news, current recipes). Kept as the safe fallback when no
   `web_run_config` is wired (`web_run_config=None`).
2. **Enable WebSearch globally (all runs).** Rejected: widens the deny surface for every run
   including writing runs that hold WRITE_TOOLS + WIKI add-dir — exactly the prompt-injection
   → exfiltration chain M-1 closes. Violates least privilege.
3. **Re-enable WebFetch instead of / alongside WebSearch.** Rejected: WebFetch is the SSRF /
   arbitrary-URL / exfiltration vector. There is no read-only-proxy middle option in the CLI
   permission model without building a new egress component (out of scope).

## Consequences

Positive:
- find-on-internet requests are answered in chat with live web content.
- Least privilege preserved: WebSearch is reachable only on a read-only, WIKI-isolated,
  intent-scoped run; every other run is unchanged.
- WebFetch stays denied everywhere — SSRF surface unchanged.

Negative / risks:
- web_task runs ingest untrusted web text into the model context (indirect prompt injection).
  Residual risk is bounded by M-1 (no write tool, no WIKI files) — a successful injection can
  only influence the chat answer, not persist or exfiltrate WIKI data.
- Added per-run WebSearch cost/latency; no hard per-user cap yet (M-4 deferred).

## Sources

- D-038 per-user systemd permission profile; D-013 Claude CLI auth (`docs/Spec-WIKI/decisions/`).
- `wiki/runner.py` — `--permission-mode dontAsk` + `--allowedTools` allowlist model; permission-denial extraction.
- Human security approval, 2026-06-26 (do-feature design gate, Path B).
