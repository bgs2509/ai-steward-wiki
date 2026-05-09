# D-006: State storage — три раздельные SQLite (`jobs.db`, `audit.db`, `sessions.db`)

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-32](../questions/Q-A-32-state-storage.md), [D-002](D-002-job-model-storage.md), [D-003](D-003-scheduler-backend.md), [D-005](D-005-no-planner-json.md)

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

1. **`data/jobs.db`** — горячая операционная БД:
   1. Таблица `jobs` (D-002, Flat + JSON payload).
   2. Таблицы APScheduler `SQLAlchemyJobStore` (D-003, схема владелец — APScheduler).
   3. Tracker memory (если Q-A-09 решится в пользу «в SQL» — иначе живёт отдельно).
2. **`data/audit.db`** — append-only audit-лог §7.4:
   1. Запросы/ответы (или их хэши — Q-E-33).
   2. Action-log (что сделал router/jobs/admin).
   3. Retention policy — отдельным решением, по умолчанию длинная.
3. **`data/sessions.db`** — runtime-state TG-диалогов:
   1. Conversation history (если Q-D-30 решится в пользу хранения).
   2. Состояния FSM aiogram (если нужны persistent).
   3. Короткая retention, можно дропать без потерь.

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
