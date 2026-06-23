---
feature: l2-dedup-ttl
bd_id: aisw-5hy
module_id: M-INGEST-IDEM
status: stable
date: 2026-05-13
stack:
  - library: sqlalchemy
    version: 2.x async (current uv.lock)
    used_for: SeenFile model + composite PK + ON CONFLICT clause
  - library: alembic
    version: 1.x (per-DB env audit)
    used_for: schema migration (PK swap, no data backfill)
  - library: pydantic-settings
    version: 2.x (current uv.lock)
    used_for: 2 new Settings fields (text/voice TTL, binary TTL)
decisions:
  - D-local-1: SeenFile PK becomes composite (owner_telegram_id, content_sha256). Existing PK on content_sha256 alone is replaced; `owner_telegram_id` loses its standalone index (now leading column of PK).
  - D-local-2: check_content WHERE clause adds `first_seen_at_utc > now - ttl_for_kind`. Older rows are treated as not-seen → ON CONFLICT path triggers `DO UPDATE SET first_seen_at_utc=excluded.first_seen_at_utc` (upsert), keeping the row but resetting the window.
  - D-local-3: "TTL per kind: `text/voice → AISW_L2_TTL_TEXT_SECONDS=60`, `photo/file → AISW_L2_TTL_BINARY_SECONDS=2592000` (30d). Both in Settings, .env-overridable."
  - D-local-4: "Migration drops legacy seen_files data (DELETE all rows then alter PK). Justification: forensic-only state, 30d retention, no business loss; alternative (data-preserving PK rebuild on SQLite) requires temp-table copy and is overkill for audit data."
  - D-local-5: "`inbox.idempotency.l2_duplicate` log event gains `within_ttl: bool` field. `l2_new` unchanged."
  - D-local-6: ACK_DEDUP_RU message stays the same — only fires now when truly within TTL, so the meaning matches.
---

# Design: L2 dedup per-kind TTL + owner-scope

## Approach

### Schema (storage/audit/models.py)

```python
class SeenFile(Base):
    __tablename__ = "seen_files"

    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)
```

Composite PK gives:
- O(1) lookup by `(owner, sha256)` — leftmost-prefix rule covers owner-only scans too.
- Per-owner uniqueness → no cross-user collision.
- `first_seen_at_utc` keeps its index — `seen_files_purge` job still uses it.

### Lookup (inbox/idempotency.py: check_content)

```python
ttl = self._ttl_for_kind(kind)  # text/voice → 60s; photo/file → 2592000s
cutoff = now - timedelta(seconds=ttl)

stmt = (
    sqlite_insert(SeenFile)
    .values(owner_telegram_id=owner, content_sha256=sha, kind=kind, first_seen_at_utc=now)
    .on_conflict_do_update(
        index_elements=[SeenFile.owner_telegram_id, SeenFile.content_sha256],
        set_={"first_seen_at_utc": now},
        where=(SeenFile.first_seen_at_utc < cutoff),  # only refresh if expired
    )
)
result = await session.execute(stmt)
inserted = result.rowcount > 0  # True for new OR refreshed-after-TTL
```

Then SELECT existing row only if `inserted == False`:

```python
if not inserted:
    row = await session.execute(
        select(SeenFile).where(
            SeenFile.owner_telegram_id == owner,
            SeenFile.content_sha256 == sha,
            SeenFile.first_seen_at_utc > cutoff,  # within TTL only
        )
    ).scalar_one_or_none()
    if row is not None:
        # true L2 hit
        return sha, SeenFileMatch(..., within_ttl=True)
```

Why this shape (ON CONFLICT DO UPDATE WHERE):
- Atomic. No SELECT-then-INSERT race.
- Single statement handles three states: new row, expired row (refresh), within-TTL collision (no-op + later SELECT).
- `inserted` (`rowcount > 0`) reliably distinguishes "proceed" vs "L2 hit"; the follow-up SELECT only runs in the rare hit path.

### Migration (alembic/audit/versions/000X_seen_files_owner_pk_ttl.py)

```python
def upgrade() -> None:
    # Audit-only data; D-018 §retention allows wipe on schema change.
    op.execute("DELETE FROM seen_files")
    with op.batch_alter_table("seen_files", recreate="always") as batch:
        batch.drop_constraint("pk_seen_files", type_="primary")
        batch.create_primary_key("pk_seen_files", ["owner_telegram_id", "content_sha256"])
        batch.drop_index("ix_seen_files_owner_telegram_id")  # now PK-leading

def downgrade() -> None:
    with op.batch_alter_table("seen_files", recreate="always") as batch:
        batch.drop_constraint("pk_seen_files", type_="primary")
        batch.create_primary_key("pk_seen_files", ["content_sha256"])
        batch.create_index("ix_seen_files_owner_telegram_id", ["owner_telegram_id"])
```

SQLite requires `batch_alter_table(recreate="always")` to rebuild table for PK change. Alembic handles temp-table dance.

### Settings (settings.py)

```python
# L2 ingest dedup (D-018 amended 2026-05-13).
l2_ttl_text_seconds: int = 60            # text/voice: retry-storm protection only
l2_ttl_binary_seconds: int = 30 * 24 * 3600  # photo/file: 30d artifact dedup
```

`.env.example` gains:
```
AISW_L2_TTL_TEXT_SECONDS=60
AISW_L2_TTL_BINARY_SECONDS=2592000
```

### Service wiring

`IdempotencyService.__init__` gains 2 params:
```python
def __init__(
    self,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    ttl_text_seconds: int = 60,
    ttl_binary_seconds: int = 2592000,
) -> None:
```
Wiring in `__main__.py` / DI passes `settings.l2_ttl_text_seconds` and `settings.l2_ttl_binary_seconds`.

### SeenFileMatch dataclass

Add `within_ttl: bool` field — present for future soft-confirm path (Variant 3, deferred), already useful in audit logs and tests.

## Test strategy

`tests/unit/inbox/test_idempotency.py` — extend with 4 invariants:

1. `test_l2_text_passes_after_ttl` — insert "я спал 8 часов", freeze clock +61s, second send → `match is None`, `inserted=True` (refreshed row).
2. `test_l2_text_blocks_within_ttl` — same text twice within 30s → second returns `SeenFileMatch(within_ttl=True)`.
3. `test_l2_photo_blocks_at_1h` — same bytes 3600s apart → match still hits (binary TTL=30d).
4. `test_l2_cross_owner_no_collision` — owner A sends text X, owner B sends same X → both succeed, two rows in DB.

Existing tests in `test_idempotency.py` likely need minor adjustment to pass owner-scope assertion (their content-only collision expectations change).

Smoke test for migration: `tests/integration/test_audit_migration_l2_ttl.py` — apply upgrade on a tmp audit.db with seeded legacy rows, assert schema = composite PK, table empty (data dropped per D-local-4).

## Log anchors

- `inbox.idempotency.l2_new` — unchanged.
- `inbox.idempotency.l2_duplicate` — gains `within_ttl: True` (only path that fires now).
- `inbox.idempotency.l2_refreshed` — new, fires when expired row is upserted (`inserted=True` but row already existed for owner+sha).

## Out-of-scope (deferred)

- Soft-confirm inline buttons (Variant 3 from /best-approach).
- Domain-aware dedup (Variant 4).
- Composite indexes optimization beyond PK.
- L1 (`tg_updates`) — keeps 24h behaviour, untouched.

## Open Questions

None — all D-local-1..6 decided. ADR captures D-local-1, D-local-3, D-local-4 (data drop).
