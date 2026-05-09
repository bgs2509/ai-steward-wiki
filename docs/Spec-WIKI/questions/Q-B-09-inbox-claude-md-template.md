# Q-B-09: Шаблон `CLAUDE.md` для Inbox-WIKI

**Tier:** B
**Источник:** [overview §9 п.9](../raw/20260507-ai-steward-wiki-only-overview.md), §8.3.1

## Формулировка

Содержание router-промпта в `Inbox-WIKI/CLAUDE.md`: список доменов, правила классификации, формат ответа в TG.

## Варианты

1. **A. Жёсткий enum доменов.** Список фиксирован в `CLAUDE.md`, обновляется при `/wiki_init`. Минусы: дрейф.
2. **B. Авто-обнаружение.** Router сканирует `USERS/<NAME>/*-WIKI/` на каждом запуске. Плюсы: zero-config.
3. **C. Hybrid — autodiscover + per-domain hint.** Каждая `<Domain>-WIKI/CLAUDE.md` содержит секцию `## Inbox hint` (когда сюда направлять); router агрегирует.

## Решение

- [x] **Вариант C** — Hybrid: autodiscover `*-WIKI/` + обязательная секция `## Inbox hint` в каждом `<Domain>-WIKI/CLAUDE.md`. Inbox `CLAUDE.md` содержит только мета-правила классификации и формат TG-ответа; каталог доменов агрегируется в runtime. Self-describing domains, zero drift, lint-проверяемый контракт.
- [x] оформлено как [D-016](../decisions/D-016-inbox-claude-md-template.md)

## Связанные

1. [Inbox-WIKI](../entities/inbox-wiki.md), [Router-agent](../entities/router-agent.md)
