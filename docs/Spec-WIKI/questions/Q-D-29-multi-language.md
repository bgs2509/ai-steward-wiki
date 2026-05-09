# Q-D-29: Multi-language

**Tier:** D
**Источник:** [overview §9 п.29](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Интерфейс бота — ru/en, определяется per-user в `roles.toml`.

## Варианты

1. **A. Поле `lang = "ru" | "en"` в `roles.toml`.** Все системные сообщения через i18n catalog.
2. **B. Auto-detect** по language-коду TG (`from.language_code`).
3. **C. Hybrid:** auto-detect default, override полем `lang` в profile.

## Решение

- [x] оформлено как [D-032](../decisions/D-032-multi-language.md) — Вариант A (MVP-ru-only, no i18n). Trigger для refactor → catalog: появление реального en-юзера или explicit запрос Henry.

## Связанные

— нет прямых.
