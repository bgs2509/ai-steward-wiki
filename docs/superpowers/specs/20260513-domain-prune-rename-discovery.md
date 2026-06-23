---
feature: domain-prune-rename
bd_id: aisw-bgu
date: 2026-05-13
status: approved
type: refactor
requirements:
  functional:
    - FR-1: Remove WIKI domain slugs `health-lite`, `home`, `hobby` from all SSoT artifacts and code surfaces.
    - FR-2: Rename WIKI domain slug `recipes` → `cooking` across all artifacts.
    - FR-3: Rename WIKI domain slug `health` → `medical` across all artifacts.
    - FR-4: Final canonical domain set (D-008) MUST equal exactly `{_default, medical, investment, budget, family, study, career, cooking}` (7 + _default).
    - FR-5: "`medical.md` template MUST contain an explicit non-interpretation guardrail (do not interpret symptoms unsolicited; on doubt redirect to a doctor)."
    - FR-6: All tests that previously used `health-lite` to exercise hyphen-in-slug normalization MUST be replaced with a synthetic hyphen slug, preserving normalization coverage in `wiki/name.py`.
    - FR-7: Record the change as `ADR-NNN-prune-and-rename-domains` (alternatives, decision, consequences).
    - FR-8: Update `tech-spec-draft.md` (status=stable) D-008 and D-032 sections with the new slug list; XML SSoT (`requirements.xml`, `technology.xml`, `knowledge-graph.xml`) regenerated via `grace-refresh`.
  non_functional:
    - NFR-1: "`make total-test` MUST exit 0 (lint + grace + coverage ≥80% + integration)."
    - NFR-2: No data migration code paths introduced (pre-deploy, zero live `<Domain>-WIKI/` directories).
    - NFR-3: No backwards-compat aliases (no `health → medical` redirect, no `recipes → cooking` alias).
    - NFR-4: "`docs/reports/*` MUST remain untouched (immutable snapshots)."
    - NFR-5: Hyphen-normalization branch in `wiki/name.py:~113` MUST remain covered.
  constraints:
    - C-1: Scope strictly limited to domain prune+rename; no bundling with other refactors.
    - C-2: Onboarding-intro prompt MUST NOT mention removed/renamed slugs after the change.
  risks:
    - R-1: "Hidden references to `health` as a substring of `health-lite` may be left dangling (e.g. test fixtures, log strings) — mitigation: word-boundary grep + per-file review."
    - R-2: "`health` is a generic English word; naive `s/health/medical/g` risks corrupting unrelated prose (e.g. `health.md` template content, log examples). Mitigation: edits scoped to slug occurrences (templates filename, REQUIRED_TEMPLATES list, D-008 list, test fixtures), with manual review of prose mentions."
    - R-3: "`home` and `hobby` are very common English words — same false-positive risk. Mitigation: rely on the user-provided surface enumeration (10 surface zones) rather than blanket replace."
    - R-4: "`prompts/domain-health.md` rename must update any include/reference. Mitigation: grep for the filename before rename."
  scope:
    in:
      - Filesystem renames in `templates/inbox-wiki/` and `prompts/`.
      - Update of `scripts/lint_templates.py` REQUIRED_TEMPLATES.
      - Update of `src/ai_steward_wiki/wiki/name.py` docstring example only (logic unchanged).
      - Update of `tests/unit/wiki/lifecycle/test_name.py` (replace `health-lite` test case with synthetic hyphen slug).
      - Update of all other tests that hard-code removed/renamed slugs.
      - Update of `docs/Spec-WIKI/research/tech-spec-draft.md` (D-008, D-032, plus any list references).
      - Update of `docs/Spec-WIKI/decisions/D-017-*.md`, `D-038-*.md`, `Q-B-10-*.md`, `Spec-WIKI/CLAUDE.md`, `Spec-WIKI/log.md`.
      - Update of `docs/superpowers/specs/*` and `plans/*/breakdown.xml` that enumerate the 10 domains.
      - Update of `/home/bgs/ai-steward/CLAUDE.md` "Шаблоны типов проектов" section.
      - Strengthening of non-interpretation guardrail in the new `medical.md`.
      - ADR creation.
      - "`grace-refresh` to regenerate `requirements.xml`, `technology.xml`, `knowledge-graph.xml`."
    out:
      - DB migrations (jobs.db / audit.db / sessions.db).
      - Backwards-compat aliases / redirects.
      - Rewriting hyphen-normalization logic in `name.py`.
      - Touching `docs/reports/*`.
      - Bundling with unrelated refactors.
    later: []
  dependencies:
    - "`grace lint` rules (semantic markup integrity unchanged — refactor is doc/template-heavy)."
    - "`make total-test` infrastructure."
