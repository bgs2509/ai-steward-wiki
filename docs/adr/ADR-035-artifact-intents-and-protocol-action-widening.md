# ADR-035: Artifact-anchored intent taxonomy and WikiRunner/StreamingDelivery `action` widening

**Status:** accepted
**Date:** 2026-07-03
**bd:** aisw-xi8
**Related:** ADR-034 (deviates from its "no Protocol change" commitment), ADR-032 (per-intent run config), D-009/D-015 (Stage-0 classifier), simulation report 2026-07-03 (100-question corpus)

## Context

The 100-question family simulation (2026-07-03) measured the 9-intent Stage-0 scheme at
≈93/100 intent accuracy with four defect clusters rooted in *classifying Python forks*
competing with the classifier: `_RECURRING_KEYWORDS` punted every recurring phrasing into
digest (medication reminders became summaries), `_detect_digest_action` made digest control
depend on which intent Stage-0 happened to emit, job management did not exist, and
sub-threshold reminders fell into the write-capable generic root runner.

The user set the design principle: **an intent exists only for a distinct artifact** (wiki
files, jobs.db rows, the live web, service state, nothing). Verbs belong in payload slots.
A 6-intent draft prompt scored 100/100 intent accuracy (99/100 incl. action/kind) on the
same corpus.

Re-anchoring the `__main__` adapters (adaptive query scoping aisw-o6m; WebSearch carve-out
aisw-dqz) onto the new taxonomy requires knowing `wiki.action == "query"` — information the
adapters cannot re-derive, since with 6 intents the intent value alone no longer encodes it.
ADR-034 committed "WikiRunner Protocol MUST NOT change"; that commitment assumed the intent
enum itself carried the query/ingest distinction.

## Alternatives

1. **Encode action into the Intent enum** (wiki_ingest/wiki_query/wiki_lint/... = 9+ members).
   ❌ Recreates the verb-multiplied taxonomy this feature deletes; intents inflate again with
   every feature (the measured failure mode).
2. **Widen the Protocols with a defaulted param** — `WikiRunner.run(..., action: str | None = None)`
   and `StreamingDelivery.run_and_deliver(..., action: str | None = None)`. Every existing
   caller and test fake keeps working unchanged (defaulted kwarg); adapters branch on
   `(intent is WIKI, action == "query")` / `(intent is WEB)`. ⭐
3. **Adapter-side re-classification** (adapter re-runs slot parsing on raw text). ❌ Duplicates
   classification (two sources of truth for the same decision — the exact disease being cured).

## Decision

Alternative 2. The 6-member artifact taxonomy (`wiki | job | web | chat | admin | unknown`)
becomes the closed Intent enum; verbs/kinds live in `WikiSlots`/`JobSlots` payload contracts
(lenient boundary parsing, DEC-4). The `action` parameter is added to both Protocols with a
`None` default — a deliberate, recorded deviation from ADR-034's "no Protocol change"
commitment, which is superseded on this point.

Guard-rails that survive unchanged: single Haiku call per message; hint fast-path and the
Stage-1a Sonnet router keep deciding WHICH wiki (domain), never intent; parsers
(dateparser/parse_recurrence) validate, never classify; destructive ops stay behind explicit
confirms; sub-threshold `job`/`admin` messages get a deterministic ru clarification and can
never reach the write-capable generic runner (DEC-2).

## Consequences

1. `prompts/classifier.md` 2.0.0 is the single classification SSoT; every change to it
   requires a full-corpus regression run (`make classifier-regress`, manual gate — documented
   in the prompt CHANGELOG discipline).
2. Classifying regexes are deleted (`_RECURRING_KEYWORDS` punt, `_detect_digest_action`);
   `tg/pipeline.py` becomes a flat intent→handler switch (DEC-1).
3. Existing jobs.db rows and pending_confirms categories remain valid (additive payload
   union, old confirm categories untouched — FR-15, R-3).
4. ADR-034's scoping *mechanics* (wiki/scope.py, hint-match thresholds) are untouched; only
   its Protocol-immutability commitment is superseded.
5. New intent values flow into structlog anchors; external log consumers (e2e scorer,
   log_watch) must migrate — tracked as scope-LATER in the discovery spec.
