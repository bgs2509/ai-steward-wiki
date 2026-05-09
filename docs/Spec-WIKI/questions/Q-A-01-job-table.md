# Q-A-01: Job-модель — 3 типа в одной таблице vs отдельные таблицы

**Tier:** A
**Источник:** [overview §9 п.1](../raw/20260507-ai-steward-wiki-only-overview.md)
**Связанные решения:** [D-001 time-tracker](../decisions/D-001-time-tracker-vs-job-model.md) — расширяет требования к `job-model`.

## Формулировка

Дискриминатор `kind` в одной таблице vs три отдельные модели? Влияет на `data/jobs.db` schema и весь scheduler-модуль.

## Изменившийся контекст (после D-001)

`job-model` больше **не ограничен 3 типами**. D-001 §Последствия п.1 фиксирует, что `job-model` обязан поддерживать:

1. Категорию `mandatory` (= нужен follow-up).
2. Поле `follow_up_delay_min`.
3. Метаданные time-tracker'а (см. [time-tracker](../entities/time-tracker.md)): tracker-survey-job (периодический опрос), tracker-followup-job («сделал?»), boundary-job (06:00 «подъём» / 23:00 «спать»).

Реальный набор `kind` уже сейчас: `reminder_job`, `wiki_job`, `digest_job`, `tracker_survey`, `tracker_followup`, `boundary_message`. Будет расти. Это меняет вес критериев в пользу гибкой схемы.

## Варианты ответа

### A. Single Table Inheritance (STI) — discriminator + nullable subclass-cols

Одна таблица `jobs`, SQLAlchemy `polymorphic_on=kind`, подклассы (`ReminderJob`, `WikiJob`, `DigestJob`, `TrackerSurveyJob`, …) с **nullable**-колонками для специфичных полей.

✅ Pros:
1. Статическая типизация, mypy-friendly, чистые ORM-классы.
2. Один SELECT на список «все задачи юзера».
3. SQL-уровневые индексы на subclass-полях возможны.

❌ Cons:
1. Каждый новый `kind` или новое поле = Alembic-миграция.
2. Nullable-зоопарк колонок при росте числа `kind` (после time-tracker уже 6+).
3. Лишний overhead на DDL при экспериментах с новыми UX-сценариями.

### B. Joined Table Inheritance — parent `jobs` + per-kind таблицы

Родительская таблица `jobs` + `reminder_jobs` / `wiki_jobs` / `digest_jobs` / `tracker_survey_jobs` / … соединяются по PK через JOIN.

✅ Pros:
1. NOT NULL констрейнты на subclass-полях.
2. Чистая нормализация, никаких nullable колонок.

❌ Cons:
1. JOIN на каждый запрос; список из 50 задач смешанных типов = N+1 или сложный SQL.
2. Каждый новый `kind` = новая таблица + миграция.
3. Over-engineering для life-сервиса с быстро эволюционирующим UX.
4. Хуже всего ложится на time-tracker, где kinds появляются от UX-итераций.

### C. Flat table + typed JSON payload ⭐ Best Practice (усилен D-001)

Общая таблица `jobs` с обязательными колонками:
`id, kind, owner_telegram_id, cron_expr, enabled, mandatory, follow_up_delay_min, created_at, last_run_at, failure_count` + `payload: JSON` (SQLite native JSON-text).

Типизация на python-уровне через Pydantic discriminated union:
```python
JobPayload = Annotated[
    Union[ReminderPayload, WikiPayload, DigestPayload,
          TrackerSurveyPayload, TrackerFollowupPayload, BoundaryPayload],
    Field(discriminator="kind"),
]
```

Поля D-001 (`mandatory`, `follow_up_delay_min`) выносятся в общие колонки — они нужны фильтрами scheduler'а и UI, не специфичны для одного kind.

✅ Pros:
1. Новый `kind` = новый Pydantic-класс + ветка `match` в dispatcher. **Без миграций.**
2. Естественно ложится на стек (Pydantic + async SQLAlchemy + SQLite).
3. Согласовано с APScheduler-философией («opaque payload в одной таблице»).
4. Time-tracker'у и predictive-replies легко добавлять экспериментальные `kind` без давления на схему БД.
5. Простой `/cron_list`: один SELECT, рендеринг через match-pattern.
6. Mandatory/follow-up — обычные колонки, индексируются нормально.

❌ Cons:
1. Нет SQL-уровня индексов на subclass-полях (`wiki_path`, `wiki_paths`, `category`, …). Для текущих use-case'ов фильтры идут по `owner`/`kind`/`enabled`/`mandatory` — все они в общих колонках.
2. Валидация payload только в коде, не в БД. Закрывается Pydantic'ом на boundary.
3. JSON-query через SQLite `json_extract` если когда-то понадобится фильтровать по содержимому payload.

## Рекомендация

После D-001 рекомендация **усиливается** в пользу варианта C:

- **80% Вариант C (Flat + JSON payload)** — теперь это уже не «приятный дефолт», а **архитектурное требование**: D-001 расширяет `job-model` минимум до 6 kinds, и time-tracker/predictive-replies продолжат добавлять новые. Любая схема, требующая миграции на каждый kind, замедлит UX-эксперименты.
- **15% Вариант A (STI)** — если зафиксировать набор `kind` на 3 (отказаться от time-tracker fold-in) и оставить scheduler «бойцовским». Не подходит при принятом D-001.
- **5% Вариант B (Joined)** — если subclass-поля разрастутся до 10+ колонок per kind. Сейчас не нужно.

## Решение

- [x] Вариант C (Flat + typed JSON payload). Юзер подтвердил 2026-05-08. См. [D-002](../decisions/D-002-job-model-storage.md) (accepted).
- [x] оформлено как [D-002](../decisions/D-002-job-model-storage.md)

## Связанные

1. [Job-model](../entities/job-model.md)
2. [D-001 time-tracker vs job-model](../decisions/D-001-time-tracker-vs-job-model.md)
3. [Time-tracker](../entities/time-tracker.md)
4. [Q-A-02: Scheduler backend](Q-A-02-scheduler-backend.md)
5. [Q-A-09: Tracker memory model](Q-A-09-tracker-memory-model.md)
