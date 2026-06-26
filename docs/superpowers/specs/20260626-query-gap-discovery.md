---
feature: query-gap
bd_id: aisw-50z
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-26
risk: medium
evidence: strong
open_questions: []
fr:
  - FR-1: A message classified by Stage-0 as intent=wiki_query MUST be ANSWERED in chat (assistant text delivered), NOT filed into a WIKI via the Stage-1a ingest router.
  - FR-2: The wiki_query answer run MUST have read access across the owner's <Domain>-WIKI/ subdirs so it can read the relevant theme WIKI and answer from stored content (Karpathy LLM Wiki query op).
  - FR-3: The wiki_query path MUST NOT present a Confirm/Cancel-to-file keyboard and MUST NOT silent-route into a WIKI via the '## Inbox hint' fast-path.
  - FR-4: wiki_ingest and unknown intents MUST keep their current routing behaviour (Stage-1a router / hint fast-path) — only wiki_query changes.
  - FR-5: The answer path MUST emit the existing observable log trace (tg.pipeline.runner.dispatched intent=wiki_query + tg.pipeline.deliver.sent) so the e2e log_watch scorer judges the turn as an answer, not an ingest ack.
nfr:
  - NFR-1: Localised change — touch only tg/pipeline.py routing predicate; no new module, no protocol change, no DB migration.
  - NFR-2: mypy --strict + ruff + ruff format + grace lint clean; make total-test fully green (coverage >=80%).
  - NFR-3: Ru-only user-facing strings (D-032); no new user-facing copy needed (reuses the generic runner answer + ACK_TEXT_RU fallback).
  - NFR-4: TDD — the bug is reproduced by a failing unit test (wiki_query with a wired router currently goes to router.route, not runner.run) before the fix.
constraints:
  - The generic fall-through runner (_run_text_pipeline ~line 1404+, self._streaming.run_and_deliver / self._runner.run) is the established answer-capable path. _WikiRunnerAdapter.run (__main__.py:413) runs Claude with cwd = wiki_root/<telegram_id> (the user ROOT) and --add-dir on that root, so the run already has cross-WIKI read access. wiki_query answering is achieved by letting it fall through to this path.
  - The bug is at tg/pipeline.py:585 — _ROUTABLE_INTENTS = {WIKI_INGEST, WIKI_QUERY, UNKNOWN}. Both the HINT_FASTPATH block (line 1157) and the ROUTABLE_BRANCH (line 1278) gate on result.intent in _ROUTABLE_INTENTS, so wiki_query is funnelled to filing with no answer path (verified).
  - Stage-1a router (inbox/router.py:RouterIntent) only has filing outcomes route/create_wiki/clarify/reject — there is NO answer outcome there. Answering must happen on the generic runner path, not in the router.
risks:
  - Running wiki_query in the user root could let Claude freelance-write to a WIKI. Mitigation - prompts/wiki.md principle 1 restricts content changes to explicit ingest/query/lint ops and instructs "query => no changes / no confirmation line"; the spec PASS criterion only requires an answer delivered. Hardening of write-prevention is out of scope (separate from the routing gap).
  - Theme-scoping (running the answer in ONE matched theme WIKI via score_catalog/is_confident) would require changing the WikiRunner protocol + _WikiRunnerAdapter + StreamingDelivery + pipeline (>=4 modules) = HIGH risk. Out of scope for this medium-risk fix; reading across all WIKIs from root already satisfies FR-2 (theme-picking delegated to the Claude run).
scope_in:
  - src/ai_steward_wiki/tg/pipeline.py — remove Intent.WIKI_QUERY from _ROUTABLE_INTENTS (line 585) + MODULE_CONTRACT header bump.
  - tests/unit/tg/test_pipeline_router.py — drop WIKI_QUERY from the routable parametrize; add a RED->GREEN test proving wiki_query (router wired) reaches runner.run / not router.route.
scope_out:
  - web_task (mode 8) and lifting the WebSearch tool denial — separate proposed mode in the e2e spec, not this bead.
  - Theme-scoping the answer run to a single matched WIKI (cwd scope) — deferred optimization; cross-WIKI root read is sufficient.
  - wiki_lint behaviour (mode 4, partial) — separate bead.
  - Hardening against Claude writing during a query run — separate concern.
---

# Discovery — query-gap: wiki_query must answer from WIKI, not file (ingest)

## Real intent

Stage-0 (MODE classifier) correctly emits `intent=wiki_query` for questions about
already-stored content, but the pipeline has no answer branch for it: `wiki_query`
sits in `_ROUTABLE_INTENTS` and is funnelled to the Stage-1a ingest router (filing).
Cycle-1 e2e: 6/7 `query__*` scenarios FAIL "filed instead of answered". The fix is
to stop treating `wiki_query` as a filing intent and let it reach the existing
answer-capable generic runner.

## Two-classifier model (verified in code + e2e spec §0)

1. Stage-0 MODE classifier (`classifier/schema.py:Intent`, `prompts/classifier.md`) — decides *what to do*.
2. Stage-1a TOPIC router (`inbox/router.py:RouterIntent`) — decides *which WIKI to file into*; outcomes are all filing (route/create_wiki/clarify/reject). No answer outcome.

`wiki_query` is a Stage-0 mode whose target action is *answer*, which is the generic
runner's job, not the router's.

## Why the minimal routing change is correct

- The generic runner (`_run_text_pipeline` ~1404+) runs Claude in the user root with
  `--add-dir` on that root (`_WikiRunnerAdapter.run`, `__main__.py:424-426`), so it can
  read every `<Domain>-WIKI/` and answer. It then delivers TEXT to chat
  (`run_and_deliver` / `runner.run` + `output.deliver`).
- Removing `WIKI_QUERY` from `_ROUTABLE_INTENTS` makes wiki_query skip BOTH filing
  branches (HINT_FASTPATH at 1157 and ROUTABLE_BRANCH at 1278, both gated on the set),
  skip the REMINDER/DIGEST/ADMIN fast-paths (different intents), and fall through to the
  generic answer runner. No new code path is needed.

## E2E PASS criterion (wiki-mode-theme-matrix.md §1/§4)

`wiki_query` PASS = `intent=wiki_query` AND an answer delivered (not an ingest ack);
FAIL = message filed instead of answered (current bug). No theme-scoping or WebSearch
required for this mode.

## Risk × evidence

risk=medium (internal routing predicate, 1 module, reversible, no schema/API/security
change). evidence=strong (e2e SSoT `wiki-mode-theme-matrix.md` §0/§1/§4 + verified code
anchors at pipeline.py:585/1157/1278/1404 and __main__.py:424). open_questions=[].
=> Gates 3/5/10 auto-approve per the matrix.
