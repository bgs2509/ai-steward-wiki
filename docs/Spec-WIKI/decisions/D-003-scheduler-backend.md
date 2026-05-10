# D-003: Scheduler backend — APScheduler AsyncIOScheduler + SQLAlchemyJobStore

**Статус:** accepted
**Дата:** 2026-05-08 (amended 2026-05-10 — internal maintenance jobs clarified)
**Контекст:** [Q-A-02](../questions/Q-A-02-scheduler-backend.md), [job-model](../entities/job-model.md), [D-002](D-002-job-model-storage.md)

## Проблема

Где исполнять триггеры job-model и где хранить job state. Опции: APScheduler (in-process), системный crontab, гибрид.

## Варианты

1. **A. APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore`** в процессе бота, persistence в `data/jobs.db` через тот же SQLAlchemy-engine, что и таблица `jobs` (D-002).
2. **B. Системный crontab** — file-based, sudo-write на CRUD из TG, дублирование state.
3. **C. Гибрид** — APScheduler для UX-jobs, crontab для системных DevOps-задач (бэкапы, ротация). По существу = A для job-model + независимый OS-cron.

## Выбор

**Вариант A.** Юзер подтвердил 2026-05-08.

Обоснование:
1. Согласовано с D-002 (общий SQLAlchemy-engine, общий SQLite).
2. Async runtime бота (`aiogram 3.x` + `asyncio`) — `AsyncIOScheduler` нативно работает в общем event-loop.
3. Hot-add/del/disable через TG (`/cron_add`, `/cron_disable`) — `scheduler.add_job/remove_job/pause_job` без файловых правок.
4. Recovery после рестарта — APScheduler перечитывает jobstore.
5. Вариант B исключён UX-требованием TG-CRUD над расписанием. Вариант C — не архитектурный, а DevOps-уровневый.

## Последствия

1. Один systemd-unit бота держит и aiogram-loop, и scheduler.
2. `data/jobs.db` хранит:
   1. Таблицу `jobs` (D-002, бизнес-уровень).
   2. Таблицы APScheduler jobstore (служебные, схема владелец — APScheduler).
   Обе через один engine, общий connection pool.
3. Misfire policy: APScheduler `misfire_grace_time` настраивается per-kind (короткий для `tracker_survey`, длинный для `digest_job`). Конкретные значения — отдельным решением при реализации.
4. Долгие job'ы (Claude CLI) запускаются через общий async runner (`cli_pool` → `systemd-run --scope` per D-038), чтобы не блокировать event-loop и не обходить isolation. Лимиты конкуррентности — Q-A-07.
5. Full off-site backup, logrotate и host-level DevOps остаются в OS-cron/systemd timer — не часть user-facing `job-model`. Lightweight in-app maintenance (`retention`, `gc`, `db_snapshot`) может идти через APScheduler как `silent` system task, потому что использует те же DB paths и audit conventions.
6. При падении процесса триггеры паузятся; mitigation — systemd `Restart=always` + misfire grace time.
7. Q-A-02 закрывается этим решением.

## Открытые подвопросы

1. Конкретные `misfire_grace_time` per-kind — при реализации.
2. Лимиты concurrency для Claude CLI job'ов — [Q-A-07](../questions/Q-A-07-concurrent-claude.md).
3. Failure mode (что слать в TG при N подряд failure) — [Q-B-19](../questions/Q-B-19-cron-failure.md).

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-scheduler-backend.md` при финализации.
