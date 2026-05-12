---
feature: inbox-wiki-router
bd_id: aisw-dsg
epic: aisw-t2r
phase: "Inbox-WIKI Phase-A"
status: discovery
created: 2026-05-12
requirements:
  functional:
    - id: FR-1
      text: "On every routable incoming TG message (text/voice transcript/document text/photo), the pipeline materialises the user's Inbox-WIKI (ensure_inbox_wiki) before any Stage-1 run."
    - id: FR-2
      text: "The raw user payload is persisted under <user>/Inbox-WIKI/raw/< utc-ts >_<source>.<ext> (text → .md with the message body; media → a small .md sidecar referencing the already-staged media path, the binary itself is staged by the existing voice/photo handlers and moved into raw/ only in Phase-E)."
    - id: FR-3
      text: "The Stage-1a Claude session for a routable message runs with cwd = <user>/Inbox-WIKI/ and overlay prompt prompts/inbox.md (the Router prompt), not the current flat <wiki_root>/<telegram_id>/ run."
    - id: FR-4
      text: "The Router's free-text reply is parsed into a structured RouterDecision Pydantic model: { target_wiki: str | None, intent: Literal['create_wiki','route','clarify','reject'], notes: str }. Parse failure → a safe RouterDecision(intent='clarify', notes=<fallback>) plus a logged parse-error event (Fail-Fast at the boundary, no crash)."
    - id: FR-5
      text: "RouterDecision.notes is delivered back to the user as the turn reply (so the bot stays useful between Phase-A and Phase-B: it explains where the content would go / asks a clarifying question). No file move, no domain-WIKI ingest, no cron in Phase-A."
    - id: FR-6
      text: "The Router run is gated by Stage-0 intent: only WIKI_INGEST / WIKI_QUERY / UNKNOWN messages go through the Router (these are the 'route this somewhere' intents). REMINDER / DIGEST / WIKI_LINT / ADMIN keep their current handling untouched."
  non_functional:
    - id: NFR-1
      text: "Structlog anchors on every decision point: inbox.router.materialized, inbox.router.staged_raw, inbox.router.run.begin/done, inbox.router.parsed (intent, target_wiki), inbox.router.parse_error — all carrying correlation_id, telegram_id, wiki_id."
    - id: NFR-2
      text: "Router run reuses the existing run_wiki_session machinery (lock manager, kill-sequence, transcript persistence, timeout). Lock id = f'{telegram_id}/Inbox-WIKI' so a router run serialises against itself but not against an unrelated domain-WIKI run."
    - id: NFR-3
      text: "Router run timeout = the configured text timeout (~300s); no new timeout knob in Phase-A."
    - id: NFR-4
      text: "mypy --strict clean, ruff/ruff-format clean, grace lint 0 issues. New MODULE_CONTRACT for the RouterDecision parser; knowledge-graph + verification-plan refreshed."
    - id: NFR-5
      text: "No new third-party dependency. RouterDecision parsing is hand-rolled (regex/line-scan over the documented prompts/inbox.md response format) — no LLM-JSON-mode change in Phase-A."
  constraints:
    - "Ru-only (D-032) — RouterDecision.notes is Russian, no i18n."
    - "All DB datetimes UTC; raw/ filenames use UTC timestamps."
    - "No bypass of pre-commit hooks."
    - "prompts/inbox.md is the SSoT for the Router response format; the parser must follow it, and any format tightening (e.g. fenced key:value block) is a change to that file with a semver bump."
    - "Phase-A is composition + a small new parser module; building blocks (ensure_inbox_wiki, run_wiki_session, Classifier) are unchanged."
  risks:
    - id: R-1
      text: "Between Phase-A and Phase-B merge the bot no longer ingests anything into domain WIKIs — it only echoes the Router's notes. Mitigation: FR-5 keeps it conversational; the epic sequences B right after A; optionally A ships behind a settings flag (decided in Brainstorming)."
    - id: R-2
      text: "Free-text Router output is brittle to parse. Mitigation: FR-4 safe fallback to 'clarify'; consider tightening prompts/inbox.md to emit a fenced, machine-parseable block (Brainstorming decision); unit tests over real transcript samples."
    - id: R-3
      text: "Double-run cost/latency if the old flat Stage-1 run is kept alongside the new Router run. Mitigation: Phase-A replaces the flat run for the routable intents (FR-3/FR-6), it does not add a second run."
    - id: R-4
      text: "raw/ directory growth (one .md per message). Mitigation: out of scope for Phase-A; retention sweep of Inbox-WIKI/raw/ is a later ops task (note in epic), not a blocker."
  scope:
    in:
      - "ensure_inbox_wiki call wired into the pipeline path."
      - "Raw payload staging into Inbox-WIKI/raw/ (text + media sidecar)."
      - "Router Stage-1a run in Inbox-WIKI/ cwd with prompts/inbox.md."
      - "New RouterDecision model + parser module (e.g. src/ai_steward_wiki/inbox/router.py)."
      - "RouterDecision.notes delivered as the turn reply."
      - "Intent gate (FR-6)."
      - "Unit tests (parser, staging path, intent gate) + an integration test that Claude really runs in Inbox-WIKI/ cwd."
    out:
      - "Domain-WIKI lookup/create + file move + Stage-1b librarian ingest (Phase-B / aisw-zd9)."
      - "Inline-button confirmation loop (Phase-C / aisw-e45)."
      - "Router → cron/job bridge (Phase-D / aisw-kcz)."
      - "## Inbox hint fast-path + per-user media _staging migration (Phase-E / aisw-12t)."
    later:
      - "Retention sweep for Inbox-WIKI/raw/."
      - "Possible switch of prompts/inbox.md to a strict machine-readable response envelope (if Brainstorming defers it)."
