---
feature: inbox-wiki-digest-presentation
bd_id: aisw-w3k
phase: Inbox-WIKI Phase-D.b.2a (presentation core)
status: design
date: 2026-05-12
depends_on: [aisw-oqq]
discovery: docs/superpowers/specs/20260512-inbox-wiki-digest-presentation-discovery.md
covers_fr: [FR-1, FR-2, FR-3, FR-4, FR-10]   # FR-5..9 → aisw-269 (Phase-D.b.2b)
approach: "Route fire_digest_job's delivery through the already-built tg.output.deliver_output(kind='digest'); grow set_digest_context by audit_session_maker; replace the one-line planner stub with a real jobs.db window query; rewrite prompts/digest.md to the D-024 HTML+TL;DR contract. No new module, no new dependency, no migration."
stack_decisions:
  - id: SD-1
    text: "tg.output.deliver_output already implements D-024's <b>-header section split (ChainSplitter._BOUNDARY_PATTERNS puts <b> first), (i/M) footers, and the >10000 send_document fallthrough. FR-2 needs NO new split code — only a digest-flavoured test + routing fire_digest_job through deliver_output. Rejected: a separate digest splitter (DRY violation)."
  - id: SD-2
    text: "set_digest_context gains one required dep: audit_session_maker: async_sessionmaker[AsyncSession] (audit.db, for the run_outputs row written by deliver_output). _digest_ctx becomes a 6-tuple. No summarizer added — deliver_output's default LengthCapSummarizer is used (consistent with _OutputDeliveryAdapter; a real Haiku post-step summarizer is a project-wide future, not digest-specific — YAGNI)."
  - id: SD-3
    text: "Planner context: a module-private async helper _build_planner_context(session, *, owner_telegram_id, window_hours, now_utc, tz) -> str in scheduler/firing.py (it already holds the jobs session, parse_job_payload, and the Job model — KISS, no new module). Queries jobs.Job rows: status=='scheduled' AND scheduled_at_utc IS NOT NULL AND scheduled_at_utc <= now_utc + window_hours, ordered by scheduled_at_utc; renders «- HH:MM — <message>» lines in the owner's tz, or «На ближайшие N ч ничего не запланировано.» when empty. tz comes from the digest job's own payload.recurrence.tz."
  - id: SD-4
    text: "prompts/digest.md rewritten to the D-024 contract: <b>📌 TL;DR</b> as the FIRST section (3-5 lines), then only-if-content <b>📅 Сегодня</b> / <b>💊 Лекарства</b> / <b>📈 Трекеры</b> / <b>📝 Обновления WIKI</b>; HTML whitelist <b><i><u><s><code><pre><a><blockquote>, escape < > & in literal text, NO MarkdownV2; one short line per item, no tables; empty → exactly «🌿 Сегодня дел нет.»; consume the «Запланировано…» planner block from the user message. semver 0.0.1 → 0.1.0."
  - id: SD-5
    text: "fire_digest_job: after the runner returns text → body = text.strip() or _DIGEST_EMPTY_RU; run_id = f'digest-{uuid4().hex[:12]}'; runs_dir = primary_path / 'data' / 'runs'; await deliver_output(sender=, chat_id=, telegram_id=owner_id, wiki_id=primary_id, run_id=run_id, text=body, runs_dir=runs_dir, audit_session_maker=audit_maker, kind='digest', job_id=job_id) — wrapped in the existing try/except → _digest_strike. Remove _DIGEST_TG_LIMIT and its truncation (deliver_output owns sizing). The _DIGEST_NO_WIKI_RU branch stays a plain sender.send_message (it is a control message, not a Claude output → no audit row)."
  - id: SD-6
    text: "__main__: set_digest_context(..., audit_session_maker=audit_maker); bump _DigestRunnerAdapter / wiring change-summary. The runner adapter is unchanged (it still returns raw text; deliver_output is called by fire_digest_job, not the adapter)."
log_anchors:
  - "scheduler.digest.delivered — now also carries n_messages, document_sent, run_id (from the DeliveryReceipt)."
  - "scheduler.digest.planner_context — new DEBUG anchor: job_id, owner_telegram_id, n_planned (count of jobs in the window)."
