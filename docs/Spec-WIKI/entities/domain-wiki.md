# Domain-WIKI

**Тип:** entity
**Статус:** draft
**Источники:** [overview §3a](../raw/20260507-ai-steward-wiki-only-overview.md), §4, §7a

## Суть

Sibling-папка вида `USERS/<NAME>/<Domain>-WIKI/` (`Health-WIKI`, `Recipes-WIKI`, `Study-WIKI`, `Schedule-WIKI`, `Expenses-WIKI`, `Travel-WIKI`, …). Роль = **librarian** (Karpathy LLM Wiki). Один домен = одна WIKI = один артефакт.

## Обязательная структура

1. `CLAUDE.md` — schema/конституция домена (librarian-режим).
2. `index.md` — каталог страниц.
3. `log.md` — append-only хронология.
4. `raw/` — неизменяемые исходники.
5. Подкатегории внутри (`entities/`, `concepts/`, ...) — обычные папки, **не WIKI** (нет `WIKI` в имени, нет своих `raw/`/`index.md`/`log.md`/`CLAUDE.md`).

## Запреты

1. **Anti-nesting** — WIKI внутри WIKI запрещены ([anti-nesting](../concepts/anti-nesting.md)).
2. **Авто кросс-доменные ingest** запрещены (ломают изоляцию). Допустим ручной запуск с `--add-dir` на соседнюю WIKI или meta-WIKI (`Crosslinks-WIKI/`).

## Связанные

1. [Sibling-only domains](../concepts/sibling-only-domains.md)
2. [Anti-nesting](../concepts/anti-nesting.md)
3. [LLM Wiki method](../concepts/llm-wiki-method.md)
4. [Q-B-10: Domain CLAUDE.md template](../questions/Q-B-10-domain-claude-md-template.md) (TBD)
