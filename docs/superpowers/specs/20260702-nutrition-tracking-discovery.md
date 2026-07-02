---
feature: nutrition-tracking
bd_id: aisw-2si
module_id: M-WIKI-LIFECYCLE
status: stable
date: 2026-07-02
risk: low
evidence: strong
open_questions: []
fr:
  - FR-1: templates/medical.md Data layout MUST include a diet/ section with an accumulating food_log.csv (schema `date,meal,item,qty_g,kcal,source`, source=logged|estimated) so food/calorie entries have exactly one SSoT location (today they land in daily/*.md or improvised history/ files — observed live 2026-07-02).
  - FR-2: templates/medical.md File resolution MUST route food entries to a new row in diet/food_log.csv (accumulating file, created only if absent) — same pattern as metrics/weight.csv.
  - FR-3: prompts/domain-medical.md MUST draw an explicit nutrition boundary — calorie estimation of self-logged food is DATA STRUCTURING (allowed without consent, marked source=estimated); clinical interpretation (therapeutic diets, deficiency diagnostics, illness-specific nutrition advice) stays under the doctor-redirect guardrail.
  - FR-4: Appending to diet/food_log.csv MUST NOT require explicit user consent — confirm remains only for deletions (per existing Pre-flight rule, D-041). Removes the consent round-trip that stateless per-message CLI runs cannot survive.
  - FR-5: templates/medical.md Inbox hint keywords MUST include food/calorie terms (еда, калории, ккал, съел, порция) so Stage-1a routing of "съел X" messages is deterministic toward Medical.
  - FR-6: templates/cooking.md MUST carry a one-line demarcation — рецепты/меню → Cooking; факт съеденного и калории → Medical — because cooking keywords (завтрак, обед, ужин) currently overlap with food-fact messages.
  - FR-7: The live Medical WIKI on the prod VPS MUST be cleaned up — delete history/NUTRITION_GUIDE.md (user decision 2026-07-02: one-off artifact, misplaced per layout rule "history/ = визиты + MEDICAL_SUMMARY.md"), migrate the calorie entries from daily/2026-07-01.md into diet/food_log.csv, and re-render the managed zone via scripts/backfill_managed_zone.py so the new template reaches the existing WIKI.
nfr:
  - NFR-1: make lint green (ruff + format + mypy + grace lint); no lint-baseline drift.
  - NFR-2: Ru-only user-facing template/prompt strings (D-032).
  - NFR-3: Template change MUST propagate to existing WIKIs only via the established managed-zone mechanism (migration.repair_managed_zone / backfill script) — no hand-editing of managed zones on prod.
  - NFR-4: prompts/domain-medical.md semver MUST be bumped (prompt content change is auditable via semver header).
constraints:
  - Per-WIKI CLAUDE.md is rendered from templates/<slug>.md at create time with template_sha256 pinned (wiki/lifecycle.py _render_claude_md); existing WIKIs receive template updates ONLY via scripts/backfill_managed_zone.py → migration.repair_managed_zone (idempotent, preserves user zone).
  - Inbox hint keywords are read from the per-WIKI CLAUDE.md via the metadata-guarded hint cache (inbox/hint_cache.py), NOT from templates/ directly — backfill on prod is therefore mandatory for FR-5 to take effect.
  - No Python code changes: all four defects are prompt/template/data-level. templates.py slug loader is NOT involved (domain presets bypass it).
  - No macros (protein/fat/carb) in the CSV schema — user decision 2026-07-02, YAGNI; columns can be added later.
  - No photo/OCR branch in the guardrail wording — user decision 2026-07-02, YAGNI.
risks:
  - R-1 (LOW): calorie estimates are inherently imprecise (industry data: crowdsourced estimates average ~38% error) — mitigated by the mandatory source=estimated flag distinguishing estimates from user-stated values.
  - R-2 (LOW): keyword overlap Medical↔Cooking could still misroute ambiguous messages ("что приготовить на ужин при диете") — mitigated by FR-6 demarcation lines in both templates; residual ambiguity falls to the LLM router as today.
  - R-3 (LOW): prod cleanup touches live user medical data — mitigation: file operations are additive/reversible except NUTRITION_GUIDE.md deletion, which the user explicitly ordered; per-WIKI git history (deploy §5) preserves recovery path.
scope_in:
  - templates/medical.md — Data layout diet/, File resolution rule, Inbox hint keywords, nutrition boundary note.
  - prompts/domain-medical.md — nutrition boundary rule + semver bump.
  - templates/cooking.md — one demarcation line.
  - prod VPS Medical WIKI cleanup + backfill run (FR-7).
scope_out:
  - Macros (protein/fat/carb) columns.
  - Photo → OCR calorie estimation.
  - Separate Nutrition-WIKI domain (rejected in /best-approach: third food domain, YAGNI, adds a new classifier fork).
  - BMR/TDEE reference file (NUTRITION_GUIDE.md deleted, not relocated — user decision).
scope_later:
  - Macros columns if the user starts asking for белки/жиры/углеводы.
  - Weekly calorie digest integration.
---

# Discovery — Nutrition/calorie tracking formalization in Medical WIKI

## Problem

Live incident 2026-07-02: the bot behaves nondeterministically on calorie requests —
sometimes counts, sometimes demands explicit consent, sometimes refuses. Two artifacts
were created in wrong locations (`history/NUTRITION_GUIDE.md`, calories in `daily/`).

## Root causes (4, verified by reading prompts/templates + live behavior)

1. **Layout hole** — `templates/medical.md` has no `diet/` section, while the parent
   `ai-steward/CLAUDE.md` Medical template has one (SSoT drift). The model improvises.
2. **Guardrail gray zone** — `prompts/domain-medical.md` forbids "interpretation"
   without defining whether calorie math is interpretation. The LLM re-decides this
   fork on every stateless run → random consent/refusal.
3. **Consent over stateless runs** — explicit confirm is required only for deletions
   (Pre-flight, D-041); asking consent for a reversible append guarantees context loss
   because each message is a fresh CLI instance.
4. **Routing fork** — `templates/cooking.md` Inbox hint already owns завтрак/обед/ужин
   keywords; food-fact messages can route to Cooking instead of Medical.

## Research

- /best-approach session 2026-07-02: 4 variants analyzed, Variant 3 chosen by user (88%).
- WebSearch: industry food-log minimum is `date, meal_type, item, qty, kcal`;
  estimated-vs-logged distinction is critical (~38% avg error in crowdsourced calorie
  data — Nutrola 2026 review; USDA FoodData Central as verified-data precedent).
- Codebase: template propagation mechanism confirmed in `wiki/lifecycle.py`
  (`_render_claude_md`), `wiki/migration.py` (`repair_managed_zone`),
  `scripts/backfill_managed_zone.py` (idempotent, user-zone-preserving).

## Decision trail

User decisions (2026-07-02): no macros (YAGNI), no OCR branch (YAGNI), delete
NUTRITION_GUIDE.md rather than keep as reference.