adr: "ADR-024 (digest presentation core) — records SD-1..SD-3 + the rejected alternatives. The Spec-WIKI D-024 'перенос в ADR' checkbox gets ticked."
out_of_scope_here:
  - "Actionable inline cards + callbacks (FR-5) → aisw-269."
  - "/expand, /digest_now (FR-6, FR-7) → aisw-269."
  - "Per-user section toggles + sessions.db 0002 (FR-8) → aisw-269."
  - "Named-subset WIKI selection (FR-9) → aisw-269."
  - "Real Haiku post-step summarizer wiring (project-wide, tracked elsewhere)."
files_touched:
  - src/ai_steward_wiki/scheduler/firing.py            # deliver_output routing, _build_planner_context, set_digest_context +audit_maker, _digest_ctx 6-tuple, drop _DIGEST_TG_LIMIT
  - src/ai_steward_wiki/__main__.py                    # set_digest_context(audit_session_maker=...), change-summary bump
  - prompts/digest.md                                  # D-024 contract rewrite, semver bump
  - docs/adr/ADR-024-digest-presentation.md            # new
  - docs/Spec-WIKI/decisions/D-024-digest-format.md    # tick the ADR checkbox
  - docs/knowledge-graph.xml / verification-plan.xml / development-plan.xml  # grace-refresh
  - tests/unit/scheduler/test_firing_digest.py         # extend: deliver_output called, planner-context query, strike on deliver failure
  - tests/unit/tg/test_output.py                       # add: digest >3500 splits at <b> headers w/ (i/M); >10000 → send_document
context7: "Not required — no new library; SQLAlchemy async select on Job and uuid4 are already used in firing.py."
---

# Design — Inbox-WIKI Phase-D.b.2a: digest presentation core (`aisw-w3k`)

## Approach (chosen)

**Route the digest through the existing output pipeline.** `aisw-oqq` left `fire_digest_job` doing a plain truncated `sender.send_message`. `tg.output.deliver_output` already does everything D-024/D-025 ask for on the *delivery* side: `<b>`-header section split (its `ChainSplitter` lists `<b>` as the top boundary), `(i/M)` continuity footers, the `>10000` → Haiku-summary + `send_document` fallthrough, atomic persist to `<wiki>/data/runs/<date>/<run_id>.md` with frontmatter, and the `audit.run_outputs` index row. So this phase is *wiring + the prompt contract + a real planner query*, not new presentation machinery.

### Why not the alternatives

- *A digest-specific splitter / formatter module* — rejected (DRY): `ChainSplitter` already prioritises `<b>` headers; a parallel implementation would drift.
- *Add a `HaikuSummarizer` to the digest registry now* — rejected (YAGNI): the real Haiku post-step summarizer is a project-wide concern (`_OutputDeliveryAdapter` also uses the `LengthCapSummarizer` default today); wiring it just for digests now is premature.
- *A `scheduler/digest_planner.py` module for the jobs query* — rejected (KISS): `firing.py` already imports `Job`, `parse_job_payload`, and owns the jobs session; one module-private helper is enough.

## Data / control flow

```
APScheduler CronTrigger → fire_digest_job(job_id)            [scheduler/firing.py]
  ├─ load Job, guard status=='scheduled', parse DigestPayload
  ├─ resolve_owner_wikis(owner) → [(primary_id, primary_path), *rest]   (empty → _DIGEST_NO_WIKI_RU, plain send, finish, no strike)
  ├─ planner_context = await _build_planner_context(jobs_session, owner_telegram_id=owner,
  │                       window_hours=payload.window_hours, now_utc=now, tz=payload.recurrence.tz)
  ├─ text = await runner(wiki_id=primary_id, wiki_path=primary_path,
  │            extra_add_dirs=[p for _,p in rest], planner_context=planner_context, correlation_id=f"digest:{job_id}")
  │     └─ _DigestRunnerAdapter → run_wiki_session(prompts/wiki.md + prompts/digest.md, --add-dir …, 600s)  [unchanged]
  ├─ body = text.strip() or "🌿 Сегодня дел нет."
  ├─ await deliver_output(sender=, chat_id=, telegram_id=owner, wiki_id=primary_id, run_id=f"digest-…",
  │            text=body, runs_dir=primary_path/"data"/"runs", audit_session_maker=audit_maker,
  │            kind="digest", job_id=job_id)                  [tg/output.py — unchanged]
  │     ├─ persist <primary>/data/runs/<date>/<run_id>.md (+ frontmatter)
  │     ├─ ≤3500 inline | ≤10000 ChainSplitter (<b>→blank→sentence) w/ (i/M) | >10000 LengthCapSummarizer + send_document
  │     └─ audit.run_outputs row
  ├─ on any exception in runner/deliver → _digest_strike (3 strikes → disabled + remove_job + DLQ)   [unchanged]
  └─ success → retry_count=0, finished_at, status stays 'scheduled'; log scheduler.digest.delivered(n_messages, document_sent, run_id)
```

