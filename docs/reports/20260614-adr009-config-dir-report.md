# Completion report — ADR-009: single explicit `CLAUDE_CONFIG_DIR`

- Date: 2026-06-14
- Epic: `aisw-cxz` (closed) · Chunk: `aisw-wt5` (closed)
- Branch: `worktree-adr-dev-life-separation`
- Commits: `0d64251` (ADRs), `1e2debd` (refactor)
- Driver: `/superautocoder` (single chunk)

## What changed

Implemented ADR-009. `claude_config_dir` is now a **single explicit field**
(`claude_config_dir: Path = Path("/var/lib/ai-steward-wiki/claude-code")`,
overridable via `AISW_CLAUDE_CONFIG_DIR`), **decoupled from `AISW_ENV`**.

1. `settings.py` — dropped `claude_config_dir_local` / `claude_config_dir_vps` slots
   and the env-resolving `@property`; added the single field; simplified the INV-6
   validator (field is never `None`); version 0.0.13.
2. `__main__.py` — added `_require_claude_config_dir()` startup fail-fast (clear
   message + `claude login` hint) called in `_amain`; removed the three dead
   `if settings.claude_config_dir is None` guards (classifier backend, wiki runner,
   cron consumer).
3. `.env.example` — migrated `AISW_CLAUDE_CONFIG_DIR_LOCAL` / `_VPS` →
   `AISW_CLAUDE_CONFIG_DIR`.
4. `docs/knowledge-graph.xml` — Settings node updated.
5. Tests — `test_settings_config_dir.py` (new: default, override, env-independence,
   old-slots-gone, fail-fast missing/present); `test_settings_inv6.py` updated.

`AISW_ENV` now governs **only** the Telegram token (`tg_bot_token_local/_prod`).

## Verification

- `make total-test`: **PASS** — ruff, ruff-format, mypy --strict, grace lint
  (errors=0), inv-lint (14/14), pytest unit **864 passed / 0 failed**, coverage
  **86.82%** (threshold 80%).
- vulture (changed files): 0 dead-code after the removals.
- `full-audit --ai` (change-scoped): **0 critical, 0 middle, 2 minor**. See
  `docs/reports/2026-06-14-audit.md`.

## Deviations

- The post-chunk `make total-test` first failed on one **unrelated** test
  (`test_maintenance_jobstore::...does_not_pickle_sessionmaker`) — root cause: it
  opens the default relative `data/` jobstore path, absent in a fresh worktree
  (runtime creates it via `_ensure_data_dirs`; the test does not). Created `data/`
  (gitignored) to match the normal environment → green. Filed `aisw-3lx`.
- `full-audit` ran change-scoped (not full repo-wide): the worktree has no Claude CLI
  auth, so integration / sentrux / uvx / pip-audit steps are environment-blocked.
  Run full `/full-audit --ai` on the main checkout / CI.

## Follow-ups (filed, not in scope)

1. `aisw-3lx` (P3, bug) — test isolation: `test_maintenance_jobstore` depends on cwd
   `./data`.
2. `aisw-at7` (P4) — annotate superseded two-slot config-dir in the 20260510 design
   specs.
3. `aisw-1yl` (P4) — add an `AISW_CLAUDE_CONFIG_DIR` env-var → field mapping test.

## Not done (awaiting deployment alignment — ADR-008/010)

- Deploy alignment (run under `bgs` on the dedicated VPS, no service user) and the
  VPS `.env` migration to `AISW_CLAUDE_CONFIG_DIR` + creating/authenticating the
  config dir on the box. The current outage fix on the VPS still needs that dir to
  exist (`mkdir -p ... && claude login`).
