# D-017: domain-WIKI `CLAUDE.md` — per-domain пресеты + fallback `_default`

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-10](../questions/Q-B-10-domain-claude-md-template.md), overview §9.10, §8.4, [D-004](D-004-inbox-wiki-scope.md), [D-016](D-016-inbox-claude-md-template.md)

## Проблема

Какой шаблон `CLAUDE.md` создавать при `/wiki_init <Domain>`: универсальный пустой каркас или curated пресеты под типичные домены (Health, Investment, Recipes, Budget, Study, Career, Home, Hobby, Family).

## Варианты

1. **A — Один универсальный шаблон:** плохой initial UX, доменное знание из parent-`CLAUDE.md` ai-steward не переиспользуется.
2. **B — Per-domain пресеты** + fallback `_default.md`. ⭐
3. **C — Default + skill-pack секции** (`@health-pack`, `@finance-pack`): overengineering для 5–10 доменов.
4. **D — B + auto-extension через LLM** для unknown доменов: отложено до реальной потребности (поверх B).

## Выбор

**Вариант B (Per-domain пресеты + `_default`).**

### Layout

```
ai-steward-wiki/templates/
├── _default.md       # fallback для unknown доменов
├── health.md
├── investment.md
├── budget.md
├── family.md
├── study.md
├── career.md
├── home.md
├── hobby.md
└── recipes.md
```

### Алгоритм `/wiki_init <Domain>`

1. Нормализация имени: `Health` / `health` / `HEALTH` → `health`.
2. Lookup в `templates/`: если есть `<normalized>.md` — копировать; иначе — `_default.md`.
3. Создать `USERS/<NAME>/<Domain>-WIKI/CLAUDE.md` с подставленным шаблоном.
4. Создать стандартную структуру (`entities/`, `concepts/`, `raw/`, `index.md`, `log.md`) — общая для всех типов.
5. Зафиксировать в `audit.db`: `(wiki, template_used, template_version, ts)`.

### Контракт пресета

Каждый шаблон **обязан** содержать:

1. **`# <Название>` + статус-блок** (тип: domain-WIKI; владелец; дата создания).
2. **`## Inbox hint`** (1–3 строки, по [D-016](D-016-inbox-claude-md-template.md)) — обязательное поле.
3. **`## Назначение`** — что хранится в этой WIKI, что нет (граница).
4. **`## Структура страниц`** — рекомендованные `entities/`, `concepts/`, специфичные подпапки (например, `lab_results/` для health).
5. **`## Правила librarian`** — как Claude работает с данными (не диагностировать в health, не давать инвест-советов в investment, т.д.).
6. **`## Конвенции именования`** — kebab-case, prefixes, дата-форматы.

`_default.md` содержит generic-версию всех секций с placeholder'ами `<TODO>`.

### Источник доменного знания

Базовое содержимое пресетов берётся из parent-`CLAUDE.md` ai-steward (`/home/bgs/ai-steward/CLAUDE.md` → раздел «Шаблоны типов проектов»: Health / Health-Lite / Investment / Budget / Family / Study / Career / Home / Hobby). Адаптируется под Wiki-doctrine ([D-015](D-015-system-prompt-inject.md), Karpathy librarian).

### Lint-правила

1. `wiki lint` проверяет: `<Domain>-WIKI/CLAUDE.md` содержит секцию `## Inbox hint` ([D-016](D-016-inbox-claude-md-template.md)).
2. Версия пресета (`# Template v1.x.0` в header) логируется в audit.db; миграция шаблона не трогает уже созданные WIKI (юзер сам решит обновлять).

## Последствия

1. Отличный initial UX: `/wiki_init Health` → готовый домен с правилами и хинтами.
2. Доменное знание SSoT — `templates/` в репо сервиса; эволюционирует через PR.
3. `_default.md` закрывает экзотику без LLM-генерации (Вариант D — отложен).
4. Запреты:
   1. **Не править `templates/<domain>.md`** через TG-команды юзера — только PR.
   2. **Не дублировать содержимое пресета** в Inbox-`CLAUDE.md` (D-016 — only via `## Inbox hint`).
   3. **Не делать `<Domain>-WIKI/<NestedDomain>-WIKI/`** — anti-nesting (Q-C-24, концепт `anti-nesting`).
5. Расширение в будущем (Вариант D) — auto-generation пресета через Claude при первом `/wiki_init <Unknown>`, с сохранением в `templates/` и обязательным юзер-ревью.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-017-domain-claude-md-template.md` (когда финализируется)