---

# Discovery — Inbox-WIKI Phase-A: stage raw content + Router-Claude invocation

**bd:** `aisw-dsg` (epic `aisw-t2r`) · **date:** 2026-05-12

## Preflight (clean)

1. Pre-commit infra alive: `core.hooksPath = .beads/hooks`, `.beads/hooks/pre-commit` bootstraps `pre-commit run` against `.pre-commit-config.yaml` (trailing-whitespace, eof-fixer, check-yaml/toml, large-files, ruff `--fix`, ruff-format, mypy --strict src, gitleaks).
2. Lint baseline: `make lint` ✅ — ruff `All checks passed!`, ruff-format `172 files already formatted`, mypy `Success: no issues found in 66 source files`.
3. `grace lint --failOn errors` ✅ — 66 governed files, 3 XML, 0 issues.
4. Sentrux: `.sentrux/rules.toml` absent → preflight skipped (project not onboarded).

## What user said / what they mean

- Literal: "wire Inbox-WIKI Phase-A via feature-workflow."
- Real goal: start composing the documented smart-inbox-routing layer (D-004, D-016, D-022, `docs/Spec-WIKI/concepts/smart-inbox-routing.md`) into the live pipeline. Today the pipeline classifies intent then runs Claude in a flat per-user dir and replies — no routing, no Inbox-WIKI, no domain WIKIs. Phase-A is the first slice: get content *into* Inbox-WIKI and run the Router agent there, producing a structured decision the later phases consume.
- Unstated assumption to surface: a live bot must not regress to "does nothing useful" between phase merges → hence FR-5 (echo the Router's notes) and risk R-1.

## Current state (grounded)

- `src/ai_steward_wiki/inbox/materialize.py` — `ensure_inbox_wiki(user_id, *, wiki_root, template_path) -> Path` exists, idempotent atomic write of `<wiki_root>/<user_id>/Inbox-WIKI/CLAUDE.md` from `templates/inbox-wiki/CLAUDE.md`. **Not referenced from `__main__.py` or `tg/pipeline.py`.**
- `prompts/inbox.md` (semver 1.0.0) — Router overlay prompt; documents a response format with `target_wiki`, `intent: create_wiki | route | clarify | reject`, `notes` — currently as free-text Markdown, not a strict envelope.
- `templates/inbox-wiki/CLAUDE.md` (3.9K) — materialised Inbox-WIKI router contract.
- `src/ai_steward_wiki/wiki/runner.py` — `run_wiki_session(*, wiki_id, wiki_path, base_prompt_path, overlay_prompt_path, run_id, correlation_id, runtime_dir, acquirer, spawner, config, on_event, user_input, media_paths, timeout_s)` — generic; works for any wiki_path/overlay.
- `src/ai_steward_wiki/__main__.py` — `_WikiRunnerAdapter.run` hard-codes `wiki_id = str(owner_telegram_id)`, `wiki_path = wiki_root / wiki_id`, overlay = a stable placeholder `# User turn`. Comment: *"proper Inbox staging lands in chunks 21+"* — never landed.
- `src/ai_steward_wiki/tg/pipeline.py` `_run_text_pipeline` — L2 dedup → `Classifier.classify` → `self._runner.run(text, owner_telegram_id, intent, media_paths, timeout_s)` → deliver. Same path for text/voice/document/photo.
- `src/ai_steward_wiki/classifier/schema.py` `Intent` enum: `REMINDER, WIKI_INGEST, WIKI_QUERY, WIKI_LINT, DIGEST, ADMIN, UNKNOWN`.

## Resolved design questions (via /questions-answers, 2026-05-12)

1. **Replace vs flag** → REPLACE, no flag. Bot is not deployed (cutover-checklist empty); a toggle is YAGNI. Router run replaces the flat Stage-1 run for `intent ∈ {WIKI_INGEST, WIKI_QUERY, UNKNOWN}`; other intents unchanged. Epic note: do not deploy `master` with Phase-A until `aisw-zd9` (Phase-B) is closed.
2. **Router output format** → TIGHTEN. `prompts/inbox.md` bumped to `1.1.0`; Router must answer with exactly one fenced block ```` ```router\ntarget_wiki: <name|null>\nintent: <route|create_wiki|clarify|reject>\nnotes: <ru text>\n``` ````. Parser extracts the block, parses `key: value`; missing/malformed block → `RouterDecision(intent="clarify", notes=raw[:N])` + logged `inbox.router.parse_error`.
3. **Parser location** → NEW MODULE `src/ai_steward_wiki/inbox/router.py` — `RouterIntent` enum, `RouterDecision` Pydantic model, `parse_router_reply(text) -> RouterDecision`. Own MODULE_CONTRACT; added to knowledge-graph + verification-plan.
4. **Adapter shape** → SEPARATE `_RouterAdapter` in `__main__.py` + narrow `Router` Protocol consumed by the pipeline (`async def route(*, text, telegram_id, correlation_id, source, media_paths, timeout_s) -> RouterDecision`). Internals: `ensure_inbox_wiki` → stage raw payload → `run_wiki_session(wiki_id=f"{telegram_id}/Inbox-WIKI", wiki_path=inbox_dir, overlay_prompt_path=prompts/inbox.md, …)` → `parse_router_reply`. `_WikiRunnerAdapter` untouched. Pipeline `is_wired` accounts for `router` on the routable branch.
5. **Media raw staging** → SIDECAR ONLY in Phase-A. For `source ∈ {voice, photo, document}` write `Inbox-WIKI/raw/<utc-ts>_<source>.md` with YAML front-matter (`source`, `staged_path`, voice→transcript, document→filename+mime); the binary stays in `media_staging_root`. `media_paths` are still forwarded to the Router for vision/Read. Binary → `Inbox-WIKI/raw/media/` is Phase-B/Phase-E.

## Dependencies / blast radius

- Touches: `tg/pipeline.py`, `__main__.py`, new `inbox/router.py`, possibly `prompts/inbox.md` (semver bump), tests under `tests/unit/inbox/`, `tests/unit/tg/`, `tests/integration/`.
- Unchanged: `ensure_inbox_wiki`, `run_wiki_session`, `Classifier`, lock manager, scheduler.
- Downstream: Phase-B (`aisw-zd9`) consumes `RouterDecision`; Phase-C/D/E build on B.
