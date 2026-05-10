# Q-B-10: Шаблон `CLAUDE.md` для domain-WIKI

**Tier:** B
**Источник:** [overview §9 п.10](../raw/20260507-ai-steward-wiki-only-overview.md), §8.4

## Формулировка

Единый дефолт vs per-domain (Health/Recipes/Study).

## Варианты

1. **A. Один дефолтный шаблон.** Karpathy librarian-схема, без специфики. Юзер дописывает руками.
2. **B. Per-domain пресеты.** Health → правила про дозы/анализы; Expenses → категоризация; Recipes → ингредиенты. Router выбирает шаблон при `intent=create_wiki`.
3. **C. Default + skill-pack.** Базовый шаблон + опциональные подключаемые «skill-pack» секции (`@health-pack`, `@finance-pack`).

## Решение

- [x] **Вариант B** — Per-domain пресеты в `templates/<domain>.md` + fallback `_default.md`. Стартовый набор: `health`, `investment`, `budget`, `family`, `study`, `career`, `home`, `hobby`, `recipes`, `_default`. Шаблоны — локальная SSoT repo сервиса; runtime не читает parent `ai-steward/CLAUDE.md`. `intent=create_wiki <Domain>` матчит имя case-insensitive; нет совпадения → `_default.md`. Каждый пресет обязан содержать секцию `## Inbox hint` (контракт из D-016).
- [x] оформлено как [D-017](../decisions/D-017-domain-claude-md-template.md)

## Связанные

1. [Domain-WIKI](../entities/domain-wiki.md)
