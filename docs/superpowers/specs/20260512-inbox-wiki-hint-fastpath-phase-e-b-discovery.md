---
feature: inbox-wiki-hint-fastpath
bd_id: aisw-5sd
epic: aisw-t2r
phase: "Inbox-WIKI Phase-E.b"
status: draft
created: 2026-05-12
parent_discovery: docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-discovery.md
note: "Split out from aisw-12t (Phase-E, bit 1). Phase-E.a (per-user media _staging, bit 2) shipped under aisw-12t. This iteration = the '## Inbox hint' fast-path / router-bypass only."
requirements:
  functional:
    - id: FR-1
      text: "Before invoking the heavy Inbox-WIKI Router-Claude run for a routable Stage-0 intent, a lightweight step builds the sender's domain catalog from the cached '## Inbox hint' of each <Domain>-WIKI/CLAUDE.md (reuse inbox/hint_cache.get_or_refresh_hint per domain, enumerated via the existing owner→[(stem, path)] resolver _resolve_owner_wikis_factory, already injected into DefaultPipeline as owner_wikis_resolver). It then matches the incoming content (Stage-0 distilled_payload, falling back to raw text) against the hint catalog. On a CONFIDENT hit it synthesises a RouterDecision(intent=ROUTE, target_wiki=<matched stem>) and feeds the EXISTING Phase-C confirm loop (route_action_to_payload → PendingConfirmDraft(category='route_ingest') → ConfirmationService.request_explicit with build_route_confirm_keyboard), skipping the ~10–30 s Sonnet Router-Claude run. On no match / ambiguous match → fall through to today's heavy Router path unchanged."
    - id: FR-2
      text: "The matching mechanism and the definition of 'confident' is the central design fork (OQ-A), decided at the Brainstorming gate: Option A = deterministic keyword/regex token-overlap scoring over the cached hint lines, fire only on a SINGLE unambiguous domain above a fixed threshold; Option B = a tiny Haiku call over the cached catalog (cheaper than the Sonnet Router but still a CLI round-trip); Option B′ = spec-faithful 'feed the cached catalog into the existing Router prompt as context' (no bypass, smaller latency win, no new heuristic); Option C = drop bit 1 entirely. Discovery records the fork only — no recommendation locked here beyond 'precision over recall' (NFR-2)."
    - id: FR-3
      text: "The fast-path NEVER silently routes — the user always confirms via the existing inline-button recap (build_route_recap / build_route_confirm_keyboard) before any file move/ingest. The synthesised RouterDecision MUST be of the same shape route_action_to_payload / on_confirm_callback / the Stage-1b librarian path already consume, so no parallel confirm/execute path is introduced."
    - id: FR-4
      text: "The fast-path sits in tg/pipeline.py inside _run_text_pipeline, AFTER the reminder fast-path (START_BLOCK_REMINDER_FASTPATH) and BEFORE START_BLOCK_ROUTABLE_BRANCH — i.e. it gates on `result.intent in _ROUTABLE_INTENTS` and runs before `self._router.route(...)`. On fall-through, START_BLOCK_ROUTABLE_BRANCH executes verbatim. It is a third pre-router fast-path of the same family as the reminder (aisw-kcz) and digest (aisw-269) ones."
    - id: FR-5
      text: "DefaultPipeline gains the dependency it needs to read the hint catalog: an InboxHintCacheRepo instance (constructed in __main__ from the sessions session-maker — the sessions.inbox_hint_cache table already exists) OR an equivalent narrow Protocol; owner_wikis_resolver is already a dep. New constructor kwarg is optional (None ⇒ fast-path disabled, fall straight through — keeps existing pipeline unit tests green without the new dep). __main__.py wires the repo + (if Option B) any Haiku CLI handle."
    - id: FR-6
      text: "New structlog anchors in tg/pipeline.py: tg.pipeline.hint_fastpath.catalog (n_domains, source — per-domain cache 'hit' vs 'refresh' aggregated), tg.pipeline.hint_fastpath.hit (target_wiki, score), tg.pipeline.hint_fastpath.miss (reason='no_match'|'ambiguous'|'empty_catalog'), tg.pipeline.hint_fastpath.fallthrough (so every bypass attempt is observable for offline threshold tuning). Existing inbox.hint_cache.{hit,refresh,invalidate_missing} from get_or_refresh_hint are unchanged."
    - id: FR-7
      text: "Unit tests with fakes only (no live Telegram / Claude / real APScheduler / filesystem outside tmp_path): a confident single-match → synthesised RouterDecision feeds the confirm loop and self._router.route is NOT called; ambiguous (two domains score above threshold) → fall through, router IS called; empty catalog (user has no domain WIKIs) → fall through; cache-hit catalog build does zero filesystem reads beyond the stat in get_or_refresh_hint; the new log anchors fire on each path. Coverage stays ≥80%."
  non_functional:
    - id: NFR-1
      text: "Reuse-only — NO new SQLite table (inbox_hint_cache exists), NO Alembic migration, NO new third-party dependency, NO prompt change unless Option B′ is chosen (then prompts/inbox.md gains a catalog block). Reuse: inbox/hint_cache.get_or_refresh_hint + InboxHintCacheRepo, inbox/parser.extract_inbox_hint, _resolve_owner_wikis_factory, inbox/route.route_action_to_payload, ConfirmationService + build_route_recap + build_route_confirm_keyboard + PendingConfirmDraft, RouterDecision/RouterIntent, PIIRedactor for logs."
    - id: NFR-2
      text: "Latency/cost is the WHY (tech-spec §4 'Экономия cache' + §8.3.3 fast/heavy two-tier): with the hint cache, 0 filesystem reads on a hot message; the bypass additionally saves the ~10–30 s + ~3500-token Sonnet Router run on the ~80 % of messages whose target is obvious from a one-line hint. The bypass MUST stay conservative: a wrong auto-route the user then cancels is worse than a 20 s router run, so 'confident' is narrow (single unambiguous match). Precision over recall."
    - id: NFR-3
      text: "mypy --strict / ruff / ruff-format / grace lint clean (`make lint`); `make total-test` semantics (lint + grace + inv-lint + coverage ≥80% + integration where applicable). Ru-only (D-032) — Phase-E.b reuses the existing route-confirm copy verbatim, no new user-facing strings. All datetime UTC. No bypass of pre-commit hooks. Isolation (CLAUDE.md §Изоляция) — nothing reads or writes /home/bgs/ai-steward/."
    - id: NFR-4
      text: "grace-refresh + grace-refresh --verify after the change — new log markers (tg.pipeline.hint_fastpath.*) and the new DefaultPipeline dependency edge (M-TG-PIPELINE → M-INBOX hint_cache, if not already present) must land in knowledge-graph.xml / verification-plan.xml. Also clears the Phase-D-era deferred 'hint fast-path' note now that the cache is wired as a catalog."
  constraints:
    - "MUST hook the EXISTING Stage-0 → (Stage-1a Router) → Phase-C confirm pipeline, not introduce a parallel one — the bypass produces a RouterDecision of the same shape the heavy router produces."
    - "The hint cache is the runtime domain catalog; it is NOT materialised into any WIKI file (tech-spec §4). Phase-E.b only READS domain CLAUDE.md files (via the metadata-guarded get_or_refresh_hint) — never writes them."
    - "No new lifecycle command, no /wiki_* surface change (D-041). The fast-path is invisible to the user except that the confirm prompt appears faster on an obvious-target message."
    - "user_id vs owner_telegram_id mismatch: get_or_refresh_hint(repo, user_id, claude_md_path) is keyed on the sessions-DB surrogate user_id, while _resolve_owner_wikis_factory is keyed on owner_telegram_id. The fast-path must resolve the surrogate user_id for the sender (the pipeline already has the user_id surrogate available wherever it touches sessions.db — confirm exact accessor in Brainstorming) before calling get_or_refresh_hint. (Identity vocabulary, D-042.)"
  risks:
    - id: R-1
      text: "False-positive auto-route — a hint-match fires on the wrong domain and the user has to cancel. Mitigation: NFR-2 precision-over-recall; only a single unambiguous match above threshold fires; everything else falls through; log every hit/miss/score for offline tuning. The user-confirm step is the final guard (FR-3) — a bad guess costs one extra tap, not a wrong file move."
    - id: R-2
      text: "The hint cache was built (chunk 6) but is currently UNWIRED — nothing populates it for all domains and nothing reads it as a catalog. Phase-E.b is real new integration (a new DefaultPipeline dep + a catalog-build helper over the resolver + per-domain get_or_refresh_hint calls), not a small tweak. Mitigation: it is its own feature-workflow iteration (this one); scope is one context window (pipeline.py edit is localised to one new pre-router block + the helper; hint_cache.py gets a catalog wrapper; tests)."
    - id: R-3
      text: "Scope creep beyond what tech-spec §4 strictly mandates — the spec uses the hint cache to FEED the Router prompt (Option B′), not necessarily to BYPASS the router (Option A/B). The bd description ('on a confident hint short-circuit the heavy Router-Claude run') reads as A/B. Mitigation: OQ-A is decided at the Brainstorming gate; if B′ is chosen the latency win shrinks but no new heuristic ships and the spec is followed literally; if C is chosen the issue closes as 'wontfix — spec-faithful path deemed sufficient'."
    - id: R-4
      text: "Catalog build cost on a cache MISS — first message after a domain CLAUDE.md edit re-reads + sha256s that file. With many domains a cold catalog is N file reads. Mitigation: get_or_refresh_hint already does stat→cache-hit on (size, mtime) so steady state is N stats, 0 reads; a cold/changed file is one read each — bounded by the user's domain count (small). Acceptable; log source breakdown (FR-6) to confirm in practice."
    - id: R-5
      text: "grace-lint governs ~66 modules + 3 XML; adding a log marker or a new module edge without a grace-refresh breaks the gate. Mitigation: NFR-4 — grace-refresh (+ --verify) is a planned step."
    - id: R-6
      text: "An empty distilled_payload (Stage-0 produced none) forces a fall-back to raw text for matching, which may be long/noisy and skew a keyword overlap. Mitigation: Option A scoring normalises by token count and requires a margin between #1 and #2 domain; if raw text is the only signal and it is ambiguous, fall through — the heavy router is the correct tool for ambiguous content."
  scope:
    in:
      - "tg/pipeline.py — a new pre-router fast-path block (between START_BLOCK_REMINDER_FASTPATH and START_BLOCK_ROUTABLE_BRANCH) inside _run_text_pipeline that builds the hint catalog, scores the incoming content, and on a confident hit synthesises a RouterDecision(ROUTE, target_wiki=<stem>) → existing route_action_to_payload → PendingConfirmDraft → request_explicit; on miss/ambiguous returns control to START_BLOCK_ROUTABLE_BRANCH. New optional DefaultPipeline ctor kwarg for the hint-cache repo (or Protocol)."
      - "inbox/hint_cache.py (or a small new helper module) — a catalog-build helper: given (repo, user_id, [(stem, path)]) → dict[stem, hint_text] via get_or_refresh_hint per domain (resolving each <Domain>-WIKI dir's CLAUDE.md), plus the scoring function for Option A (if A is chosen). __all__ / MODULE_MAP / SCOPE updated."
      - "src/ai_steward_wiki/__main__.py — construct InboxHintCacheRepo from the sessions session-maker and inject it into DefaultPipeline; if Option B, wire a Haiku CLI handle."
      - "tests/unit/tg/test_pipeline*.py — fast-path hit / ambiguous / empty-catalog / fallthrough cases + log-anchor assertions; tests/unit/inbox/ — catalog-build helper + scorer unit tests."
      - "grace-refresh + grace-refresh --verify."
      - "If Option B′ instead of A/B: prompts/inbox.md gains a cached-catalog context block; _RouterAdapter passes the catalog into the prompt; the pipeline change is just building+passing the catalog (no synthesised RouterDecision)."
    out:
      - "Any change to how the heavy Inbox-WIKI Router-Claude run works internally (prompts/inbox.md, _RouterAdapter), UNLESS Option B′ is chosen."
      - "Populating the hint cache outside the on-message hot path (e.g. a warm-up job at boot) — the lazy get_or_refresh_hint is sufficient for MVP; a warm-up job is a possible later optimisation."
      - "Invalidation on domain CLAUDE.md edit beyond what get_or_refresh_hint's (size, mtime) stat already gives — no fs-watch."
      - "Per-call vision timeout, captions, video_note ext, ops/retention.purge_staging cleanup — unrelated deferred items from the media-handling track."
    later:
      - "A boot-time / periodic hint-cache warm-up job (refresh all domains for all owners) if cold-catalog latency proves noticeable in production logs (R-4)."
  open_questions:
    - id: OQ-A
      text: "Matching mechanism + 'confident' definition: Option A deterministic keyword/regex token-overlap with a single-unambiguous-match threshold + margin | Option B tiny Haiku call over the cached catalog | Option B′ spec-faithful 'feed the catalog into the existing Router prompt' (no bypass) | Option C drop bit 1. Decide at the Brainstorming gate. Discovery leans A or B′ (A = biggest latency win + fully deterministic + testable; B′ = literal spec compliance, smaller win, zero new heuristic) over B (a Haiku round-trip is cheaper than Sonnet but still a CLI call — middling) and over C (the bd issue is explicitly open and the win is real)."
    - id: OQ-B
      text: "Where does the pipeline get the sessions-DB surrogate user_id for the sender (needed by get_or_refresh_hint)? Confirm the exact accessor / whether to add one in Brainstorming. If awkward, an alternative is to re-key get_or_refresh_hint on owner_telegram_id (a one-line signature change, since wiki_path is the real cache key and user_id is just a scoping column) — decide in Brainstorming."
    - id: OQ-C
      text: "Scoring details for Option A (if chosen): tokenisation (lowercase word-split vs the hint's own comma/keyword list), stop-word handling, threshold value, the margin between #1 and #2 domain, and whether to also match on the domain stem name itself. Belongs in Brainstorming/Plan, not Discovery."
