# Q-B-19: Failure mode для cron

**Tier:** B
**Источник:** [overview §9 п.19](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Retry-политика, уведомление при падении, dead-letter queue.

## Варианты

1. **Retry exponential backoff** (3 попытки: 1m / 5m / 30m).
2. **DLQ-таблица** `failed_jobs` с причиной и payload-snapshot.
3. **Notify owner + admin** после исчерпания retry.
4. **Auto-disable job** после N подряд неудач (например, 5).

## Решение

- [x] **Вариант C** — production-grade failure mode:
  1. **Error taxonomy:** `TransientError` (timeout, network, lock contention, Claude rate-limit) → retry с exp.backoff `1m → 5m → 30m` (3 попытки); `PermanentError` (invalid payload, file missing, WIKI deleted, validation fail) → сразу DLQ без retry.
  2. **DLQ:** `jobs.db.dead_letter(job_id, payload_snapshot, last_error, failed_at)`; attempts пишутся в `audit.db.job_attempts(job_id, attempt, ts, status, error)`.
  3. **Auto-disable** recurring jobs после 3 подряд провалов → `status=paused` + TG-нотификация owner'у «job отключён, /resume или /delete». One-shot jobs не auto-disable.
  4. **Alert dedup:** категория `medication` — alert на первом fail; остальные — только после исчерпания retry.
  5. **Retry-overrides** в payload job'а (per-category дефолты).
  6. Минимальная TG-команда `/retry <job_id>` для manual redrive из DLQ. `/dlq` browse и cleanup-команды — отложены до реальной потребности.
- [x] оформлено как [D-019](../decisions/D-019-cron-failure-mode.md)

## Связанные

1. [Job-model](../entities/job-model.md), [Q-B-18](Q-B-18-cron-result-routing.md)
