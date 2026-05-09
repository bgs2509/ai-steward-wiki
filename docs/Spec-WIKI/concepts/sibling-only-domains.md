# Sibling-only domains

**Тип:** concept
**Статус:** draft
**Источники:** [overview §3a п.3](../raw/20260507-ai-steward-wiki-only-overview.md)

## Правило

Внутри `USERS/<NAME>/` каждый домен — самостоятельный артефакт `<Domain>-WIKI/` непосредственно как sibling. Юзер растёт **горизонтально** (новые siblings), а не вертикально (вложение).

## Примеры

`Health-WIKI`, `Recipes-WIKI`, `Study-WIKI`, `Schedule-WIKI`, `Expenses-WIKI`, `Travel-WIKI`, `Inbox-WIKI`.

## Кросс-доменные запросы

1. **Запрещено:** автоматические кросс-доменные ingest (ломают изоляцию).
2. **Допустимо:** ручной `--add-dir` на соседнюю WIKI (read-only).
3. **Допустимо:** отдельная meta-WIKI (`Crosslinks-WIKI/`) как ещё один sibling.

## Связанные

1. [Domain-WIKI](../entities/domain-wiki.md)
2. [Anti-nesting](anti-nesting.md)
