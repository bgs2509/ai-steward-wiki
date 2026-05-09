# D-009: Classifier engine — гибрид Haiku → CLI Sonnet

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-04](../questions/Q-A-04-classifier-engine.md), overview §2.1 / §8.3.3, [D-002](D-002-job-model-storage.md), [D-004](D-004-inbox-wiki-scope.md)

## Проблема

Каждое TG-сообщение требует решения: (1) reminder с распарсенным временем (горячий путь, прямая запись в `jobs`), (2) wiki_action (требует контекста WIKI и истории), (3) unclear (нужен heavy reasoning). Один движок на все случаи либо медленный/дорогой (CLI на каждый «напомни в 18:00»), либо слепой к контексту (Haiku без WIKI).

## Варианты

1. **A. Только Claude CLI** — единый pipeline, но 10–30с latency и $0.01–0.05/msg на каждом сообщении.
2. **B. Только Haiku API** — 1–2с, дёшево, но без WIKI-контекста и истории.
3. **C. Гибрид Haiku → CLI Sonnet** — горячий путь Haiku, тяжёлый CLI только при необходимости.

## Выбор

**Вариант C.** Юзер подтвердил 2026-05-08 с явным указанием моделей: Stage-0 = Haiku, Stage-1 CLI = Sonnet.

## Архитектура

### Stage-0: Haiku intent classifier

1. Прямой вызов `anthropic.messages.create(model="claude-haiku-4-5", ...)` (без CLI).
2. System-промпт: классификация в один из трёх intent'ов + опциональный distill распарсенного времени для reminder.
3. Output: structured JSON через `response_format` / tool_use:
   ```json
   {
     "intent": "reminder" | "wiki_action" | "unclear",
     "reminder": {
       "title": "...",
       "when": "ISO8601",
       "repeat": null | {...}
     } | null,
     "confidence": 0.0..1.0
   }
   ```
4. Контекст: только текст сообщения юзера + краткая history (последние N сообщений) + список существующих WIKI-папок (имена). Без чтения CLAUDE.md.
5. Latency target: 1–2 секунды.

### Stage-1: CLI Sonnet (только при wiki_action / unclear / низкой confidence)

1. `claude --model sonnet -p "<router-prompt>" --add-dir <Inbox-WIKI>` ([D-007](D-007-add-dir-scope.md)).
2. Cwd = `USERS/<NAME>/Inbox-WIKI/` ([D-004](D-004-inbox-wiki-scope.md)).
3. Полный доступ к router-CLAUDE.md, истории сессии, sibling WIKI через CLAUDE.md auto-walk.
4. Latency tolerated: 10–30 секунд.

### Маршрутизация

```
TG message
   ↓
Stage-0 (Haiku)
   ├─ intent=reminder & confidence ≥ 0.85 & время распарсено
   │     → INSERT INTO jobs (kind='reminder_job', payload=...) [D-002]
   │     → отправить TG-подтверждение
   │
   ├─ intent=wiki_action OR confidence < 0.85
   │     → Stage-1 CLI Sonnet в Inbox-WIKI
   │
   └─ intent=unclear
         → Stage-1 CLI Sonnet в Inbox-WIKI (router решает: уточнить / создать WIKI / выполнить)
```

## Обоснование

1. Best-practice **two-tier routing** (Anthropic «Building effective agents» 2024, LangChain RouterChain, OpenAI Assistants).
2. Экономика: 70–90% сообщений — простые reminder'ы, не требуют CLI.
3. UX: «напомни в 18:00 позвонить маме» получает подтверждение за 1–2с, а не за 20с.
4. Согласовано с overview §8.3.3.
5. Чистое разделение: Haiku — extraction/classification без действий; CLI Sonnet — actions с полным контекстом.
6. Sonnet (а не Opus) на Stage-1 — баланс качества/стоимости для router-агента; Opus резервируется на ingest-job в `<Domain>-WIKI`.

## Confidence threshold и fallback

1. Threshold по умолчанию: `0.85`. Точное значение калибруется на pilot-данных.
2. Если Haiku вернул `intent=reminder` но `confidence < threshold` ИЛИ время не распарсилось ⇒ fallback на Stage-1.
3. Если Haiku API недоступен (timeout, rate-limit, 5xx) ⇒ fallback на Stage-1 CLI с пометкой degraded в audit.db.
4. Если Stage-0 misclassified (reminder создан ошибочно) — юзер отменяет через TG inline-кнопку; событие пишется в audit как `classifier_correction` для офлайн-калибровки.

## Последствия

1. Появляется отдельный модуль `classifier/haiku.py` — синхронный Anthropic SDK call, без подключения к CLI-инфраструктуре.
2. Anthropic API key — отдельный конфиг; CLI auth ([Q-C-20](../questions/Q-C-20-claude-cli-auth.md)) к нему не относится.
3. NL-time-parsing ([Q-A-05](../questions/Q-A-05-nl-time-parsing.md)) — выполняется внутри Stage-0 Haiku-промпта (LLM-парсинг), либо отдельной библиотекой; решается в Q-A-05.
4. Concurrent CLI ([Q-A-07](../questions/Q-A-07-concurrent-claude.md)) — нагрузка на CLI снижается ~10× благодаря Stage-0, но проблема не отменяется.
5. Метрика `stage0_to_stage1_ratio` — KPI системы (target ≥70% сообщений завершаются на Stage-0).
6. Q-A-04 закрывается этим решением.

## Запреты

1. Stage-0 Haiku **не выполняет действий** — только классифицирует и distill'ит структурированные поля. Любая запись в `jobs` происходит в оркестраторе после Stage-0, а не внутри Haiku-вызова.
2. Stage-0 **не читает CLAUDE.md / WIKI-страницы** — это работа Stage-1.
3. Не использовать Sonnet/Opus на Stage-0 — нарушает экономику решения.
4. Не использовать Haiku на Stage-1 — теряется reasoning в сложных wiki_action.
5. Не уменьшать confidence threshold ниже 0.7 без явного ADR-override — это путь к silent misclassification.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-classifier-engine.md` при финализации.
