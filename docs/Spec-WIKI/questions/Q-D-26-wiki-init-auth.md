# Q-D-26: `/wiki_init` авторизация

**Tier:** D
**Источник:** [overview §9 п.26](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Кто создаёт новые `<Domain>-WIKI/` — user сам или только admin? Лимит количества WIKI на юзера.

## Варианты

1. **A. User сам, без лимитов.** Свобода. Может создать мусор.
2. **B. User сам, лимит N WIKI** (например, 20).
3. **C. Только admin создаёт.** Bottleneck.
4. **D. User сам, но первая создаётся через классификатор автоматически** (§2.1 п.3) — так чище.

## Решение

- [x] **Вариант D** — user creates + classifier auto-suggest + soft limit + typo protection + reversible delete:
  - **`/wiki_init <Domain>`** доступен юзеру; lookup пресета по [D-017](../decisions/D-017-domain-claude-md-template.md), `_default.md` fallback.
  - **Auto-suggest** (overview §2.1 п.3): router в Inbox-WIKI не находит подходящий домен → inline-кнопки «✅ Создать `Travel-WIKI`» / «❌ Сохранить в Inbox» / «✏️ Другое имя».
  - **Soft limit:** 20 WIKI per user; warning на 16/20, hard reject на 20/20 до удаления.
  - **Typo protection:**
    1. Нормализация имени case-insensitive (по [D-008](../decisions/D-008-wiki-marker-format.md) regex).
    2. Fuzzy-match Levenshtein ≤ 2 с существующими → подсказка «Возможно, ты имел в виду `<existing>`?».
  - **Reversible delete:** `/wiki_delete <Domain>` → graduated explicit confirm ([D-023](../decisions/D-023-tg-confirmations.md)) → перенос в `<USER>/_trash/<Domain>-WIKI-<ts>/`, retention 30d. Восстановление: `/wiki_restore <Domain>`. После 30d — hard delete (housekeeping silent-job).
  - **`_trash/`** исключается из autodiscover ([D-016](../decisions/D-016-inbox-claude-md-template.md)) — не считается active WIKI.
- [x] оформлено как [D-029](../decisions/D-029-wiki-init-auth.md), superseded by [D-041](../decisions/D-041-no-direct-wiki-commands.md)

## Связанные

1. [Classifier](../entities/classifier.md), [Domain-WIKI](../entities/domain-wiki.md)