## `_build_planner_context` sketch

```python
async def _build_planner_context(
    session: AsyncSession, *, owner_telegram_id: int, window_hours: int,
    now_utc: datetime, tz: str,
) -> str:
    horizon = now_utc + timedelta(hours=window_hours)
    rows = (await session.execute(
        select(Job).where(
            Job.owner_telegram_id == owner_telegram_id,
            Job.status == "scheduled",
            Job.scheduled_at_utc.is_not(None),
            Job.scheduled_at_utc <= horizon,
        ).order_by(Job.scheduled_at_utc)
    )).scalars().all()
    zone = ZoneInfo(tz)
    lines: list[str] = []
    for job in rows:
        try:
            p = parse_job_payload(job.payload)
        except ValidationError:
            continue
        title = getattr(p, "message", None) or getattr(p, "prompt_hint", None) or job.kind
        local = job.scheduled_at_utc.replace(tzinfo=UTC).astimezone(zone)
        lines.append(f"- {local:%H:%M} — {title}")
    if not lines:
        return f"На ближайшие {window_hours} ч ничего не запланировано."
    return f"Запланировано на ближайшие {window_hours} ч:\n" + "\n".join(lines)
```

(Recurring `digest_job` rows have `scheduled_at_utc IS NULL` and are excluded — they are cron-driven; surfacing "your digest itself fires daily at 09:00" is noise. One-shot `reminder_job` rows carry `scheduled_at_utc` and a `message` title.)

## `prompts/digest.md` (new contract — to be written verbatim in the plan)

`<b>📌 TL;DR</b>` (3-5 lines) → then any of `<b>📅 Сегодня</b>` / `<b>💊 Лекарства</b>` / `<b>📈 Трекеры</b>` / `<b>📝 Обновления WIKI</b>` that have content → one short line per item → HTML whitelist only, escape `< > &` in literal text, never MarkdownV2 → empty ⇒ exactly `🌿 Сегодня дел нет.` → use the «Запланировано…» block from the user message → read-only, never edit files. `semver: 0.1.0`.

## GRACE delta

- `M-SCHEDULER-FIRING` (`scheduler/firing.py`): `set_digest_context` signature `+audit_session_maker`; `_digest_ctx` 6-tuple; new private `_build_planner_context`; `_DIGEST_TG_LIMIT` removed; DEPENDS `+ai_steward_wiki.tg.output.deliver_output`. Version bump.
- `M-RUNTIME-WIRING` (`__main__.py`): `firing.set_digest_context(..., audit_session_maker=audit_maker)`. Change-summary bump.
- `verification-plan.xml`: `scheduler.digest.delivered` fields updated; new `scheduler.digest.planner_context` anchor; new test refs.
- `grace-refresh` (targeted) after code; ADR-024 added; D-024 ADR-checkbox ticked.

## Verification intent

1. `tests/unit/scheduler/test_firing_digest.py` — `fire_digest_job` calls `deliver_output` with `kind="digest"`, `runs_dir == primary_path/"data"/"runs"`, `job_id`, the owner id as `telegram_id`; planner context contains the in-window reminder's `HH:MM — message` and excludes an out-of-window one; a `deliver_output` exception → `_digest_strike` (retry_count bumped); empty WIKI set → `_DIGEST_NO_WIKI_RU` plain send, no strike; `DigestNotInitialisedError` if `set_digest_context` not called.
2. `tests/unit/tg/test_output.py` — a digest body with three `<b>` headers and length in (3500, 10000] splits into ≤3 parts cut at `<b>` boundaries, each ending `(i/M)`, tags balanced; a >10000 body → one summary message + `send_document`.
3. `make lint` clean; `make total-test` (unit + coverage ≥80%) green; integration left under `RUN_INTEGRATION=1` (real Claude — not in this PR's gate).
