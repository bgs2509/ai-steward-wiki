---
feature: inbox-wiki-route-ingest
bd_id: aisw-zd9
epic: aisw-t2r
phase: "Inbox-WIKI Phase-B"
status: discovery
created: 2026-05-12
requirements:
  functional:
    - id: FR-1
      text: "After Phase-A's Router returns a RouterDecision with intent ∈ {ROUTE, CREATE_WIKI}, the pipeline resolves a concrete target <Domain>-WIKI for the owner: CREATE_WIKI → WikiLifecycleManager.create_wiki(owner, decision.target_wiki, template_id); ROUTE → lifecycle.lookup(owner, decision.target_wiki), and if the named WIKI does not exist, create it (the Router asserted it belongs there) — with a logged warning."
    - id: FR-2
      text: "AntiSpamCapError on create (owner at wiki cap) → no ingest; reply with decision.notes + a ru hint that the WIKI cap is reached; log inbox.route.cap_reached. WikiNameError on a malformed Router-proposed name → reply decision.notes + a ru 'не смог разобрать имя вики' hint; log inbox.route.bad_name."
    - id: FR-3
      text: "The Phase-A raw payload (Inbox-WIKI/raw/<ts>_<source>.md sidecar, and for media the staged binary in media_staging_root) is moved into the target WIKI's raw/ before the Stage-1b run: text sidecar → <Domain>-WIKI/raw/<ts>_<source>.md; media binary → <Domain>-WIKI/raw/media/<ISO8601>_<sha8>.<ext> via inbox.staging.promote_path_to_raw(wiki_root=<Domain>-WIKI dir). On Stage-1b failure the moved raw stays in <Domain>-WIKI/raw/ (logged inbox.route.ingest_failed) — a future retry/sweep re-ingests; no rollback in Phase-B."
    - id: FR-4
      text: "A Stage-1b 'librarian' session runs IN the target WIKI: run_wiki_session(wiki_id=f'{owner}/{primary}', wiki_path=<Domain>-WIKI dir, base_prompt_path=prompts/wiki.md, overlay_prompt_path=prompts/domain-<slug>.md if it exists else prompts/domain-default.md, user_input=<ingest instruction referencing the moved raw file path(s) + the original user text>, media_paths=[<promoted binary>] when media, timeout_s=<configured text timeout, or vision timeout for media>). The aggregated assistant text is the ingest summary."
    - id: FR-5
      text: "The user reply on a successful route+ingest = decision.notes + '\\n\\n' + the Stage-1b ingest summary (so the user sees both 'куда' and 'что записал'). On a Stage-1b WikiRunnerError → reply decision.notes + a ru 'не удалось разложить — попробую позже' hint (the raw stays in the target WIKI). intent ∈ {CLARIFY, REJECT} keeps Phase-A behaviour (reply decision.notes, no WIKI touched)."
    - id: FR-6
      text: "Structlog anchors: inbox.route.target_resolved (owner, target_wiki, created: bool), inbox.route.cap_reached, inbox.route.bad_name, inbox.route.raw_moved (src, dest), inbox.route.ingest.begin/done (wiki_id, run_id, latency_ms, chars), inbox.route.ingest_failed — all with correlation_id, telegram_id."
  non_functional:
    - id: NFR-1
      text: "Reuses WikiLifecycleManager (create/lookup), run_wiki_session (lock manager, kill-sequence, transcript persistence), inbox.staging.promote_path_to_raw — no reimplementation. mypy --strict / ruff / ruff-format / grace lint clean; coverage stays ≥80%."
    - id: NFR-2
      text: "No new third-party dependency. No new SQLite table (Phase-B is filesystem + Stage-1b only; pending-action persistence is Phase-C)."
    - id: NFR-3
      text: "Lock discipline: the Stage-1b run takes the per-WIKI lock keyed on the target WIKI (wiki_id=f'{owner}/{primary}'), independent of the Inbox-WIKI lock used by the Stage-1a router run in Phase-A."
    - id: NFR-4
      text: "Ru-only (D-032) — all user-facing strings Russian. All datetimes UTC. No bypass of pre-commit hooks. Per-WIKI git auto-commit (M-OPS-BACKUP) is NOT wired by Phase-B (separate concern); raw moves and Stage-1b page writes are plain filesystem ops."
  constraints:
    - "Building blocks (WikiLifecycleManager, run_wiki_session, promote_path_to_raw, RouterDecision, prompts/wiki.md + prompts/domain-*.md) are unchanged in their public API; Phase-B composes them."
    - "Phase-B auto-executes route+ingest (no user confirmation) — the inline-button confirm loop is Phase-C (aisw-e45). Phase-B is a strict prerequisite for C/D."
    - "template_id for create_wiki: Phase-B uses a single default (templates/_default.md → template_id='_default'); a domain→template mapping (health/budget/…) is a later refinement, NOT Phase-B (the Router emits only a name, not a template id)."
    - "WikiLifecycleManager.create_wiki currently writes a minimal CLAUDE.md (frontmatter only, empty template_sha256) and does NOT render the full template body — pre-existing behaviour, NOT fixed by Phase-B."
  risks:
    - id: R-1
      text: "Router proposes a target_wiki that doesn't exist on a ROUTE intent (hallucinated name). Mitigation: FR-1 treats ROUTE-with-missing-target as create (Router said it belongs there) + logs a warning; alternative (reject/clarify) is heavier and overlaps Phase-C — decided in Brainstorming."
    - id: R-2
      text: "Moving the raw payload BEFORE Stage-1b means a failed run leaves an un-ingested file in <Domain>-WIKI/raw/. Mitigation: it's content-addressed for media (idempotent on retry); the text sidecar is a small audit artifact; FR-3 logs the failure; a retry/sweep job is a later ops task. Alternative (copy → ingest → delete-Inbox-copy-on-success) considered in Brainstorming."
    - id: R-3
      text: "Auto-executing a WIKI mutation from a single user message (no confirm) until Phase-C lands. Mitigation: the WIKI is the user's own private sandbox; Stage-1b only does atomic page writes + log.md append (per prompts/wiki.md); per-WIKI git history (when wired) gives undo; acceptable for an MVP slice; Phase-C adds the gate."
    - id: R-4
      text: "Two Claude CLI invocations per routable message now (Stage-1a router + Stage-1b librarian) — latency + token cost roughly double for routable turns. Mitigation: accepted (the spec's two-stage design, §8.3.3); the ## Inbox hint fast-path (Phase-E) skips Stage-1a on confident hints; Phase-B does not add a third run."
  scope:
    in:
      - "Resolve/create the target <Domain>-WIKI from RouterDecision (via WikiLifecycleManager)."
      - "Move the Phase-A raw payload (text sidecar + media binary) into <Domain>-WIKI/raw/."
      - "Stage-1b librarian run in the target WIKI (run_wiki_session + prompts/wiki.md + prompts/domain-*.md overlay)."
      - "Pipeline orchestration: router.route() → if ROUTE/CREATE_WIKI → resolve+move+ingest → reply notes + summary; else reply notes."
      - "A new runtime adapter + narrow Protocol for the Stage-1b ingest step; possibly a thin inbox/route.py helper module for target-resolution + raw-move."
      - "Unit tests (target resolution incl. cap/bad-name/missing-on-route, raw move, adapter ingest, pipeline two-step branch) + an integration scenario (routable text → real Stage-1a + Stage-1b → page written in <Domain>-WIKI)."
      - "GRACE: new module(s) + verification refs + dep/CrossLink updates + dev-plan Phase-B entry."
    out:
      - "Inline-button confirmation loop before executing (Phase-C / aisw-e45)."
      - "Router → cron/job bridge (Phase-D / aisw-kcz)."
      - "## Inbox hint fast-path + per-user media _staging migration (Phase-E / aisw-12t)."
      - "Domain → template_id mapping for create_wiki (later refinement)."
      - "Full template-body rendering in create_wiki (pre-existing gap)."
      - "Per-WIKI git auto-commit on ingest (M-OPS-BACKUP wiring — separate concern)."
      - "Retry/sweep of un-ingested files left in <Domain>-WIKI/raw/ (later ops task)."
    later:
      - "Restore-from-trash on a ROUTE to a soft-deleted name (Phase-C/lifecycle UX)."
      - "Smarter ingest prompt (page-structure hints per domain)."
