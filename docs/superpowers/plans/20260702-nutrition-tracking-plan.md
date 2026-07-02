# Plan — Nutrition tracking in Medical WIKI (aisw-2si, Variant 3)

> SSoT for execution. Specs: `20260702-nutrition-tracking-discovery.md`,
> `20260702-nutrition-tracking-design.md`, ADR-033.
> No Python changes → no TDD cycle; verification = make lint + grace lint + manual
> template parse (load_template) + live-bot check after deploy.

## Task 1 — templates/medical.md (FR-1, FR-2, FR-5, FR-6)

1. Data layout: add item `7. diet/ — food_log.csv: date,meal,item,qty_g,kcal,source (source = logged | estimated)`.
2. File resolution: add rule — еда/калории → новая строка в `diet/food_log.csv`
   (один накопительный файл; создавать только если ещё нет); `source=logged` если
   калории назвал юзер, `source=estimated` если оценил бот.
3. New section «Питание и калории» (boundary, template side):
   оценка калорий записанной еды разрешена без подтверждения, помечать «(оценка)»;
   рецепты и меню — в Cooking-WIKI.
4. Inbox hint keywords += `еда, калории, ккал, съел, порция`.

## Task 2 — prompts/domain-medical.md (FR-3, FR-4; NFR-4)

1. semver 1.0.0 → 1.1.0.
2. Add rule 5: оценка калорийности самозаписанной еды = структурирование данных —
   выполняй без запроса согласия, помечай `source=estimated`; клиническая
   интерпретация питания (лечебные диеты, дефициты, питание при заболеваниях) —
   редирект к врачу.
3. Add rule 6: дозапись в `diet/food_log.csv` не требует подтверждения;
   подтверждение — только на удаление записей.

## Task 3 — templates/cooking.md (FR-6)

1. Format: add line — факт съеденного и подсчёт калорий → Medical-WIKI; здесь только
   рецепты, меню, списки покупок.

## Task 4 — verification (local)

1. `make lint` (ruff + format + mypy + grace lint) — green, no baseline drift.
2. `uv run python -c "load_template('medical'), load_template('cooking')"` — templates
   parse, sha changes confirmed.
3. Grep: no leftover contradictions (e.g. old layout item count references).

## Task 5 — commit + merge

1. Commits (Conventional, meta scope): `feat(medical-template): ...`,
   `feat(domain-medical-prompt): ...`, `docs(adr): ADR-033 ...`, specs+plan.
2. Merge worktree branch into local master.

## Task 6 — prod deploy + cleanup (FR-7) — requires push (in-scope per approved V3)

1. Push master; on prod VPS: `git pull` + restart `aisw-bot.service` (verify host/path
   from live ssh config — memory says vpn-gpu-1:/home/bgs/works/ai-steward-wiki, recent
   session says vpn-2; verify before acting).
2. Run `scripts/backfill_managed_zone.py --wiki-root <prod wikis> --templates-dir <prod templates>`
   (dry-run first, then real) → live Medical/Cooking WIKI managed zones updated.
3. Live Medical WIKI cleanup:
   - create `diet/food_log.csv` with header + migrated rows from `daily/2026-07-01.md`
     (source=estimated);
   - delete `history/NUTRITION_GUIDE.md` (user-ordered);
   - keep `daily/2026-07-01.md` prose intact (remove only the misplaced calorie block
     if it duplicates food_log).
4. Verify: managed zone of live Medical CLAUDE.md contains diet/ + new keywords.

## Task 7 — finish

1. `bd close aisw-2si`.
2. Report to user (live-bot smoke test suggestion: «съел сырники 200г»).

## Self-review checklist

- [x] Every FR (1–7) covered: T1 (FR-1/2/5/6), T2 (FR-3/4), T3 (FR-6), T6 (FR-7).
- [x] Every NFR: T4 (NFR-1), ru-only strings by construction (NFR-2), T6.2 uses
      backfill mechanism only (NFR-3), T2.1 semver bump (NFR-4).
- [x] ADR decision implemented: DEC-1..6 map to T1–T3, T6.
- [x] No placeholders.
- [x] Order respects dependencies (edits → verify → commit → deploy → cleanup).
