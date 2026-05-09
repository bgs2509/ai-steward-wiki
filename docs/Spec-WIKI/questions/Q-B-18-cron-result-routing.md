# Q-B-18: Cron-результат без активного чата

**Tier:** B
**Источник:** [overview §9 п.18](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Куда слать результат cron-запуска (тот же `chat_id` владельца? админу при ошибке?).

## Варианты

1. **A. Owner chat по умолчанию.** На успех и на ошибку.
2. **A+admin-on-fail.** Owner всегда, admin дополнительно при `exit_code != 0`.
3. **Silent success.** Только при ошибке или важном изменении (heuristic).

## Решение

- [x] **Вариант C** — per-category routing + admin shadow channel:
  - **`notify_policy` в payload каждого job'а:**
    - `always` (medication, reminder, digest) — owner получает результат всегда, даже пустой ack.
    - `on_output` (default) — owner получает только non-empty output (cron(8)-classic).
    - `silent` (housekeeping: cleanup, GC, retention, audit-rotation) — никаких TG-сообщений; всё в `audit.db`.
  - **Admin shadow channel:** `admin_chat_id` в config; failures (после исчерпания retry, по D-019) и auto-disable события дублируются туда. Success — не дублируется (privacy boundary).
  - **Single-tenant Henry-N** (D-013): `admin_chat_id == owner_chat_id`; де-факто как B, но architecturally ready for multi-tenant. Дедупликация: при `admin == owner` failure-сообщение шлётся один раз.
- [x] оформлено как [D-020](../decisions/D-020-cron-result-routing.md)

## Связанные

1. [Q-B-19: Failure mode](Q-B-19-cron-failure.md)
