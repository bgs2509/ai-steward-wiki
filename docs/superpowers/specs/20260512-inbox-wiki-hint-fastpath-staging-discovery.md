---
feature: inbox-wiki-hint-fastpath-staging
bd_id: aisw-12t
epic: aisw-t2r
phase: "Inbox-WIKI Phase-E"
status: draft
created: 2026-05-12
requirements:
  functional:
    # --- Bit 2: per-user media _staging (subsumes aisw-64c) — Phase-E.a ---
    - id: FR-1
      text: "Voice and photo (and image-document) ingestion stages the raw bytes under the SENDER'S per-user Inbox-WIKI — <wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging/<run_id>_<sha8>.<ext> — instead of the single shared settings.media_staging_root. A new pure helper inbox_wiki_path(telegram_id, *, wiki_root) -> Path in inbox/materialize.py returns <wiki_root>/<telegram_id>/<INBOX_WIKI_DIRNAME>; the staging dir itself is created lazily by stage_media's mkdir(parents=True) — no CLAUDE.md materialisation is required just to stage media (ensure_inbox_wiki stays the first-contact materialiser used by the Router adapter)."
    - id: FR-2
      text: "VoiceHandler / PhotoIngestor no longer bind a fixed inbox_root at construction time; the per-user staging root is resolved per message from telegram_id. Concrete shape is an Open Question (OQ-A): (a) .handle(..., inbox_root: Path) per-call arg with the pipeline computing inbox_root; (b) the pipeline gets wiki_root injected and passes it; (c) an inbox_wiki_path_resolver: Callable[[int], Path] injected into DefaultPipeline. on_voice / on_photo / the image-document branch in tg/pipeline.py pass the resolved root. _run_text_pipeline / media_paths / promote_path_to_raw are unchanged (the staged path is still absolute; the runner adapter still promotes it into the target WIKI on success)."
    - id: FR-3
      text: "The 24h _staging retention sweep iterates EVERY user's Inbox-WIKI staging dir, not one shared dir: a new wrapper sweep_all_user_staging(wiki_root, *, now, ttl_s) globs <wiki_root>/*/Inbox-WIKI/ and calls sweep_staging on each (sweep_staging itself, keyed on a single inbox_root, is unchanged). scheduler/maintenance.register_media_staging_sweep_job is re-pointed from a staging_root: Path to wiki_root: Path; register_all_retention_jobs / register_all_maintenance_jobs and __main__.py wiring updated accordingly. Failed runs still leave the file in _staging for this sweep (D-022 No-WIKI flow)."
    - id: FR-4
      text: "settings.media_staging_root and the comments/log fields referencing it are removed (no internal backwards-compat shim — nothing has shipped to prod that depends on it; the field was an explicit MVP placeholder per DEC-C1-2). [If OQ-B decides to keep it as a fallback for users with no Inbox-WIKI yet, this FR is replaced by a 'keep + document' variant in Brainstorming — Discovery recommends removal because Inbox-WIKI is always reachable via inbox_wiki_path(telegram_id) and the dir auto-creates.]"
    - id: FR-5
      text: "Unit tests: per-user staging path assertion for voice + photo + image-document (each lands under <wiki_root>/<tid>/Inbox-WIKI/raw/media/_staging/); inbox_wiki_path returns the expected path; sweep_all_user_staging removes stale files across multiple user dirs and leaves fresh ones; the existing tests/unit/inbox/test_staging.py and any voice/photo handler tests updated for the new construction/handle shape; tests/unit/scheduler/* for the re-pointed sweep job. Coverage stays ≥80%."
    - id: FR-6
      text: "Structlog: media.staged already logs the full path (now per-user) — no change needed there; the sweep wrapper emits maintenance.media_sweep.done (already present) plus a per-user-dir count; new/renamed anchors only where the wiring shape forces it. PII tier-2: paths under raw/media/_staging never embed the user name (only telegram_id, which is already non-secret and present in every log line)."
    # --- Bit 1: '## Inbox hint' fast-path router-bypass — Phase-E.b (recommended split) ---
    - id: FR-7
      text: "[Phase-E.b — RECOMMENDED SPLIT, see Scope/LATER] Before invoking the heavy Inbox-WIKI Router-Claude run for a routable Stage-0 intent, a lightweight deterministic step builds the user's domain catalog from the cached '## Inbox hint' of each <Domain>-WIKI/CLAUDE.md (reuse inbox/hint_cache.get_or_refresh_hint per domain, enumerated via the existing owner→[(stem,path)] resolver) and matches the incoming content (Stage-0 distilled_payload / raw text) against the hints. The matching mechanism and the definition of 'confident' are the central design fork (OQ-C): Option A = deterministic keyword/regex overlap, fire only on a SINGLE unambiguous domain match above a threshold; Option B = a tiny Haiku call over the catalog (still far cheaper than the Sonnet Router run in Inbox-WIKI). On a confident hit → synthesise a RouterDecision(intent=ROUTE, target_wiki=<matched stem>) and feed the EXISTING Phase-C confirm loop (route_action_to_payload → PendingConfirmDraft → ConfirmationService.request_explicit) — skipping the ~10–30s Router-Claude run (tech-spec §8.3.3 fast/heavy two-tier). On no match or ambiguity → fall through to today's heavy Router path unchanged."
    - id: FR-8
      text: "[Phase-E.b] The fast-path never SILENTLY routes — the user still confirms via the existing inline-button recap before any file move/ingest. The hint catalog is cached (sessions.db.inbox_hint_cache, already exists, metadata-guarded, no TTL) so a hot message does zero filesystem reads on a cache hit. New structlog anchors: tg.pipeline.hint_fastpath.catalog (n_domains, source='cache'|'refresh'), tg.pipeline.hint_fastpath.hit (target_wiki, score), tg.pipeline.hint_fastpath.miss (reason='no_match'|'ambiguous'), tg.pipeline.hint_fastpath.fallthrough."
  non_functional:
    - id: NFR-1
      text: "Reuse-only: inbox/staging.py stage_media/promote_*/sweep_staging, inbox/materialize.py, inbox/hint_cache.py (Phase-E.b), inbox/parser.extract_inbox_hint, the owner→wikis resolver (__main__._resolve_owner_wikis_factory), ConfirmationService + the route-confirm keyboard family + route_action_to_payload (Phase-E.b), scheduler/maintenance + register_all_*; PIIRedactor for log lines. NO new SQLite table (inbox_hint_cache already exists), NO Alembic migration, NO new third-party dependency."
    - id: NFR-2
      text: "mypy --strict / ruff / ruff-format / grace lint clean (run `make lint` AND `make total-test` semantics: lint + grace + inv-lint + coverage ≥80% + integration where applicable); coverage stays ≥80%. All new behaviour unit-tested with fakes — no live Telegram / Claude / real APScheduler thread / real filesystem outside tmp_path in unit tests. The sweep wrapper is tested by calling it directly on a tmp tree of fake user dirs with backdated mtimes."
    - id: NFR-3
      text: "Ru-only (D-032) — no new user-facing strings in Phase-E.a; Phase-E.b reuses the existing route-confirm copy verbatim. All datetime in DB / sweep cutoffs are UTC. No bypass of pre-commit hooks. Isolation (CLAUDE.md §Изоляция): nothing reads or writes /home/bgs/ai-steward/."
    - id: NFR-4
      text: "Latency/cost intent (the WHY of bit 1): tech-spec §4 'Экономия cache' — with the hint cache, 0 filesystem reads on the hot path; bit 1's router-bypass additionally saves the ~10–30s + ~3500-token Sonnet Router run on the ~80% of messages whose target is obvious from a one-line hint. The bypass MUST stay conservative (precision over recall) — a wrong auto-route that the user then has to cancel is worse than a 20s router run, so 'confident' is defined narrowly (single unambiguous match)."
    - id: NFR-5
      text: "grace-refresh after the change — new log markers (maintenance.media_sweep per-user count, tg.pipeline.hint_fastpath.*) and any moved module exports must land in knowledge-graph.xml / verification-plan.xml. (Also clears the Phase-D-era deferred #6 'grace-refresh' note for the new media markers.)"
  constraints:
    - "Phase-E.a MUST keep the staged-path → target-WIKI promotion adapter-side (DEC-C4-1): the runner adapter still owns promote_path_to_raw(staging_path, wiki_root=<target wiki>); only the *origin* of the staged file changes (shared dir → per-user Inbox-WIKI). In current MVP the target WIKI is still the flat <wiki_root>/<owner_telegram_id>/, so promotion is unaffected by where staging lives."
    - "Phase-E.b MUST hook the EXISTING Stage-0 → (Stage-1a Router) → Phase-C confirm pipeline, not introduce a parallel one: the bypass produces a RouterDecision of the same shape the heavy router produces, so route_action_to_payload / on_confirm_callback / the Stage-1b librarian path are reused verbatim. It sits BEFORE the `if result.intent in _ROUTABLE_INTENTS and self._router is not None:` block (or just inside it, before `self._router.route(...)`)."
    - "The hint cache is the runtime domain catalog; it is NOT materialised into any WIKI file (tech-spec §4). Phase-E.b only READS domain CLAUDE.md files (via the metadata-guarded get_or_refresh_hint) — it never writes them."
    - "No new lifecycle command, no /wiki_* surface change (D-041). The fast-path is invisible to the user except that the confirm prompt appears faster."
  risks:
    - id: R-1
      text: "[E.a] VoiceHandler/PhotoIngestor are currently constructed once at startup with a fixed inbox_root — moving the path to per-call ripples into __main__ wiring + every handler test. Mitigation: pick the OQ-A shape that minimises churn (likely (b) inject wiki_root into DefaultPipeline, or (c) a one-line resolver) and update tests in lockstep; the change is mechanical."
    - id: R-2
      text: "[E.a] A message arrives before the user's Inbox-WIKI exists. Mitigation: inbox_wiki_path is pure path arithmetic and stage_media.mkdir(parents=True) creates the tree; full CLAUDE.md materialisation is only needed for the Router run (which already calls ensure_inbox_wiki). So staging media for a brand-new user just works. Verify with a unit test on a fresh tmp wiki_root."
    - id: R-3
      text: "[E.a] sweep_all_user_staging globbing <wiki_root>/* will also see _trash/ and any non-numeric dirs — must skip anything without an Inbox-WIKI/ child rather than assume the layout. Mitigation: glob <wiki_root>/*/Inbox-WIKI and derive the staging dir from each; tolerate missing dirs. Unit-tested with a mixed tree (a user dir, a _trash dir, a stray file)."
    - id: R-4
      text: "[E.b] False-positive auto-route — a hint-match fires on the wrong domain and the user has to cancel. Mitigation: NFR-4 — precision over recall; only a single unambiguous match above threshold fires; everything else falls through to the heavy router. Tunable threshold; log every hit/miss for offline tuning."
    - id: R-5
      text: "[E.b] The hint cache was built (chunk 6) but is currently UNWIRED — nothing populates it for all domains and nothing reads it as a catalog. Phase-E.b is therefore real new integration, not a small tweak; combined with bit 2 it exceeds one context window (pipeline.py alone is ~2.1k lines). Mitigation: SPLIT — Phase-E.a (bit 2, this iteration) + Phase-E.b (bit 1, follow-up). This is the Plan-Sizing 'fit, not fragment' call; the two bits share almost no files."
    - id: R-6
      text: "[E.b] The bd description's framing ('on a confident hint short-circuit the heavy Router-Claude run') goes BEYOND what tech-spec §4 strictly mandates (the spec uses the hint cache to FEED the Router prompt, not to bypass it). Three readings on the table (OQ-C / Scope): A = implement the conservative keyword-match bypass as written; B = spec-faithful 'pass the cached catalog into the Inbox Router prompt' (smaller latency win, no new heuristic); C = defer bit 1 entirely. Recommendation: split it out (E.b) and decide A vs B at that phase's Brainstorming gate."
    - id: R-7
      text: "[both] grace-lint governs ~66 modules + 3 XML; moving/renaming an export or a log marker without a grace-refresh breaks the gate. Mitigation: NFR-5 — grace-refresh (+ --verify) is a planned step, not an afterthought."
  scope:
    in:
      - "Phase-E.a (THIS iteration): inbox/materialize.py — add inbox_wiki_path(telegram_id, *, wiki_root). tg/voice.py + tg/photo.py — per-call (or resolver-injected) staging root instead of fixed inbox_root. tg/pipeline.py — on_voice / on_photo / image-document branch resolve & pass the per-user root; DefaultPipeline gets whatever injection OQ-A picks. inbox/staging.py (or scheduler/maintenance.py) — sweep_all_user_staging(wiki_root, ...) wrapper. scheduler/maintenance.py — register_media_staging_sweep_job re-pointed to wiki_root; register_all_* signatures updated. src/ai_steward_wiki/__main__.py — media-pipeline wiring + sweep-job wiring updated; settings.media_staging_root removed (OQ-B). settings.py — drop media_staging_root (OQ-B). tests/unit/inbox/test_staging.py + voice/photo handler tests + scheduler sweep tests updated. grace-refresh + grace-refresh --verify."
    out:
      - "Per-call vision timeout 30s (DEC-C2-4 deferred — separate task)."
      - "Document/voice captions (DEC-C3-2 deferred — separate task)."
      - "register_all_retention_jobs not being called from __main__ (DEC-C4-2 pre-existing gap — separate task)."
      - "video_note wrong ext (cosmetic — separate task)."
      - "Any change to how the heavy Inbox-WIKI Router-Claude run works (prompts/inbox.md, _RouterAdapter) — untouched in E.a."
    later:
      - "Phase-E.b (FOLLOW-UP, recommended): the '## Inbox hint' fast-path router-bypass (FR-7, FR-8) — its own feature-workflow iteration with a Brainstorming gate that picks OQ-C Option A vs B, the matching mechanism, and the 'confident' threshold. Touches tg/pipeline.py (the routable branch), inbox/hint_cache.py (catalog-build helper over the resolver), tests. A new child bd issue under aisw-12t (or aisw-12t relabelled to E.b once E.a closes the aisw-64c subsumption)."
  open_questions:
    - id: OQ-A
      text: "Per-user staging root injection shape: (a) VoiceHandler/PhotoIngestor .handle(..., inbox_root) per-call + pipeline computes it; (b) inject wiki_root: Path into DefaultPipeline and pass it; (c) inject inbox_wiki_path_resolver: Callable[[int], Path] into DefaultPipeline. Lean (b) or (c) — keeps the handler protocol stable-ish and the pipeline already owns telegram_id. Decide in Brainstorming."
    - id: OQ-B
      text: "Keep settings.media_staging_root as a fallback (e.g. for the sweep job before any Inbox-WIKI exists, or as an escape hatch) or remove it outright? Discovery recommends REMOVE (no internal backwards-compat shim; the per-user path always resolves and auto-creates). Confirm at the Discovery gate."
    - id: OQ-C
      text: "[Phase-E.b] Hint fast-path matching: Option A deterministic keyword/regex overlap with a narrow single-unambiguous-match threshold | Option B tiny Haiku call over the cached catalog | Option C defer bit 1. Decide at Phase-E.b Brainstorming (out of scope for this Discovery beyond recording the fork)."
    - id: OQ-D
      text: "Split confirmed? Discovery strongly recommends Phase-E.a now / Phase-E.b later (R-5, Plan-Sizing). If rejected and both bits stay in one iteration, the plan must still be one-context-window-sized — which Discovery judges unlikely given pipeline.py's size + the bit-1 design work. User call at the Discovery gate."
