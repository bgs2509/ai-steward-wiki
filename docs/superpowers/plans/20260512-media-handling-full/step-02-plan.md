# step-02 plan — chunk 2 M-WIKI-RUNNER (bd_id: aisw-m2m)

> Executed 2026-05-12. SSoT for chunk-2 execution. See discovery-chunk2.md.

## Decision (auto-applied, DEC-C2-1)
`claude` CLI 2.1.139 has no `--image`; `--file` is for remote `file_id`s. Local images
are exposed to Claude via `--add-dir <staged-file-dir>` + the file path in `user_input`
(Read tool). Only viable mechanism — no ADR (not a choice among alternatives).

## Tasks (TDD)

1. **RED** — `tests/unit/wiki/test_runner.py`: `test_run_wiki_session_grants_media_dirs_via_add_dir` (argv contains media dir right after wiki path) + `test_run_wiki_session_no_media_argv_unchanged` (regression). Run → fail (`media_paths` kwarg unknown). ✅
2. **GREEN** — `src/ai_steward_wiki/wiki/runner.py`: `_build_argv(media_dirs=...)` appends each dir after `str(wiki_path)` (variadic `--add-dir`); `run_wiki_session(media_paths=...)` computes `sorted({p.parent for p in media_paths})`, passes to argv, logs `media_count`. Header v0.0.7. ✅
3. **RED→GREEN** — `tests/unit/tg/test_pipeline.py`: `test_on_photo_full_pipeline_runs_runner_with_media`, `test_on_photo_l2_dedup_hit_sends_dedup_ack`; updated `_make_idem` to mock `check_content`/`record_dedup_choice`, added `_make_runner/_make_classifier/_make_output`, `_FakeRef.staging_path`. ✅
4. **GREEN** — `src/ai_steward_wiki/tg/pipeline.py`:
   - `WikiRunner.run(media_paths=...)` Protocol param.
   - `_run_text_pipeline(media_paths=, skip_l2_dedup=, source=...|"photo")` — wraps L2 dedup in `if not skip_l2_dedup`, forwards `media_paths` to non-streaming `runner.run`.
   - `PHOTO_PROMPT_RU` constant (synthetic Stage-1 prompt for caption-less image).
   - `on_photo`: stage → L2 dedup on image bytes (kind="file") → if dup `ACK_DEDUP_RU`; else if full pipeline `_run_text_pipeline(text=PHOTO_PROMPT_RU.format(path=ref.staging_path), source="photo", media_paths=[ref.staging_path], skip_l2_dedup=True)`; else `ACK_PHOTO_RU`.
   - `_handle_image_branch` (image-document): doc bytes already deduped at `on_document` entry → if full pipeline run pipeline with media_paths; else `ACK_PHOTO_RU`.
   - Header v0.3.3.
5. **GREEN** — `tests/unit/tg/test_pipeline_document.py`: `test_image_jpeg_routed_to_runner_with_media` (replaces `..._routes_through_photo_ingestor`), `test_image_jpeg_without_full_pipeline_acks`; `_StagedRef.staging_path`.
6. **GREEN** — `src/ai_steward_wiki/__main__.py`: `_WikiRunnerAdapter.run(media_paths=...)` → `run_wiki_session(media_paths=media_paths)`. Header v0.1.2.
7. **GREEN** — `src/ai_steward_wiki/tg/photo.py`: contract clarified (vision owned by wiki pipeline via media_paths), no code change. Header v0.0.2.
8. **GREEN** — `tests/unit/tg/test_pipeline_streaming.py`: `_runner_factory.run` accepts `media_paths=None`.
9. **VERIFY** — `make total-test` exit 0 (424 tests, coverage 90.70%, ruff/mypy/grace/inv-lint clean).

## Acceptance
- `grep "media_paths" src/ai_steward_wiki/wiki/runner.py` non-empty; `--add-dir` variadic with media dirs.
- Manual (operator): send photo of a receipt → bot replies with vision-extracted text; logs `tg.pipeline.photo` → `tg.pipeline.runner.dispatched` → `tg.pipeline.deliver.sent`. (Pending operator run.)

## Out of scope (later chunks)
Per-call vision timeout 30s (deferred — global wiki_runner_timeout_s for now); F.audio/video_note/caption (chunk 3 / aisw-ahv); promote_to_raw + sweep (chunk 4 / aisw-8r9); e2e (chunk 5 / aisw-nzl).
