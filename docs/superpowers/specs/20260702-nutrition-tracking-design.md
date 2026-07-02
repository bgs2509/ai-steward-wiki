---
feature: nutrition-tracking
bd_id: aisw-2si
module_id: M-WIKI-LIFECYCLE
status: stable
date: 2026-07-02
risk: low
evidence: strong
open_questions: []
stack:
  - Markdown domain templates (templates/medical.md, templates/cooking.md) — managed-zone source, propagated via wiki/migration.py repair_managed_zone.
  - Markdown Stage-1b overlay prompt (prompts/domain-medical.md) — read per CLI run, semver-versioned.
  - CSV data file convention (diet/food_log.csv) — same accumulating-file pattern as metrics/weight.csv.
  - scripts/backfill_managed_zone.py — existing idempotent prod propagation tool (no new code).
decisions:
  - DEC-1 Nutrition lives INSIDE Medical-WIKI (diet/), not a separate Nutrition domain — a third food domain (after Cooking) would violate SSoT and add a new classifier fork; chosen as Variant 3 in /best-approach (88%), user confirmed. ADR-033.
  - DEC-2 food_log.csv schema `date,meal,item,qty_g,kcal,source` with source=logged|estimated — industry minimum; the source flag separates user-stated values from model estimates (~38% avg error in crowdsourced calorie data) and legally/ethically marks estimates as non-clinical.
  - DEC-3 Calorie estimation of self-logged food is classified as data structuring, NOT medical interpretation — allowed without consent; clinical nutrition topics stay under the doctor redirect.
  - DEC-4 No consent for food_log appends — explicit confirm remains deletion-only (D-041 pre-flight unchanged); consent dialogs are structurally broken over stateless per-message CLI runs.
  - DEC-5 Routing determinism via Inbox hint keywords (Medical gains еда/калории/ккал/съел/порция) + reciprocal demarcation lines in both medical.md and cooking.md.
  - DEC-6 No macros, no OCR, delete NUTRITION_GUIDE.md — user decisions 2026-07-02 (YAGNI).
---

# Design — Nutrition tracking in Medical WIKI (Variant 3)

## Approach

Close all four root causes with prompt/template-level changes only; propagate to the
existing prod WIKI through the established managed-zone backfill. No Python changes.

## Changes

1. **templates/medical.md**
   - Data layout: add `7. diet/ — food_log.csv: date,meal,item,qty_g,kcal,source`.
   - File resolution: add rule — еда/калории → строка в `diet/food_log.csv`
     (накопительный файл; `source=logged` если юзер назвал калории сам,
     `source=estimated` если оценил бот).
   - Non-interpretation boundary note (template side, mirrors prompt).
   - Demarcation: рецепты и меню → Cooking-WIKI.
   - Inbox hint keywords += еда, калории, ккал, съел, порция.
2. **prompts/domain-medical.md** (semver 1.0.0 → 1.1.0)
   - New rule: оценка калорийности самозаписанной еды = структурирование данных,
     разрешено без согласия, помечать `source=estimated`; клиническая интерпретация
     (лечебные диеты, дефициты, питание при заболеваниях) → редирект к врачу.
   - New rule: append в food_log.csv не требует подтверждения; confirm — только
     удаление (как и было по D-041).
3. **templates/cooking.md**
   - Demarcation line: рецепты/меню/готовка → здесь; факт съеденного и калории →
     Medical-WIKI.
4. **Prod (VPS) one-shot cleanup** — via ssh, after deploy:
   - `scripts/backfill_managed_zone.py` (idempotent) → live Medical/Cooking WIKIs get
     the new managed zone; hint cache refreshes via metadata guard.
   - Delete `history/NUTRITION_GUIDE.md` (user-ordered; recoverable via per-WIKI git).
   - Create `diet/food_log.csv`, migrate calorie entries from `daily/2026-07-01.md`
     (source=estimated), leave daily/ prose intact.

## Data model

`diet/food_log.csv` header: `date,meal,item,qty_g,kcal,source`
- `meal` ∈ завтрак|обед|ужин|перекус
- `source` ∈ logged|estimated

## UX flow (target)

«съел сырники 200г» → Stage-1a hint fast-path → Medical → append row
(`source=estimated`, kcal оценён) → «✏️ записал: сырники 200г ≈ 370 ккал (оценка)».
Никаких consent-вопросов; отказ — только на клинические запросы.