---

# Inbox-WIKI Phase-E — `## Inbox hint` fast-path + per-user media `_staging` — Discovery

`bd_id: aisw-12t` · epic `aisw-t2r` · subsumes `aisw-64c`.

## What the bd issue asks for

Two loosely-related bits, both under "Inbox-WIKI Phase-E":

1. **`## Inbox hint` fast-path** (bit 1) — before the heavy Inbox-WIKI Router-Claude run, match the incoming content against the cached `## Inbox hint` catalog of the user's domain WIKIs; on a *confident* hit, skip the router and go straight to a `ROUTE` confirm for that target WIKI. (tech-spec §8.3.3 fast/heavy two-tier.)
2. **Per-user media `_staging`** (bit 2, was deferred `aisw-64c`) — stage voice/photo bytes under `<wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging/` instead of the single shared `settings.media_staging_root`; make the 24h `_staging` sweep iterate per-user dirs; decide whether to keep `media_staging_root` as a fallback.

## Current state (verified)

- `inbox/staging.py` — `stage_media(data, *, ext, run_id, inbox_root, mime)` writes `<inbox_root>/raw/media/_staging/<run_id>_<sha8>.<ext>`; `sweep_staging(inbox_root, *, now, ttl_s)` sweeps that one dir; `promote_path_to_raw(staging_path, *, wiki_root, now)` re-hashes + moves into `<wiki_root>/raw/media/<ISO8601>_<sha8>.<ext>`.
- `tg/voice.py` `VoiceHandler(transcriber, *, inbox_root)` and `tg/photo.py` `PhotoIngestor(*, inbox_root)` bind a **fixed** `inbox_root` at construction.
- `src/ai_steward_wiki/__main__.py` constructs both with `inbox_root=settings.media_staging_root` (single shared dir); `register_all_retention_jobs(..., media_staging_root=settings.media_staging_root)` wires the daily sweep via `scheduler/maintenance.register_media_staging_sweep_job(scheduler, staging_root=...)`.
- `_WikiRunnerAdapter.run` promotes `media_paths` into the target WIKI (`wiki_root/<owner_telegram_id>` in MVP) on a successful run; failed runs leave files in `_staging` for the sweep — **unaffected** by where staging lives.
- `inbox/hint_cache.py` (`InboxHintCacheRepo` + `get_or_refresh_hint`) and `inbox/parser.py` (`extract_inbox_hint`, regex `^## Inbox hint$ … (?=^## |EOF)`) and `sessions.db.inbox_hint_cache` **exist but are unwired** — nothing populates the cache for all domains and nothing reads it as a router-feeding catalog or a bypass.
- `tg/pipeline.py` (~2.1k lines): the routable branch is `if result.intent in _ROUTABLE_INTENTS and self._router is not None: … await self._router.route(...)` → on `ROUTE`/`CREATE_WIKI` → `route_action_to_payload` → `PendingConfirmDraft` → `ConfirmationService.request_explicit` → executed in `on_confirm_callback` via the Stage-1b librarian. The reminder fast-path (`aisw-kcz`) and digest fast-path (`aisw-269`) already sit *before* the router in `_run_text_pipeline` — bit 1 would be a third pre-router fast-path of the same family.
- No `inbox_wiki_path(telegram_id)` helper exists yet (referenced by the bd description) — must be added.

