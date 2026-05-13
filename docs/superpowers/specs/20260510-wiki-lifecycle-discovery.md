# Discovery — M-WIKI-LIFECYCLE (NL pre-flight, anti-spam, soft-delete, frontmatter)

**Feature:** chunk 8 of `20260510-ai-steward-wiki-mvp` epic.
**bd_id:** aisw-9s4 (claimed, in_progress).
**Date:** 2026-05-10.
**Status:** stable.

## Problem

Per `tech-spec-draft.md` §5 and D-041, WIKI lifecycle (create / delete / restore)
is driven exclusively by NL prompts — there are NO direct `/wiki_*` commands
(INV-7). We need a pure-Python lifecycle layer that the Inbox router will call
once Stage-1a Claude has classified the user's intent. This chunk delivers:

1. Deterministic name normalisation pipeline (Cyrillic → Latin ISO 9 →
   PascalCase → `-WIKI` suffix → regex `^[A-Z][A-Za-z0-9]*-WIKI$`).
2. Anti-spam guardrails — hard cap (default 20 / user) + Levenshtein ≤2
   near-duplicate detection against existing names of the same owner.
3. Soft-delete to `_trash/<UTC-ts>_<Name>-WIKI/` with 30-day retention window
   and atomic restore (hard-delete sweeper itself is chunk 13 retention).
4. CLAUDE.md frontmatter (`schema_version`, `template_id`, `last_migrated_at`,
   `template_sha256`) + managed/user-zone HTML markers + linear v1→v2 migration
   preserving user-zone content verbatim.
5. 5-step pre-flight grounding for destructive operations (D-041): acquire
   locks → frontmatter+schema check → template consistency → staging-size
   guard → write-area ownership/permissions.

## Functional requirements

1. **FR-1 Name normalisation.** `normalize_wiki_name(raw)` returns
   `WikiName(primary, hyphenated_lookup)`. Cyrillic input is transliterated
   via ISO 9:1995 table, non-alphanumeric splits, PascalCase join, `-WIKI`
   appended. Empty / invalid input → `WikiNameError`.
2. **FR-2 Lookup.** `lookup(owner, name_or_hyphenated)` resolves either the
   primary form (e.g. `HealthLite-WIKI`) or the hyphenated lookup form
   (`multi-word`) used for template fallback.
3. **FR-3 Anti-spam cap.** On create, if owner already has ≥20 active WIKIs
   (not counting `_trash/`), raise `AntiSpamCapError`. Cap configurable via
   `Settings.wiki_max_per_user`.
4. **FR-4 Levenshtein near-duplicate.** On create, compute Levenshtein
   distance (case-insensitive on the slug portion) against every existing
   primary name of that owner. If ≤2, return the existing wiki instead of
   creating a duplicate (`NearDuplicateMatch`).
5. **FR-5 Soft-delete.** `soft_delete(wiki_id)` atomically moves the wiki
   directory to `_trash/<UTC-ts>_<Name>-WIKI/`. Returns a `TrashedWiki`
   record with `deleted_at` (UTC ISO 8601).
6. **FR-6 Restore.** `restore(trashed_wiki)` while `deleted_at + 30d > now`,
   atomically moves it back to the owner root.
7. **FR-7 Frontmatter.** Parse / serialise YAML-like frontmatter
   (`schema_version: int`, `template_id: str`, `last_migrated_at: ISO`,
   `template_sha256: str`). Managed/user-zone HTML markers:
   `<!-- managed:start --> … <!-- managed:end -->` and
   `<!-- user:start --> … <!-- user:end -->`.
8. **FR-8 Migration v1→v2.** `migrate_v1_to_v2(claude_md_path)` is linear and
   idempotent. Preserves user-zone verbatim, refreshes managed-zone from the
   current template, bumps `schema_version` to 2, writes atomically
   (tmp + `os.replace`). No-op if already v2.
9. **FR-9 Pre-flight.** `preflight(wiki_path)` returns a `PreflightReport`
   with 5 named checks: `locks`, `frontmatter`, `template`, `staging`,
   `permissions`. Each step records `ok: bool`, `detail: str`. Aggregated
   `ok` field is `True` only if all five pass.

## Non-functional requirements

1. **NFR-1** Pure stdlib for ISO 9 + Levenshtein (no new deps — `transliterate`
   and `rapidfuzz` are NOT in `pyproject.toml`; adding them would require
   Context7 verification + a separate change).
2. **NFR-2** All datetime values stored as UTC ISO 8601 strings; user-TZ
   never enters this layer.
3. **NFR-3** All file system mutations atomic — tmp + `os.replace` for files,
   `os.replace` for directory rename within the same FS.
4. **NFR-4** Strict typing (`mypy --strict`) and Pydantic v2 frozen models on
   public boundaries (`WikiName`, `Frontmatter`, `PreflightReport`,
   `TrashedWiki`).
5. **NFR-5** Logging via structlog with `event` strings prefixed `wiki.lifecycle.*`.

## Out of scope

1. Real 30d hard-delete sweeper scheduling (chunk 13 `M-OPS-PII` retention).
2. Full domain template catalogue (chunk 15 `M-TEMPLATES`).
3. systemd-run wrapping (chunk 16).
4. Actual NL classification pipeline that drives lifecycle (chunks 5/6/7 —
   already implemented; this chunk is the receiver).

## INV check coverage

- **INV-7** (no `/wiki_*` commands) — enforced by `scripts/lint_invariants.py`
  added in this chunk: greps source tree for direct `rm -rf` / `mv` on wiki
  paths outside `lifecycle.py`. Exit-1 on violation.

## Risks

1. ISO 9 table coverage — we hard-code the standard 33-letter Russian alphabet
   pairs. Mitigation: explicit table + unit test for every Cyrillic letter.
2. Levenshtein performance — O(n·m) per existing name × 20 cap is trivial;
   no fast-path needed.
3. Frontmatter parser correctness — full YAML lib is overkill; we accept a
   strict subset (`key: value`, no nested mappings) and fail fast on anything
   else.
