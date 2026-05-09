# Two-stage launch

**Тип:** concept
**Статус:** draft
**Источники:** [overview §2.1](../raw/20260507-ai-steward-wiki-only-overview.md)

## Суть

Каждое сообщение юзера обрабатывается двумя последовательными Claude-вызовами: **Классификатор** → **Исполнитель**.

## Шаги

1. **Идентификация.** Бот принимает сообщение, по `telegram_id` находит `home_dir = USERS/<NAME>/`. Неизвестный ID игнорируется молча.
2. **Сохранение входа** в `Inbox-WIKI/raw/<timestamp>_<source>.<ext>`.
3. **Stage 1 — Классификатор.** Claude (или Haiku fast-path) выбирает целевую WIKI, сессию, при необходимости создаёт новую `<Domain>-WIKI/`.
4. **Stage 2 — Исполнитель.** Claude запускается в выбранной WIKI с `cwd=<wiki>`, `--add-dir <wiki>`, инжектом LLM Wiki system prompt.
5. **Стриминг** ответа в TG; список изменённых страниц + последняя запись `log.md` — отдельным сообщением.

## Альтернативы

1. **Power-user `/run <wiki> <prompt>`** — минует Stage 1, указывает WIKI явно (§6).
2. **Cron** — три типа задач без классификатора ([Job-model](../entities/job-model.md)).

## Связанные

1. [Classifier](../entities/classifier.md)
2. [Smart inbox routing](smart-inbox-routing.md)
3. [Q-A-04: Classifier engine](../questions/Q-A-04-classifier-engine.md)
