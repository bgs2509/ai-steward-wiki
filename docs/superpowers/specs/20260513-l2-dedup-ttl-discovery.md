---
feature: l2-dedup-ttl
bd_id: aisw-5hy
module_id: M-INGEST-IDEM
status: stable
date: 2026-05-13
fr:
  - FR-1: Daily-repeat text messages with identical normalized payload (e.g. "я спал 8 часов") MUST pass through L2 on subsequent days and reach the classifier/WIKI runner
  - FR-2: L2 still suppresses near-instant retry-storms for text/voice (TG webhook retry, double-tap) within a short configurable window
  - FR-3: Photo/file with identical bytes MUST keep current behaviour — silently skipped for the full retention window (30 days) — these are genuine artifact duplicates
  - FR-4: Two different owners (telegram_id) sending identical content MUST NOT collide — each owner has independent dedup space
  - FR-5: L2 dedup window is per-kind and externally configurable via .env (AISW_L2_TTL_TEXT_SECONDS, AISW_L2_TTL_BINARY_SECONDS) with sane defaults (60s text/voice, 30d photo/file)
  - FR-6: Existing dedup_hits audit semantics preserved — when L2 fires we still write a row with action="auto_skip"
  - FR-7: One-shot Alembic migration converts seen_files PK from (content_sha256) to composite (owner_telegram_id, content_sha256), backfilling no data (purge can re-fill within 30d)
nfr:
  - NFR-1: check_content latency stays O(1) per call — must keep unique index, no full-table scan
  - NFR-2: structlog event inbox.idempotency.l2_duplicate gains within_ttl=True|False field
  - NFR-3: mypy --strict + ruff + grace lint clean; no new ignores
  - NFR-4: Unit tests cover: legit repeat after TTL (text), retry-storm within TTL (text), photo dedup at 1h still hits, cross-owner same-text passes for both
  - NFR-5: Backward compatibility — existing seen_files rows MAY be dropped during migration (audit-only data, no business loss)
constraints:
  - SQLite + Alembic per-DB (audit.db has its own alembic env)
  - SeenFile PK is part of the public DB schema — index/PK change requires explicit Alembic revision
  - D-018 spec says "TTL 30d" globally — this feature amends D-018 with per-kind TTL (will be reflected via ADR + D-018 amendment note)
  - Pipeline still uses fast-path L2 BEFORE classifier (no Stage-0 Haiku before dedup — cost preservation)
risks:
  - R-1 (data loss on migration): dropping seen_files rows loses 30d of retry-protection state; mitigated by short TTL=60s for text — replays naturally within 60s window, photo/file replays from scratch (acceptable, retention is forensic not load-bearing)
  - R-2 (TTL too short): 60s may miss legit retries beyond 1 min (rare TG webhook backoff); mitigated by .env config — operator can raise
  - R-3 (TTL too long): >60s starts hitting daily-log repeats; mitigated by per-kind split — daily logs are text, photo/file keep 30d
  - R-4 (clock skew): first_seen_at_utc is server UTC, no client clock involved — non-issue
scope_in:
  - src/ai_steward_wiki/storage/audit/models.py (SeenFile PK → composite)
  - src/ai_steward_wiki/inbox/idempotency.py (per-kind TTL filter in check_content)
  - src/ai_steward_wiki/config/settings.py (or wherever Settings lives — add 2 fields)
  - alembic/audit/versions/000X_seen_files_owner_pk_ttl.py (new revision)
  - tests/unit/inbox/test_idempotency.py (extend with TTL + owner-scope cases)
  - docs/Spec-WIKI/decisions/D-018-ingest-idempotency.md (amendment 2026-05-13)
  - docs/adr/ADR-NNN-l2-dedup-per-kind-ttl.md (new ADR)
scope_out:
  - Soft-confirm (inline-buttons "записать снова?") — deferred, Variant 3 from /best-approach
  - Domain-aware dedup (Variant 4) — deferred
  - L1 dedup changes (tg_updates TTL=24h stays)
  - dedup_hits retention (already 90d, unchanged)
---

# Discovery: L2 dedup per-kind TTL + owner-scope (aisw-5hy)

## Symptom

Юзер отправляет одинаковый текст ежедневно (`"я спал 8 часов"`, `"вес 75"`, `"ок"`) — после первого раза все последующие в течение ~30 дней режутся `ACK_DEDUP_RU = "Уже видел такое сообщение — повторно не запускаю."`, классификатор и WIKI-runner не вызываются, данные не попадают в `<Domain>-WIKI/`.

## Root cause

Два независимых дефекта в `src/ai_steward_wiki/inbox/idempotency.py` + `storage/audit/models.py`:

1. **Семантический:** L2 lookup проверяет только факт наличия `content_sha256` в `seen_files`, без time-window filter. TTL=30d реализован через background purge (`ops/retention.py: seen_files_purge`), а не через SELECT. → дубль считается дублем все 30 дней. Для текстовых habit-логов это убивает функциональность.
2. **Структурный:** `SeenFile.content_sha256` — PK *без* `owner_telegram_id`. Хеш-пространство глобальное. Два разных юзера с одинаковой нормализованной фразой коллидируют (юзер B получит ACK как «дубль» сообщения юзера A). MODULE_CONTRACT обещает "per owner", реализация не соответствует.

## Why per-kind TTL (Variant 2 from /best-approach)

- **Text/voice — event-log семантика:** «я спал 8 часов» сегодня и завтра — два разных события, нормально иметь одинаковый payload. Дедуп нужен только от retry-storm (TG webhook 3× за 60с при флакающей сети, double-tap «Send»). TTL=60s покрывает все известные retry-сценарии TG.
- **Photo/file — artifact семантика:** тот же файл с теми же байтами почти всегда означает «случайно отправил повторно». Сохраняем TTL=30d (текущее retention поведение).

Этот разрыв — фундаментальный. Один глобальный TTL не работает.

## Why owner-scope index

D-018 §"Layer L2 — content hash (`seen_files`)" говорит про per-owner space. Текущий PK на одном `content_sha256` — баг реализации, не spec. Композитный PK `(owner_telegram_id, content_sha256)` приводит схему в соответствие со spec и одновременно исправляет cross-user collision.

## Industry context (best practices research)

1. **Idempotency-Key pattern (Stripe, RFC draft-ietf-httpapi-idempotency-key-header):** short TTL (24h max, обычно минуты), per-tenant scope. Не глобальный hash-space.
2. **Webhook dedup (GitHub, Slack):** delivery-id с TTL равным retry-окну провайдера (часы, не дни).
3. **Anti-pattern, который мы сейчас имеем:** "content fingerprint forever-cache" — характерно для систем, где content приравнивается к identity (CDN, object storage), но не к user input.

## Open Questions

None — Variant 2 chosen via `/best-approach` (см. контекст сессии). Default'ы TTL зафиксированы (60s / 30d), .env override доступен.

## Verification anchors

- `tests/unit/inbox/test_idempotency.py::test_l2_text_passes_after_ttl` — RED → GREEN
- `tests/unit/inbox/test_idempotency.py::test_l2_text_blocks_within_ttl` — regression
- `tests/unit/inbox/test_idempotency.py::test_l2_photo_blocks_at_1h` — preserve binary behaviour
- `tests/unit/inbox/test_idempotency.py::test_l2_cross_owner_no_collision` — new invariant
- structlog: `inbox.idempotency.l2_duplicate` with `within_ttl=True|False`
