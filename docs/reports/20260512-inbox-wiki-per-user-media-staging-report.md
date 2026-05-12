# Completion Report — Inbox-WIKI Phase-E.a: per-user media `_staging`

- **bd:** `aisw-12t` (epic `aisw-t2r`; subsumes `aisw-64c`; blocks `aisw-5sd`)
- **Date:** 2026-05-12
- **Spec:** `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-{discovery,design}.md` · **Plan:** `docs/superpowers/plans/20260512-inbox-wiki-per-user-media-staging-plan.md`

## Scope

The bd issue bundled two bits under "Inbox-WIKI Phase-E"; Discovery split them:

1. **Per-user media `_staging`** (bit 2, was deferred `aisw-64c`) — **DONE here.** Voice/photo bytes now stage under `<wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging/` instead of the single shared `settings.media_staging_root`; the 24h sweep iterates every per-user Inbox-WIKI; `settings.media_staging_root` removed; `promote_path_to_raw` unchanged.
2. **`## Inbox hint` fast-path router-bypass** (bit 1) — **split out → `aisw-5sd` (Phase-E.b)**, its own feature-workflow iteration (the hint cache is currently unwired and the matching mechanism is a real design fork; bundling on top of `tg/pipeline.py`'s ~2.1k lines blew the Plan-Sizing budget).

## Decisions (design frontmatter T-1..T-10)

- **T-1** `inbox_wiki_path(telegram_id, *, wiki_root) -> Path` — pure path helper in `inbox/materialize.py` (no IO; tree created lazily by `stage_media.mkdir`). `ensure_inbox_wiki` unchanged.
- **T-2/T-3** `wiki_root: Path | None = None` injected into `DefaultPipeline`; `VoiceHandler`/`PhotoIngestor` `inbox_root` moved from a required `__init__` kwarg to an optional default + an optional `.handle(..., inbox_root=…)` per-call override (effective root = call > constructor; neither → `ValueError`). `wiki_root=None` ⇒ handlers fall back to their constructor root (keeps existing pipeline unit tests green).
- **T-4** `sweep_all_user_staging(wiki_root, *, now, ttl_s) -> int` in `inbox/staging.py` — iterates `<wiki_root>/<child>/Inbox-WIKI/`, skips `_trash`/stray files/dirs without `Inbox-WIKI`, sums `sweep_staging`; emits `media.staging_sweep_all_done`.
- **T-5** `scheduler/maintenance.py`: `register_media_staging_sweep_job(staging_root→wiki_root)`, callable `_run_media_sweep→_run_all_user_media_sweep`, `register_all_retention_jobs(media_staging_root→wiki_root_for_media_sweep)`. 04:30 UTC cron + idempotency unchanged.
- **T-6** `settings.media_staging_root` removed (was an explicit MVP placeholder per `DEC-C1-2`; nothing in prod read it; no internal back-compat shim).
- **T-7** `__main__.py`: handlers built without `inbox_root`; `DefaultPipeline(wiki_root=settings.wiki_root)`; `register_all_retention_jobs(wiki_root_for_media_sweep=settings.wiki_root)`; `runtime.scheduler.started` log field `media_staging_root→wiki_root`. `_WikiRunnerAdapter.run` `promote_path_to_raw` unchanged.
- **T-8** No new dependency / SQLite table / Alembic migration / user-facing string / prompt change. `ops/retention.purge_staging` (a separate currently-unwired single-dir helper) left untouched — flagged below.
- **T-9** Tests updated/added across `test_materialize.py`, `test_staging.py`, `test_voice.py`, `test_photo.py`, `test_maintenance.py`, `test_pipeline.py`.
- **T-10** `grace-refresh --verify` (targeted): knowledge-graph.xml (new `fn-inbox_wiki_path`, `fn-sweep_all_user_staging`; updated M-INBOX/CrossLinks), verification-plan.xml (new marker `media.staging_sweep_all_done`, updated V-M-INBOX evidence).

## Verification (evidence)

- `make lint` → ruff check ✅ · ruff format --check ✅ (192 files) · mypy `src` ✅ (70 files, 0 errors).
- `make grace-lint` → governed 70, xml 3, **0 errors / 0 warnings**.
- `make inv-lint` → **all 14 invariant checks passed**.
- `make test-cov` → unit suite passes, `--cov-fail-under=80` gate ✅ (exit 0).
- Integration suite (`tests/integration`, real Claude CLI) — gated, skipped under `CLAUDECODE=1`; not run here (per the nightly cadence in `docs/runbook`).

## Deferred / follow-ups

1. **Phase-E.b** — `## Inbox hint` fast-path router-bypass → `aisw-5sd` (own feature-workflow iteration; design fork OQ-C: deterministic keyword-match vs tiny-Haiku vs spec-faithful "feed the Router prompt" vs drop).
2. **`ops/retention.purge_staging`** — a separate, currently-unwired single-dir `_staging` sweep helper in `ops/retention.py`; now redundant given `sweep_all_user_staging`. Dead code; worth a small cleanup task.
3. Pre-existing (unchanged by this iteration): per-call vision timeout 30s (`DEC-C2-4`); doc/voice captions (`DEC-C3-2`); `video_note` wrong ext (cosmetic).
