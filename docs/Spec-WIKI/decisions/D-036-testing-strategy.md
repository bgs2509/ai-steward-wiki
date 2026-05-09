# D-036: Testing strategy — pyramid (unit + integration + manual e2e)

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-35](../questions/Q-E-35-testing.md), [D-006](D-006-state-storage-layout.md), [D-009](D-009-classifier-engine.md), [D-013](D-013-claude-cli-auth.md), [D-019](D-019-cron-failure-mode.md), [D-021](D-021-timeouts-kill-policy.md), [D-034](D-034-pii-redactor.md)

## Проблема

Сервис состоит из множества внешних зависимостей: Claude CLI subprocess ([D-009](D-009-classifier-engine.md)), aiogram TG, APScheduler ([D-003](D-003-scheduler-backend.md)), 3×SQLite WAL ([D-006](D-006-state-storage-layout.md)), faster-whisper STT ([D-022](D-022-voice-photo-input.md)), filesystem WIKI с advisory lock ([D-012](D-012-wiki-lock.md)). Без явной testing-policy критические паттерны (retry/DLQ из [D-019](D-019-cron-failure-mode.md), timeouts из [D-021](D-021-timeouts-kill-policy.md), redactor из [D-034](D-034-pii-redactor.md)) могут оказаться непокрыты.

## Варианты

1. **A — Только unit с fake claude-binary в PATH.**
2. **B — Test pyramid: unit (adapter-mock) + integration (real CLI behind flag) + manual e2e.** ⭐
3. **C — B без e2e.**
4. **D — B + property-based testing (`hypothesis`).**
5. **E — B + mutation testing (`mutmut`).**

## Выбор

**Вариант B.**

### Уровни

#### 1. Unit — `tests/unit/`

1. **Stack:** `pytest` + `pytest-asyncio` (strict-mode, `asyncio_mode=auto`).
2. **Claude CLI mock:** `ClaudeRunner` Protocol + `FakeClaudeRunner` impl (in-memory deterministic). НЕ fake-binary в PATH (медленнее на fork+exec, не Windows-portable).
   ```python
   class ClaudeRunner(Protocol):
       async def run(self, *, cwd: Path, args: list[str], stdin: str | None) -> RunResult: ...
   class FakeClaudeRunner:
       def __init__(self, scripted: list[RunResult]): ...
   ```
3. **TG mock:** `aiogram.test_utils.MockedBot` для handler-тестов.
4. **DB:** SQLite через `tmp_path` fixture, WAL-режим включён (как в D-006).
5. **Scheduler:** APScheduler НЕ запускается реально — `scheduler.add_job` mock'ается; time-travel через `freezegun`.
6. **Coverage target:** 80% soft на core (`classifier`, `executor`, `scheduler_glue`, `redactor`, `notify`, `dlq`); 60% soft на boundary-код.
7. **Запуск:** `pytest tests/unit -q` (fast, <30s).

#### 2. Integration — `tests/integration/`

1. **Trigger:** env `RUN_INTEGRATION=1` (default skip).
2. **Real Claude CLI:** реальный subscription (Henry-N), реальный `claude` бинарь. Тесты дёргают `--print` на минимальных WIKI fixtures.
3. **Real SQLite:** не in-memory; реальный `tmp_path/jobs.db` с WAL.
4. **Real APScheduler:** `AsyncIOScheduler` с `MemoryJobStore` (не SQLAlchemy чтоб не загрязнить tmp_path); реальный `scheduler.run_now()`.
5. **Покрывает critical paths:**
   1. Stage-0 Haiku → Stage-1 Sonnet handoff (D-009).
   2. Retry/DLQ под TransientError (D-019).
   3. Timeout → SIGTERM → SIGKILL (D-021).
   4. PII redactor end-to-end на реальных prompt'ах (D-034).
   5. WIKI-lock contention (D-012).
6. **Запуск:** `RUN_INTEGRATION=1 pytest tests/integration -q` (~5-10 min, ~$0.10-0.50 Anthropic billing per run).
7. **CI:** nightly job в GitHub Actions (когда репо появится) — не на каждый PR.

#### 3. Manual E2E — `tests/e2e/checklist.md`

1. **Не автоматизированный;** human-driven smoke-test перед релизом.
2. **Setup:** sandboxed VPS + test TG-bot token (`@ai_steward_wiki_test_bot`) + dedicated test-Henry-N юзер.
3. **Checklist** (markdown файл, обновляемый):
   1. Voice ingest → STT → classify → wiki-write.
   2. Photo ingest → vision → wiki-write.
   3. Cron-job execute → digest → TG notify.
   4. `/cancel` flow.
   5. Streaming edit → chain split при >4000 chars.
   6. `/wiki_init <Domain>` → materialize template.
   7. Allowlist hot-reload через SIGHUP.
   8. PII-redaction видна в `audit.db.audit_events` для prompt с email/phone.
4. **Когда:** перед каждым релизом (тегом git); audit-checklist хранится в repo.

### Что МОКАЕТСЯ всегда (даже в integration)

1. TG Bot API — `MockedBot` (real TG требовал бы полный e2e setup).
2. Anthropic Haiku API на Stage-0 — opt-in real через отдельный flag `RUN_HAIKU=1` (отдельная стоимость).
3. faster-whisper — fake transcript fixture (модель тяжёлая, в integration-tests запускать дорого).

### Pytest layout

```
tests/
├── conftest.py                    # shared fixtures (tmp_path DB, FakeClaudeRunner, MockedBot)
├── unit/
│   ├── test_classifier.py
│   ├── test_redactor.py
│   ├── test_dlq.py
│   ├── test_scheduler_glue.py
│   ├── test_handlers/
│   └── test_streaming.py
├── integration/
│   ├── test_real_cli_handoff.py
│   ├── test_retry_dlq.py
│   ├── test_timeouts.py
│   └── test_pii_e2e.py
└── e2e/
    └── checklist.md
```

### CI gate

1. PR-gate: `pytest tests/unit` обязателен (fail = block merge).
2. Nightly: `RUN_INTEGRATION=1 pytest tests/integration` (informational, не блокирует merge).
3. Pre-release: human runs e2e/checklist.md, фиксирует results в release notes.

## Последствия

1. Adapter-interface чище fake-binary в PATH (быстрее, portable).
2. Real-CLI integration ловит upstream Anthropic-changes, не покрытые mock'ом.
3. E2E manual = дисциплина, но необходим для TG-specific bugs (callback_query, edit-frames, voice).
4. Coverage gate реалистичен (80% soft, не 100%).
5. Запреты:
   1. **Не запускать integration в PR-gate** — только nightly + manual.
   2. **Не использовать fake `claude` shell-script в PATH** — adapter-interface only.
   3. **Не пропускать e2e перед релизом** — checklist обязателен.
   4. **Не моcкать SQLite через `unittest.mock.MagicMock`** — реальный `tmp_path/*.db` всегда.
   5. **Не запускать APScheduler `start()` в unit-тестах** — только `add_job` мок + freezegun.
   6. **Не использовать `mutmut` / `hypothesis`** в MVP — добавить отдельным решением если будут реальные edge-case bugs.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-036-testing-strategy.md` (когда финализируется)
