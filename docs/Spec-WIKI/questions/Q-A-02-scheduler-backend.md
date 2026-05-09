# Q-A-02: Scheduler backend

**Tier:** A
**Источник:** [overview §9 п.2](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

APScheduler + SQLAlchemyJobStore (SQLite) vs системный crontab vs гибрид.

## Варианты

1. **A. APScheduler + SQLAlchemyJobStore.** Job persistence в SQLite, in-process, hot-add/del через TG. Плюсы: полный контроль, hot-reload, простота. Минусы: при рестарте сервиса задачи паузятся.
2. **B. Системный crontab.** Изоляция через systemd. Минусы: трудный CRUD из TG, file-based.
3. **C. Гибрид.** APScheduler для wiki/digest_job, crontab для reminder_job (3 утра lint и т.п.).

## Решение

- [x] Вариант A (APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore`). Юзер подтвердил 2026-05-08. См. [D-003](../decisions/D-003-scheduler-backend.md) (accepted).
- [x] оформлено как [D-003](../decisions/D-003-scheduler-backend.md)

## Связанные

1. [Job-model](../entities/job-model.md)
