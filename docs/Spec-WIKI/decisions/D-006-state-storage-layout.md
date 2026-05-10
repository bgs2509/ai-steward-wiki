# D-006: State storage — три раздельные SQLite (`jobs.db`, `audit.db`, `sessions.db`)

**Статус:** accepted
**Дата:** 2026-05-08 (amended 2026-05-10 — добавлены `seen_files`, `tg_updates`, `prompt_versions`, `admin_events`, `onboarding_events` в `audit.db`; `inbox_hint_cache` в `sessions.db`)
**Контекст:** [Q-A-32](../questions/Q-A-32-state-storage.md), [D-002](D-002-job-model-storage.md), [D-003](D-003-scheduler-backend.md), [D-005](D-005-no-planner-json.md), [D-015](D-015-system-prompt-inject.md), [D-018](D-018-ingest-idempotency.md), [D-028](D-028-admin-access.md), [D-030](D-030-onboarding.md)

## Проблема

Какое количество физических SQLite-файлов держит сервис и как распределены таблицы (jobs, APScheduler jobstore, audit, sessions, tracker memory).

## Варианты

1. **A. Одна БД `state.db` со всеми таблицами.** Простой MVP, но нет границ retention/PII/backup.
2. **B. Три БД** — `jobs.db`, `audit.db`, `sessions.db`. Чистые bounded contexts.
3. **C. Гибрид** — `jobs.db` + `audit.db`, sessions в Redis/memory.

## Выбор

**Вариант B.** Юзер подтвердил 2026-05-08.

Обоснование:
1. Database-per-bounded-context (DDD, Sam Newman) — устаканенная практика. Для SQLite в одном процессе обходится почти бесплатно (3 engine'а вместо 1).
2. Согласовано с D-002/D-003 (`jobs.db` уже зафиксирован как имя для jobs + APScheduler jobstore).
3. Чистые границы retention/PII/backup — упрощает Q-E-33 (audit PII), Q-E-36 (backup), Q-D-30 (chat history).
4. Write-lock SQLite на разных файлах не пересекается → меньше contention между audit-write и jobs-write.

## Раскладка таблиц

1. **`data/jobs.db`** — горячая операционная БД (write-lock критичен, никаких dedup/audit нагрузок):
   1. `jobs` ([D-002](D-002-job-model-storage.md), Flat + JSON payload).
   2. Таблицы APScheduler `SQLAlchemyJobStore` ([D-003](D-003-scheduler-backend.md), схема владелец — APScheduler).
   3. `tracker_answers` ([D-014](D-014-tracker-memory-model.md)) — append-only, retention 90d.
2. **`data/audit.db`** — append-only audit + observability/dedup bounded context:
   1. `chat_log` ([D-033](D-033-chat-history.md)) — plaintext-диалог, retention 30d.
   2. `audit_events` — action-log router/jobs/admin/redactor, retention 90d ([D-034](D-034-pii-redactor.md)).
   3. `admin_events` ([D-028](D-028-admin-access.md)) — admin-elevation trail, retention 90d.
   4. `tg_updates` ([D-018](D-018-ingest-idempotency.md) L1) — webhook-retry dedup, TTL 24h.
   5. `seen_files` ([D-018](D-018-ingest-idempotency.md) L2, amended 2026-05-10 — переехал из `jobs.db`) — content-hash dedup, TTL 30d.
   6. `dedup_hits` ([D-018](D-018-ingest-idempotency.md)) — outcome совпадений (user choice).
   7. `prompt_versions` ([D-015](D-015-system-prompt-inject.md)) — semver+sha256 системных промптов на каждый CLI-вызов.
   8. `onboarding_events` ([D-030](D-030-onboarding.md)) — measure-показ обязательных intro-элементов per user.
3. **`data/sessions.db`** — runtime-state TG-диалогов и hot-path кэшей (короткая retention, можно дропать без потерь):
   1. `users` — sync-snapshot из `users.toml` ([D-031](D-031-allowlist-hot-reload.md), [D-042](D-042-unify-user-config.md)).
   2. Состояния FSM aiogram (persistent, если нужны).
   3. `pending_users` ([D-030](D-030-onboarding.md)) — заявки `/start` от unknown до admin-approve.
   4. `pending_confirms` ([D-023](D-023-tg-confirmations.md)) — explicit-confirm TTL 10мин.
   5. `inbox_hint_cache(user_id, wiki_path, mtime, content_sha256, hint_text)` — runtime-каталог `## Inbox hint` per Domain-WIKI; двухуровневая инвалидация mtime→sha256 (см. tech-spec §4); regen on cache-miss или service-restart.

## Pragma и настройки (без вилки)

Применяются ко всем трём БД:
1. `journal_mode=WAL` — concurrent read/write.
2. `synchronous=NORMAL` — баланс надёжности/производительности.
3. `foreign_keys=ON` — целостность ссылок.
4. `busy_timeout=5000` — терпеливое ожидание lock'а.

## Миграции

Alembic per БД:
1. `alembic/jobs/` — миграции `jobs.db`.
2. `alembic/audit/` — миграции `audit.db`.
3. `alembic/sessions/` — миграции `sessions.db`.

Каждая директория — отдельный `alembic.ini` с `script_location` и `sqlalchemy.url`. Релизный процесс прогоняет все три по очереди.

## Cross-DB транзакции

Не существуют в SQLite между файлами. Стратегия:
1. Audit — **best-effort**: сначала пишется бизнес-операция в `jobs.db`, затем audit-запись в `audit.db`. Потеря audit при крэше между шагами допустима (alert через service-logging Q-E-34).
2. Если когда-нибудь понадобится строгая cross-DB атомарность — outbox-pattern (записать в outbox-таблицу `jobs.db`, асинхронный worker переносит в `audit.db`).
3. Sessions — никогда не требует атомарности с другими БД.

## Последствия

1. Конфиг сервиса определяет три URL (env: `JOBS_DB_URL`, `AUDIT_DB_URL`, `SESSIONS_DB_URL`), default — `sqlite+aiosqlite:///data/{name}.db`.
2. Backup: hot (jobs) частый малый, audit append-only периодический, sessions можно дропать.
3. PII локализован в `audit.db` — Q-E-33 решается локально.
4. Q-A-32 закрывается этим решением.

## Запреты

1. Не складывать audit/sessions в `jobs.db`.
2. Не вводить «общую state.db» как «упрощение» — это возврат к Варианту A с потерей всех плюсов B.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-state-storage-layout.md` при финализации.
