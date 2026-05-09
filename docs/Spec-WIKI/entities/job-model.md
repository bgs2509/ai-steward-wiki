# Job-model

**Тип:** entity
**Статус:** review
**Источники:** [overview §8.3.2](../raw/20260507-ai-steward-wiki-only-overview.md), §2.2, [D-001](../decisions/D-001-time-tracker-vs-job-model.md), [D-002](../decisions/D-002-job-model-storage.md)

## Суть

Унифицированный объект расписания в `data/jobs.db`. Одна таблица `jobs` (Flat + typed JSON payload — см. [D-002](../decisions/D-002-job-model-storage.md)), дискриминатор `kind`. Все kinds обслуживает один APScheduler-loop, отличаются action-handler'ом по `kind`.

## Схема таблицы (D-002)

Общие колонки:

1. `id` — PK.
2. `kind` — дискриминатор, `str`.
3. `owner_telegram_id` — владелец/получатель.
4. `cron_expr` — расписание (cron-выражение или ISO для разовых).
5. `enabled` — `bool`, выкл без удаления.
6. `mandatory` — `bool`, требует ли follow-up (D-001).
7. `follow_up_delay_min` — `int | None`, через сколько минут слать «сделал?» (D-001).
8. `created_at`, `last_run_at`, `failure_count` — служебные.
9. `payload` — `JSON`, kind-специфичные поля.

Типизация payload — Pydantic discriminated union на boundary:

```python
JobPayload = Annotated[
    Union[ReminderPayload, WikiPayload, DigestPayload,
          TrackerSurveyPayload, TrackerFollowupPayload, BoundaryPayload],
    Field(discriminator="kind"),
]
```

Новый `kind` = новый Pydantic-класс + ветка `match` в dispatcher. **Без Alembic-миграций.**

## Текущий набор `kind`

Минимум 6 (после D-001), будет расти:

1. **`reminder_job`** — TG-сообщение по расписанию. **Claude CLI не запускается.** Payload: `chat_id`, `message`, `lead_time`.
2. **`wiki_job`** — Claude в одной WIKI с фиксированным промптом (например, `daily ingest`). Соответствует §6 `/cron_add`. Payload: `wiki_path`, `prompt`.
3. **`digest_job`** — Claude с `--add-dir` в несколько WIKI + чтение `planner.json`, сводка → TG. Payload: `wiki_paths`, `prompt`, `chat_id`.
4. **`tracker_survey`** — периодический опрос «что делал?» (см. [time-tracker](time-tracker.md)). Payload: окно, шаг, ссылка на predictive-replies.
5. **`tracker_followup`** — follow-up «сделал?» через `follow_up_delay_min` минут после mandatory-item.
6. **`boundary_message`** — фиксированные границы дня (06:00 «подъём», 23:00 «спать»).

## Развилки

1. Schema хранения — закрыта в [D-002](../decisions/D-002-job-model-storage.md): Flat + JSON payload.
2. Backend — закрыт в [D-003](../decisions/D-003-scheduler-backend.md): APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore`.

## Что НЕ существует в сервисе

1. **Файл `planner.json` отсутствует** в любом виде (per-user, per-WIKI, export). См. [D-005](../decisions/D-005-no-planner-json.md). Все planner-семантичные items живут в `jobs.db` как строки с соответствующим `kind`.

## Связанные

1. [D-001 time-tracker vs job-model](../decisions/D-001-time-tracker-vs-job-model.md)
2. [D-002 job-model storage](../decisions/D-002-job-model-storage.md)
3. [Q-A-01 Job-table schema](../questions/Q-A-01-job-table.md) (закрыт D-002)
4. [Q-A-02 Scheduler backend](../questions/Q-A-02-scheduler-backend.md)
5. [time-tracker](time-tracker.md)
