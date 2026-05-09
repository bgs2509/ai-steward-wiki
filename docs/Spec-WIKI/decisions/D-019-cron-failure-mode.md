# D-019: cron failure mode — error taxonomy + retry + DLQ + auto-disable

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-19](../questions/Q-B-19-cron-failure.md), overview §9.19, [D-003](D-003-scheduler-backend.md), [D-006](D-006-state-storage-layout.md), [D-020](D-020-cron-result-routing.md)

## Проблема

Job (cron-trigger или one-shot) может упасть (timeout, network, rate-limit, invalid payload, file missing, lock contention). Без явной policy: тихий drop напоминаний (включая `medication`), повисшие ресурсы, спам failures от «ядовитого» recurring job'а.

## Варианты

1. **A — Минимум** (APScheduler defaults + log): тихие потери, неприемлемо для `medication`.
2. **B — Retry exp.backoff + DLQ + final notify**: классика, но `medication`-first-fail не закрыт; «ядовитый» recurring повторяет 3 retry каждый tick.
3. **C — B + error taxonomy + auto-disable + alert dedup**. ⭐
4. **D — C + manual redrive UI** (`/dlq`, `/dlq_clear`): отложено до реальной потребности; `/retry` оставлен в C.

## Выбор

**Вариант C.**

### Error taxonomy

1. **TransientError** → retry с exp.backoff:
   1. `subprocess.TimeoutExpired` (timeout по [D-021](D-021-timeouts-kill-policy.md)).
   2. Network errors (`aiohttp.ClientError`, `TelegramNetworkError`, DNS).
   3. Claude rate-limit (`429`, `529`).
   4. Lock contention (advisory lock по [D-012](D-012-wiki-lock.md)) — backoff с jitter.
   5. SQLite `database is locked` (concurrent writers).
2. **PermanentError** → сразу в DLQ без retry:
   1. `ValidationError` (invalid payload).
   2. `FileNotFoundError` для critical paths (`<wiki>` deleted).
   3. `WikiNotFoundError` (UID/path resolved → ничего нет).
   4. Claude CLI exit `127` / `126` (binary missing / permission).
   5. Auth-failures CLI ([D-013](D-013-claude-cli-auth.md)).
3. **UnknownError** (всё, что не классифицировано) → treat as Transient (consequence: retry; покрытие edge cases без молчаливых потерь).

### Retry policy

1. **Дефолт:** 3 попытки, exp.backoff `1m → 5m → 30m`, jitter ±20%.
2. **Override per-category в payload:**
   1. `medication`: 5 попыток `1m → 5m → 15m → 60m → 240m`.
   2. `digest_job`: 1 попытка (если упал — DLQ; следующий tick поднимет fresh).
3. Каждая попытка пишется в `audit.db.job_attempts(job_id, attempt_no, started_at, finished_at, status, error_class, error_msg)`.

### DLQ

1. **Storage:** `jobs.db.dead_letter(dlq_id PK, job_id, payload_snapshot JSON, last_error TEXT, error_class TEXT, failed_at INTEGER, redrive_count INTEGER DEFAULT 0)`.
2. **Retention** 30d, после — GC через silent housekeeping-job ([D-020](D-020-cron-result-routing.md)).
3. **Manual redrive:** TG-команда `/retry <dlq_id>` → копия payload в `jobs` со `status=pending`, increment `redrive_count`, audit-event.

### Auto-disable

1. Триггер: **3 подряд** провалов на разных tick'ах для **recurring** job'а (one-shot не auto-disable).
2. Действие: `jobs.status = 'paused'` + payload-флаг `auto_disabled=true`, в audit — причина.
3. TG-нотификация owner'у (по [D-020](D-020-cron-result-routing.md) policy `always` для disable-events): «job `<title>` отключён после 3 неудач: `<last_error>`. /resume или /delete».
4. Reset счётчика «подряд неудач»: успешный run обнуляет.

### Alert dedup

1. **`medication`** (и любая категория с флагом `payload.alert_on_first_fail=true`) — TG-alert на **первом** fail, не дожидаясь исчерпания retry. Последующие попытки — silent (не спамить).
2. **Остальные категории** — TG-alert только после исчерпания retry (на DLQ-event).
3. **Auto-disable** — отдельный alert (см. выше).

### Cleanup ресурсов

В `finally` любого attempt: освободить per-WIKI lock ([D-012](D-012-wiki-lock.md)), вернуть Semaphore-слот ([D-011](D-011-concurrent-claude.md)), убить orphan child-процессы.

## Последствия

1. Production-grade reliability без перегруза command-API.
2. `medication` категория защищена от молчаливых потерь.
3. «Ядовитые» recurring jobs не спамят больше 3 раз.
4. Запреты:
   1. **Не классифицировать `subprocess.TimeoutExpired` как Permanent** (это всегда Transient).
   2. **Не делать retry для `ValidationError`** (детерминированно сломан payload).
   3. **Не выпиливать orphan-child cleanup** (источник resource leak).
5. `/dlq browse` и `/dlq_clear` команды — отложены; добавить при первой operational потребности.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-019-cron-failure-mode.md` (когда финализируется)
