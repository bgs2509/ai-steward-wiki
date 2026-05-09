# D-012: Lock на запись в WIKI — `.wiki.lock` (fcntl advisory) поверх D-011

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-08](../questions/Q-A-08-lock-on-wiki.md), overview §9.8, [D-011](D-011-concurrent-claude.md), [D-006](D-006-state-storage-layout.md)

## Проблема

[D-011](D-011-concurrent-claude.md) даёт directory-level `asyncio.Lock` per-WIKI, но только в пределах одного процесса сервиса. Открытые риски:
1. рестарт сервиса в середине CLI-сессии — in-memory lock теряется;
2. multi-process сценарии (admin-скрипт, debug-запуск) не сериализуются;
3. external writer (Obsidian) — нет сигнала о вторжении;
4. atomic write `index.md` / `log.md` / entity-страницы при concurrent access.

## Варианты

1. **A.** только D-011 in-memory lock — рестарт-race не покрыт.
2. **B.** `<wiki>/.wiki.lock` advisory (`fcntl.flock` / `fasteners.InterProcessLock`) поверх D-011.
3. **C.** optimistic (mtime/git) — interleaved writes в `index.md`/`log.md` ломают консистентность.

## Выбор

**Вариант B.** Юзер подтвердил 2026-05-08.

## Архитектура

### Lockfile

1. Путь: `<wiki>/.wiki.lock` (один на WIKI, в её корне).
2. Содержимое (JSON, перезаписывается при acquire):
   ```json
   {
     "pid": 12345,
     "start_time": 1234567890.123,
     "host": "vps-01",
     "job_kind": "ingest_job | router | digest_job | wiki_job | tracker_*",
     "acquired_at": "2026-05-08T14:30:00+03:00"
   }
   ```
3. Lock-механизм: `fcntl.flock(fd, LOCK_EX | LOCK_NB)` (или `fasteners.InterProcessLock`).
4. `.wiki.lock` добавляется в `.gitignore` каждой WIKI ([Q-E-37](../questions/Q-E-37-git-in-wiki.md), TBD).

### Acquire-порядок (расширение D-011)

```python
async with cli_semaphore:                  # D-011 capacity
    async with wiki_lock(wiki):            # D-011 in-memory
        with InterProcessLock(wiki / ".wiki.lock"):  # D-012 on-disk
            await spawn_claude_cli(...)
```

Порядок строгий: semaphore → in-memory → on-disk. Disk acquire **после** in-memory, чтобы in-process очередь работала первой и не было лишних попыток `flock` с конкурентами того же процесса.

### Stale lock recovery

1. При попытке acquire, если `.wiki.lock` существует и `flock` падает с `EWOULDBLOCK`:
   1. читаем JSON, извлекаем `pid` и `host`;
   2. если `host != socket.gethostname()` ⇒ ждём (другая машина) — для in-process сервиса это не наш кейс, но safe-guard.
   3. иначе `os.kill(pid, 0)` — если `ProcessLookupError` ⇒ stale, лог в audit.db `wiki_lock_recovered`, удаляем файл, повторяем acquire.
2. Timeout acquire по умолчанию — 60с (`Q-B-17` уточнит). После timeout — задача возвращается в priority queue с понижением приоритета +1 (избегаем live-lock).

### Atomic write страниц

1. Все запись в WIKI-файлы (`index.md`, `log.md`, страницы) внутри сервиса — через `tmp + os.replace`:
   ```python
   tmp = path.with_suffix(path.suffix + ".tmp")
   tmp.write_text(content, encoding="utf-8")
   os.replace(tmp, path)  # atomic в пределах одной FS
   ```
2. Для CLI-агента — это правило фиксируется в WIKI CLAUDE.md (`templates/inbox-wiki/CLAUDE.md`, [Q-B-09](../questions/Q-B-09-inbox-claude-md-template.md)/[Q-B-10](../questions/Q-B-10-domain-claude-md-template.md)) как обязательная конвенция.
3. `log.md` — append-only; запись через `open(path, "a")` под тем же `.wiki.lock`.

### External writers (Obsidian)

1. `flock` — advisory: Obsidian его игнорирует.
2. Перед write проверяем `mtime` целевого файла; если он изменился между read и write (relative к началу CLI-сессии) — сервис логирует `external_edit_detected` в audit.db, а CLI-агент инструктируется через CLAUDE.md re-read и merge.
3. Жёсткая блокировка Obsidian вне scope MVP.

## Обоснование

1. Best-practice: git `index.lock`, pip `~/.cache/pip/.lock`, poetry — все advisory lock через flock/fasteners.
2. Advisory ≠ mandatory — Obsidian не блокируется, но мы детектим вторжение по mtime.
3. Durable: lockfile переживает рестарт; stale recovery по PID — стандартная техника (git делает то же).
4. Composable с D-011: in-memory lock — fast path внутри процесса, on-disk — корректность поверх рестартов и multi-process.
5. Atomic rename — POSIX-гарантия, без зависимостей.

## Последствия

1. Зависимость: `fasteners>=0.19` (или stdlib `fcntl` напрямую).
2. Появляется `runtime/wiki_lock.py` — `acquire_wiki_lock(wiki, job_kind)` context manager со stale recovery.
3. `<wiki>/.gitignore` template (через `templates/inbox-wiki/.gitignore`) — содержит `.wiki.lock`.
4. WIKI CLAUDE.md template обязывает CLI-агента писать через `tmp+rename` и не трогать `.wiki.lock`.
5. Метрики audit.db: `wiki_lock_acquire_ms`, `wiki_lock_recovered`, `external_edit_detected`.
6. Q-A-08 закрывается этим решением.

## Запреты

1. Не использовать mandatory lock (`fcntl.lockf` через NFS-mount без проверки support) — только advisory `flock`.
2. Не acquire'ить on-disk lock до in-memory — нарушает порядок и приводит к ненужному disk-IO в hot path.
3. Не писать в файлы WIKI напрямую без `tmp+os.replace` — теряется атомарность при kill-сигнале.
4. Не коммитить `.wiki.lock` в git — обязательно в `.gitignore`.
5. Не делать lock на отдельные файлы внутри WIKI — gran остаётся WIKI-level (избегаем deadlock между `index.md` и `log.md`).
6. Не игнорировать stale recovery — без проверки `kill -0 <pid>` система зависнет после crash сервиса.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-wiki-lock.md` при финализации.
