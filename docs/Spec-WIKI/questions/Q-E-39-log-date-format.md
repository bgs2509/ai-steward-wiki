# Q-E-39: Format даты в `log.md`

**Tier:** E
**Источник:** [overview §9 п.39](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

UTC или локальная TZ VPS (Europe/Moscow).

## Варианты

1. **A. UTC.** Консистентно с серверной практикой; парсится без TZ-двусмысленности.
2. **B. Локальная TZ юзера.** Удобно читать.
3. **C. ISO 8601 с TZ-suffix** (`2026-05-08T14:30+03:00`). Однозначно и читаемо.

## Решение

- [x] оформлено как [D-040](../decisions/D-040-log-date-format.md): ISO 8601 с TZ-offset (`YYYY-MM-DDTHH:MM±HH:MM`), minute-granularity, Europe/Moscow default + per-WIKI override через frontmatter. Application-logs/audit.db остаются UTC.

## Связанные

1. [LLM Wiki method](../concepts/llm-wiki-method.md)
