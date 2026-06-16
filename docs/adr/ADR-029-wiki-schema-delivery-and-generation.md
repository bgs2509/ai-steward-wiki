# ADR-029: Per-WIKI schema delivery + LLM generation for unknown domains

**Status:** Accepted
**Date:** 2026-06-16
**Beads:** aisw-db6, aisw-b50
**Amends:** [D-017](../Spec-WIKI/decisions/D-017-domain-claude-md-template.md) (realizes deferred Variant D), [D-039](../Spec-WIKI/decisions/D-039-claude-md-evolution.md) (managed/user zones)

## Context

In the Karpathy LLM-Wiki model the per-WIKI `CLAUDE.md` **is** the schema — it tells
the model how data is laid out and how to ingest. Two production defects broke this:

1. **The schema was never delivered.** `WikiLifecycleManager.create_wiki` wrote a
   frontmatter-only `CLAUDE.md` (`template_sha256: <empty>`, no managed zone). Every
   WIKI — Medical, Budget, … — had `body=0chars`. The runner folds `CLAUDE.md` into
   the prompt, so the model received *no* `## Data layout`; it improvised a parallel
   `pages/*.md` structure instead of appending to the canonical `metrics/*.csv`. The
   empty `## Inbox hint` also made the deterministic pre-router fast-path always miss.
2. **Fixed presets don't scale.** D-017 chose "per-domain presets + `_default`" and
   deferred Variant D (LLM auto-generation for unknown domains) "until real need".
   A user creating an arbitrary-domain WIKI (anime, coal industry, fishing) only ever
   got the generic `_default` — no topic-appropriate structure.

## Decision

The per-WIKI `CLAUDE.md` **managed zone** is the single source of truth for a WIKI's
data layout, and it is always materialized:

1. **Known domain** → `create_wiki` renders the static preset body
   (`templates/<slug>.md`) into the managed zone via `load_template` + `render_v2`,
   stamping the real `template_sha256`. `resolve_template_id(raw_name)` maps a name to
   a preset slug or `_default`.
2. **Existing WIKIs** → `migration.repair_managed_zone` backfills the managed zone from
   the template, preserving the user zone; idempotent on matching sha. One-shot
   `scripts/backfill_managed_zone.py` ran it across the deployment.
3. **Unknown domain** → `wiki.schema_gen.apply_generated_schema` runs **one Sonnet
   turn** (`prompts/schema-gen.md`) that picks one of five data-shape archetypes
   (time-series / ledger / encyclopedia / collection / journal) and adds
   topic-specific sections from the WIKI name + first content. The result is written
   with `template_id=_generated` and `sha=hash(content)`. Because no static template
   resolves for `_generated`, `repair_managed_zone`/backfill **skip** it → the
   generated schema persists and co-evolves (Karpathy "co-evolve" principle).
   Validation (`validate_schema`) requires Data layout + File resolution + Inbox hint
   + ≥1 topic section; on failure or generator error it falls back to `_default`.
4. **All templates** gained an explicit "## File resolution" rule (append to the
   existing `*_<metric>.csv`; one file per concept; never spawn parallel structures),
   because directory + columns alone left file-naming to guesswork.

## Consequences

- Data lands in the canonical layout, not an improvised `pages/` tree; the hint
  fast-path is no longer permanently dead.
- New WIKIs of any domain get a topic-tailored schema for one extra Sonnet call at
  create (user-accepted latency); cost is bounded to creation, a rare event.
- `_generated` is a deliberate non-resolving sentinel — anything that loads templates
  by id must treat it as "leave as-is" (backfill/repair already do).
- New events: `wiki.schema_gen.applied|invalid|failed`, `wiki.lifecycle.repair.*`.
- Verified end-to-end: a coal-industry document produced a real `_generated` schema
  with coal sections and CSV/page data (see 20260616 completion report).
