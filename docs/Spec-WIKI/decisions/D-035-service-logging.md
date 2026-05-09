# D-035: Service logging — structlog → stdout JSON → journald

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-34](../questions/Q-E-34-service-logging.md), [D-006](D-006-state-storage-layout.md), [D-019](D-019-cron-failure-mode.md), [D-021](D-021-timeouts-kill-policy.md), [D-034](D-034-pii-redactor.md)

## Проблема

Сервис бежит как systemd unit на VPS. Application-логи нужны для (1) debug runtime issues (retry/DLQ из [D-019](D-019-cron-failure-mode.md), CLI-timeouts из [D-021](D-021-timeouts-kill-policy.md)), (2) trace correlation через слои (TG-handler → classifier → executor → notify), (3) отделение от business-events в `audit.db` ([D-006](D-006-state-storage-layout.md)) и от диалогов в `chat_log` ([D-033](D-033-chat-history.md)). Глобальный `~/.claude/CLAUDE.md` § Logging требует structured fields + correlation_id + log-anchors на critical branches; PII-redactor ([D-034](D-034-pii-redactor.md)) должен прогоняться над application-логами тоже.

## Варианты

1. **A — stdlib `logging` → stdout (text) → journald.**
2. **B — `structlog` → stdout (JSON) → journald, с processors-pipeline.** ⭐
3. **C — B + дублирующий file-sink с rotate.**
4. **D — B + Loki/Grafana/OTLP сразу.**
5. **E — `loguru` → stdout → journald.**

## Выбор

**Вариант B.**

### Стек

1. `structlog` >= 24.x как primary logger.
2. Stdlib `logging` interop (через `structlog.stdlib.LoggerFactory`) — для совместимости с aiogram/APScheduler internals, которые пишут через stdlib.
3. Sink: stdout JSON-lines, journald собирает автоматически (12-factor §XI).
4. Никакой ротации в app — journald делает persistence + ротацию.

### Processor pipeline

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,           # correlation_id, user_id
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        pii_redactor_processor,                            # D-034
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
```

### Стабильный набор полей

1. `ts` — UTC ISO 8601.
2. `level` — `debug|info|warning|error|critical`.
3. `event` — short event name (snake_case): `job_started`, `cli_timeout`, `dlq_pushed`, `redaction_applied`.
4. `module` — Python module name.
5. `correlation_id` — UUID4, биндится в начале каждого запроса/job-run через `contextvars`.
6. `user_id` — TG user_id (если контекст связан с юзером).
7. `wiki_id` — активная WIKI (если есть).
8. `job_id` / `run_id` — если контекст job-execution.

Опциональные: `category` (job-category из D-019/D-021), `duration_ms`, `error_type`.

### Correlation context

1. На входе TG-update: `bind_contextvars(correlation_id=uuid4(), user_id=update.from_user.id)`.
2. На входе scheduled job: `bind_contextvars(correlation_id=uuid4(), job_id=job.id, category=job.category)`.
3. `clear_contextvars()` в `finally` — защита от leak между requests.
4. Async-safe: `structlog.contextvars` использует `contextvars.ContextVar` — корректная пропагация в `asyncio.Task`.

### Log levels policy

1. Default: `INFO`.
2. Override через env: `LOG_LEVEL=DEBUG`, per-module через `LOG_LEVEL_<MODULE>=DEBUG` (например `LOG_LEVEL_CLASSIFIER=DEBUG`).
3. Никогда не `DEBUG` в production по умолчанию (PII-leak risk даже с redactor).

### PII redactor integration

1. `pii_redactor_processor(logger, method_name, event_dict)` — вызывает `redactor.redact_dict(event_dict)`.
2. Применяется ко **всем** string-полям event_dict (не только `event`).
3. Результат: `event_dict` со replaced strings + meta-поле `_redacted: list[RedactionEvent]` (если что-то заменялось — для observability качества regex-pack).
4. Failure-mode: если processor падает — лог пишется без redactor + critical-event `pii_processor_failed` в audit.db (fail-loud, не silent-skip).

### Journald query

1. Live tail: `journalctl -u ai-steward-wiki -f -o json | jq`.
2. Filter by correlation_id: `journalctl -u ai-steward-wiki -o json | jq 'select(.correlation_id == "<uuid>")'`.
3. Persistence — настраивается на VPS (`Storage=persistent` в `journald.conf`); ротация — journald default (10% disk).

### Future-proof

1. JSON output → легко сменить sink на Loki/OTLP без переписывания app: добавить `Vector` или `Promtail` exporter, читающий journald.
2. Schema стабильна (см. поля выше) — индексирование на стороне observability stack тривиально.

## Последствия

1. Один dependency (`structlog`); zero-rotation-complexity (journald handles).
2. Единая точка PII-redaction для всех application-логов.
3. Correlation context работает через async-границы (`contextvars`).
4. Loki/OTLP добавляются позже без переписывания.
5. Запреты:
   1. **Не писать в файлы** application-логи — только stdout.
   2. **Не использовать `print()`** для логов — всё через structlog.
   3. **Не логировать plaintext PII** — pipeline всегда применяет redactor (D-034).
   4. **Не использовать `loguru`** или другие альтернативы (один logger в проекте).
   5. **Не пересекать с `audit.db`** — application-логи (как работал код) ≠ business-events (что сделано).
   6. **Не ставить `DEBUG` уровень в production без явного env-flag.**

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-035-service-logging.md` (когда финализируется)
