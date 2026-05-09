# Q-A-03: Inbox-WIKI per-user vs global

**Tier:** A
**Источник:** [overview §9 п.3](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

`USERS/<NAME>/Inbox-WIKI/` (изоляция, sandbox-friendly) vs глобальный `INBOX/` с роутингом по `chat_id` (проще, единая router-схема).

## Варианты

1. **A. Per-user `Inbox-WIKI`.** Согласуется с sibling-only моделью §3a, лучшая изоляция, легко применять path-traversal проверки §7.2. Минусы: дубль schema-файлов на каждого юзера, дрейф router-промптов.
2. **B. Global `INBOX/`.** Один router-`CLAUDE.md`. Минусы: ломает sandbox юзера, требует новой роли «system-WIKI».
3. **C. Per-user + shared template.** Per-user структура, но шаблон `CLAUDE.md` — один файл в репо, копируется/симлинкается при `/wiki_init`.

## Решение

- [x] Вариант C (per-user структура + shared template, materialize at init). Юзер подтвердил 2026-05-08. См. [D-004](../decisions/D-004-inbox-wiki-scope.md) (accepted).
- [x] оформлено как [D-004](../decisions/D-004-inbox-wiki-scope.md)

## Связанные

1. [Inbox-WIKI](../entities/inbox-wiki.md)
2. [Sibling-only domains](../concepts/sibling-only-domains.md)
