# M-RUNTIME-WIRING — Completion Report

**Chunk:** 18 (post-MVP)
**bd_id:** `aisw-cq4`
**Date:** 2026-05-11
**Status:** Closed
**Commit:** `9ebd475` (local; not pushed)

## Summary

Added the missing process entrypoint `src/ai_steward_wiki/__main__.py` that
composes already-built modules into a runnable bot. Fulfills the contract
already declared in `deploy/systemd/aisw-bot.service`
(`ExecStart=python -m ai_steward_wiki`) and enables local testing with
`AISW_TG_BOT_TOKEN_LOCAL`.

## What ships

- `src/ai_steward_wiki/__main__.py` — single-file entrypoint with full async
  lifecycle (bootstrap → polling → graceful shutdown).
- `src/ai_steward_wiki/settings.py` — new optional field `users_toml_path`
  (None / missing → empty allowlist for frictionless local first-run).
- `tests/unit/test_runtime_wiring.py` — 13 unit tests.
- Discovery + Design specs under `docs/superpowers/specs/`.
- Knowledge-graph / development-plan / verification-plan updated.

## Lifecycle (in order)

1. `configure_logging` from active `Settings.log_level`
2. Fail-fast on missing `tg_bot_token` for the active env
3. `_ensure_data_dirs` + `_run_all_migrations` (alembic upgrade head, per DB,
   via `asyncio.to_thread`)
4. Build async engines for jobs / audit / sessions
5. `_load_users_config` → `sync_to_sessions_db` → `replace_global` allowlist
6. `build_scheduler(...).start()` (APScheduler, sync SQLite URL)
7. `build_bot(token)` + `build_dispatcher(allowlist)`
8. `_install_signal_handlers` wires SIGINT + SIGTERM → stop event
9. `dp.start_polling(bot)` racing with stop event
10. Graceful shutdown: stop_polling → cancel polling task → scheduler.shutdown
    → engine.dispose × 3 → bot.session.close

## Out of scope (explicit deferral)

- No production message handlers. Middleware gates updates; unrouted messages
  are silently ignored by aiogram default. The full TG → classifier → runner
  pipeline lands in a separate future chunk (working title:
  `M-TG-HANDLERS-WIRING`).
- No webhook mode — long-polling only.

## Quality gates (all green)

| Gate | Result |
|---|---|
| `ruff check .` | passed |
| `ruff format --check .` | 155 files formatted |
| `mypy --strict src` | 62 files, no issues |
| `grace lint --failOn errors` | 0 errors / 0 warnings |
| `make inv-lint` | 14/14 INV checks pass |
| `pytest tests/unit` | 330 passed, 1 skipped |
| Coverage | 91.08% (gate 80%) |
| Pre-commit (commit time) | ruff / ruff-format / mypy / gitleaks all passed |

## How to run locally

```bash
# .env (already present in repo root):
#   AISW_ENV=local
#   AISW_TG_BOT_TOKEN_LOCAL=<from @BotFather>
#   AISW_JOBS_DB_URL=sqlite+aiosqlite:///data/jobs.db   # (+audit + sessions)
#   AISW_TG_ADMIN_TELEGRAM_IDS=<your telegram_id>
uv sync
uv run python -m ai_steward_wiki     # starts polling; Ctrl-C for graceful shutdown
```

## Tests added

| Test | Asserts |
|---|---|
| `test_sync_url_strips_aiosqlite` | `+aiosqlite` stripped |
| `test_sync_url_passthrough_for_plain_sqlite` | plain `sqlite://` passthrough |
| `test_sync_url_rejects_non_sqlite` | fail-fast on postgresql |
| `test_ensure_data_dirs_creates_parent` | mkdir parents |
| `test_ensure_data_dirs_ignores_non_file_urls` | `:memory:` no-op |
| `test_load_allowlist_none_path_returns_empty` | None → empty |
| `test_load_allowlist_missing_file_returns_empty` | missing file → empty |
| `test_load_allowlist_reads_existing_file` | TOML parsed |
| `test_install_signal_handlers_sets_event_on_sigterm` | signal → event.set |
| `test_amain_composes_and_shuts_down_cleanly` | full lifecycle with mocks |
| `test_amain_requires_active_tg_token` | RuntimeError on missing token |
| `test_main_invokes_asyncio_run` | `main()` → `asyncio.run` |
| `test_signal_constants_available` | SIGINT/SIGTERM presence |

## Next chunk (suggested)

`M-TG-HANDLERS-WIRING` — register routers/handlers on the Dispatcher so
allowlisted messages flow through `M-INBOX` → `M-CLASSIFIER-STAGE0` →
`M-WIKI-RUNNER` and replies go via `M-TG-TEXT.deliver_output`.
