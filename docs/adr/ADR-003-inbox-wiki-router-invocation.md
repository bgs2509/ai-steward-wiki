# ADR-003: Inbox-WIKI Stage-1a router invocation — dedicated adapter + fenced-block reply contract

**Status:** accepted
**Date:** 2026-05-12
**Context:** [D-004](../Spec-WIKI/decisions/D-004-inbox-wiki-scope.md), [D-016](../Spec-WIKI/decisions/D-016-inbox-claude-md-template.md), [D-022](../Spec-WIKI/decisions/D-022-voice-photo-input.md), [smart-inbox-routing](../Spec-WIKI/concepts/smart-inbox-routing.md); bd `aisw-dsg` (Inbox-WIKI Phase-A), epic `aisw-t2r`. Discovery/design/plan: `docs/superpowers/specs|plans/20260512-inbox-wiki-router-*.md`.

## Context

The pipeline classified Stage-0 intent and then ran Claude in a flat per-user dir (`<wiki_root>/<telegram_id>/`) — no Inbox-WIKI, no routing. Phase-A wires the first slice of smart-inbox-routing: get content *into* the user's `Inbox-WIKI/` and run the Stage-1a Router agent there, producing a structured decision that later phases (`aisw-zd9/e45/kcz/12t`) consume. Four design forks needed deciding (resolved via `/questions-answers`, 2026-05-12).

## Options & decisions

1. **Roll-out: replace vs feature-flag.** The bot is not deployed (cutover-checklist empty), so a toggle is pure YAGNI (a flag to add, then a PR to remove). → **Replace** the flat run for routable intents (`WIKI_INGEST | WIKI_QUERY | UNKNOWN`); other intents and the no-router case keep the legacy path; the bot stays useful between Phase-A and Phase-B by replying with the parsed `RouterDecision.notes`. Caveat recorded in the epic: do not deploy `master` with Phase-A until `aisw-zd9` closes.
2. **Router reply format: free-text + tolerant parser vs strict envelope.** Parsing prose from an LLM is brittle. → **Tighten** `prompts/inbox.md` to `v1.1.0`: the Router must answer with exactly one fenced ```` ```router ```` block of `key: value` lines (`target_wiki`, `intent`, `notes`). A `key: value` block (vs JSON) is reproduced near-perfectly by Sonnet and Cyrillic in `notes` doesn't break it. The parser still falls back to `RouterDecision(intent=CLARIFY, parsed_ok=False, …)` on a missing/malformed block — defence-in-depth, not the primary mechanism.
3. **Parser location.** A new `src/ai_steward_wiki/inbox/router.py` (`RouterIntent`, `RouterDecision`, `RouterError`, `parse_router_reply`) rather than folding into `inbox/parser.py` (which parses the static `## Inbox hint` block — a different source/format/consumer). Matches the GRACE "one module — one contract" idiom.
4. **Runtime adapter shape.** A dedicated `_RouterAdapter` in `__main__.py` behind a narrow `Router` Protocol (`route(...) -> RouterDecision`), reusing `run_wiki_session` (lock manager, kill-sequence, transcript persistence), rather than overloading `_WikiRunnerAdapter.run` with flags that change its return type. Zero risk to the existing path; clean types; natural to extend in Phase-B.
5. **Raw media staging (boundary with Phase-E).** In Phase-A, media (`voice/photo/document`) only gets a `.md` sidecar in `Inbox-WIKI/raw/<utc-ts>_<source>.md` (YAML front-matter + `staged_path`, voice → transcript); the binary stays in `media_staging_root`. Its move into `Inbox-WIKI/raw/media/_staging/` and the per-user sweep are Phase-E (`aisw-12t`, which subsumes the old `aisw-64c`). `media_paths` are still forwarded to the Router for vision/Read.

## Consequences

1. New module `M-INBOX-ROUTER` in the knowledge graph; `M-TG-PIPELINE-CLASSIFIER` and `M-RUNTIME-WIRING` gain the dependency; new log anchors `tg.pipeline.router.dispatched|delivered|error` and `inbox.router.staged_raw|run.begin|run.done|parsed|parse_error`.
2. `prompts/inbox.md` is now the SSoT for the Router reply format; any future tightening is a change to that file with a semver bump.
3. `_RouterAdapter` is constructed with `base_prompt_path = prompts/wiki.md` and `inbox_overlay_path = prompts/inbox.md`, `inbox_template_path = templates/inbox-wiki/CLAUDE.md`, lock id `f"{telegram_id}/Inbox-WIKI"`, and the configured text-turn timeout (no new knob).
4. This is the **pattern Phases B–E build on**: B consumes `RouterDecision` to look up/create the target `<Domain>-WIKI` and run a Stage-1b librarian ingest there; C wires `proposed actions → confirm loop`; D wires `reminder/aggregator → jobs.db`; E adds the `## Inbox hint` fast-path + per-user media staging.
5. `Inbox-WIKI/raw/` grows one file per routable message; a retention sweep for it is a later ops task (noted in the epic), not a Phase-A blocker.
