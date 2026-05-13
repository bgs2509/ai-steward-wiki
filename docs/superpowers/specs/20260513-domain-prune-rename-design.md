---
feature: domain-prune-rename
bd_id: aisw-bgu
date: 2026-05-13
status: approved
approach: mechanical-rename-with-guardrail-merge
references:
  discovery: ./20260513-domain-prune-rename-discovery.md
stack:
  - "git mv for filesystem renames (preserves history)"
  - "ruff + mypy + grace lint as gates"
  - "no new dependencies"
---

# Design — Prune and Rename WIKI Domains

## Approach: Mechanical Rename with Guardrail Merge

Pre-deploy refactor; no live data, no aliases, no migration. Execute as a sequence of atomic, lint-verified steps grouped by surface zone:

1. **Filesystem layer (templates + prompts).**
   - `git mv templates/inbox-wiki/health.md → templates/inbox-wiki/medical.md`
   - `git mv templates/inbox-wiki/recipes.md → templates/inbox-wiki/cooking.md`
   - `git rm templates/inbox-wiki/{health-lite,home,hobby}.md`
   - `git mv prompts/domain-health.md → prompts/domain-medical.md`
   - Strengthen `medical.md` with non-interpretation guardrail (single new section, citing concrete examples).

2. **Validator layer.**
   - `scripts/lint_templates.py`: update `REQUIRED_TEMPLATES` list (8 entries instead of 10, with rename).

3. **Code layer.**
   - `src/ai_steward_wiki/wiki/name.py:113`: docstring example uses `health-lite` as illustration. Replace with a synthetic example (e.g. `multi-word-slug` → `Multi Word Slug`) that exercises the same hyphen branch. Logic untouched.

4. **Test layer.**
   - `tests/unit/wiki/lifecycle/test_name.py`: replace `health-lite` parametrize case with a synthetic hyphenated slug (e.g. `"multi-word"`), keeping the assertion that hyphen-normalization works.
   - `tests/unit/wiki/test_runner.py`, `tests/integration/test_pipeline_classifier_e2e.py`, `tests/integration/classifier/test_real_cli.py`, `tests/integration/conftest.py`: swap removed/renamed slugs for canonical replacements.
   - Other test files (`test_handlers.py`, `test_output.py`, `test_lifecycle.py`, etc.) — review per file; if `health` is just a generic example, swap to `medical`; if `home`/`hobby` are fixtures, swap to `_default` or `family` per semantic intent.

5. **Docs layer (Spec-WIKI).**
   - `tech-spec-draft.md`: D-008 list (lines ~319, 330-331), D-032 normalization (lines ~432, 789, 803).
   - `D-017-domain-claude-md-template.md`, `D-038-per-user-systemd.md`, `Q-B-10-*.md`, `Spec-WIKI/CLAUDE.md`, `Spec-WIKI/log.md`: scoped updates of domain enumerations.
   - `docs/superpowers/specs/20260510-wiki-lifecycle-{discovery,design}.md` and `plans/20260510-ai-steward-wiki-mvp/breakdown.xml`: domain lists.

6. **Onboarding layer.**
   - `templates/onboarding-intro.ru.md`: domain enumeration updated.

7. **Global CLAUDE.md.**
   - `/home/bgs/ai-steward/CLAUDE.md` "Шаблоны типов проектов": remove Health-Lite, Home, Hobby template blocks; rename Health→Medical, Recipes→Cooking (if present — verify first).

8. **GRACE artifacts.**
   - `grace-refresh` regenerates `knowledge-graph.xml`. `requirements.xml` + `technology.xml` auto-regenerated from frontmatter via pre-commit hook.

9. **ADR.**
   - `docs/adr/ADR-{next-number}-prune-and-rename-domains.md` documenting alternatives (keep all 10 / partial cut / full cut chosen), decision, consequences.

## Data model

No data model changes. No DB columns reference domain slugs (verified: domain is filesystem-only, classifier output, no schema column).

## UX flow

User-visible: 7+`_default` domain choices instead of 10. Onboarding prompt updated. No bot-command surface changes.

## Module map (impact)

- `M-WIKI-NAME` (`wiki/name.py`) — docstring only; no contract change.
- `M-TEMPLATES` (`templates/inbox-wiki/`) — file set changes; treated as configuration, not code.
- `M-CLASSIFIER-STAGE0/1A/1B` — domain list literal in classifier prompts only (handled via `templates/inbox-wiki/` + `prompts/`).
- `M-WIKI-LIFECYCLE` — tests updated, no contract change.

## Alternatives considered

- **A1: Keep alias map (`health → medical` redirect).** Rejected: pre-deploy = no consumers; aliases would be technical debt from day 1.
- **A2: Partial cut (keep `home`/`hobby`, only rename).** Rejected: `home`/`hobby` semantic redundancy with `_default`/`family` already established; bundling avoids second refactor.
- **A3: `medical-lite` retained as separate domain.** Rejected: classifier confusion (Stage-1a precision drop measured anecdotally). Guardrail merged into `medical.md`.

## Risks (mitigation)

Inherited from Discovery: R-1, R-2, R-3, R-4. Mitigation = scoped grep, word-boundary patterns, manual review of prose contexts.

## Verification plan (intent)

- `make lint` clean (ruff + format + mypy).
- `grace lint` clean (semantic markup intact).
- `uv run pytest tests/unit` green; coverage ≥80%.
- `RUN_INTEGRATION=1 uv run pytest tests/integration` green.
- Acceptance grep returns 0 hits (excluding `docs/reports/`).

## bd

- Epic: `aisw-bgu`
