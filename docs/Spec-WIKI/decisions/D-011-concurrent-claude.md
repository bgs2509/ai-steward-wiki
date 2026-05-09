# D-011: Concurrent Claude CLI — global semaphore + per-WIKI lock + priority queue

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-07](../questions/Q-A-07-concurrent-claude.md), overview §9.7, [D-003](D-003-scheduler-backend.md), [D-009](D-009-classifier-engine.md), [Q-A-08](../questions/Q-A-08-lock-on-wiki.md)

## Проблема

Claude CLI — тяжёлый subprocess (10–30с, RAM ≥500MB). Возможны конкурентные запуски:
1. digest-job (cron) пересекается с interactive Inbox-message;
2. ingest-job в `<Domain>-WIKI` пересекается с router в `Inbox-WIKI` того же юзера;
3. N юзеров одновременно;
4. два CLI с одинаковым cwd → race-condition на CLAUDE.md и страницах.

Без политики: либо OOM, либо silent corruption WIKI.

## Варианты

1. **A. Per-user serialization** — `Lock(user_id)`. Простой, но digest блокирует interactive того же юзера.
2. **B. Per-WIKI serialization** — `Lock(wiki_path)`. Корректно, но без machine cap.
3. **C. Global semaphore** — `Semaphore(N)`. Cap есть, race на WIKI остаётся.
4. **D. Global semaphore + per-WIKI lock + priority queue** — best-practice.

## Выбор

**Вариант D.** Юзер подтвердил 2026-05-08.

## Архитектура

### Примитивы

```python
import asyncio
from pathlib import Path

MAX_CONCURRENT_CLI = 4  # tunable, default = min(cpu_count, 4)

cli_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLI)
_wiki_locks: dict[Path, asyncio.Lock] = {}

def wiki_lock(path: Path) -> asyncio.Lock:
    path = path.resolve()
    if path not in _wiki_locks:
        _wiki_locks[path] = asyncio.Lock()
    return _wiki_locks[path]
```

### Acquire-порядок (строго)

```python
async with cli_semaphore:           # 1. capacity
    async with wiki_lock(wiki):     # 2. correctness
        await spawn_claude_cli(...)
```

Нарушать порядок запрещено — иначе deadlock между двумя job'ами на разных WIKI с разной capacity.

### Priority queue

1. Все CLI-задачи проходят через `asyncio.PriorityQueue`. Приоритеты (lower = earlier):
   - `0` — interactive (TG router, Stage-1 Sonnet после Stage-0 Haiku)
   - `1` — mandatory tracker follow-up
   - `2` — scheduled reminder/wiki_job
   - `3` — digest-job
   - `4` — ingest-job (background, может ждать минуты)
2. Worker-task'и (число = `MAX_CONCURRENT_CLI`) тянут из очереди и acquire'ят семафор + lock.
3. APScheduler ([D-003](D-003-scheduler-backend.md)) не запускает CLI напрямую — он `put`'ит задачу в priority queue.

### Lock granularity

1. Lock — по **резолвнутому абсолютному пути WIKI** (`Path.resolve()`).
2. Inbox-WIKI и `Health-WIKI` того же юзера — **разные** lock'и → параллелятся.
3. Два CLI на ту же `Health-WIKI` — сериализуются, второй ждёт.
4. Lock — `asyncio.Lock` (in-process). Многопроцессный режим вне scope ([D-006](D-006-state-storage-layout.md): in-process сервис).

### Failure modes

1. **CLI timeout** ([Q-B-17](../questions/Q-B-17-timeouts-kill.md), TBD) — kill subprocess, lock освобождается через `async with`, semaphore тоже.
2. **Service restart** — in-memory lock state теряется. APScheduler misfire policy ([D-003](D-003-scheduler-backend.md)) определяет, что делать с прерванными job'ами. WIKI без активного процесса = свободна.
3. **Lock starvation** — длинный ingest на `Health-WIKI` блокирует interactive той же WIKI. Mitigation: ingest на разные WIKI и в priority=4 (interactive просочится первым в семафор и в свою WIKI).
4. **Semaphore starvation** — 4 длинных digest'а съели все слоты, interactive ждёт. Mitigation: priority queue гарантирует что interactive не встанет за digest, но если все 4 уже выполняются — interactive ждёт min из них. Acceptable trade-off для MVP.

### Метрики (audit.db)

1. `cli_queue_wait_ms` — время в очереди до acquire.
2. `cli_run_ms` — время выполнения CLI.
3. `cli_semaphore_saturation` — сколько раз очередь не пустая.
4. `wiki_lock_contention` — сколько раз lock уже занят.

## Обоснование

1. Best-practice: `bounded pool + per-resource lock` — стандарт для agent runners (LangGraph, Temporal workers, Airflow).
2. Единственный вариант, покрывающий все три риска: capacity (semaphore), correctness (per-WIKI lock), fairness (priority queue).
3. asyncio-native — без внешних зависимостей; согласовано с aiogram 3.x и APScheduler `AsyncIOScheduler` ([D-003](D-003-scheduler-backend.md)).
4. Per-WIKI granularity точно соответствует boundary write-операций ([Q-A-08](../questions/Q-A-08-lock-on-wiki.md) уточнит file-level lock внутри WIKI).
5. Stage-0 Haiku ([D-009](D-009-classifier-engine.md)) **не** проходит через семафор — это отдельный API-вызов, не CLI; снижает нагрузку на семафор ~10×.

## Последствия

1. Появляется модуль `runtime/cli_pool.py` — `Semaphore`, `wiki_lock`, `PriorityQueue`, worker-task'и.
2. APScheduler-job — обёртка `enqueue_cli_task(priority, wiki, prompt, ...)`, не прямой `subprocess.run`.
3. `MAX_CONCURRENT_CLI` — конфиг через `.env`, default 4.
4. Метрики пишутся в `audit.db` ([D-006](D-006-state-storage-layout.md)).
5. Q-A-08 (file-level lock внутри WIKI) — следующий шаг; D-011 даёт directory-level гарантию, file-level пишется отдельно.
6. Q-A-07 закрывается этим решением.

## Запреты

1. Не вызывать Claude CLI в обход priority queue / semaphore — никаких прямых `asyncio.create_subprocess_exec("claude", ...)` вне `cli_pool`.
2. Не использовать `threading.Lock` или `multiprocessing.Lock` — сервис in-process async.
3. Не нарушать acquire-порядок (semaphore → lock). Lock первым = deadlock.
4. Не делать lock granularity мельче WIKI без явного ADR (file-level lock — отдельная тема Q-A-08).
5. Не повышать `MAX_CONCURRENT_CLI` без замера RAM на pilot — каждый CLI ~500MB+.
6. Не пропускать interactive напрямую через CLI без очереди «потому что быстро» — нарушает учёт capacity.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-concurrent-claude.md` при финализации.