---

# Discovery — Prune and Rename WIKI Domains

## Intent (literal)

Сократить и переименовать предустановленные WIKI-домены:
1. Удалить: `health-lite`, `home`, `hobby`.
2. Переименовать: `recipes` → `cooking`, `health` → `medical`.

Канонический финальный список (D-008): `_default, medical, investment, budget, family, study, career, cooking`.

## Real intent (analysis)

1. **Семантическая честность.** `health` сейчас хранит клинические данные (анализы, давление, лекарства) — `medical` точнее отражает scope. `recipes` слишком узко; `cooking` покрывает рецепты + готовку + меню.
2. **Снижение когнитивной нагрузки на классификатор.** 10 доменов → 7 + `_default`. `health-lite` vs `medical` — частая ошибка Stage-1a (юзер пишет «голова болит» → куда?). Удаление дублей повышает precision.
3. **Безопасность.** Удаляя `health-lite` (дневник самочувствия), теряем guardrail про non-interpretation. Решение: перенести его в `medical.md` явным разделом.
4. **Поглощение.** `home` и `hobby` редко используются юзерами; их content естественно ложится в `_default` (свободные заметки) и `family` (домашние события). Сохранение этих доменов было YAGNI с самого старта.

## Blind spots

1. **`health` как substring и обычное английское слово.** Любая глобальная замена опасна (см. R-2). Edits scoped по surface zones из user-задачи.
2. **`health-lite` как фикстура hyphen-нормализации.** Это единственное предустановленное slug-имя с дефисом → удаление сломает покрытие. Mitigation: заменить на синтетический slug-с-дефисом в тесте (FR-6).
3. **`prompts/domain-health.md`.** Если на него ссылаются includes/глобы — нужно обновить. Grep по basename перед rename.
4. **`onboarding-intro.ru.md`.** Юзер-facing промпт может перечислять домены — обязательно обновить (C-2).
5. **`breakdown.xml` в planning artifacts.** Plans содержат XML-перечисления доменов — нужно обновить, иначе `grace-refresh` может ругаться на drift.

## Best-practice anchors

1. **Pre-deploy rename = clean cut** (no aliases). Industry pattern: aliases имеют смысл только когда есть live consumers; их нет.
2. **Safety guardrails consolidation.** При слиянии scope (health-lite → medical), guardrails переносятся явно, не подразумеваются.
3. **Test coverage preservation under rename.** Если slug использовался как fixture для логики, заменять синтетическим эквивалентом, не удалять кейс.

## Open questions

Нет — задача предельно конкретна.

## Acceptance (mirror)

1. `ls templates/inbox-wiki/` показывает ровно 8 файлов: `_default.md, medical.md, investment.md, budget.md, family.md, study.md, career.md, cooking.md`.
2. `grep -rn "health-lite\|\bhome\b\|\bhobby\b\|recipes" --include="*.md" --include="*.py" --include="*.xml" templates/ src/ tests/ docs/Spec-WIKI/ docs/superpowers/` → 0 совпадений (исключая `docs/reports/`).
3. `make total-test` → exit 0.
4. ADR замёржен.
5. Onboarding-промпт обновлён.

## bd

- Epic: `aisw-bgu`
