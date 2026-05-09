# Q-E-34: Логирование сервиса

**Tier:** E
**Источник:** [overview §9 п.34](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Stdout → journald / structlog → файл / отдельный observability стек.

## Варианты

1. **A. structlog → stdout → journald.** Просто, `journalctl -u ai-steward-wiki`.
2. **B. structlog → JSON-файл с rotate.** Удобно для парсинга, но ещё один артефакт.
3. **C. + Loki/Grafana / OTLP.** Out-of-scope MVP.

## Решение

- [x] оформлено как [D-035](../decisions/D-035-service-logging.md): `structlog` → stdout JSON → journald, processors-pipeline с PII-redactor (D-034), correlation_id через `contextvars`.

## Связанные

— нет.
