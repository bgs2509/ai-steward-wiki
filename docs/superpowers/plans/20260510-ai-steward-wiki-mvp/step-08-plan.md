# step-08-plan.md — Chunk 8 / M-WIKI-LIFECYCLE

**bd_id:** aisw-9s4
**Module:** M-WIKI-LIFECYCLE
**Window estimate:** 0.55

## Goal
Add NL-driven WIKI lifecycle layer per discovery+design 2026-05-10:
ISO 9 name normalisation, anti-spam cap + Levenshtein near-duplicate,
soft-delete + restore, frontmatter v1→v2 linear migration, 5-step pre-flight
grounding, INV-7 lint script.

## Steps (TDD)

1. **Settings** — add `wiki_root`, `wiki_max_per_user`, `wiki_trash_retention_days`,
   `wiki_template_dir` to `Settings`.
2. **name.py (RED → GREEN)** — `tests/unit/wiki/lifecycle/test_name.py`:
   ISO 9 mapping cases, PascalCase, regex reject, lookup forms. Then
   `wiki/name.py`: `WikiName` frozen Pydantic + `normalize_wiki_name`.
3. **lifecycle.py (RED → GREEN)** — `test_lifecycle.py`: cap enforcement,
   Levenshtein near-match → existing, soft-delete atomic move, restore within
   window, trash excluded from cap. Then `wiki/lifecycle.py`:
   `WikiLifecycleManager` + Pydantic `TrashedWiki`, `NearDuplicateMatch`,
   `_levenshtein`, exceptions.
4. **migration.py (RED → GREEN)** — `test_migration.py`: v1 user content
   preserved, idempotent on v2, atomic tmp+replace, schema_version bumped.
   Then `wiki/migration.py`: `Frontmatter`, parser/renderer, zone parser,
   `migrate_v1_to_v2`.
5. **preflight.py (RED → GREEN)** — `test_preflight.py`: each step pass/fail.
   Then `wiki/preflight.py`: `PreflightCheck`, `PreflightReport`, `preflight()`.
6. **Barrel** — extend `wiki/__init__.py` MODULE_MAP + re-exports.
7. **INV-7 lint** — `scripts/lint_invariants.py` with grep checks; exit-1 on hit.
8. **Quality gate** (must pass):
   - `uv run pytest tests/unit/wiki/lifecycle -q`
   - `uv run pytest tests/unit -q`
   - `uv run ruff check src/ai_steward_wiki/wiki tests/unit/wiki`
   - `uv run ruff format --check src/ai_steward_wiki/wiki tests/unit/wiki`
   - `uv run mypy src/ai_steward_wiki/wiki`
   - `make lint`
   - `make total-test`
   - `python scripts/lint_invariants.py`
9. **Commit** — `feat(M-WIKI-LIFECYCLE): wiki naming, anti-spam cap, soft-delete, frontmatter v1→v2 migration`
   with `bd_id: aisw-9s4` trailer.
10. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=9,
    ClosedChunks+=8, append note) + `bd close aisw-9s4`.

## Verification

```bash
uv run pytest tests/unit/wiki/lifecycle -q
make lint
make total-test
python scripts/lint_invariants.py
```

## Out of scope

1. APScheduler trash-purge sweeper (chunk 13).
2. Full domain template catalogue (chunk 15).
3. systemd-run wrap (chunk 16).
4. Real Claude CLI invocation (already chunk 7 nightly).
