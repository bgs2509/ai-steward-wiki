# Q-A-05: NL-парсинг времени

**Tier:** A
**Источник:** [overview §9 п.5](../raw/20260507-ai-steward-wiki-only-overview.md)

> **Update ([D-042](../decisions/D-042-unify-user-config.md), 2026-05-10):** упоминания `roles.toml` ниже — историческая формулировка; текущий SSoT для user-attributes (включая `timezone`) — `users.toml`.

## Формулировка

Локальный (`dateparser`, `parsedatetime`) vs LLM-парсинг vs гибрид. Часовой пояс юзера в `roles.toml` обязателен.

## Варианты

1. **A. Локальный.** Дёшево, детерминированно. `dateparser` хорошо работает с русским. Минусы: edge-cases («через две пятницы»).
2. **B. LLM-парсинг.** Universal, но дорого/медленно/недетерминированно.
3. **C. Гибрид.** Сначала `dateparser`; на провал → LLM с явным system-промптом «верни ISO».

## Решение

- [x] Вариант C — гибрид: `dateparser` first, Haiku fallback на edge-cases, Stage-1 escalation на ambiguous. Юзер подтвердил 2026-05-08. См. [D-010](../decisions/D-010-nl-time-parsing.md) (accepted).
- [x] оформлено как [D-010](../decisions/D-010-nl-time-parsing.md)

## Связанные

1. [Smart inbox routing](../concepts/smart-inbox-routing.md)
