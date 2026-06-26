---
feature: classifier-quality
date: 20260626
beads: [aisw-32p, aisw-zgf]
status: discovery
---

# Stage-0 Classifier Quality — Discovery

## Problem

Stage-0 (Haiku) classification quality is degrading real conversations on the prod WIKI bot.
Three concrete failure modes, all observed live (cid tg-2463, prod log
`/home/bgs/.claude/jobs/9299b18c/tmp/aisw_logs.jsonl`, n=90 classifications):

1. **(aisw-32p, P1) High `unknown` rate.** 32 / 90 messages (35.6%) classified
   `intent=unknown`, frequently at `confidence` 0.90–0.98. Concrete, classifiable
   messages ("Записал давление 128/82", "Сколько у меня акций Сбербанка") fall into
   `unknown`, producing confirm-friction and misroutes. Root cause: the prompt's intent
   definitions push the model to `unknown` whenever the **target domain/WIKI** is unclear,
   even though the **user action** (file a fact / ask about stored facts / search the web)
   is perfectly clear. Domain selection is a *downstream* stage's job, not Stage-0's.

2. **(aisw-32p, P1) Haiku timeout drops the message.** The Haiku CLI call times out at
   30 s (~3× in the corpus) → `classifier.stage0.error` raised as `ClassifierError` →
   pipeline replies with a generic "не удалось распознать" ack and the message is never
   processed. There is no graceful degradation: a transient timeout should not look like
   a permanent classification failure.

3. **(aisw-zgf, P2) "покажи все вики мои" → `admin`.** Benign read-only "list my WIKIs"
   phrasings are classified `intent=admin` (verified live, cid tg-2463), and the pipeline
   admin branch (`tg.pipeline.admin.declined`) short-circuits with `ACK_ADMIN_RU`. The
   message never reaches the Inbox router where the `list_wikis` path lives. Benign
   list/read/self queries must NOT be classified as `admin`.

## Evidence (prod)

- Intent distribution (what the classifier DID, n=90): `unknown` 32, `wiki_query` 15,
  `wiki_ingest` 11, `reminder` 10, `wiki_lint` 8, `digest` 7, `admin` 4, `web_task` 3.
- Real message texts harvested from per-WIKI `raw/*_text.md` on `vpn-gpu-1`
  (`.../workspace/wikis/6156629438/*/raw/*_text.md`, -mtime 5).

## Functional requirements

- **FR-1** Materially reduce the `unknown` rate on a labelled corpus of real messages,
  without sacrificing precision of the concrete intents.
- **FR-2** "покажи все вики мои" and variants ("какие у меня вики", "список вики",
  "покажи мои вики") MUST NOT be classified `admin`; they must be classified so they reach
  the Inbox router's `list_wikis` path (a *routable* Stage-0 intent: `wiki_ingest` or
  `unknown` — `unknown` is the catch-all that the heavy router resolves to `list_wikis`).
- **FR-3** A Stage-0 Haiku **timeout** MUST degrade gracefully: route to the Inbox router
  (or a safe default) instead of raising `ClassifierError` / dropping the message.
- **FR-4** No regression of existing intents: `smalltalk`, router `list_wikis`,
  `wiki_query`, `wiki_ingest`, `reminder`, `digest`, `web_task`, and REAL `admin`.

## Non-functional / constraints

- **NFR-1 (security, CRITICAL)** The classifier's `admin` INTENT only decides "is this an
  admin command?". The actual admin AUTHORIZATION is enforced SEPARATELY by an allowlist in
  `auth/admin.py` (`AdminService.assert_admin`, role-gated, tenancy-gated). Narrowing what
  the classifier calls `admin` does NOT weaken auth — the allowlist still gates every admin
  operation. Verified: `tg/pipeline.py` admin branch only sends `ACK_ADMIN_RU` (there is no
  real admin handler wired to the classifier intent). Change is scoped to **detection** only.
- **NFR-2** Surgical / additive prompt edits — no wholesale rewrite. Bump prompt semver.
- **NFR-3** Ru-only user-facing strings (MVP, D-032).

## Out of scope

- Stage-1a/1b router prompt changes (the `list_wikis` path already exists, aisw-rl1).
- Switching the Stage-0 backend or model.
- Adding a dedicated Stage-0 `list_wikis` intent (routing through `unknown` already works).
