# step-09-plan.md — Chunk 9 / M-INGEST-IDEM

**bd_id:** aisw-6t5
**Module:** M-INGEST-IDEM
**Window estimate:** 0.25
**Source:** D-018 (amended 2026-05-10) — two-layer ingest dedup without LLM.

## Goal

Implement ingest idempotency per D-018 in `src/ai_steward_wiki/inbox/idempotency.py`:
1. **L1** — TG `update_id` INSERT OR IGNORE into `audit.db.tg_updates` (24h TTL, GC out of scope here).
2. **L2** — SHA-256 content hash into `audit.db.seen_files` with normalisation:
   text → `NFKC + strip + lower + collapse whitespace`; voice/photo/file → raw bytes.
3. **Dedup audit** — `record_dedup_choice` appends `audit.db.dedup_hits`.

Both layers live in `audit.db` (INV-4, amended D-018). No auto-block on L2 — caller (TG layer in chunk 10) decides UX.

## Steps (TDD)

1. **Recon** — confirm `TgUpdate`, `SeenFile`, `DedupHit` already exist in
   `src/ai_steward_wiki/storage/audit/models.py` (baseline 0001).
2. **Tests RED** — `tests/unit/inbox/idempotency/test_idempotency.py`:
   - normalize_text NFKC + whitespace collapse cases
   - compute_content_hash text-invariance and bytes-kinds
   - compute_content_hash type-mismatch raises TypeError
   - L1 first-sight True, second False, independence across update_ids
   - L2 first-sight registers, returns None
   - L2 duplicate preserves first owner_telegram_id
   - L2 bytes collision detected regardless of kind label
   - record_dedup_choice writes DedupHit row
   - L2 collision does not block (no exception)
3. **GREEN** — `src/ai_steward_wiki/inbox/idempotency.py`:
   `normalize_text`, `compute_content_hash`, `SeenFileMatch` dataclass,
   `IdempotencyService` (check_update_id / check_content / record_dedup_choice)
   over `async_sessionmaker[AsyncSession]`. SQLite `INSERT … ON CONFLICT DO NOTHING`
   for atomic L1/L2 register. structlog events on every code path.
4. **Quality gate** (all green):
   - `uv run pytest tests/unit/inbox/idempotency -q`
   - `uv run pytest tests/unit -q`
   - `make lint`
   - `make grace-lint`
   - `make total-test`
   - `uv run python scripts/lint_invariants.py`
5. **Commit** — `feat(M-INGEST-IDEM): L1 tg_updates + L2 seen_files dedup per D-018`
   with `bd_id: aisw-6t5` trailer.
6. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=10,
   ClosedChunks+=9, note) + `bd close aisw-6t5`.

## Verification

```bash
uv run pytest tests/unit/inbox/idempotency -q       # 10 passed
make lint                                           # ruff + format + mypy ✓
make grace-lint                                     # errors=0
make total-test                                     # full pipeline exit 0
uv run python scripts/lint_invariants.py            # INV-7 ok
```

## Out of scope

1. APScheduler `tg_updates_purge` / `seen_files_purge` retention sweepers (later chunk).
2. TG inline-confirm UX (chunk 10 — M-TG-TEXT consumes `SeenFileMatch`).
3. LLM-based L3 semantic dedup (D-018 §"Layer 3" — not in MVP).
