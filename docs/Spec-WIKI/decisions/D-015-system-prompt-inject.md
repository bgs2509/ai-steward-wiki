# D-015: system prompt inject — hybrid (`--append-system-prompt` + per-WIKI `CLAUDE.md`)

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-C-21](../questions/Q-C-21-system-prompt-inject.md), overview §9.21, [D-007](D-007-add-dir-scope.md), [D-009](D-009-classifier-engine.md), [llm-wiki-method](../concepts/llm-wiki-method.md)

## Проблема

Где SSoT для Wiki-doctrine («строгий библиотекарь Karpathy») и как она применяется в каждой WIKI без drift'а, не затирая дефолтный system prompt Claude Code.

## Варианты

1. **A — только `--append-system-prompt @prompts/wiki.md`:** глобальный enforce, нет per-WIKI кастомизации.
2. **B — только per-WIKI `CLAUDE.md`:** drift, 50 WIKI = 50 копий doctrine, нарушает SSoT.
3. **C — Hybrid:** global doctrine через `--append-system-prompt` + per-WIKI `CLAUDE.md` через auto-walk (D-007). ⭐
4. **D — `--system-prompt` (replace):** затирает Claude Code defaults, anti-pattern.

## Выбор

**Вариант C (Hybrid).**

### Layout

```
ai-steward-wiki/prompts/
├── wiki.md          # Wiki-doctrine (Stage-1 Sonnet, любой WIKI)
├── classifier.md    # Stage-0 Haiku instructions (D-009)
└── inbox.md         # extension для Inbox-WIKI (router-agent)
```

### Inject mechanism

1. **Stage-0 Haiku (classifier):** `--append-system-prompt @prompts/classifier.md`.
2. **Stage-1 Sonnet (executor) в любой `<Domain>-WIKI`:** `--append-system-prompt @prompts/wiki.md`. Per-WIKI профиль приходит через CLAUDE.md auto-walk (D-007).
3. **Stage-1 в Inbox-WIKI (router-agent):** `--append-system-prompt @prompts/wiki.md` + `@prompts/inbox.md` (наследование, DRY). Если CLI принимает только один файл — конкатенация в build-time через `cat wiki.md inbox.md > .build/inbox-system.md`.
4. **`@file` reference**, не inline string — CLI перечитывает файл, дешевле логировать факт «использован prompt v1.2», нет проблем с экранированием.

### Версионирование

1. Header в каждом prompt-файле: `# Wiki Doctrine v1.2.0` + `LAST_CHANGE: ...`.
2. Версия логируется в audit.db на каждый CLI-запуск: `(call_id, prompt_name, prompt_version, prompt_sha256)`.
3. Изменение doctrine = bump semver + commit; deploy → все WIKI используют новую версию автоматически.

### SoC

| Слой | SSoT | Что описывает |
|------|------|---------------|
| Anthropic defaults | CLI built-in | tools, safety, ground rules |
| Service doctrine | `prompts/wiki.md` (`+inbox.md`) | Karpathy LLM Wiki method, ingest/query/lint operations |
| Per-WIKI profile | `<wiki>/CLAUDE.md` (auto-walk) | конкретные правила домена, schema, templates |
| Per-call task | TG message → user prompt | задача юзера |

## Последствия

1. Обновление Wiki-doctrine — правка одного файла в репо сервиса.
2. Per-WIKI кастомизация остаётся через CLAUDE.md (D-007 не нарушается).
3. Audit-trail позволяет связать поведение Claude с конкретной версией doctrine (для debug).
4. Запреты:
   1. **Не использовать `--system-prompt` (replace)** — затирает Anthropic-defaults.
   2. **Не дублировать Wiki-doctrine в `<wiki>/CLAUDE.md`** — это derived knowledge.
   3. **Не править `prompts/wiki.md` через TG-команды юзера** — только через PR в репо сервиса (single-tenant, но процесс).
5. Build-step для Inbox: если CLI поддерживает несколько `--append-system-prompt` — наследование за счёт двух флагов; если нет — pre-build конкатенация в `.build/`.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-015-system-prompt-inject.md` (когда финализируется)
