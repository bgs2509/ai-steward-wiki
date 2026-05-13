# ADR-028: L2 Ingest Dedup — Per-Kind TTL + Owner-Scope Composite PK

**Status:** Accepted
**Date:** 2026-05-13
**Beads:** aisw-5hy
**Amends:** [D-018](../Spec-WIKI/decisions/D-018-ingest-idempotency.md) §"Layer L2 — content hash"

## Context

D-018 L2 layer (content SHA-256 dedup over `seen_files`, retention 30d) currently has two production-breaking defects:

1. **Semantic — no time window in lookup.** `IdempotencyService.check_content` looks up `seen_files` by `content_sha256` alone. TTL=30d is enforced by a background purge job (`ops.retention.seen_files_purge`), not by SELECT. Consequence: legitimate daily-repeat text (`"я спал 8 часов"`, `"вес 75"`, `"ок"`, recurring queries) is blocked for the full retention window. Habit-tracking — a primary life-domain use case — is unusable.
2. **Structural — global hash space.** `SeenFile.content_sha256` is the sole PK. Two different `telegram_id`s sending identical normalized payloads collide; the second sender receives an L2-hit ACK for a row owned by the first sender. D-018 spec describes L2 as per-owner; the implementation contradicts the spec.

The single-line, single-TTL model in D-018 conflates two content classes with opposite semantics:
- **Text/voice** — event-log content; identical payload across days = different events.
- **Photo/file** — artifact content; identical bytes ≈ accidental resend.

## Alternatives

1. **A. Disable L2 for text entirely; keep for binary.**
   Pros: trivial diff. Cons: loses retry-storm protection for text (TG webhook 3× retries on flaky network); throws away the cheap fast-path that prevents Classifier/Claude invocation on duplicates.
2. **B. Single short TTL (60s) for all kinds.**
   Pros: one knob, one rule. Cons: photo/file re-uploads within a week pass through silently — for binaries, that is usually a user error worth flagging.
3. **C. Per-kind TTL + owner-scope composite PK.** *(this ADR)*
   Pros: matches the semantic split of content classes; preserves retry-storm protection for text/voice; preserves artifact-dedup behaviour for photo/file; fixes the cross-owner collision in the same migration. Cons: requires Alembic migration (PK swap), 2 new Settings fields.
4. **D. Soft-confirm inline buttons on every L2 hit.**
   Pros: zero data loss, user-driven. Cons: UX noise for daily habit logs; doesn't help with retry-storm (yields N buttons per second); deferred.
5. **E. Domain-aware dedup (classifier runs before L2).**
   Pros: semantically perfect. Cons: inverts the pipeline architecture (L2 is fast-path BEFORE Claude/classifier specifically to avoid cost); deferred.

## Decision

Adopt **Alternative C** for the L2 layer:

1. **Composite PK.** `seen_files` PK becomes `(owner_telegram_id, content_sha256)`. Existing standalone index on `owner_telegram_id` is dropped (now leading column of PK).
2. **Per-kind TTL.**
   - `text` / `voice` → `AISW_L2_TTL_TEXT_SECONDS = 60` (retry-storm only).
   - `photo` / `file` → `AISW_L2_TTL_BINARY_SECONDS = 2592000` (30d, current artifact retention).
3. **Atomic upsert pattern.** `check_content` uses `INSERT … ON CONFLICT DO UPDATE … WHERE first_seen_at_utc < now − ttl`. New rows AND expired rows refresh atomically; only within-TTL collisions return a `SeenFileMatch`.
4. **`SeenFileMatch.within_ttl: bool`** — present for future soft-confirm (Variant D, deferred) and for audit logs.
5. **Migration drops legacy `seen_files` data.** Forensic-only state, 30d retention, no business loss; SQLite PK-change requires `batch_alter_table(recreate="always")` and avoiding a data-preserving rebuild keeps the migration trivially atomic.
6. **D-018 is amended in-place** (Spec-WIKI markdown) with a 2026-05-13 note pointing to this ADR for the per-kind TTL rule. L1 (`tg_updates`, 24h) is untouched.

## Consequences

**Positive:**
- Daily habit-log text passes through L2 from day 2 onward.
- Cross-owner collisions disappear.
- Retry-storm protection preserved for text/voice (60s window covers TG webhook retry envelope).
- Photo/file behaviour unchanged from user perspective.
- TTLs are operator-tunable via `.env` without code redeploy.

**Negative / accepted:**
- Existing `seen_files` rows are wiped on migration (≤30d of forensic state; `dedup_hits` audit table at 90d retention is unaffected).
- The `inbox.idempotency.l2_duplicate` event semantics narrow — it now only fires for within-TTL collisions, which is the intent but does change historical log queries.
- `MODULE_CONTRACT` for `M-INGEST-IDEM` must be amended (purpose line: "per owner, per kind, with per-kind TTL").
- Soft-confirm UX (Variant D) and domain-aware dedup (Variant E) remain open future work — tracked separately, not blocked by this change.
