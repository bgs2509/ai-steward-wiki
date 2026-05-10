# D-015: system prompt inject — hybrid (prompt files + CLI `--append-system-prompt` + per-WIKI `CLAUDE.md`)

**Статус:** accepted
**Дата:** 2026-05-08 (amended 2026-05-10 — Stage-0 backend prompt loading clarified)
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
├── classifier.md    # Stage-0 Haiku instructions (D-009), backend-independent
├── inbox.md         # extension для Inbox-WIKI (router-agent)
└── domain-<type>.md # optional extension для Stage-1b executor
```

### Inject mechanism

1. **Stage-0 Haiku (classifier):**
   1. Default subscription-only backend ([D-009](D-009-classifier-engine.md)) запускает `claude -p ... --model claude-haiku-4-5` и передаёт `prompts/classifier.md` через `--append-system-prompt @prompts/classifier.md`.
   2. Optional API backend читает тот же `prompts/classifier.md` и передаёт его как SDK/API system instructions.
   3. Stage-0 всегда запускается без WIKI tools и без чтения `CLAUDE.md`.
2. **Stage-1b Sonnet (executor) в `<Domain>-WIKI`:** `--append-system-prompt @prompts/wiki.md` + optional `@prompts/domain-<type>.md` если такой extension существует. Per-WIKI профиль приходит через CLAUDE.md auto-walk (D-007).
3. **Stage-1a в Inbox-WIKI (router-agent):** `--append-system-prompt @prompts/wiki.md` + `@prompts/inbox.md` (наследование, DRY). Если CLI принимает только один файл — конкатенация в build-time через `cat wiki.md inbox.md > .build/inbox-system.md`. Для Stage-1b аналогично собирается `.build/domain-<type>-system.md`.
4. **File-based prompt source**, не inline string: все backends читают repo-файл перед вызовом. В каждом model-call логируется факт «использован prompt v1.2» и sha256.

### Версионирование

1. Header в каждом prompt-файле: `# Wiki Doctrine v1.2.0` + `LAST_CHANGE: ...`.
2. Версия логируется в audit.db на каждый model-call: `(call_id, prompt_name, prompt_version, prompt_sha256, injection_mode)`, где `injection_mode ∈ {api_system, cli_append}`.
3. Изменение doctrine = bump semver + commit; deploy → все WIKI используют новую версию автоматически.

### SoC

| Слой | SSoT | Что описывает |
|------|------|---------------|
| Anthropic defaults | CLI built-in | tools, safety, ground rules |
| Service doctrine | `prompts/wiki.md` (`+inbox.md`, `+domain-<type>.md`) | Karpathy LLM Wiki method, ingest/query/lint operations |
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
5. Build-step для Stage-0/Inbox/Domain: если CLI поддерживает несколько `--append-system-prompt` — наследование за счёт нескольких флагов; если нет — pre-build конкатенация в `.build/`.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-015-system-prompt-inject.md` (когда финализируется)
