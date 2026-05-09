# D-018: ingest idempotency — двухслойный dedup без LLM (TG `update_id` + content hash)

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-11](../questions/Q-B-11-ingest-idempotency.md), overview §9.11, [D-002](D-002-job-model-storage.md), [D-006](D-006-state-storage-layout.md)

## Проблема

Юзер случайно отправляет одно и то же TG-сообщение/файл дважды (webhook-retry, forward, копипаст) → ингест создаёт дубль job'ов и напоминаний. Нужен дедуп-механизм с разумным разделением классов дублей.

## Варианты

1. **A — SHA-256 hash контента** (без TG-слоя): не покрывает retry, неточно для PDF.
2. **B — TG `(chat_id, update_id)` dedup**: только retry, не ловит копипаст.
3. **C — LLM-сравнение** (router-Claude): дорого, нестабильно.
4. **D — Полный гибрид L1+L2+L3** (TG + hash + LLM): production end-state, но overengineering для MVP.
5. **E — L1+L2 без LLM** (TG idempotency + content hash). ⭐

## Выбор

**Вариант E (двухслойный dedup без LLM).**

### Layer 1 — TG idempotency (webhook-retry защита)

1. **Storage:** `audit.db.tg_updates(update_id PK, chat_id, ts)`; TTL 24h (cleanup-job, по [D-019](D-019-cron-failure-mode.md) policy `silent`).
2. **Lookup:** на каждом TG webhook'е — `INSERT OR IGNORE`; если уже есть — скипнуть полностью, не создавать audit-event повторно.
3. **Прозрачно для юзера:** retry от TG-сервера не виден.

### Layer 2 — content hash (forward / copypaste защита)

1. **Storage:** `jobs.db.seen_files(hash TEXT PK, wiki TEXT, first_seen INTEGER, tg_message_id INTEGER, tg_chat_id INTEGER, content_kind TEXT)`; TTL 30d.
2. **Hash вычисление** (нормализация перед SHA-256):
   1. **Текст:** `unicode-NFKC + strip + lower + collapse whitespace`.
   2. **Файл:** raw bytes (без перекодирования).
   3. **Голос:** SHA-256 от bytes (после транскрипции — отдельный hash от нормализованного текста, тоже в `seen_files`).
   4. **Фото:** SHA-256 от bytes; OCR-текст — отдельным hash'ом.
3. **На совпадении:** не блокировать ингест автоматически; показать inline-кнопки в TG:
   1. «Уже видел такое N дней назад в `<WIKI>` (job `<title>`). Создать ещё раз?» / «Открыть существующий» / «Игнорировать».
4. **Аудит:** все совпадения логируются в `audit.db.dedup_hits(hash, tg_message_id, ts, user_choice)`.

### Layer 3 — LLM-сравнение

**Не реализуется в MVP.** Если в будущем появятся жалобы на «семантические дубли» (тот же билет, переформулирован) — добавится точечно на этапе job-creation в `router-agent`, не на каждом ingest'е. Использует `tracker_answers` или sessions.db для recent-job lookup.

### TTL и GC

1. L1 TTL **24h** (TG webhook retry окно — секунды; 24h — щедрый запас).
2. L2 TTL **30d** (типичный horizon «забыл что добавлял»).
3. GC выполняется housekeeping-job'ом (категория `silent` по [D-020](D-020-cron-result-routing.md)).

## Последствия

1. Покрытие 95% реальных кейсов детерминированно и бесплатно.
2. Семантические дубли остаются user-visible (юзер увидит в `/today` и удалит сам).
3. Запреты:
   1. **Не использовать L1 для контент-дедупа** — `update_id` ≠ identity контента.
   2. **Не блокировать ингест L2 автоматически** — всегда давать выбор юзеру (UX > strict).
4. Расширение до L3 (LLM) — без переделки L1/L2.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-018-ingest-idempotency.md` (когда финализируется)
