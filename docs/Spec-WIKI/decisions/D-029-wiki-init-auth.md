# D-029: `/wiki_init` авторизация — user creates + auto-suggest + soft limit + reversible delete

**Статус:** superseded-by [D-041](D-041-no-direct-wiki-commands.md)
**Дата:** 2026-05-09
**Контекст:** [Q-D-26](../questions/Q-D-26-wiki-init-auth.md), overview §2.1, §9.26, [D-008](D-008-wiki-marker-format.md), [D-016](D-016-inbox-claude-md-template.md), [D-017](D-017-domain-claude-md-template.md), [D-023](D-023-tg-confirmations.md)

## Проблема

Создание `<Domain>-WIKI/` юзером без ограничений — мусор и typo-clones (`Healtg-WIKI`, `health-WIKI`). Только-admin — bottleneck. Нужен баланс: свобода + protection.

## Варианты

1. **A — User сам, без лимитов.**
2. **B — User сам, лимит N WIKI.**
3. **C — Только admin создаёт.**
4. **D — User creates + classifier auto-suggest + soft limit + typo protection + reversible delete.** ⭐

## Выбор

**Вариант D.**

### `/wiki_init <Domain>`

1. Доступен юзеру; lookup пресета по [D-017](D-017-domain-claude-md-template.md), `_default.md` fallback.
2. Создаёт `USERS/<USER>/<Domain>-WIKI/` с CLAUDE.md из пресета + пустыми `_output/`, `data/runs/`.

### Auto-suggest (overview §2.1 п.3)

Router в Inbox-WIKI ([D-016](D-016-inbox-claude-md-template.md)) не находит подходящий домен → inline-кнопки:

1. ✅ `Создать <Travel>-WIKI`
2. ❌ `Сохранить в Inbox`
3. ✏️ `Другое имя` (free-text input)

### Soft limit

1. Hard cap: **20 WIKI per user**.
2. Warning на 16/20: «Осталось 4 слота — может, удалить ненужные?».
3. Hard reject на 20/20 до удаления: «Лимит 20. Удали ненужные через `/wiki_delete`».

### Typo protection

1. **Normalization** имени case-insensitive (по [D-008](D-008-wiki-marker-format.md) regex).
2. **Fuzzy-match** Levenshtein ≤ 2 с существующими WIKI юзера → подсказка:
   «Возможно, ты имел в виду `Health-WIKI`? [Yes / No, create new]».

### Reversible delete

1. **`/wiki_delete <Domain>`** → graduated explicit confirm ([D-023](D-023-tg-confirmations.md)).
2. После confirm — перенос в `USERS/<USER>/_trash/<Domain>-WIKI-<ts>/`, **не hard delete**.
3. **Retention 30d.** Восстановление: `/wiki_restore <Domain>`.
4. После 30d — hard delete (housekeeping silent-job, `notify_policy=silent` per [D-020](D-020-cron-result-routing.md)).

### `_trash/` исключения

1. Исключается из autodiscover ([D-016](D-016-inbox-claude-md-template.md)) — не считается active WIKI.
2. Не учитывается в soft limit (20).
3. Anti-nesting [D-027](D-027-anti-nesting-admin-boundary.md) walk skip'ает `_trash/`.

## Последствия

1. UX свободный, но защищён от typo-spam и accidental delete.
2. Запреты:
   1. **Не hard-delete без 30d retention** (кроме explicit `/wiki_purge`).
   2. **Не считать `_trash/`** в soft limit / autodiscover.
   3. **Не пропускать fuzzy-match** на create — typo-protection load-bearing.
3. Future: `--force` flag на `/wiki_delete` для skip retention (опционально, не MVP).

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-029-wiki-init-auth.md` (когда финализируется)
