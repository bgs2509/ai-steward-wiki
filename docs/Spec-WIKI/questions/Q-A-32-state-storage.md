# Q-A-32: Хранилище состояния

**Tier:** A (повышен с E 2026-05-08 — foundational storage layout, blocks D-002/D-003)
**Источник:** [overview §9 п.32](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Одна общая SQLite `data/jobs.db` или раздельные БД (jobs/audit/sessions). Миграции — Alembic.

## Варианты

1. **A. Одна БД, несколько таблиц.** Простота, единая транзакция.
2. **B. Три БД** (`jobs.db`, `audit.db`, `sessions.db`). Изоляция backup/retention.
3. **C. Одна БД + WAL.** Конкурентные read/write устойчивее.

## Решение

- [x] Вариант B (три раздельные SQLite: `jobs.db`, `audit.db`, `sessions.db`). WAL+NORMAL+foreign_keys, Alembic per БД. Юзер подтвердил 2026-05-08. См. [D-006](../decisions/D-006-state-storage-layout.md) (accepted).
- [x] оформлено как [D-006](../decisions/D-006-state-storage-layout.md)

## Связанные

1. [Job-model](../entities/job-model.md)
