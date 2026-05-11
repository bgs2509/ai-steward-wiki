---
feature: M-RUNTIME-WIRING
bd_id: aisw-cq4
status: stable
date: 2026-05-11
chunk: 18
approach: single-file-entrypoint
stack:
  - aiogram 3.x (Dispatcher.start_polling)
  - APScheduler AsyncIOScheduler
  - alembic.config.Config + alembic.command.upgrade (programmatic)
  - signal (SIGINT/SIGTERM via asyncio loop.add_signal_handler)
  - structlog
references:
  - docs/superpowers/specs/20260511-runtime-wiring-discovery.md
---

# Design — M-RUNTIME-WIRING

## Approach

**Single-file entrypoint** `src/ai_steward_wiki/__main__.py` композирует уже
существующие модули. Никаких новых бизнес-абстракций. Структура:

```
__main__.py
├── async def _amain() -> None         # full lifecycle, asyncio.run target
│   ├── 1. setup_logging(level)
│   ├── 2. _ensure_data_dirs(settings)
│   ├── 3. _run_migrations(jobs|audit|sessions URLs)
│   ├── 4. build engines + sessionmakers (jobs/audit/sessions)
│   ├── 5. _load_allowlist(settings) + sync_to_sessions_db
│   ├── 6. scheduler = build_scheduler(jobs_db_sync_url); scheduler.start()
│   ├── 7. bot = build_bot(token); dp = build_dispatcher(allowlist)
│   ├── 8. _install_signal_handlers(stop_event)
│   ├── 9. polling task + stop_event wait → graceful_shutdown
│   └── 10. finally: stop_event/cleanup
└── def main() -> None                  # asyncio.run(_amain())
    if __name__ == "__main__": main()
```

## Key decisions

1. **D-RW-01: Migrations programmatic, не subprocess.**
   `alembic.config.Config(str(ini_path)) + command.upgrade(cfg, "head")`.
   *Why:* избегаем `subprocess` + не зависим от `alembic` CLI в PATH;
   transactional (Alembic сам обрабатывает offline/online).
   *Trade-off:* alembic.command.upgrade — sync, поэтому оборачиваем в
   `asyncio.to_thread`. Это нормально на старте.

2. **D-RW-02: Async→sync URL для SQLAlchemyJobStore.**
   `_sync_url_for_jobstore(async_url)` — детерминированная замена
   `sqlite+aiosqlite://` → `sqlite://`. Чистая функция, unit-tested.

3. **D-RW-03: Allowlist — опциональный users.toml.**
   Добавляем поле `Settings.users_toml_path: Path | None = None`. Если None
   или файл отсутствует — стартуем с пустым `UsersConfig(users=[])`. Это
   делает first-run local frictionless. Для VPS users.toml обязателен через
   `EnvironmentFile`.

4. **D-RW-04: Graceful shutdown через `asyncio.Event`.**
   `loop.add_signal_handler(SIGINT|SIGTERM, stop_event.set)`. После
   `start_polling` запускаем задачу-наблюдатель: `await stop_event.wait()`
   → `await dp.stop_polling()` → `scheduler.shutdown(wait=False)` →
   `await bot.session.close()` → `await engine.dispose()` для каждого engine.
   *Why:* asyncio-native, без race.

5. **D-RW-05: No handlers registered.**
   Дефолтный aiogram Dispatcher игнорирует updates без обработчиков. Это
   делает MVP-runtime safe — middleware гейтит, остальное no-op. Production
   handlers — отдельный чанк `M-TG-HANDLERS-WIRING`.

6. **D-RW-06: Correlation ID на жизнь процесса.**
   `correlation_id = f"proc-{uuid4().hex[:8]}"` — пробрасывается в каждый
   log событий startup/shutdown.

7. **D-RW-07: Pre-existing data dirs.**
   Перед migrations: для каждого URL вида `sqlite+aiosqlite:///path/to/db` —
   `path.parent.mkdir(parents=True, exist_ok=True)`. Без этого Alembic
   падает на отсутствующей директории.

## Module map

```
src/ai_steward_wiki/
└── __main__.py        (NEW, ROLE=RUNTIME)
    Imports:
      .settings.get_settings
      .logging_setup.setup_logging
      .tg.bot.build_bot, build_dispatcher
      .scheduler.core.build_scheduler
      .storage.{jobs,audit,sessions}.engine.{build_engine, build_sessionmaker}
      .auth.allowlist.Allowlist, sync_to_sessions_db
      .auth.users_toml.UsersConfig, load_users_toml
```

Нового публичного API не вводим — `__main__.py` имеет `MAP_MODE: NONE`
(внутренний runtime, не импортируется другими модулями).

## Data model

Без изменений в БД. Только runtime composition.

## Logging anchors

Все логи через `structlog.get_logger("ai_steward_wiki.runtime")`:

| Event | Block | Fields |
|-------|-------|--------|
| `runtime.start` | START_BLOCK_BOOTSTRAP | correlation_id, env, log_level |
| `runtime.migrations.begin` | START_BLOCK_MIGRATIONS | db_name |
| `runtime.migrations.done` | END_BLOCK_MIGRATIONS | db_name, applied |
| `runtime.allowlist.loaded` | START_BLOCK_ALLOWLIST | users_count, path_present |
| `runtime.scheduler.started` | START_BLOCK_SCHEDULER | jobs_url |
| `runtime.polling.start` | START_BLOCK_POLLING | bot_id |
| `runtime.signal.received` | START_BLOCK_SHUTDOWN | signal |
| `runtime.shutdown.done` | END_BLOCK_SHUTDOWN | correlation_id |

## Verification plan

1. **unit/test_runtime_url_conversion.py** — `_sync_url_for_jobstore` детерминизм.
2. **unit/test_runtime_compose.py** — `_amain` запускается под моки, доходит
   до `polling.start` и корректно завершается на `stop_event.set()`.
3. **unit/test_runtime_signals.py** — `_install_signal_handlers` подписывает
   SIGINT/SIGTERM на set() event.
4. **unit/test_runtime_data_dirs.py** — `_ensure_data_dirs` создаёт parent.
5. **manual smoke (local):** `uv run python -m ai_steward_wiki` с тестовым
   токеном → лог `runtime.polling.start`, отправка сообщения от не-allowlisted
   юзера → silent drop, Ctrl-C → `runtime.shutdown.done`, exit 0.

## Risks revisited

- **Async loop sharing.** APScheduler `AsyncIOScheduler` сам цепляется за
  текущий running loop при `.start()`. Делаем `.start()` **внутри** `_amain`
  после `asyncio.run` — корректно.
- **`alembic.command.upgrade` sync в asyncio.** Оборачиваем в
  `asyncio.to_thread` — ок.
- **Polling cancellation.** aiogram 3.x `dp.stop_polling()` — coroutine,
  корректно дожидается завершения. Если не доступен в текущей версии —
  fallback на `asyncio.Task.cancel()` polling task.
