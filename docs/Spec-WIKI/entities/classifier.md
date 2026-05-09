# Classifier

**Тип:** entity
**Статус:** review
**Источники:** [overview §2.1](../raw/20260507-ai-steward-wiki-only-overview.md), §8.3.3

## Суть

Первый из двух Claude-вызовов в [двухступенчатом запуске](../concepts/two-stage-launch.md). Решает: (1) в какой WIKI-папке работать, (2) продолжить существующую сессию или начать новую, (3) нужна ли новая `<Domain>-WIKI/`.

## Входы

1. Текущее сообщение юзера.
2. Недавняя история промптов юзера.
3. Список существующих WIKI-папок в `home_dir`.

## Выходы

1. `target_wiki: Path` — куда отправить Исполнителя.
2. `session_action: new | resume(session_id)`.
3. `create_wiki: Optional[Domain]` — если нужна новая WIKI.

## Реализация (D-009)

Двухступенчатый routing:

1. **Stage-0 (Haiku API)** — `claude-haiku-4-5` через Anthropic SDK, structured output `{intent, reminder?, confidence}`. Intent ∈ {`reminder`, `wiki_action`, `unclear`}. Latency 1–2с. Не читает WIKI/CLAUDE.md.
2. **Стрелка к Job** — если `intent=reminder` & `confidence≥0.85` & время распарсено ⇒ INSERT в `jobs` (D-002), Stage-1 не вызывается.
3. **Stage-1 (CLI Sonnet)** — `claude --model sonnet -p ... --add-dir <Inbox-WIKI>` (D-007) в cwd `USERS/<NAME>/Inbox-WIKI/` (D-004). Полный контекст: router-CLAUDE.md, история, sibling WIKI. Latency 10–30с.

См. [D-009](../decisions/D-009-classifier-engine.md).

## Связанные

1. [Inbox-WIKI](inbox-wiki.md)
2. [Router-agent](router-agent.md)
3. [Two-stage launch](../concepts/two-stage-launch.md)
