# D-002: Job-model storage schema (одна таблица vs три)

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-01](../questions/Q-A-01-job-table.md), [job-model](../entities/job-model.md), [D-001](D-001-time-tracker-vs-job-model.md)

## Проблема

Как хранить унифицированный `job-model` (6+ kinds после D-001: `reminder_job`, `wiki_job`, `digest_job`, `tracker_survey`, `tracker_followup`, `boundary_message`, и набор будет расти за счёт time-tracker и predictive-replies).

## Варианты

1. **A. Single Table Inheritance (STI):** одна таблица `jobs`, `polymorphic_on=kind`, nullable subclass-колонки. Миграция на каждый новый kind.
2. **B. Joined / Class Table Inheritance:** `jobs` + per-kind таблица, JOIN по PK. Чистая нормализация, но JOIN на каждый запрос и миграция на каждый kind.
3. **C. Flat + typed JSON payload:** общие колонки `id, kind, owner_telegram_id, cron_expr, enabled, mandatory, follow_up_delay_min, created_at, last_run_at, failure_count` + `payload JSON`. Типизация через Pydantic discriminated union на boundary.

## Выбор

**Вариант C (Flat + typed JSON payload).** Юзер подтвердил 2026-05-08.

Обоснование:
1. Q-A-01 рекомендация: 80% C / 15% A / 5% B.
2. D-001 фиксирует минимум 6 kinds и продолжающийся рост — любая схема с миграцией-на-kind заблокирует UX-эксперименты.
3. `mandatory` и `follow_up_delay_min` (требования D-001 §Последствия) выносятся в общие колонки, индексируются, доступны фильтрам scheduler/UI без `json_extract`.
4. Согласовано с APScheduler-философией (opaque payload в одной таблице).

## Последствия

1. Таблица `jobs` в `data/jobs.db` имеет фиксированный набор общих колонок + `payload JSON`. Новые kinds добавляются Pydantic-классом + веткой dispatcher, **без Alembic-миграций**.
2. Валидация `payload` — на уровне приложения через Pydantic discriminated union (`Field(discriminator="kind")`), не на уровне БД.
3. Индексы создаются на горячих общих полях (`owner_telegram_id`, `kind`, `enabled`, `mandatory`). JSON-фильтры через SQLite `json_extract` — только если появятся реальные use-case'ы.
4. `entities/job-model.md` нужно обновить: вместо «3 типов» — текущий список 6 kinds + ссылка на этот D-002.
5. Q-A-01 закрывается этим решением.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-job-model-storage.md` при финализации (когда пойдём в код).
