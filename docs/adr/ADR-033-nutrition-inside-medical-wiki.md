# ADR-033: Nutrition/calorie tracking lives inside Medical-WIKI, not a separate domain

**Status:** accepted
**Date:** 2026-07-02
**bd:** aisw-2si
**Related:** D-041 (pre-flight/confirm model), ADR-029 (wiki schema delivery), templates/cooking.md

## Context

Live incident 2026-07-02: the bot handled calorie requests nondeterministically —
sometimes counting, sometimes demanding consent, sometimes refusing — and improvised
file placement (`history/NUTRITION_GUIDE.md`, calories inside `daily/*.md`). Four root
causes: no `diet/` in the Medical template layout (SSoT drift vs the parent ai-steward
Medical template), no nutrition boundary in the non-interpretation guardrail, a consent
round-trip that stateless per-message CLI runs cannot survive, and Inbox-hint keyword
overlap with Cooking (завтрак/обед/ужин).

## Alternatives

1. **Prompt-only fix** (guardrail line, no layout) — ❌ leaves the layout hole; file
   improvisation continues.
2. **Nutrition inside Medical: `diet/food_log.csv` + guardrail boundary + routing
   keywords + Cooking demarcation** — ⭐ closes all four causes; follows the parent
   ai-steward Medical template convention (CoC).
3. **Separate Nutrition-WIKI domain** — ❌ a THIRD food domain (after Cooking) violates
   SSoT, adds a new classifier fork instead of removing one, and is YAGNI for a single
   CSV without macros/OCR.

## Decision

Alternative 2 (chosen by the user as Variant 3, /best-approach 2026-07-02).

1. `diet/food_log.csv` (`date,meal,item,qty_g,kcal,source`; `source=logged|estimated`)
   is the single SSoT for food/calorie facts inside Medical-WIKI.
2. Calorie **estimation of self-logged food = data structuring** — allowed without
   consent, always marked `source=estimated`. Clinical nutrition (therapeutic diets,
   deficiencies, illness-specific advice) stays under the doctor redirect.
3. Appends never require confirm; explicit confirm remains deletion-only (D-041).
4. Routing determinism: Medical Inbox hint gains food keywords; medical.md and
   cooking.md carry reciprocal demarcation lines (рецепты/меню → Cooking; факт
   съеденного и калории → Medical).

## Consequences

1. Existing WIKIs receive the new managed zone only via
   `scripts/backfill_managed_zone.py` (idempotent) — mandatory step on prod deploy.
2. The `source=estimated` flag is the audit boundary between user-stated values and
   model estimates (crowdsourced calorie estimates average ~38% error — industry data).
3. No macros / no OCR for now (YAGNI, user decision); adding columns later is a
   template + backfill change, no migration pain.
4. If nutrition grows into meal-planning/macros territory, revisit Alternative 3 with
   a dedicated discovery.