---

# Inbox-WIKI Phase-E.b — `## Inbox hint` fast-path (router-bypass on confident hint) — Discovery

`bd_id: aisw-5sd` · epic `aisw-t2r` · split from `aisw-12t` (Phase-E, bit 1). Phase-E.a (per-user media `_staging`, bit 2) already shipped under `aisw-12t`.

## What the bd issue asks for

Before the heavy Inbox-WIKI Router-Claude run for a routable Stage-0 intent, match the incoming content against the cached `## Inbox hint` catalog of the sender's domain WIKIs; on a **confident** hit, skip the ~10–30 s Sonnet Router and go straight to a `ROUTE` confirm for that target WIKI — feeding the *existing* Phase-C confirm loop. Never silently routes; the user still confirms via inline buttons. (tech-spec §4 "Inbox hint / hint-cache / hot-path", §8.3.3 fast/heavy two-tier; `docs/Spec-WIKI/concepts/smart-inbox-routing.md`; D-004, D-016.)

## Current state (verified this session)

- `tg/pipeline.py` (~2 134 lines): `_run_text_pipeline` runs `classify` → `START_BLOCK_REMINDER_FASTPATH` (intent=REMINDER + confidence ≥ threshold + time_parser wired → handled, `return`) → `START_BLOCK_ROUTABLE_BRANCH` (`if result.intent in _ROUTABLE_INTENTS and self._router is not None:` → `await self._router.route(...)` → on `ROUTE`/`CREATE_WIKI` with librarian+output wired → `route_action_to_payload(decision, …)` → `PendingConfirmDraft(category="route_ingest", recap_text=build_route_recap(decision))` → `self._confirm.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)` → `return`) → legacy runner branch. The reminder (`aisw-kcz`) and digest (`aisw-269`) fast-paths already sit before the router — bit 1 is a third of the same family. The synthesised `RouterDecision` would feed the *same* `route_action_to_payload → PendingConfirmDraft → request_explicit` triple verbatim.
- `inbox/hint_cache.py`: `InboxHintCacheRepo` (get / upsert / invalidate over `sessions.inbox_hint_cache`) + `get_or_refresh_hint(repo, user_id, claude_md_path) -> str | None` — stat→cache-hit on `(size, mtime)`, else read+sha256+`extract_inbox_hint`+upsert. Logs `inbox.hint_cache.{hit,refresh,invalidate_missing}`. **Built but unwired** — nothing calls it as a catalog.
- `inbox/parser.py`: `extract_inbox_hint(text)` — regex `^## Inbox hint$ … (?=^## |EOF)`. ~55 lines.
- `__main__._resolve_owner_wikis_factory(wiki_root) -> Callable[[int], Awaitable[list[tuple[str, Path]]]]` — async resolver `owner_telegram_id → [(wiki_dir_name, path), …]` minus `Inbox-WIKI`. **Already injected** into `DefaultPipeline` as `owner_wikis_resolver` and used by the digest fast-path (`extract_wiki_names` over the stems). `DefaultPipeline` does **not** currently get an `InboxHintCacheRepo` — that is the new dep (FR-5).
- `get_or_refresh_hint` keys on `user_id` (sessions-DB surrogate), the resolver keys on `owner_telegram_id` — identity-vocabulary mismatch to resolve (OQ-B, constraint).
- `sessions.inbox_hint_cache` table + Alembic migration already exist — no schema work.

## Recommendation (Discovery)

Implement bit 1 as its own iteration (this one). At the Brainstorming gate pick **OQ-A**: Discovery leans **Option A** (deterministic keyword/regex token-overlap, single-unambiguous-match threshold + margin) — biggest latency win, fully deterministic, trivially unit-testable, no extra CLI round-trip — or **Option B′** (feed the cached catalog into the existing Router prompt) if literal tech-spec §4 compliance is preferred over the bigger win; **not** Option B (a Haiku call is cheaper than Sonnet but still a CLI round-trip — middling) and **not** Option C (the bd issue is explicitly open, the cache is built, and the ~80 %/20 s win is real). Whichever wins, the user-confirm step (FR-3) is the hard guard against a wrong guess.

Spec refs: `docs/Spec-WIKI/research/tech-spec-draft.md` §4, §8.3.3; `docs/Spec-WIKI/concepts/smart-inbox-routing.md`; `docs/Spec-WIKI/entities/{inbox-wiki,router-agent}.md`; `D-004` (Inbox per-user + template), `D-016` (Inbox hint), `D-022` (media — unrelated here); parent discovery `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-discovery.md` (FR-7, FR-8, R-4..R-6, OQ-C).
