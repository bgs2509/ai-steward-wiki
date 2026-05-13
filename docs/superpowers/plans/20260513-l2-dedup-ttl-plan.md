---
feature: l2-dedup-ttl
bd_id: aisw-5hy
module_id: M-INGEST-IDEM
discovery: docs/superpowers/specs/20260513-l2-dedup-ttl-discovery.md
design: docs/superpowers/specs/20260513-l2-dedup-ttl-design.md
adr: docs/adr/ADR-028-l2-dedup-per-kind-ttl.md
date: 2026-05-13
status: ready
---

# Implementation Plan: L2 dedup per-kind TTL + owner-scope (aisw-5hy)

## Pre-flight

- [x] Discovery approved (FR/NFR/Scope)
- [x] Design approved (composite PK, per-kind TTL, atomic upsert)
- [x] ADR-028 written
- [x] Lint baseline clean (ruff/format/mypy green)

## Step 1 — Settings (RED→GREEN)

**Files:** `src/ai_steward_wiki/settings.py`, `.env.example`

1. Add two fields under existing AISW_ prefix block:
   ```python
   l2_ttl_text_seconds: int = 60
   l2_ttl_binary_seconds: int = 30 * 24 * 3600
   ```
2. Add to `.env.example`:
   ```
   AISW_L2_TTL_TEXT_SECONDS=60
   AISW_L2_TTL_BINARY_SECONDS=2592000
   ```
3. Verify with `uv run python -c "from ai_steward_wiki.settings import Settings; s=Settings(); print(s.l2_ttl_text_seconds, s.l2_ttl_binary_seconds)"` → `60 2592000`.

## Step 2 — Model + Migration (RED→GREEN)

**Files:** `src/ai_steward_wiki/storage/audit/models.py`, new `alembic/audit/versions/000X_seen_files_owner_pk_ttl.py`

1. Edit `SeenFile`:
   ```python
   class SeenFile(Base):
       __tablename__ = "seen_files"
       owner_telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
       content_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
       kind: Mapped[str] = mapped_column(String(16), nullable=False)
       first_seen_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)
   ```
2. Generate revision: `uv run alembic -c alembic/audit/alembic.ini revision -m "seen_files owner pk ttl" --rev-id=0007`.
3. Hand-edit `upgrade`/`downgrade` per design doc (DELETE legacy rows, `batch_alter_table(recreate="always")`, swap PK, drop redundant index).
4. Apply: `uv run alembic -c alembic/audit/alembic.ini upgrade head` against tmp DB (smoke-test via integration test in Step 5).

## Step 3 — IdempotencyService (RED→GREEN)

**File:** `src/ai_steward_wiki/inbox/idempotency.py`

1. Update MODULE_CONTRACT `PURPOSE` line: `Two-layer ingest dedup (D-018 amended 2026-05-13, ADR-028): L1 TG update_id (24h) + L2 SHA-256 content hash (per-owner, per-kind TTL: text/voice=60s, photo/file=30d).`
2. Add `within_ttl: bool` to `SeenFileMatch`.
3. `IdempotencyService.__init__` gains kwargs `ttl_text_seconds: int = 60`, `ttl_binary_seconds: int = 2592000`. Store as `self._ttl_text`, `self._ttl_binary`.
4. New private `_ttl_for_kind(kind)` returns seconds based on kind.
5. Rewrite `check_content` per design doc — `ON CONFLICT DO UPDATE … WHERE first_seen_at_utc < cutoff`, follow-up SELECT only on `inserted=False`.
6. Add new log event `inbox.idempotency.l2_refreshed` when upsert refreshed an expired row (i.e. `inserted=True` but row pre-existed — detect via "did we conflict?" by re-fetching after upsert if needed; simpler: separate SELECT-before-UPSERT in test, or rely on `rowcount` semantics). **Decision:** use simpler 2-phase — `SELECT … WHERE owner=? AND sha=?`; if row exists and within TTL → hit; if exists and expired → UPDATE; if absent → INSERT. Wrap in single transaction. Trade-off: 1 extra SELECT in steady-state vs simpler logic. Acceptable for an audit-tier code path.
7. Emit `within_ttl=True` in `l2_duplicate` log only on real hit.

