---
feature: hang-diagnostics
bd_id: aisw-xbc
module_id: M-FOUNDATION-LOGGING
status: stable
date: 2026-06-20
stack:
  - library: structlog
    version: 24.4.0 (uv.lock)
    used_for: JSON-lines events for heartbeat/lag/anchors/lifecycle; reuse traced + logging_events SSoT
  - library: aiogram
    version: 3.15.0 (uv.lock)
    used_for: AiogramSender boundary anchors; CorrelationMiddleware lifecycle event
  - library: asyncio (stdlib)
    version: py3.11+
    used_for: heartbeat task, loop.add_signal_handler(SIGUSR1), asyncio.all_tasks()+Task.get_stack() dump
  - library: faulthandler (stdlib)
    version: py3.11+
    used_for: C-level thread-stack dump on SIGUSR1 (works even if loop callbacks are wedged)
decisions:
  - D-local-1: New module src/ai_steward_wiki/ops/observability.py owns ALL diagnostics — heartbeat+lag loop, SIGUSR1 install, asyncio task-stack dump, faulthandler register. Single SoC home, testable in isolation.
  - D-local-2: Heartbeat measures lag via monotonic drift — loop expected to wake every interval; lag_ms = (actual_monotonic - expected_monotonic)*1000. Emits runtime.loop.heartbeat each tick (INFO, cheap) and runtime.loop.lag (WARNING) only when lag_ms > obs_loop_lag_warn_ms.
  - D-local-3: Auto-dump — when lag_ms > obs_loop_lag_dump_ms (a higher bar), trigger the same dump as SIGUSR1, rate-limited by obs_dump_min_interval_s.
  - D-local-4: Boundary anchors reuse the existing traced decorator (logging_setup.py:104) but with a threshold wrapper — log .done only when duration_ms > obs_io_slow_threshold_ms, ALWAYS log .error. Avoids happy-path noise (hybrid cost). Applied to AigramSender methods + the audit write in deliver_output.
  - D-local-5: Handler lifecycle — CorrelationMiddleware.__call__ wraps handler in try/finally measuring duration; emits tg.update.handled (duration_ms, failed:bool) always (one line per update is acceptable, low traffic), and a tg.update.handler_slow WARNING when over obs_handler_slow_threshold_ms.
  - D-local-6: Stack dump format — asyncio tasks dumped as structlog event runtime.diag.task_dump with a list of {name, frames:[file:line:func + source-line]} (NO arg values). faulthandler.dump_traceback(all_threads=True) writes plain text to stderr→journald. Both fire on SIGUSR1; faulthandler is the C-level fallback that survives a wedged loop.
  - D-local-7: Lifecycle wiring in __main__ — create heartbeat task next to polling_task/consumer_task; install SIGUSR1 via loop.add_signal_handler; faulthandler.enable() at startup; cancel heartbeat + remove signal handler in the RUNTIME_SHUTDOWN finally block.
  - D-local-8: Config via Settings (AISW_ prefix), mirroring storage_slow_query_threshold_ms precedent — obs_heartbeat_interval_s=20.0, obs_loop_lag_warn_ms=500, obs_loop_lag_dump_ms=5000, obs_io_slow_threshold_ms=1000, obs_handler_slow_threshold_ms=5000, obs_dump_min_interval_s=60.0. No magic numbers.
  - D-local-9: "New event-name constants centralised in logging_events.py (SSoT): RUNTIME_LOOP_HEARTBEAT, RUNTIME_LOOP_LAG, RUNTIME_DIAG_TASK_DUMP, TG_UPDATE_HANDLED, TG_UPDATE_HANDLER_SLOW, plus IO anchor prefixes."
---

# Design: event-loop hang diagnostics logging

## Architecture (diagnostics-only)

```
__main__._amain()
 ├─ faulthandler.enable()                      # C-level crash/thread dumps
 ├─ observability.install_sigusr1(loop)        # kill -USR1 <pid> → dump
 ├─ heartbeat_task = create_task(             # FR-1/FR-2/FR-6
 │     observability.run_heartbeat(settings))  #  tick → heartbeat; lag>warn → lag; lag>dump → auto task_dump
 ├─ polling_task / consumer_task / stop_task   # (existing)
 └─ finally: cancel heartbeat_task, remove signal handler   # RUNTIME_SHUTDOWN
```

## The three diagnostic questions → which signal answers it

1. **WHEN did it freeze?** → `runtime.loop.heartbeat` simply stops. Last heartbeat ts = freeze instant. (FR-1)
2. **WHERE / is it a sync block?** → `runtime.loop.lag{lag_ms}` spikes before/at the freeze; a boundary anchor `*.start` with no matching `*.done` over threshold names the call. (FR-2, FR-3)
3. **WHAT is stuck?** → `runtime.diag.task_dump` (asyncio suspended frames) + faulthandler thread dump, on SIGUSR1 or auto on lag. (FR-5, FR-6)

## Heartbeat + lag (core)

```python
async def run_heartbeat(s: Settings) -> None:
    loop = asyncio.get_running_loop()
    interval = s.obs_heartbeat_interval_s
    expected = loop.time() + interval
    while True:
        await asyncio.sleep(interval)
        now = loop.time()
        lag_ms = int((now - expected) * 1000)
        _log.info(RUNTIME_LOOP_HEARTBEAT, lag_ms=lag_ms)         # cheap, every tick
        if lag_ms > s.obs_loop_lag_warn_ms:
            _log.warning(RUNTIME_LOOP_LAG, lag_ms=lag_ms)
        if lag_ms > s.obs_loop_lag_dump_ms:
            _maybe_dump(s)                                        # rate-limited auto-dump
        expected = now + interval
```

Note: if the loop is *fully* wedged, `run_heartbeat` itself won't tick — that's the point: the
heartbeat GAP is the signal. The auto-dump catches the *pre-freeze* lag ramp; SIGUSR1 +
faulthandler (C-level) catch the *already-frozen* state on demand.

## Boundary anchor (threshold-gated, reuses traced semantics)

A thin `anchored(event, threshold_ms)` async context manager (or a traced variant) wraps each
external call: measures `perf_counter`, logs `.error` always, logs `.done(duration_ms)` only if
over threshold. Applied to `AiogramSender.send_message/edit_message_text/send_document` and the
`_record_run_output` await inside `deliver_output`.

## PII / safety

Task dump prints `task.get_stack()` frames → file:line:func + the source code line; never the
values of locals/args. faulthandler likewise. No user content, no tokens (tokens come from
`.env`, never appear as source literals). Diagnostics-only: nothing restarts or mutates state.

## Out of scope (deferred)

systemd watchdog / Restart, aiogram session-timeout tuning, dispatch changes, metrics backends.
