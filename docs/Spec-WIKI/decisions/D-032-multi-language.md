# D-032: Multi-language — MVP-ru-only, no i18n

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-D-29](../questions/Q-D-29-multi-language.md), overview §9.29, [D-013](D-013-claude-cli-auth.md), [D-028](D-028-admin-access.md), [D-030](D-030-onboarding.md)

## Проблема

Сервис шлёт системные строки: подтверждения ([D-023](D-023-tg-confirmations.md)), digest ([D-024](D-024-digest-format.md)), ошибки ([D-019](D-019-cron-failure-mode.md)), `/cancel` reply ([D-021](D-021-timeouts-kill-policy.md)), onboarding/`/start` ([D-030](D-030-onboarding.md)), `/wiki_init` flow ([D-029](D-029-wiki-init-auth.md)), notify cron-результатов ([D-020](D-020-cron-result-routing.md)). Нужно решить: hardcode `ru`, catalog-ready заглушка, auto-detect+override, или полный i18n. Контент юзера (заметки, ответы Claude по WIKI) — вне скоупа: пишется на любом языке самим юзером и обрабатывается LLM.

## Варианты

1. **A — MVP-ru-only, no i18n.** ⭐
2. **B — Catalog-ready dict, ru-only locale.**
3. **C — Auto-detect + override `lang` в `users.toml`, ru+en сразу.**
4. **D — `lang` в `users.toml`, hardcoded `ru`, catalog как заглушка.**
5. **E — Полный i18n с `gettext`/Babel.**

## Выбор

**Вариант A.**

### Scope

1. Все системные строки — `ru` hardcoded (inline в коде; формирующие функции возвращают готовые `str`).
2. Никакого `lang`-поля в `users.toml` ([D-030](D-030-onboarding.md)) — schema остаётся как есть.
3. Никакого i18n-механизма (catalog/gettext/Babel) — не вводим.
4. `from.language_code` из TG update **игнорируется** для системных строк.

### Out of scope (не наше решение здесь)

1. Контент, который пишет/получает Claude CLI внутри WIKI — на любом языке (русский для Henry); это управляется per-WIKI `CLAUDE.md` (D-016/D-017), не системой.
2. Voice STT ([D-022](D-022-voice-photo-input.md)) — `faster-whisper` сам определяет язык речи; результат передаётся LLM as-is.

### Trigger для refactor → catalog

Когда хотя бы одно из:

1. Появляется реальный en-юзер в `users.toml` (multi-tenant с `ENABLE_SELF_SIGNUP=true`, [D-030](D-030-onboarding.md)).
2. Henry сам запрашивает en-режим.

При триггере — открывается отдельный D-XXX (Волна 9+): дизайн catalog (`LOCALES: dict[str, dict[str, str]]`), добавление поля `lang` в `users.toml`, выбор default policy (hardcoded `ru` vs `from.language_code`).

### Code discipline (необязательно, но рекомендация)

1. Системные строки группировать в одном модуле `messages.py` (per-feature или общий) — упрощает будущий refactor.
2. Не вкладывать строки в шаблоны Jinja для системных нотификаций (только для digest-карточек, где Jinja уже в [D-024](D-024-digest-format.md)).

Это **рекомендация, не enforcement** — нет lint-правила; нарушение допустимо.

## Последствия

1. Zero overhead на i18n в MVP.
2. Single-tenant Henry (русскоязычный per `~/.claude/CLAUDE.md`) получает natural UX.
3. Refactor в catalog при появлении en-юзера — локальный (день работы): создать `LOCALES`, ввести `t(key, lang)`, заменить ~50–200 inline-строк, добавить `lang` в schema `users.toml`. Не архитектурный.
4. Запреты:
   1. **Не вводить `lang` в `users.toml`** до триггера — поле без эффекта = noise (нарушает SSoT principle `~/.claude/CLAUDE.md`).
   2. **Не использовать `from.language_code`** для системных строк — auto-detect без catalog бессмыслен.
   3. **Не делать LLM-translated runtime** системных строк — недетерминированно, дорого, ломает UX consistency.
   4. **Не разбрасывать i18n-заглушки** (`_("text")`, `gettext("text")`) — premature abstraction.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-032-multi-language.md` (когда финализируется)
