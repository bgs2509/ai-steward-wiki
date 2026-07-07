# Completion Report — Classifier v2.0: 6 artifact-anchored intents

- **bd_id:** aisw-xi8
- **module:** M-CLASSIFIER-STAGE0 (cross-cutting: M-TG-PIPELINE-CLASSIFIER, M-STORAGE-JOBS, M-SCHEDULER-FIRING/CRON-USER/CONSUMER, M-RUNTIME-WIRING, M-WIKI-RUNNER, NEW M-SCHEDULER-MANAGE/M-CLASSIFIER-REGRESS)
- **date:** 2026-07-07
- **decision origin:** night-simulation of 100 family questions against the prod 9-intent classifier (71/13/16 verdicts) → `/best-questions`/`/best-approach` session → user's own principle "an intent exists only for a distinct artifact" → `do-feature`

## What changed

Replaced the Stage-0 classifier's 9 verb-multiplied intents (`reminder`/`wiki_ingest`/`wiki_query`/`wiki_lint`/`digest`/`web_task`/`smalltalk`/`admin`/`unknown`) with **6 artifact-anchored intents** (`wiki`/`job`/`web`/`chat`/`admin`/`unknown`); verbs/kinds moved into `WikiSlots`/`JobSlots` payload contracts. Haiku is now the SOLE classifier (ADR-035) — the classifying Python forks that competed with it (`_RECURRING_KEYWORDS` digest-punt, `_detect_digest_action`) are deleted; `tg/pipeline.py` is a flat 6-branch dispatch spine.

Fixes 5 of the simulation's measured defect clusters: daily check-in / recurring reminders no longer collapse into digest (#13/43/54/57/71); job management now exists (list/cancel/reschedule, #89/90); the chat-trap that swallowed diary facts and knowledge questions is closed by prompt negatives (#50/53); digest control is generic across all job kinds instead of fragile intent-dependent regex (#35/91/99); sub-threshold `job`/`admin` classifications can never reach the write-capable generic runner by construction (#78/96).

New capabilities: `job.kind=recurring` (fixed-text cron reminder, no LLM at fire time — medication-reminder determinism) and `job.kind=check_in` (bot generates a fresh question on schedule, ru fallback on CLI failure); `scheduler/manage.py` (needle-match disambiguation + destructive confirm for cancel/reschedule); a committed 100-case regression corpus + `make classifier-regress` harness (mandatory manual gate before any future `prompts/classifier.md` change).

**Production side-effect found during Phase A**: Stage-0's unbounded extended-thinking budget made complex phrasings generate 3–6k invisible tokens (25–50s), systematically crossing the 30s CLI timeout. Fixed via `ClaudeCliBackend.max_thinking_tokens=0` for Stage-0 only (time-parse fallback keeps thinking — date arithmetic degrades without it). Net effect: Stage-0 latency drops from ~15–30s to ~2–3s per message for every user.

**Two HIGH-severity bugs found by Step-12 review** (invisible to green `total-test`/`classifier-regress` because test fixtures shared the same wrong assumptions) and fixed before close:
1. `manage.py` filtered jobs by kind literal `"digest"`, but `firing.create_digest_job` persists `"digest_job"` — digest job list/cancel/reschedule could never find a real digest row, silently defeating the #35/91/99 fix above.
2. `check_in` jobs transitioned to a terminal `finished`/`failed` status on first fire and never reset to `scheduled` — the CronTrigger kept firing but every fire after the first silently no-op'd. Fixed by rewinding status after delivery (mirrors `recurring_reminder`'s non-terminal design, DEC-7).

## Files (highlights — 70 files changed across 34 commits)

- `src/ai_steward_wiki/classifier/schema.py` — `Intent` v2 enum, `WikiSlots`/`JobSlots`/`parse_slots`.
- `src/ai_steward_wiki/classifier/backend.py` — `max_thinking_tokens`, `unwrap_fenced_json`-based envelope parsing.
- `prompts/classifier.md` — 2.0.0 (taxonomy swap, verbatim rule, chat-trap negatives, catalog worked example).
- `src/ai_steward_wiki/tg/pipeline.py` — flat dispatch spine, sub-threshold gate, `_is_routable`, job list/cancel/reschedule/create-confirm handlers, `_handle_chat`/`_handle_admin`.
- `src/ai_steward_wiki/scheduler/manage.py` (NEW) — `list_owner_jobs`/`match_jobs_by_needle`/`cancel_job`/`reschedule_once`/`reschedule_recurring`.
- `src/ai_steward_wiki/scheduler/firing.py`, `cron_user.py`, `consumer.py` — `recurring_reminder`/`check_in` firing bridges, ru fallback.
- `src/ai_steward_wiki/storage/jobs/payloads.py` — `RecurringReminderPayload`/`CheckInPayload` (additive union).
- `src/ai_steward_wiki/wiki/runner.py`, `__main__.py` — `action` Protocol widening (ADR-035), Stage-0 thinking-off wiring.
- `scripts/classifier_regress.py` + `tests/corpus/classifier/questions.json` (NEW) — regression harness + 100-case corpus.
- ~24 test files migrated to Intent v2 (mechanical DEC-14 mapping) across `tests/unit/tg/`, `tests/unit/scheduler/`, `tests/integration/`.
- `docs/adr/ADR-035-*.md` — records the deliberate ADR-034 Protocol-immutability deviation.

## Verification (evidence)

- `make total-test` → **1213 passed**, 0 failed, coverage 87.62%; ruff/mypy --strict/grace-lint/inv-lint all clean.
- `make classifier-regress` → **GATE: PASS**, intent 100/100, intent+action+kind 100/100 (on the corpus that measured 93/100 for the v1 classifier).
- `make grace-lint` → 0 issues (103 governed files, 3 XML).
- Independent verification discipline followed throughout: every phase's gate was re-run by the orchestrator directly (not trusted from subagent reports); a Step-12 review pass (dedicated agent, full-diff read) found the 2 HIGH + 4 MEDIUM/LOW defects above, each independently confirmed via direct code reads before dispatching fixes.
- `RUN_INTEGRATION=1 pytest tests/integration` — deferred (this suite guards against recursive `claude` CLI invocation and self-skips when run from inside a Claude Code session by design; must be run from a plain shell before deploy per the project's atomic-deploy convention).

## Known limitations / deferred

- **aisw-7t9** (P2, new) — `cron_user`'s own recurring jobs likely share check_in's pre-existing status-lifecycle bug (fires once, never resets); found as a byproduct of fixing check_in, predates aisw-xi8, needs its own investigation+fix.
- **aisw-4zt** (P3) — Haiku normalizes Russian case endings in the `needle`/`text` verbatim slots (e.g. "сводку"→"сводка") on 4/100 corpus cases; non-gating (informational metric), `needle`'s real consumer already does fuzzy token-matching so exact verbatim isn't architecturally required there.
- **aisw-xqz / aisw-oub** (P4) — pre-existing knowledge-graph `<depends>` gap and unescaped `&` in two XML files; noticed during GRACE Plan, unrelated to this feature.
- **aisw-8nb** (P4) — `M-TG-CALLBACKS` knowledge-graph node missing (same class of gap as `M-DIGEST-CARDS`, fixed opportunistically during this feature's GRACE-refresh); pre-existing, from the unrelated aisw-163 feature.
- 100-question family simulation surfaced 3 more defect clusters not in this feature's scope (product-level UX gaps, not classifier-taxonomy bugs) — tracked only in project memory, not yet as bd issues: reminder undo/list ergonomics beyond the new `job` management surface, and voice/photo intent edge cases outside the 100-question text corpus.