---

# Discovery — Inbox-WIKI Phase-B: RouterDecision → domain WIKI select/materialise + move + Stage-1b ingest

**bd:** `aisw-zd9` (epic `aisw-t2r`) · **date:** 2026-05-12

## Preflight (clean)

1. Pre-commit infra alive (`core.hooksPath = .beads/hooks` bootstraps `pre-commit run` against `.pre-commit-config.yaml`).
2. `make lint` ✅ — ruff/ruff-format clean, mypy 67 files 0 errors.
3. `grace lint --failOn errors` ✅ — 0 issues.
4. Sentrux: `.sentrux/rules.toml` absent → skipped.

## What user said / what they mean

- Literal: "wire Inbox-WIKI Phase-B via feature-workflow."
- Real goal: close the loop opened by Phase-A — turn a `RouterDecision` into an actual content move + ingest into a domain WIKI, so the bot finally *does* something with what the user sends (not just echoes the Router's notes). This is the slice that lifts the epic from "smart classify" to "smart store".
- Unstated: must not regress Phase-A's CLARIFY/REJECT behaviour; must not require confirmation yet (that's Phase-C); the two-Claude-runs latency is accepted.

## Current state (grounded)

- `src/ai_steward_wiki/inbox/router.py` (Phase-A) — `RouterDecision(intent, target_wiki, notes, raw, parsed_ok)`, `RouterIntent ∈ {route, create_wiki, clarify, reject}`, `RouterError`.
- `src/ai_steward_wiki/__main__.py::_RouterAdapter` (Phase-A) — `route(...) -> RouterDecision`; materialises Inbox-WIKI, stages raw sidecar into `Inbox-WIKI/raw/`, runs Stage-1a in `Inbox-WIKI/` with `prompts/inbox.md`. `tg/pipeline.py` routes `WIKI_INGEST/WIKI_QUERY/UNKNOWN` to `router.route()` and replies `decision.notes`.
- `src/ai_steward_wiki/wiki/lifecycle.py::WikiLifecycleManager` — `create_wiki(owner, raw_name, template_id) -> WikiName` (idempotent on existing, anti-spam cap, Levenshtein near-dup → returns existing if active), `lookup(owner, name) -> WikiName | None`, `list_active`, `soft_delete`, `restore`. Layout `<wiki_root>/<owner>/<Name>-WIKI/`. `WikiName(primary, hyphenated_lookup, slug)`. `AntiSpamCapError`, `WikiNotFoundError`. `create_wiki` writes a minimal frontmatter CLAUDE.md (no template body).
- `src/ai_steward_wiki/wiki/runner.py::run_wiki_session(*, wiki_id, wiki_path, base_prompt_path, overlay_prompt_path, run_id, correlation_id, runtime_dir, acquirer, spawner, config, on_event, user_input, media_paths, timeout_s)` — generic Stage-1a/1b; `aggregate_text(result.events)` extracts the reply.
- `src/ai_steward_wiki/inbox/staging.py::promote_path_to_raw(staging_path, *, wiki_root, now=None) -> Path` — atomic move into `<wiki_root>/raw/media/<ISO8601>_<sha8>.<ext>`, content-addressed, idempotent.
- `prompts/wiki.md` (semver 1.0.0) — Stage-1a/1b base prompt (Karpathy ingest/query/lint, log.md append, atomic edits). `prompts/domain-default.md`, `prompts/domain-health.md`, `prompts/domain-finance.md` (semver 1.0.0) — Stage-1b domain overlays. `templates/_default.md` + domain templates exist.
- No Stage-1b "ingest" wiring exists yet — `_WikiRunnerAdapter` runs Stage-1 in the *flat* `<wiki_root>/<telegram_id>/` dir for non-routable intents; nothing runs a librarian session in a `<Domain>-WIKI/`.

