# Q-C-23: Регистр и формат `WIKI`-маркера

**Tier:** C
**Источник:** [overview §9 п.23](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

§5 `.upper()` — substring или suffix `-WIKI`? Точное правило/regex.

## Варианты

1. **A. Substring case-insensitive.** `"WIKI" in name.upper()`. Просто, но `WIKILEAKS-data` тоже пройдёт.
2. **B. Strict suffix `-WIKI` (case-sensitive).** Однозначно. Имя обязано заканчиваться на `-WIKI`.
3. **C. Regex `^[A-Z][A-Za-z0-9]*-WIKI$`.** Доменное имя + suffix. Самый строгий.

## Решение

- [x] Вариант C (regex `^[A-Z][A-Za-z0-9]*-WIKI$`, fullmatch). Юзер подтвердил 2026-05-08. См. [D-008](../decisions/D-008-wiki-marker-format.md) (accepted).

## Связанные

1. [Anti-nesting](../concepts/anti-nesting.md)
