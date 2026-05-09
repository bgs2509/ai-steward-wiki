# D-020: cron-результат routing — per-category `notify_policy` + admin shadow channel

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-18](../questions/Q-B-18-cron-result-routing.md), overview §9.18, [D-013](D-013-claude-cli-auth.md), [D-019](D-019-cron-failure-mode.md)

## Проблема

Job сработал по расписанию без активного чата. Куда слать output: всегда owner'у / только при non-empty / silent для housekeeping; нужно ли отдельно admin-channel для operational events.

## Варианты

1. **A — Всегда owner_chat:** не масштабируется на multi-tenant, нет silent-режима для housekeeping.
2. **B — Per-category `notify_policy`** (`always` / `on_output` / `silent`).
3. **C — B + admin shadow channel** для failures и operational events. ⭐
4. **D — Heuristic silent success:** ненадёжно для `medication`.

## Выбор

**Вариант C.**

### `notify_policy` в payload каждого job'а

| Policy | Семантика | Категории по дефолту |
|--------|-----------|----------------------|
| `always` | Owner получает результат всегда, даже пустой ack | `medication`, `reminder`, `digest`, `tracker_question` |
| `on_output` | Owner получает только non-empty output (cron(8)-classic) | `wiki_job` (general), `inbox_classify` |
| `silent` | Никаких TG-сообщений; всё в `audit.db` | `cleanup`, `gc`, `retention`, `audit_rotation` |

Override через `payload.notify_policy` per-job.

### Routing

1. **Success:**
   1. `always` → `owner_chat_id` всегда (даже на пустом output → ack «✅ `<title>`»).
   2. `on_output` → `owner_chat_id` только если non-empty.
   3. `silent` → only `audit.db.job_outputs`.
2. **Failure** (после исчерпания retry, по [D-019](D-019-cron-failure-mode.md)):
   1. `owner_chat_id` всегда (независимо от `notify_policy`).
   2. **+ `admin_chat_id`** (shadow channel, см. ниже).
3. **Auto-disable** ([D-019](D-019-cron-failure-mode.md)):
   1. `owner_chat_id` + `admin_chat_id`.
4. **`medication`-first-fail** ([D-019](D-019-cron-failure-mode.md)):
   1. `owner_chat_id` сразу; `admin_chat_id` — нет (это ещё не финальный fail).

### Admin shadow channel

1. **Config:** `admin_chat_id` в `service.toml`/env. Single-tenant Henry-N ([D-013](D-013-claude-cli-auth.md)) → `admin_chat_id == owner_chat_id`.
2. **Что admin получает:**
   1. Failures после исчерпания retry.
   2. Auto-disable события.
   3. (Future) operational alerts: WAL bloat, disk-low, scheduler restart.
3. **Что admin НЕ получает:**
   1. Success outputs (privacy boundary owner ↔ admin).
   2. `medication`-first-fail alerts (это owner-personal).
4. **Дедупликация:** при `admin_chat_id == owner_chat_id` failure-сообщение шлётся **один раз** (lookup перед send).

### Storage

1. `audit.db.job_outputs(job_id, run_id, ts, output_size, sent_to_owner BOOL, sent_to_admin BOOL, suppressed_reason TEXT)`.
2. Все 3 policy логируются (даже `silent`) — для debug и retroactive review.

## Последствия

1. Single-tenant Henry-N работает де-факто как Вариант B; multi-tenant поддержан без переделок.
2. Privacy boundary owner ↔ admin зафиксирована: admin видит health сервиса, не контент юзера.
3. Housekeeping-job'ы (cleanup, GC) не спамят TG.
4. Запреты:
   1. **Не дублировать success в admin-channel** даже при `admin == owner`.
   2. **Не использовать `silent`** для категорий с user-impact (`medication`, `reminder`, `tracker_question`).
   3. **Не игнорировать `notify_policy`** в коде — explicit dispatcher на одну точку отправки.
5. Будущее: добавить `admin_alerts`-категорию (WAL bloat, disk-low) поверх контракта без изменений.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-020-cron-result-routing.md` (когда финализируется)
