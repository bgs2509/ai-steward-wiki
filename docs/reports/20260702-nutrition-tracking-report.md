# Completion Report — Nutrition/calorie tracking formalization in Medical WIKI

- **bd_id:** aisw-2si
- **module:** templates (medical.md, cooking.md), prompts (domain-medical.md)
- **date:** 2026-07-02
- **decision origin:** ADR-033 — Variant 3 (nutrition inside Medical-WIKI, not a separate domain), 88% via `/best-approach`, after a live incident where the bot handled calorie requests nondeterministically

## What changed

Fixed four root causes of nondeterministic calorie/nutrition behavior found in a 2026-07-02 incident (the bot alternated between counting, demanding consent, and refusing, and improvised file placement like `history/NUTRITION_GUIDE.md` or calories inside `daily/*.md`):

1. **Layout drift** — `templates/medical.md` had no `diet/` entry (SSoT drift vs. the parent `ai-steward` Medical template). Added `diet/food_log.csv` (`date,meal,item,qty_g,kcal,source`; `source=logged|estimated`) as the single SSoT for food/calorie facts.
2. **Missing guardrail boundary** — `prompts/domain-medical.md` had no rule distinguishing nutrition estimation from clinical interpretation. Added: calorie estimation of self-logged food is *data structuring* (allowed without consent, always tagged `source=estimated`); clinical nutrition (therapeutic diets, deficiencies, illness-specific advice) still redirects to a doctor.
3. **Consent round-trip that can't survive stateless CLI runs** — appends to `food_log.csv` never require confirmation; explicit confirm stays deletion-only (existing D-041 discipline), removing the impossible-to-satisfy consent flow.
4. **Cooking/Medical keyword overlap** (завтрак/обед/ужин) caused misrouting — `templates/medical.md` and `templates/cooking.md` now carry reciprocal demarcation lines (recipes/menus → Cooking; fact of what was eaten + calories → Medical) and the Medical Inbox hint catalog gained food keywords.

Rejected alternatives (ADR-033): a prompt-only fix (leaves the layout hole), and a separate Nutrition-WIKI domain (a third food domain after Cooking — SSoT violation, YAGNI for a single CSV without macros/OCR).

## Files

- `templates/medical.md` (+17/-2) — `diet/food_log.csv` layout entry.
- `prompts/domain-medical.md` (+4/-1) — nutrition-as-structuring guardrail boundary.
- `templates/cooking.md` (+1) — reciprocal demarcation line.
- `docs/adr/ADR-033-nutrition-inside-medical-wiki.md`, `docs/superpowers/specs/20260702-nutrition-tracking-{discovery,design}.md`, `docs/superpowers/plans/20260702-nutrition-tracking-plan.md`.
- Follow-up fix `938c758`: discovery frontmatter made YAML-parseable and XML regenerated (pre-commit hook caught non-parseable frontmatter after the initial spec commit).

## Verification (evidence, per bd close reason)

- Feature complete: nutrition formalized in Medical WIKI (Variant 3), **deployed to vpn-2**, live WIKI cleaned up.
- Prod backfill: `scripts/backfill_managed_zone.py` ran with `fixed=1` (sha `40bda02848aa` matches local); `diet/food_log.csv` migrated with 10 existing rows; stale `history/NUTRITION_GUIDE.md` deleted; new routing keywords live. No service restart needed — overlay prompts are read per-CLI-run (`runner.py:267`) and the managed zone updates via the backfill script.
- `daily/*.md` entries left intact per D-041's deletion discipline (only the misplaced nutrition data was migrated, not the daily journal itself).

## Known limitations / deferred

- No macros or OCR (explicit user decision, YAGNI) — the `food_log.csv` schema has room to grow columns later via a template + backfill change, without a data migration.
- If nutrition tracking grows into meal-planning/macros territory, ADR-033 flags that as needing its own discovery pass revisiting Alternative 3 (separate domain).
- `source=estimated` is an audit boundary, not an accuracy guarantee — ADR-033 notes crowdsourced calorie estimates average ~38% error (industry baseline), which is a property of estimation in general, not something this feature could close.
