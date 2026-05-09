# D-040: log.md date format — ISO 8601 с TZ-offset, minute-granularity, per-WIKI override

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-39](../questions/Q-E-39-log-date-format.md), [D-006](D-006-state-storage-layout.md), [D-010](D-010-nl-time-parsing.md), [D-033](D-033-chat-history.md), [D-035](D-035-service-logging.md)

## Проблема

`log.md` внутри runtime WIKI (Karpathy LLM Wiki append-only лог операций ingest/query/lint) — human-facing, юзер читает глазами, но также парсится сервисом для retention/cleanup/cross-ref. Spec-WIKI собственный `CLAUDE.md` §4.6 использует date-only `[YYYY-MM-DD]` — для мета-вики разработки это OK, но для runtime WIKI с десятками операций в день — недостаточно. UTC vs local TZ vs ISO с offset — trade-off читаемости и однозначности.

**Различение:** `log.md` runtime WIKI ≠ application-логи сервиса ([D-035](D-035-service-logging.md), UTC ISO в structlog). И ≠ business-events в `audit.db` ([D-006](D-006-state-storage-layout.md), UTC ISO).

## Варианты

1. **A — UTC, ISO 8601 (`2026-05-09T14:30:00Z`).**
2. **B — Локальная Europe/Moscow без TZ-suffix.**
3. **C — ISO 8601 с TZ-offset suffix, Europe/Moscow default, per-WIKI override.** ⭐
4. **D — Двойной формат (UTC frontmatter + local human-string).**
5. **E — Date-only `YYYY-MM-DD` (как Spec-WIKI собственный).**

## Выбор

**Вариант C.**

### Header format

```markdown
## [2026-05-09T17:30+03:00] ingest | lab_results_2026-05.pdf

## [2026-05-09T18:12+03:00] query | "что у меня было в апреле по давлению"

## [2026-05-09T19:00+03:00] lint | broken-link fix
```

1. **Anchor:** `## [<iso8601-with-offset>] <op> | <title>`.
2. **Granularity:** до минуты. Секунды опускаем — лишний шум для human-reader, parsing работает (`datetime.fromisoformat` parses optional seconds).
3. **`<op>` ∈** `init | ingest | query | lint | refactor | migrate` (последнее — для `claude_md_migrate` событий из [D-039](D-039-claude-md-evolution.md)).

### TZ source

1. **Default:** Europe/Moscow (consistent с tracker [D-010](D-010-nl-time-parsing.md), Henry-домицилирован в Москве).
2. **Override:** per-WIKI через frontmatter `CLAUDE.md`:
   ```yaml
   ---
   schema_version: 4
   template_id: career-domain
   timezone: Europe/Berlin     # override
   ---
   ```
3. **Resolution:** `tz = wiki_frontmatter.get('timezone') or default_user_timezone or 'Europe/Moscow'`. Reader логики (lint, retention) использует тот же resolved tz.

### Парсинг

```python
from datetime import datetime
ts = datetime.fromisoformat("2026-05-09T17:30+03:00")
# tz-aware datetime, comparison-safe vs другими aware-datetimes
```

Python 3.11+ `datetime.fromisoformat` корректно обрабатывает `+HH:MM` suffix.

### Append rules

1. **Append-only;** прошлые записи никогда не редактируются (Karpathy method).
2. **Author of entry:** бот пишет от имени executor'а (классификатор/router/cron-job).
3. **Required fields:** `ts`, `op`, `title`. Optional body после header — markdown свободной формы.
4. **Atomicity:** запись через `tmp + os.replace` под `.wiki.lock` ([D-012](D-012-wiki-lock.md)) — никаких partial appends.

### Differences from sibling timestamps

| Контекст | Формат | TZ |
|----------|--------|-----|
| `log.md` runtime WIKI (D-040) | `YYYY-MM-DDTHH:MM±HH:MM` | per-WIKI / Europe/Moscow default |
| Application-logs (D-035) | `YYYY-MM-DDTHH:MM:SS.sssZ` | UTC |
| `audit.db.audit_events.ts` (D-006) | `YYYY-MM-DDTHH:MM:SS.sssZ` | UTC |
| `chat_log.ts` (D-033) | `YYYY-MM-DDTHH:MM:SS.sssZ` | UTC |
| `tracker_answers.answered_at` (D-014) | `YYYY-MM-DDTHH:MM:SS.sssZ` | UTC |
| `jobs.db` schedule datetimes (D-010) | UTC internally; user TZ только на input/output | UTC storage, user-tz display |

**Rationale:** machine-storage = UTC (сравнения, retention, cross-tz юзеры). Human-facing markdown = local-tz с явным offset (читаемо + parse-able).

### Spec-WIKI собственный log.md

Не меняется. Spec-WIKI — мета-зона разработки, использует `## [YYYY-MM-DD]` per Spec-WIKI/CLAUDE.md §4.6. D-040 регулирует только runtime WIKI (Health-WIKI/log.md, Career-WIKI/log.md, Inbox-WIKI/log.md, etc.).

## Последствия

1. Human-readable + machine-parseable; нет TZ-ambiguity.
2. Multi-region ready через per-WIKI frontmatter override.
3. Granularity (минуты) — sweet-spot для human-reader и для multiple ops/day.
4. Различение storage-tz (UTC) и display-tz (local) сохранено.
5. Запреты:
   1. **Не использовать `Z` suffix** в `log.md` — только explicit `+HH:MM`.
   2. **Не опускать TZ-suffix** — implicit TZ запрещён.
   3. **Не редактировать прошлые записи** — append-only.
   4. **Не использовать date-only** в runtime WIKI — minute-granularity обязательна.
   5. **Не путать `log.md` с application-логами** — application-logs идут в structlog/journald (D-035), не в WIKI markdown.
   6. **Не записывать секунды/наносекунды** в header (шум для reader, не нужно для granularity).

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-040-log-date-format.md` (когда финализируется)
