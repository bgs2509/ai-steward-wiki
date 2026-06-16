# Completion report — Inbox create/ingest fix chain (2026-06-16)

**Date:** 2026-06-16 · **Branch:** `master` (pushed + deployed) · **Driver:** feature-workflow (per-fix)

## Goal

Make a user-pasted document of a *new* domain reliably become a properly-schema'd
WIKI with its data ingested — end to end — through the Telegram → classify → route
→ create → schema → ingest pipeline. Six defects were found and fixed in sequence,
each diagnosed from production journald logs and deployed to the VPS.

## Fixes (all closed, deployed to master)

| bd_id | Commit | Problem → Fix |
|-------|--------|---------------|
| aisw-t6w | 38fc8ad | Ingest silent-data-loss: writing runs ran under `--permission-mode dontAsk` with no `--allowedTools`, so Write/Edit were denied; bot reported `exit_code 0` while CSV/log.md were never written. → `WRITE_TOOLS` allow-list on writing runs (router/classifier stay read-only) + `permission_denials` surfaced on `WikiRunResult` + WARNING `wiki.run.permission_denied`. |
| aisw-db6 | 1f0469f | Empty schema: `create_wiki` wrote frontmatter-only `CLAUDE.md` (managed zone empty in **every** WIKI), so the model never saw the `## Data layout` and improvised `pages/`; the `## Inbox hint` was empty too → hint fast-path always missed. → `create_wiki` renders the template managed zone (`load_template`+`render_v2`); `repair_managed_zone` backfill for existing WIKIs; all 8 templates gained an explicit file-name rule. |
| aisw-b50 | 29a7956 | Fixed presets don't scale to arbitrary domains. → LLM schema generation at create for unknown domains: archetype + topic sections (`prompts/schema-gen.md`, one Sonnet turn), stored `template_id=_generated` (co-evolves, not clobbered); fallback `_default` on failure. Realizes D-017 Variant D. |
| aisw-378 | 06a387f | Telegram splits a long paste into separate messages; aiogram dispatches each concurrently → one document fragments into different WIKIs (Medical + Investment). → `InboxAggregator` debounces text per chat 3s into ONE classify/route (epoch-guard, "⏳ Думаю…" loader lifecycle); hint fast-path skips long text (`MAX_FASTPATH_CHARS=600`). |
| aisw-aca (ph.1) | 80417c8 | A new-domain document never reached the create path: router chose `clarify`; explicit "создай X" was Stage-0-classified `admin` → generic root run that freelance-created a malformed WIKI (hallucinated frontmatter, empty scaffold, data lost). → `inbox.md` router proposes `create_wiki`+name for self-contained new-domain material (not clarify); `intent=admin` tamed (safe `ACK_ADMIN_RU`, never a root run). The existing CREATE_WIKI confirm path then creates + schema_gen + ingests the document. |
| aisw-zpn | a142d45 | Large-document create+ingest hit the 300s budget mid-write → "failed" message despite ~18 files written, no resume. → separate `wiki_ingest_timeout_s=600`; on `WikiRunnerTimeoutError`, detect partial data (`_wiki_has_ingested_content`) → honest "занёс частично, пришли ещё раз" (`status="partial"`); `build_ingest_prompt` "дополни-не-дублируй" for soft-resume. |

## End-to-end outcome (verified on VPS, coal-industry document)

The same 12 009-char coal report was re-sent after each fix. Final behaviour:
1. **Aggregation** — two split messages → one `flush n_parts=2 chars=12009`. ✅
2. **Routing** — `intent: create_wiki, target_wiki: Угольная-отрасль-WIKI` (not clarify/admin). ✅
3. **Create** — `wiki.lifecycle.created` via the proper `lifecycle.create_wiki`. ✅
4. **Schema** — `wiki.schema_gen.applied`: real generated schema (`template_id=_generated`, real sha, sections `## Компании-производители`, `## Виды угля`, `## Временные ряды`, `## Аналитика`). ✅
5. **Data** — real ingest: `metrics/{production,exports,market}.csv` + `pages/companies/*.md` ×8 + `coal-types/*.md` ×4 + `regions/*.md` ×3. ✅
6. **Timeout** — the run hit 300s mid-ingest (now 600s + honest partial + soft-resume). ✅ fixed in aisw-zpn.

## Key decisions

- **Schema authority is the per-WIKI `CLAUDE.md` managed zone** (Karpathy "schema" layer). Known domains → static preset; unknown → LLM-generated `_generated` schema that backfill/repair skip so it co-evolves. → ADR-029.
- **Aggregate first, route once.** Split/burst messages are debounced into a single logical input before classify/route. → ADR-030.
- **"create vs route" is the router's decision, not Stage-0's.** Stage-0 has no `create_wiki` intent (and shouldn't — it can't see the WIKI catalog); the document drives the create proposal, so no fragile "создай X" command + `admin` is tamed. → ADR-031.
- **Permission posture:** writing runs keep `dontAsk` + an explicit additive `--allowedTools` (Write/Edit/MultiEdit); Bash/Read unchanged; router/classifier stay read-only. (aisw-t6w)

## Deferred follow-ups (open bd)

- **aisw-9sn** — Phase 2: user name-override on the create-confirm card (FSM/button; interacts with the aggregator debounce).
- **aisw-90t** — Phase 2: aggregate voice/photo series (needs `pipeline.on_voice`/`on_photo` preprocess/route split).
- **aisw-0d3** — mitigated (document auto-proposes create → no retype); residual L2-dedup-on-identical-text tuning if it recurs.

## Cleanup performed on VPS

- Misrouted coal `pending_confirms` 11/12 cancelled; orphan raw moved to `_legacy_*`.
- Malformed first `Russian-Coal-WIKI` (hallucinated frontmatter, no data) hard-deleted to backup (kept out of `_trash` to avoid near-dup on re-create).
