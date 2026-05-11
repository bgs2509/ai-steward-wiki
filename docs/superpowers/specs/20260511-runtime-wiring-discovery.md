---
feature: M-RUNTIME-WIRING
bd_id: aisw-cq4
status: stable
date: 2026-05-11
chunk: 18
sources:
  - deploy/systemd/aisw-bot.service (ExecStart contract)
  - src/ai_steward_wiki/tg/bot.py (build_bot, build_dispatcher)
  - src/ai_steward_wiki/scheduler/core.py (build_scheduler)
  - src/ai_steward_wiki/storage/{jobs,audit,sessions}/engine.py
  - src/ai_steward_wiki/auth/allowlist.py (sync_to_sessions_db)
  - docs/reports/20260511-ai-steward-wiki-mvp-report.md
---

# Discovery — M-RUNTIME-WIRING (process entrypoint)

## Intent

Существующие 17 чанков построили модули (Bot/Dispatcher factory, scheduler,
storage engines, allowlist, классификатор, runner) и deploy-юнит
`aisw-bot.service`, в котором `ExecStart=python -m ai_steward_wiki`. Сам
`__main__.py` не реализован — процесс негде запустить. Этот чанк закрывает
runtime-композицию: единая точка входа, которая склеивает уже готовые модули
в работающий процесс и позволяет локально проверить бота против тестового
TG-токена.

## FR (Functional Requirements)

1. **FR-1.** Команда `uv run python -m ai_steward_wiki` запускает процесс,
   читает Settings из `.env`, и при `AISW_ENV=local` использует
   `AISW_TG_BOT_TOKEN_LOCAL`. Отсутствие токена в активном слоте → fail-fast.
2. **FR-2.** На старте процесс выполняет per-DB Alembic upgrade head для
   `jobs.db`, `audit.db`, `sessions.db` до открытия polling-петли.
3. **FR-3.** Allowlist загружается из `users.toml` (если файл указан/существует)
   и синхронизируется в `sessions.db` через `sync_to_sessions_db`.
4. **FR-4.** APScheduler стартует ДО `start_polling`; останавливается при
   shutdown с `wait=False`.
5. **FR-5.** Aiogram polling запускается через `dp.start_polling(bot)` с
   уже подключённым allowlist middleware (`build_dispatcher`).
6. **FR-6.** Сигналы `SIGINT`/`SIGTERM` приводят к graceful shutdown:
   stop polling → stop scheduler → закрыть SQLA engines → закрыть aiogram session.
7. **FR-7.** Все этапы (startup/shutdown) логируются через `structlog` с
   обязательными полями `event`, `correlation_id` (на сессию процесса).

## Non-Functional Requirements

1. **NFR-1 (Isolation).** Никаких production-handler'ов в этом чанке. Дефолтное
   поведение для нерасроутенных сообщений — silent ignore (aiogram default).
   Это сознательно: actual TG → classifier → runner pipeline — отдельный чанк.
2. **NFR-2 (Idempotent migrations).** Повторный запуск `alembic upgrade head`
   на уже мигрированной БД — no-op, не падает.
3. **NFR-3 (Type-strict).** `mypy --strict` зелёный.
4. **NFR-4 (Test-isolation).** Сам `__main__.py` не должен ломать unit-тесты —
   импорт пакета не должен подниматься polling/scheduler/DB.
5. **NFR-5 (Local dev friendly).** Дефолтные `*_db_url` из Settings указывают
   на относительные `data/*.db` под cwd — `mkdir -p data` выполняется автоматически.

## Constraints

1. **C-1.** Контракт `ExecStart=python -m ai_steward_wiki` уже зафиксирован в
   `deploy/systemd/aisw-bot.service` — менять нельзя.
2. **C-2.** Async-aiosqlite URL'ы для приложения, sync-sqlite URL для
   `SQLAlchemyJobStore` (см. `build_scheduler` docstring) — конверсия
   обязательна.
3. **C-3.** `users.toml` путь не в Settings — нужно либо добавить поле, либо
   разрешить отсутствие файла и стартовать с пустым allowlist (полезно для
   first-run local). Решено в Design.

## Risks

1. **R-1.** Alembic API меняется между версиями — будем использовать стабильный
   программный путь `alembic.config.Config + command.upgrade`.
   **Mitigation:** Context7 verify на Execution.
2. **R-2.** Если test-бот получает реальные сообщения от чужих пользователей —
   allowlist пустой может «упасть» в middleware. **Mitigation:** middleware
   уже отбивает по `is_allowed`; пустой allowlist = всех отбивает. Безопасно.
3. **R-3.** Двойной запуск процесса → конкуренция на SQLite/scheduler.
   **Mitigation:** scheduler ставит `max_instances=1`, SQLite WAL + busy_timeout
   уже включён в `pragmas`. Принимаем для local dev, для VPS — systemd-юнит.

## Scope

**IN:**
- `src/ai_steward_wiki/__main__.py` (новый модуль, MODULE_CONTRACT, ROLE=RUNTIME)
- Хелпер `_run_migrations(engine_url)` (внутри `__main__.py` или
  отдельным `ops/migrations.py` — решено в Design)
- Unit-тесты: композиция (моки), graceful shutdown handler
- Опциональное поле `users_toml_path` в Settings (по умолчанию None — пустой allowlist)

**OUT (deferred):**
- Регистрация production message-handler'ов (TG → classifier → runner pipeline)
- Webhook mode (только long-polling в MVP)
- Hot-reload allowlist на SIGHUP (есть отдельный chunk про admin)

**LATER:**
- Health-check endpoint (HTTP) — не требуется в MVP, journald даёт диагностику.

## Stakeholders

- **Юзер (Gena_Beeline_Local).** Хочет запустить локально для тестов своего бота.
- **VPS deploy.** Контракт ExecStart уже ссылается на `__main__`.

## Open Questions

Ни одного — все вопросы решены в Design ниже.
