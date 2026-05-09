# D-021: Claude CLI timeouts & kill-policy — per-category timeouts + UX cancel

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-17](../questions/Q-B-17-timeouts-kill.md), overview §9.17, [D-006](D-006-state-storage-layout.md), [D-011](D-011-concurrent-claude.md), [D-012](D-012-wiki-lock.md), [D-019](D-019-cron-failure-mode.md)

## Проблема

Claude CLI subprocess может зависнуть (rate-limit retry, MCP-tool hang, бесконечный loop). Без kill-policy: висящий процесс держит per-WIKI lock и Semaphore-слот, блокирует другие jobs, юзер видит «бот не отвечает». Также юзер может захотеть отменить run явно (передумал, опечатался).

## Варианты

1. **A — Единый hard timeout 300s + SIGTERM/SIGKILL**: ломает digest (600s) и inbox_classify (15s) одновременно.
2. **B — Per-category timeouts + standard kill**: dispersion закрыта, но нет UX отмены.
3. **C — B + `/cancel` команда + inline-кнопка**. ⭐
4. **D — C + soft warn + `/extend`**: overengineering для single-tenant; отложено.

## Выбор

**Вариант C.**

### Per-category hard timeouts

| Категория | Timeout | Обоснование |
|-----------|---------|-------------|
| `reminder` / `notification` | N/A | Без Claude CLI — мгновенно |
| `inbox_classify` (Haiku Stage-0) | **15s** | Fast classification, fail-fast |
| `wiki_job` (general Sonnet) | **120s** | Типичный TG-flow |
| `digest_job` | **600s** | Многостраничная агрегация |
| `tracker_question` | **30s** | Короткий predictive-replies prompt |

Override через `payload.timeout_sec` per-job.

### Kill sequence

1. `asyncio.wait_for(proc.communicate(), timeout=T)` → на превышении:
2. `proc.terminate()` (SIGTERM).
3. `await asyncio.wait_for(proc.wait(), timeout=10)` (10s grace для cleanup).
4. На повторном timeout — `proc.kill()` (SIGKILL).
5. **`finally`**:
   1. Освободить per-WIKI lock ([D-012](D-012-wiki-lock.md)).
   2. Вернуть Semaphore-слот ([D-011](D-011-concurrent-claude.md)).
   3. Удалить запись из `sessions.db.active_runs`.
   4. Сбросить partial output в `audit.db.job_outputs` (для debug).
6. **Классификация результата** для [D-019](D-019-cron-failure-mode.md): timeout → `TransientError` → retry.

### UX cancel

1. **При запуске Claude CLI** в TG-flow: бот отправляет «⏳ работаю над `<title>`…» с `InlineKeyboardMarkup` кнопкой `❌ Отмена` (`callback_data="cancel:<run_id>"`).
2. **Callback handler:** lookup `run_id` в `sessions.db.active_runs`, вызвать `cancel(run_id)` (тот же путь, что hard timeout — SIGTERM → 10s → SIGKILL).
3. **TG команда `/cancel`** (без аргументов): отменяет последний active run в текущем чате (`SELECT … ORDER BY started_at DESC LIMIT 1`); fallback для voice/photo сценариев, где inline-кнопку показать неудобно.
4. **`/cancel <run_id>`**: явная отмена конкретного run'а (для случаев с несколькими параллельными).
5. **Confirmation:** «✋ отменено».

### Storage — `sessions.db.active_runs`

```
active_runs(
  run_id TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  job_id INTEGER,
  started_at INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  wiki TEXT NOT NULL,
  category TEXT NOT NULL,
  timeout_sec INTEGER NOT NULL,
  inline_message_id TEXT
)
```

INDEX по `(chat_id, started_at DESC)` для fast lookup при `/cancel`.

### Orphan cleanup на старте сервиса

1. При старте: `SELECT pid FROM active_runs WHERE pid IS NOT NULL`.
2. Для каждого `pid`: проверить `os.kill(pid, 0)`; если процесс жив (с прошлой жизни сервиса) — `proc.terminate() → 10s → kill`.
3. Очистить `active_runs`.
4. Audit-event: «orphan cleanup: N runs killed».

### Race conditions

1. **Cancel-vs-completion:** транзакция в `sessions.db` — `UPDATE active_runs SET status='cancelling' WHERE run_id=? AND status='running'`. Если 0 rows affected — run уже завершился штатно; cancel становится no-op.
2. **Timeout-vs-cancel:** оба пути ведут к одному cleanup-flow; первый выигрывает, второй no-op.
3. **Concurrent cancel:** идемпотентен (повторный SIGTERM на мёртвом процессе — no-op).

## Последствия

1. Production-grade UX отмены: стандарт modern TG-ботов.
2. Per-category timeouts закрывают семантическую дисперсию; `inbox_classify` fail-fast не висит на digest-окне.
3. Orphan-cleanup защищает от resource leak после crash сервиса.
4. Запреты:
   1. **Не использовать `proc.kill()` без `proc.terminate()`** — нарушает graceful shutdown Claude CLI (теряет partial output, может оставить child-MCP-процессы).
   2. **Не уменьшать grace period < 5s** — Claude CLI flush logs занимает время.
   3. **Не освобождать lock/semaphore вне `finally`** — leak на любом раннем return.
5. Будущее: soft warn перед kill (Вариант D) и `/extend` команда — отложены до реальной потребности.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-021-timeouts-kill-policy.md` (когда финализируется)