## Recommendation

**Split** (OQ-D): do **bit 2 (Phase-E.a)** in this iteration — it's well-defined, ~6 files, no design fork, fits one context window comfortably; it also closes the `aisw-64c` subsumption and clears the Phase-D-era "grace-refresh for new media markers" note. Defer **bit 1 (Phase-E.b)** to its own feature-workflow iteration: it's real new integration (the hint cache is unwired), the matching mechanism + "confident" threshold is a genuine design decision (OQ-C: deterministic keyword-match vs tiny-Haiku vs spec-faithful "feed the router prompt" vs defer), and bundling it with bit 2 on top of `tg/pipeline.py`'s size blows the Plan-Sizing budget.

If the split is rejected, both bits stay under `aisw-12t` and the plan must still be one-window-sized — which Discovery judges unlikely; expect to split anyway at Writing-Plans.

Spec refs: `docs/Spec-WIKI/research/tech-spec-draft.md` §4 (Inbox hint / hint-cache / hot-path algorithm), §8.3.3 (fast/heavy two-tier — `docs/Spec-WIKI/concepts/smart-inbox-routing.md`); `D-004` (Inbox per-user + template), `D-016` (Inbox hint), `D-022` (media handling — `_staging` path, 24h sweep, No-WIKI flow); `docs/reports/20260512-media-handling-full-report.md` §Deferred #4; `docs/superpowers/plans/20260512-media-handling-full/breakdown.xml` `DEC-C1-2`, `DEC-C4-1`.
