# Completion Report — Codex subscription fallback for Claude limits

- **bd_id:** aisw-8gw
- **module:** M-LLM-FAILOVER (NEW), M-LLM-CODEX (NEW), M-CLASSIFIER-STAGE0, M-WIKI-RUNNER, M-WIKI-MIGRATION, M-SCHEDULER-CONSUMER, M-FOUNDATION-SETTINGS, M-RUNTIME-WIRING
- **date:** 2026-07-08
- **decision origin:** ADR-035 — automatic failover to a second LLM provider when the Claude subscription hits its usage limit, without introducing API-key billing

## What changed

Added automatic provider failover: when the Claude Code CLI subscription is exhausted, the bot fails over to Codex CLI authenticated via a ChatGPT subscription (`codex login --device-auth`), never an API key. `M-LLM-FAILOVER` implements one process-local circuit (closed/open/half-open, cooldown) shared across every call site; `M-LLM-CODEX` wraps the Codex CLI under least-privilege, non-interactive, ephemeral invocations with capability-specific sandboxes (`read-only` for classification/query, `workspace-write` scoped to exactly the selected WIKI directory for writes). Lightweight work (Stage-0 classification, schema generation, cron text fallback) routes to `gpt-5.4-mini` at low reasoning; complex WIKI agent runs route to `gpt-5.5` at medium reasoning. A non-blocking startup readiness check (binary present, exact pinned version, ChatGPT-subscription login, required non-interactive CLI flags) gates whether fallback is enabled at all — any readiness failure silently keeps Claude-only behavior instead of crashing startup.

**Production deployment (2026-07-08) found and fixed two bugs invisible to unit tests**, both only reachable by running against a real installed `codex-cli 0.142.5` binary:

1. Readiness checked `codex login status` output on stdout only; `codex-cli 0.142.5` actually prints `Logged in using ChatGPT` to **stderr** with empty stdout. Readiness always failed with `subscription_auth_required` even when correctly authenticated, permanently disabling fallback in production. Fixed in `72d7bff` by matching against both streams.
2. The readiness success branch logged nothing, so operators had no positive journald signal that fallback was actually enabled — only the failure branch (`llm.provider.failed`) existed. Added `llm.provider.ready` (`outcome=fallback_enabled`) in `4a901cb`.

Both were caught by driving the real deployment end-to-end (`codex login status` manually, then `journalctl -u aisw-bot`), not by code review — the unit test's `StubSpawner` fixtures had baked in the same wrong stdout assumption the production code did.

## VPS prerequisites (vpn-2) — first-time setup, now documented in `docs/runbook/deploy.md` §7

Codex's `workspace-write` sandbox depends on Ubuntu 24.04's restricted user namespaces (`kernel.apparmor_restrict_unprivileged_userns=1`), which requires `bubblewrap` plus a loaded AppArmor profile (`bwrap-userns-restrict`) — neither existed on vpn-2 before this deployment. Two host-level issues were found and fixed during setup, independent of this feature's own code:

- `apparmor.service` was already broken on vpn-2 (unrelated orphaned snapd profile referencing a missing directory) and failed to (re)load any profile, including the new one. Fixed by creating the missing stub directory; `apparmor.service` is now `active`.
- `npm` is not installed on vpn-2. Codex CLI 0.142.5 was installed from the official GitHub release binary (`codex-x86_64-unknown-linux-musl.tar.gz`) to `/usr/local/bin/codex` instead of via `npm install -g`; version output is identical either way.

## Files (highlights — 41 files changed across 25 commits, cb7a314..4a901cb)

- `src/ai_steward_wiki/llm/failover.py` (NEW, 404 lines) — provider circuit: state machine, cooldown, evidence-based trip/recovery, replay guard.
- `src/ai_steward_wiki/llm/codex.py` (NEW, 590 lines) — restricted argv/env builders, structured/text/agent execution, JSONL normalization, non-model readiness checks.
- `src/ai_steward_wiki/settings.py` — `llm_codex_enabled`, `codex_cli_binary/version/home`, `codex_light_*`/`codex_complex_*` model+reasoning pairs.
- `src/ai_steward_wiki/classifier/backend.py`, `wiki/schema_gen.py`, `scheduler/consumer.py` — Codex text/structured fallback wired into Stage-0, schema generation, and cron text paths.
- `src/ai_steward_wiki/wiki/runner.py` — safe agent failover for the WIKI-writing CLI path.
- `src/ai_steward_wiki/__main__.py` — one shared provider policy built at startup from non-blocking readiness; injected into classifier, WIKI, schema, and digest/cron adapters; `llm.provider.ready`/`llm.provider.failed` startup anchors.
- `docs/adr/ADR-035-codex-subscription-fallback.md`, `docs/technology.xml`, `docs/superpowers/specs/20260703-codex-subscription-fallback-design.md`, `docs/superpowers/plans/20260705-codex-subscription-fallback-plan.md`.
- `docs/runbook/deploy.md` §7 — install/auth/smoke runbook, including the bwrap/AppArmor sandbox prerequisite.

## Verification (evidence)

- `uv run pytest tests/unit` → exit 0, all green, 88% total coverage (re-verified 2026-07-08 after the readiness fixes); `make lint` (ruff/ruff-format/mypy --strict) → exit 0; `grace lint --profile standard --failOn errors` → 0 issues.
- Fake-CLI subprocess fixtures (`tests/integration/llm/test_fake_codex_cli.py`) validate exact argv, environment allowlist, output parsing, cancellation, timeout, and provider-local event normalization without invoking a real model.
- **Real production smoke on vpn-2 (2026-07-08, runbook §7.2)**, all pass: `gpt-5.4-mini` returns schema-valid structured JSON; `gpt-5.5` completes with a `turn.completed` JSONL event; `read-only` sandbox cannot create a file; `workspace-write` sandbox writes only inside the selected WIKI directory and leaves an outside canary file untouched.
- `aisw-bot` restarted on `4a901cb` (post-fix): journald shows `llm.provider.ready` with `outcome=fallback_enabled`, `binary=/usr/local/bin/codex`, `version=0.142.5`, and no error-level entries through `runtime.polling.start`.

## Known limitations / deferred

- Prod checkout (`/home/bgs/works/ai-steward-wiki` on vpn-2) is currently on this feature branch, not `master` — PR #2 (draft) is open and unmerged; switch prod back to `master` after merge.
- `worktree-aisw-xi8-classifier-v2` (classifier v2.0, unrelated feature) independently claimed ADR-035 for its own decision; that branch's ADR was renumbered to ADR-036 on 2026-07-08 to avoid a collision, since this branch's ADR-035 predates it by 35 minutes (2026-07-05 21:22 vs 21:57).
- Codex quota/cost monitoring beyond the circuit's own cooldown is out of scope for this feature (ADR-035 explicitly defers it).
