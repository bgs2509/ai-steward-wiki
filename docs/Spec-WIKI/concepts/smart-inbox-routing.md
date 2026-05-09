# Smart inbox routing

**Тип:** concept
**Статус:** draft
**Источники:** [overview §8.1, §8.3.1, §8.3.3](../raw/20260507-ai-steward-wiki-only-overview.md)

## Суть

Юзер не указывает целевую папку и команду. Любой контент в TG → `Inbox-WIKI/raw/` → Router-Claude классифицирует и предлагает действия inline-кнопками. Подтверждение → перемещение в целевую WIKI + ingest + создание cron-задач.

## Три класса сценариев (§8.1)

1. **Reminder-as-message (lightweight cron)** — «*разбуди в 6*». Без Claude. Только scheduler + sendMessage.
2. **Aggregator / digest** — «*каждый день в 9 утра сводка*». Claude с `--add-dir` в несколько WIKI + чтение `planner.json`.
3. **Smart inbox + auto-routing** — фото билета/чека/афиши. Claude классифицирует → спрашивает условия напоминания → создаёт cron + кладёт в WIKI.

## Двухуровневый intent-detection (§8.3.3)

1. **Fast path** — Haiku/regex для тривиальных intent (`reminder` / `wiki_action` / `unclear`). Миллисекунды.
2. **Heavy path** — Router-Claude в `Inbox-WIKI/`. ~10–30 сек.

Экономия токенов и latency на ~80% типичных сообщений.

## Связанные

1. [Inbox-WIKI](../entities/inbox-wiki.md)
2. [Classifier](../entities/classifier.md)
3. [Job-model](../entities/job-model.md)
4. [Two-stage launch](two-stage-launch.md)
