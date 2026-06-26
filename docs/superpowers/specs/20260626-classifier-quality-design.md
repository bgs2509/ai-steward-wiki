---
feature: classifier-quality
date: 20260626
beads: [aisw-32p, aisw-zgf]
status: design
---

# Stage-0 Classifier Quality — Design

## Approach

Two surgical changes plus an offline evaluation harness.

### 1. `prompts/classifier.md` (the main lever) — semver 1.3.0 → 1.4.0

Additive edits, no wholesale rewrite:

1. **Classify by ACTION, not domain.** New top rule: pick the intent from what the user is
   *doing* (state a fact to keep, ask about stored facts, search the web, schedule a
   reminder, …). Choosing the target WIKI/domain is a later stage's job — never fall back
   to `unknown` merely because the domain is unclear. This directly attacks the confident-
   `unknown` failure (root cause of aisw-32p high unknown-rate).

2. **Disambiguation cues** for the two most-confused concrete intents:
   - `wiki_ingest` — the message *states / records* a fact, datum, receipt, measurement,
     note or recipe to keep (cues: «записал», «купил», «потратил», «прошёл», «запиши»,
     «сохрани», «конспект», «рецепт …: …»). Short bare data ("давление 120/80 утром")
     still counts.
   - `wiki_query` — the message *asks about the user's own previously-stored* data
     (cues: «сколько у меня…», «что я записывал…», «какое было…», «когда…»).

3. **Tighten `admin`** to real operational/admin commands only: managing the allowlist
   (add/remove/approve/reject users), elevation/demotion of admin privileges, quotas/limits,
   ops runbooks, restart/deploy. Explicit NEGATIVE list: benign read-only / list / self
   queries about the user's OWN WIKIs or data are NEVER `admin`.

4. **"List my WIKIs" → `unknown`.** Requests to see/list the user's own WIKIs/topics
   («покажи мои вики», «какие у меня вики», «список вики», «покажи все вики мои») →
   `unknown` (NOT `admin`). `unknown` is *routable* (`_ROUTABLE_INTENTS` in `tg/pipeline.py`
   = `{wiki_ingest, unknown}`), so it reaches the Inbox router which resolves it to
   `RouterIntent.LIST_WIKIS` and replies with the catalog (path added in aisw-rl1). There is
   no dedicated Stage-0 `list_wikis` intent and adding one is out of scope.

### 2. `classifier/stage0.py` — graceful timeout fallback (aisw-32p, FR-3)

`backend.call()` may raise `ClassifierTimeoutError` (a `ClassifierError` subclass) when the
Haiku CLI exceeds `timeout_s`. Today that propagates to `tg/pipeline.py`, is caught as
`ClassifierError`, and the user gets a dead-end "не удалось распознать" ack.

Change: `classify()` catches **`ClassifierTimeoutError` only** and returns a safe **fallback
`ClassifierResult`** with `intent=unknown`, `confidence=0.0`,
`distilled_payload={"fallback": "stage0_timeout"}` (plus the normal backend/model/prompt/
latency audit fields). Because `unknown` is routable, the pipeline forwards the message to
the Inbox router (or, if no router is wired, the generic answer runner) instead of dropping
it. A structured `classifier.stage0.timeout_fallback` log anchor records the event.

`ClassifierSchemaError` (genuinely malformed model output) is intentionally NOT caught — that
is a permanent fault for which the existing error ack is the right behaviour.

TDD: a deterministic unit test injects a fake backend whose `call()` raises
`ClassifierTimeoutError`, and asserts `classify()` returns `intent=unknown` /
`confidence=0.0` / the fallback payload instead of raising.

### admin vs auth — why narrowing detection is safe

`auth/admin.py::AdminService.assert_admin` gates EVERY admin operation on
`role == "admin"` + tenancy rules, independent of the classifier. The classifier `admin`
INTENT feeds only `tg/pipeline.py`'s `admin.declined` branch (a safe ack — no privileged
action). Reclassifying a benign "list my wikis" out of `admin` removes a false friction
point; it cannot grant anyone admin powers because the allowlist still gates the real
operations. The change touches DETECTION only.

## Eval strategy

A new offline harness, `scripts/classifier_eval.py`, feeds a labelled corpus
(`tests/fixtures/classifier_corpus.jsonl`, ~46 real messages with ground-truth
`expected_intent`) through the REAL Stage-0 classifier (worktree code +
`ClaudeCliBackend` → local authenticated `claude` CLI, Haiku) and prints:

- per-intent precision / recall,
- overall accuracy,
- `unknown` rate,
- `admin` false-positive count (expected != admin but predicted == admin),
- a dedicated "list my wikis NOT admin / routable" check,
- a confusion list of misses (text, expected, predicted).

**Not added to `make total-test`** (and excluded from coverage gating): it makes REAL,
paid, non-deterministic Haiku calls and depends on an authenticated `claude` CLI + network.
total-test must stay deterministic and offline. The harness is a manual quality gate run
before/after the prompt change; its header documents this. The deterministic timeout-fallback
behaviour IS covered by a normal unit test (no network).

Run once per change (note nondeterminism); re-run a flapping key case 2–3×.

## Files touched

- `prompts/classifier.md` — semver bump + additive rules (aisw-32p, aisw-zgf).
- `src/ai_steward_wiki/classifier/stage0.py` — timeout fallback (aisw-32p).
- `scripts/classifier_eval.py` — NEW offline eval harness.
- `tests/fixtures/classifier_corpus.jsonl` — NEW labelled corpus.
- `tests/unit/classifier/test_stage0.py` — NEW timeout-fallback unit test.
