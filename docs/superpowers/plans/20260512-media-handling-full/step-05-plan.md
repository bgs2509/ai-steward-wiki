# step-05 plan — chunk 5 M-INTEGRATION-E2E + docs (bd_id: aisw-nzl)

> Executed 2026-05-12. SSoT for chunk-5 execution. Final chunk — closes the epic.

## Tasks

1. **GREEN** — `tests/integration/test_e2e_pipeline.py`: add `test_photo_routed_to_runner_with_media` (photo → fake_runner.run with `media_paths` → fake_output.deliver) and `test_photo_with_caption_carries_caption` (caption in runner prompt); update `test_photo_then_confirm_callback` for the new photo path (was asserting `ACK_PHOTO_RU`, now asserts the runner was called). Header v0.1.1; SCOPE 4→6 scenarios; MODULE_MAP updated. (Suite gated by `RUN_INTEGRATION=1` + claude binary + not `CLAUDECODE=1` — skipped in `total-test`.)
2. **DOCS** — `docs/reports/20260512-media-handling-full-report.md` — completion report (per-chunk summary, decisions, deferred items, verification table).
3. **CLAUDE.md** — reviewed; nothing in the project `CLAUDE.md` became incorrect (`faster-whisper` was already listed in the stack; `uv sync` now installs it; no env-key enumeration to update). No change made (avoid unnecessary churn).
4. **`grace lint`** — green (governed=66 xml=3 errors=0 warnings=0); full `grace-refresh` of knowledge-graph/verification-plan noted as a follow-up (not blocking; no integrity violation).
5. **breakdown.xml** — RunState → complete; all chunks marked closed; total-test log + decisions complete.
6. **`bd close`** all chunk issues + epic `aisw-hcl`.
7. **VERIFY** — `make total-test` exit 0 (432 tests, coverage 90.54%, ruff/mypy/grace/inv-lint clean).

## Acceptance
- `docs/reports/20260512-media-handling-full-report.md` exists.
- `grace lint --failOn errors` exit 0.
- All beads (`aisw-zny`, `aisw-m2m`, `aisw-ahv`, `aisw-8r9`, `aisw-nzl`, `aisw-hcl`) closed.
- Manual operator acceptance pending (run the bot, send each media type).

## Out of scope (follow-ups — see report §"Deferred")
Per-call vision timeout 30s; document/voice captions; `register_all_retention_jobs` wiring; per-user Inbox-WIKI staging; video_note ext; full `grace-refresh`.