## Step 4 — Wiring (GREEN)

**Files:** `src/ai_steward_wiki/__main__.py` (or wherever `IdempotencyService` is constructed)

1. Grep: `grep -rn "IdempotencyService(" src/` to find construction sites.
2. Pass `ttl_text_seconds=settings.l2_ttl_text_seconds, ttl_binary_seconds=settings.l2_ttl_binary_seconds`.

## Step 5 — Tests (RED first, then GREEN)

**File:** `tests/unit/inbox/idempotency/test_idempotency.py`

1. Update existing cross-owner test (`line ~110-118`): expectation flips — owner 2002 sending same normalized text as owner 1001 → `m2 is None` (no collision). Rename if needed: `test_l2_cross_owner_no_collision`.
2. Add `test_l2_text_passes_after_ttl` — use `freezegun` or `monkeypatch` of `_utc_naive`; insert text, advance clock +61s, second send → `match is None`.
3. Add `test_l2_text_blocks_within_ttl` — same text twice within 30s → `match.within_ttl is True`.
4. Add `test_l2_photo_blocks_at_1h` — same bytes 1h apart → `match.within_ttl is True` (binary TTL=30d).
5. Add `test_l2_refreshes_expired_row` — insert, advance 61s, second send → exactly one row in `seen_files` for that (owner, sha), `first_seen_at_utc` ≈ now (refreshed).
6. Run tests: `uv run pytest tests/unit/inbox/idempotency -v`. Confirm all green.

**File:** `tests/integration/storage/test_audit_migration_l2_ttl.py` (new)

7. Apply Alembic upgrade on tmp audit.db with 3 seeded legacy rows; assert post-upgrade: composite PK (`PRAGMA table_info`, `PRAGMA index_list`), table empty.
8. Run: `RUN_INTEGRATION=1 uv run pytest tests/integration/storage/test_audit_migration_l2_ttl.py -v`.

## Step 6 — Spec amendment

**File:** `docs/Spec-WIKI/decisions/D-018-ingest-idempotency.md`

1. Append section `## Уточнение 2026-05-13 (per-kind TTL, owner-scope PK)` with 2-3 lines pointing to ADR-028.

## Step 7 — Quality gates

1. `make lint` — must stay green.
2. `uv run pytest tests/unit -q` — full unit suite green.
3. `grace lint --failOn errors` (if available in project).
4. `grace-refresh` to sync `knowledge-graph.xml` + `verification-plan.xml` for `M-INGEST-IDEM`.

## Step 8 — Commit + bd close

1. `smart-commit` or manual commits:
   - `feat(M-INGEST-IDEM): per-kind L2 TTL + owner-scope composite PK (aisw-5hy)`
   - `docs(adr): ADR-028 per-kind L2 dedup TTL`
   - `chore(audit-migration): alembic 0007 seen_files owner_pk_ttl`
2. `bd close aisw-5hy --reason="L2 dedup per-kind TTL + owner-scope shipped"`.

## Verification matrix (FR → step)

| FR    | Verified by                                          |
|-------|------------------------------------------------------|
| FR-1  | Step 5 test_l2_text_passes_after_ttl                 |
| FR-2  | Step 5 test_l2_text_blocks_within_ttl                |
| FR-3  | Step 5 test_l2_photo_blocks_at_1h                    |
| FR-4  | Step 5 test_l2_cross_owner_no_collision (updated)    |
| FR-5  | Step 1 Settings + .env.example, Step 4 wiring        |
| FR-6  | Existing tests on `record_dedup_choice` unchanged    |
| FR-7  | Step 2 migration + Step 5 integration test           |

## Out of scope

- Soft-confirm inline buttons (Variant 3 from /best-approach).
- Domain-aware dedup (Variant 4).
- L1 (`tg_updates`) changes.