## Resolved design questions (via /questions-answers, 2026-05-12)

1. **ROUTE to a missing target** → AUTO-CREATE. `intent=ROUTE & lookup→None` → `WikiLifecycleManager.create_wiki(owner, decision.target_wiki, "_default")` + log `inbox.route.route_target_was_missing` (warning); then proceed as CREATE_WIKI. `create_wiki`'s built-in Levenshtein near-dup already absorbs typos.
2. **Raw move timing** → MOVE BEFORE Stage-1b. `promote_path_to_raw` (media) + `os.replace` (text sidecar) → `<Domain>-WIKI/raw/` first, then `run_wiki_session`. On `WikiRunnerError` the moved raw stays there un-ingested (logged `inbox.route.ingest_failed`); retry/sweep is a later ops task. Forward-only + idempotent-retry pattern; reuses `promote_path_to_raw` as-is.
3. **Where the new logic lives** → NEW `src/ai_steward_wiki/inbox/route.py` (pure helpers: `RouteTarget` dataclass, `RouteRejection` variant, `resolve_target_wiki(decision, lifecycle, owner, *, default_template_id) -> RouteOutcome`, `move_raw_into_wiki(inbox_dir, wiki_dir, *, source, media_paths) -> MovedRaw`, `build_ingest_prompt(user_text, moved_raw) -> str`) + `_LibrarianAdapter` in `__main__.py` behind a narrow `Librarian` Protocol (`ingest(decision, *, telegram_id, user_text, source, media_paths, timeout_s, correlation_id) -> IngestOutcome`). Pipeline orchestrates `router.route() → if intent ∈ {ROUTE, CREATE_WIKI} and self._librarian → librarian.ingest(...)`. Mirrors Phase-A; gives Phase-C a clean confirm-gate seam between `route()` and `ingest()`.
4. **User reply composition** → `decision.notes + "\n\n" + ingest_summary` on success, delivered via `deliver_output` (hybrid-size policy; `run_id` from `run_wiki_session`). Stage-1b `WikiRunnerError` → `send_message(decision.notes + "\n\nНе удалось разложить по полочкам — попробую позже.")` (raw stays in the target WIKI). Cap/bad-name (FR-2) → `send_message(decision.notes + <ru hint>)`, no ingest. `CLARIFY/REJECT` → unchanged Phase-A behaviour.
5. **template_id for create_wiki** → ALWAYS `"_default"` in Phase-B. Domain→template mapping + full template-body rendering = a later refinement (right place: extend the Router to emit a template id, or a separate domain classifier — not Phase-B).
6. **Stage-1b timeout** → ALWAYS `timeout_s=None` (the configured text timeout, ~300s), regardless of source. Stage-1b is a full WIKI turn (read input + atomic page edits + log.md append), not a short vision-classify; the D-022 vision timeout (~30s) would falsely kill legitimate ingests. No new timeout knob.

## Dependencies / blast radius

- Touches: `tg/pipeline.py` (two-step branch after `router.route()`), `__main__.py` (new `_LibrarianAdapter` + wiring), new `inbox/route.py`, tests, GRACE XMLs.
- Unchanged public API: `WikiLifecycleManager`, `run_wiki_session`, `promote_path_to_raw`, `RouterDecision`, prompts.
- Downstream: Phase-C (`aisw-e45`) wraps the resolve+move+ingest behind a confirm gate; Phase-D builds on the same orchestration point for cron.
- Note: epic `aisw-t2r` — Phase-A+B together are the deployable unit; once `aisw-zd9` closes the "do not deploy master" caveat from Phase-A is lifted.
