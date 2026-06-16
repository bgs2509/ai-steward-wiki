# ADR-031: Create-WIKI-from-document via the router + admin-intent taming

**Status:** Accepted
**Date:** 2026-06-16
**Beads:** aisw-aca (phase 1; chain aisw-rz3/2z6/dm2/0d3), aisw-zpn
**Relates:** [D-041](../Spec-WIKI/decisions/D-041-no-direct-wiki-commands.md) (no direct commands), ADR-029 (schema), ADR-030 (aggregation)

## Context

After aggregation (ADR-030) a new-domain document reached the pipeline correctly, but
the create flow still failed:

- The Stage-0 classifier (`classifier.md`) has 7 intents with **no `create_wiki`**.
  An explicit "создай <name>" was classified `admin` (the nearest bucket,
  "administrative action"). `admin ∉ _ROUTABLE_INTENTS`, so it fell into the generic
  legacy runner — a Claude run in the user **root** with Write access — which
  freelance-created a malformed WIKI: empty `.gitkeep` scaffold + a hand-written
  `CLAUDE.md` with a non-existent `template_id` and a hallucinated `template_sha256`.
  The document's data was lost.
- The router (Stage-1a) chose `clarify` for the document itself instead of proposing
  a new WIKI, so the existing CREATE_WIKI confirm path was never entered.

"Create vs route" requires knowing the user's existing WIKI catalog — which only the
**router** loads. Stage-0 classifies a single message in isolation and is the wrong
layer to decide it.

## Decision

1. **Router proposes create on documents.** `prompts/inbox.md` (Stage-1a) returns
   `intent: create_wiki` + a proposed name for self-contained **new-domain** material
   with no matching WIKI — instead of `clarify`. A semantic criterion ("material to
   store", not a question/reply/command) plus a soft length floor guard against spam;
   few-shot examples anchor it. The existing `pipeline.py` CREATE_WIKI branch then
   builds the confirm card → on confirm runs `_LibrarianAdapter.ingest` →
   `lifecycle.create_wiki` + `schema_gen` (ADR-029) + ingests the document (`user_text`).
   No new "create command" is introduced (D-041 spirit: drive the happy-path, not a
   second intent with fragile document-linkage).
2. **Tame `intent=admin`.** There is no real admin handler; `admin` is short-circuited
   to a safe reply (`ACK_ADMIN_RU`) and **never** runs Claude in the user root —
   closing the freelance-create hole.
3. **Large-document ingest** (aisw-zpn): a separate `wiki_ingest_timeout_s=600` for the
   heavier create+ingest path; on `WikiRunnerTimeoutError`, `_wiki_has_ingested_content`
   distinguishes partial success (real files written) from total failure → honest
   `status="partial"` "занёс частично — пришли ещё раз" + a "дополни-не-дублируй"
   ingest instruction so a re-send (soft-resume) completes it. No resume engine.

## Consequences

- A pasted document of any domain → one-tap "Create & ingest" card → proper WIKI +
  generated schema + ingested data (verified: `Угольная-отрасль-WIKI`).
- Misclassified create requests are harmless (no root-run); the user is nudged to send
  material instead.
- A very large document either fits the 600s budget or returns an honest partial with
  soft-resume — never a misleading "failed" over written data.
- **Deferred:** user name-override on the confirm card (aisw-9sn) — needs an FSM/button
  input flow that interacts with the aggregator; the router-proposed name + WIKI picker
  is the phase-1 UX.
- New events: `tg.pipeline.admin.declined`, `inbox.route.ingest_timeout`.
