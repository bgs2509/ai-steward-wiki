# ADR-027: Prune and Rename WIKI Domains

**Status:** Accepted
**Date:** 2026-05-13
**Beads:** aisw-bgu

## Context

Initial canonical WIKI domain set (D-008, D-017, Q-B-10) — 10 presets:
`health, health-lite, investment, budget, family, study, career, home, hobby, recipes` + `_default`.

Pre-deploy observations:
1. `health` semantically blurs clinical data (labs, BP, prescriptions) with
   lifestyle tracking. Rename to `medical` is more honest.
2. `health-lite` (mood/sleep diary) is a soft sibling of `health` and a
   frequent classifier-disambiguation failure in Stage-1a. Its safety
   guardrail (non-interpretation of symptoms) is useful, but the separate
   domain is not.
3. `home` and `hobby` overlap heavily with `_default` (free notes) and
   `family` (household events). Carry no unique structure.
4. `recipes` is too narrow; `cooking` covers recipes + meal planning +
   shopping lists.

Pre-deploy = no live `<Domain>-WIKI/` directories. Migration cost = 0.

## Alternatives

1. **A. Keep 10 domains, fix classifier prompts.**
   Pros: zero refactor. Cons: classifier confusion persists; `health` vs
   `medical` semantic mismatch remains; `home`/`hobby` carry no structure
   but consume Stage-1a precision budget.
2. **B. Partial cut (keep `home`/`hobby`, only rename).**
   Pros: smaller PR. Cons: requires a second refactor; classifier confusion
   continues for `home`/`hobby`.
3. **C. Full cut + rename (chosen).**
   `_default, medical, investment, budget, family, study, career, cooking`.
   Pros: one atomic refactor; classifier precision improves; semantic
   accuracy; safety guardrail consolidated into `medical.md` (explicit
   non-interpretation section).
   Cons: breaking change to canonical list. Acceptable because pre-deploy.
4. **D. Backwards-compat aliases (`health → medical` redirect map).**
   Pros: smooth rename. Cons: technical debt from day 1; pre-deploy has
   no consumers to protect.

## Decision

**C — Full cut + rename, no aliases.**

Canonical list after change: `_default, medical, investment, budget,
family, study, career, cooking` (7 + `_default`).

Safety: `medical.md` carries an explicit `## Non-interpretation guardrail`
section absorbing the `health-lite` mood/sleep diary use case (do not
interpret symptoms unsolicited; on doubt redirect to doctor; do not
recommend treatments).

Test coverage: `health-lite` was the only hyphen-in-slug fixture in
`tests/unit/wiki/lifecycle/test_name.py`. Replaced by a synthetic
`multi-word` slug to preserve coverage of `wiki/name.py` hyphen-normalization
(`_camel_to_hyphen`).

## Consequences

1. `scripts/lint_templates.py` REQUIRED_TEMPLATES updated to 8 entries.
2. `templates/` filesystem matches canonical list exactly (8 files).
3. `prompts/domain-health.md` renamed to `prompts/domain-medical.md`.
4. Spec-WIKI artifacts (`tech-spec-draft.md`, `D-017`, `Q-B-10`) reflect new
   list; `log.md` historical entries remain (append-only).
5. Onboarding prompt (`templates/onboarding-intro.ru.md`) no longer mentions
   removed/renamed slugs.
6. No DB migrations needed — domain slug is not a schema column.
7. No backwards-compat alias map: any reference to a removed slug after
   this change is a defect.

## References

- D-008: WIKI marker format (regex).
- D-017: domain-CLAUDE.md template.
- Q-B-10: per-domain preset decision.
- Discovery: `docs/superpowers/specs/20260513-domain-prune-rename-discovery.md`.
- Design: `docs/superpowers/specs/20260513-domain-prune-rename-design.md`.
