# Q-B-17: Таймауты и kill-policy

**Tier:** B
**Источник:** [overview §9 п.17](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Макс. длительность одного `/run`, поведение при зависании, прерывание из TG (`/cancel`).

## Варианты

1. **Hard timeout** N сек (например, 300) → SIGTERM → SIGKILL через 10 сек.
2. **`/cancel <run_id>`** или просто `/cancel` для последнего активного run в чате.
3. **Soft warn**: при достижении 80% таймаута — пинг юзеру «сейчас прерву».
4. **Per-job-type лимиты**: reminder=0 (без Claude), wiki_job=120s, digest_job=600s.

## Решение

- [x] **Вариант C** — per-category timeouts + UX-отмена:
  - **Per-category hard timeouts** (override через `payload.timeout_sec`):
    - `reminder` / `notification`: N/A (без Claude).
    - `inbox_classify` (Haiku Stage-0): 15s.
    - `wiki_job` (general Sonnet): 120s.
    - `digest_job`: 600s.
    - `tracker_question`: 30s.
  - **Kill sequence:** `proc.terminate()` (SIGTERM) → 10s grace → `proc.kill()` (SIGKILL); lock (D-012) и semaphore (D-011) освобождаются в `finally`.
  - **UX cancel:** при запуске Claude CLI бот шлёт «⏳ работаю над `<title>`…» с inline-кнопкой `❌ Отмена`; команда `/cancel` отменяет последний active run в чате (fallback для voice/photo).
  - **Storage:** `sessions.db.active_runs(run_id PK, chat_id, started_at, pid, wiki, category)`; на старте сервиса — kill orphan'ов (running runs из прошлой жизни).
  - **Race conditions:** cancel-vs-completion разруливается транзишеном `pending → cancelled` под транзакцией; завершившийся первым выигрывает.
  - **Soft warn перед kill** не делаем в MVP (Вариант D отложен).
- [x] оформлено как [D-021](../decisions/D-021-timeouts-kill-policy.md)

## Связанные

1. [Job-model](../entities/job-model.md), [Q-A-07](Q-A-07-concurrent-claude.md)
