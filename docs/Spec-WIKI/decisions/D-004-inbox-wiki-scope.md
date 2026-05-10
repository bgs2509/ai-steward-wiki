# D-004: Inbox-WIKI — per-user структура + shared template

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-03](../questions/Q-A-03-inbox-scope.md), [Inbox-WIKI](../entities/inbox-wiki.md), overview §3a / §7.2 / §8.3.1

> **Update ([D-041](D-041-no-direct-wiki-commands.md), 2026-05-09):** упоминания `/wiki_init` ниже — историческая команда; lifecycle-операции выполняются NL-промптом router'у с pre-flight + explicit confirm. Механика рендеринга шаблона и валидации имени остаётся; только триггер сменился со slash-команды на intent.

## Проблема

Где живёт Inbox: `USERS/<NAME>/Inbox-WIKI/` (per-user) или глобальный `INBOX/` рядом с `USERS/`. Влияет на файловую SSoT, path-traversal проверки, scope `--add-dir`, admin-доступ, drift router-промптов.

## Варианты

1. **A. Per-user `USERS/<NAME>/Inbox-WIKI/`, `CLAUDE.md` руками.** Простейший MVP, но drift router-логики между юзерами без миграционного механизма.
2. **B. Global `INBOX/` + routing по `chat_id`.** Прямо нарушает §7.2 (чужие `USERS/*` в запретах) и ломает per-user sandbox §3a.
3. **C. Per-user структура + shared template, materialize at init.** Файлы лежат per-user, `Inbox-WIKI/CLAUDE.md` рендерится при `/wiki_init` из шаблона в репо. Обновление шаблона → миграция по юзерам.

## Выбор

**Вариант C.** Юзер подтвердил 2026-05-08.

Обоснование:
1. Полное соответствие overview §3a (sibling-only), §7.2 (path-traversal), §8.3.1 (Inbox-WIKI per-user).
2. SSoT router-шаблона зафиксирован — один файл в репо (`templates/inbox-wiki/CLAUDE.md` или эквивалент), drift только намеренный.
3. Industry best practice для мульти-тенантных AI-сервисов: per-tenant isolation + cookiecutter-pattern.
4. Естественно подключается к Q-E-38 (CLAUDE.md schema evolution) и Q-D-26 (`/wiki_init` авторизация).
5. Вариант B исключён нарушением §7.2. Вариант A — частный случай C без renderer'а.

## Последствия

1. Файловая структура: `USERS/<NAME>/Inbox-WIKI/{CLAUDE.md, raw/, index.md, log.md, ...}` per-user. Совпадает с overview §8.3.1.
2. Появляется новый артефакт: `templates/inbox-wiki/` в репо сервиса — SSoT шаблона. Закрывает половину [Q-B-09](../questions/Q-B-09-inbox-claude-md-template.md) (содержание router-промпта).
3. `/wiki_init` для Inbox требует renderer'а. Минимально — copy + substitute переменных (`{{user_name}}`, `{{telegram_id}}`, список доменов юзера). Конкретный движок (плоский str.format / Jinja2) — отдельный микро-вопрос при реализации.
4. Политика per-user override: какие секции `Inbox-WIKI/CLAUDE.md` юзер может править руками, какие управляются миграцией — оформить как часть Q-B-09 или отдельным вопросом.
5. Миграция шаблона при обновлении — Q-E-38 (auto-migrate vs only new).
6. `--add-dir` scope (Q-C-22) и anti-nesting boundary для admin (Q-C-24) решаются в рамках per-user модели — не блокируются этим решением.
7. Q-A-03 закрывается этим решением.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-inbox-wiki-scope.md` при финализации.
