---
feature: hang-diagnostics
bd_id: aisw-xbc
module_id: M-FOUNDATION-LOGGING
status: stable
date: 2026-06-20
risk: medium
fr:
  - FR-1: Event-loop heartbeat — a background task emits runtime.loop.heartbeat at a fixed cadence (~15-30s); its absence in the journal marks the freeze instant
  - FR-2: Event-loop lag gauge — heartbeat measures scheduling drift (lag_ms = actual_sleep - expected_sleep); a runtime.loop.lag event fires only when lag_ms exceeds a threshold (detects synchronous blocking)
  - FR-3: Boundary anchors on external I/O — AiogramSender.send_message/edit_message_text/send_document and audit-DB writes log start/done with duration_ms, threshold-gated (only slow/failed calls on the happy path)
  - FR-4: Handler lifecycle — CorrelationMiddleware emits tg.update.handled with duration_ms on exit (success or exception), plus a slow-handler warning over threshold; makes 'received without handled' observable per update_id
  - FR-5: On-demand stack dump — SIGUSR1 dumps asyncio.all_tasks() coroutine frames (task.get_stack) + thread stacks (faulthandler) to the journal
  - FR-6: Auto stack dump — the lag watchdog auto-triggers the FR-5 dump when lag_ms breaches a (higher) threshold, rate-limited to avoid log floods
  - FR-7: All new event names registered in logging_events.py (SSoT catalog)
nfr:
  - NFR-1: Diagnostics-only — no auto-restart, no systemd watchdog, no behavioural change to message handling
  - NFR-2: Hybrid cost — heartbeat is continuous but cheap and infrequent; all other anchors are threshold-gated so happy-path log volume stays near-zero
  - NFR-3: No PII — stack dumps print code frames (file:line + source line + coroutine name), never argument values; thresholds and durations carry no user content
  - NFR-4: Thresholds configurable via Settings (pydantic-settings, .env) with safe defaults; no magic numbers
  - NFR-5: mypy --strict + ruff + ruff format + grace lint clean; unit coverage for the observability module
  - NFR-6: Reuse existing traced decorator (logging_setup.py) for boundary anchors — DRY, no parallel timing implementation
constraints:
  - asyncio single event loop; heartbeat task created at startup alongside polling_task/consumer_task and cancelled in the shutdown block (__main__.py RUNTIME_SHUTDOWN)
  - structlog JSON → journald via PrintLoggerFactory (logging_setup.configure_logging) — new events are plain structlog calls
  - aiogram 3.15.0; start_polling default handle_as_tasks=True (handlers are background tasks); AiohttpSession default timeout 60s
  - SIGUSR1 handler must be installed on the running loop (loop.add_signal_handler) and must be safe to call repeatedly
risks:
  - Heartbeat itself adds a task that could mask issues if it shares a failing primitive → mitigated: heartbeat only sleeps + logs, no I/O, no locks
  - Stack dump under SIGUSR1 while loop is wedged must still run — faulthandler.register works at C level (signal-safe); asyncio.all_tasks() dump runs as a normal callback and may not fire if the loop is fully stuck → mitigated by ALSO using faulthandler (C-level) so at least thread stacks always dump
  - Threshold too low → log flood → mitigated by rate-limiting the lag/auto-dump events
  - faulthandler output is plain text to stderr (not structlog JSON) → acceptable for forensics; document the format mismatch
scope_in:
  - src/ai_steward_wiki/ops/observability.py (NEW — heartbeat+lag task, SIGUSR1 install, asyncio task-stack dump, faulthandler register)
  - src/ai_steward_wiki/logging_setup.py (ensure traced usable on bound methods; no API change expected)
  - src/ai_steward_wiki/logging_events.py (register new event-name constants — SSoT)
  - src/ai_steward_wiki/tg/bot.py (AiogramSender — threshold-gated boundary anchors on send/edit/send_document)
  - src/ai_steward_wiki/tg/middleware_correlation.py (tg.update.handled + duration_ms + slow warn)
  - src/ai_steward_wiki/tg/output.py (audit-write boundary anchor in deliver_output; split pre-send/post-send anchor)
  - src/ai_steward_wiki/__main__.py (start heartbeat task + install SIGUSR1 handler; cancel on shutdown)
  - src/ai_steward_wiki/settings (thresholds + cadence config)
  - tests/unit/ops/test_observability.py (NEW), tests/unit/tg/* (anchor + lifecycle assertions)
scope_out:
  - systemd WatchdogSec / sd_notify / Restart=always (auto-recovery — explicitly deferred, diagnostics-only per user)
  - aiogram session timeout tuning (separate fix, not logging)
  - Any change to how updates are dispatched (handle_as_tasks etc.)
  - Off-host log shipping / metrics backend (Prometheus etc.)
---

# Discovery: event-loop hang diagnostics logging (aisw-xbc)

## Symptom & motivation

On 2026-06-20 the bot (`aisw-bot.service` on vpn-gpu-1) froze **entirely**: the asyncio
event loop stopped progressing at 11:38:04 right after `tg.pipeline.route.confirm_executed`,
inside `deliver_output` (`output.py`). Neither polling nor the APScheduler jobs produced any
log line for 5+ minutes; the process stayed alive at ~0.6% CPU.

Diagnosing it required **out-of-band forensics**: `ssh`, `systemctl`, `ss -tnp`, a Telegram
`getUpdates` 409 probe, `/proc/<pid>/task/*/stack`, and an ephemeral `py-spy dump`. The
journal alone could not answer *when it froze*, *where*, or *what was stuck*. Worse, library
defaults contradicted the first hypotheses (aiogram has a 60s session timeout; `start_polling`
runs handlers as background tasks) — so the true freeze mechanism remained **unproven**.

## Real intent

Make the **journal self-sufficient** for the hang class of bug: from logs alone, answer
**when** (heartbeat gap), **where** (boundary anchor without its `.done`), and **what**
(task-stack dump frame). No more ssh+py-spy archaeology.

## What the current code lacks (verified)

1. `CorrelationMiddleware` (`middleware_correlation.py:81-84`) logs `tg.update.received` on
   entry only; `finally` just resets the correlation id — no exit/duration event.
2. `AiogramSender.send_message/edit_message_text/send_document` (`bot.py:102-149`) — thin
   wrappers with no timing, no anchors.
3. `deliver_output` (`output.py:361-409`) logs `tg.output.delivered` only AFTER send + DB
   write — a hang inside is invisible.
4. No event-loop heartbeat / lag gauge / task-stack dump / faulthandler anywhere.

## Existing asset to reuse (DRY)

`traced` decorator (`logging_setup.py:104`) already emits `.start/.done(duration_ms)/.error`
PII-safe. `logging_events.py` is the SSoT event-name catalog. New events go there.

## Scope decision

Diagnostics-only (user directive 2026-06-20): no auto-recovery layer. Hybrid cost: heartbeat
continuous+cheap, everything else threshold-gated. Stack dumps to journal approved by user
(PII risk low — frames not values).
