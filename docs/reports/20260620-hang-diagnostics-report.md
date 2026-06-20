# Completion Report: event-loop hang diagnostics logging (aisw-xbc)

**Date:** 2026-06-20
**Type:** feature (observability, diagnostics-only)
**Module:** M-FOUNDATION-LOGGING + new M-OPS-OBSERVABILITY

## Why

On 2026-06-20 `aisw-bot` on vpn-gpu-1 froze entirely: the asyncio event loop stopped at
11:38:04 inside `deliver_output` (after `tg.pipeline.route.confirm_executed`), no polling and
no scheduler for 5+ minutes, no error logged. Root cause required out-of-band forensics
(`ssh`, `ss`, `getUpdates` 409 probe, `/proc/.../stack`, ephemeral `py-spy`). Goal: make the
**journal self-sufficient** for the hang class of bug — answer *when*, *where*, *what* from logs
alone. Diagnostics-only (no auto-recovery) per user directive; hybrid cost; stack dumps approved.

## What shipped

1. **Loop heartbeat + lag gauge** (`ops/observability.run_heartbeat`) — `runtime.loop.heartbeat`
   every ~20s (its ABSENCE marks the freeze instant); `runtime.loop.lag` WARNING over
   `obs_loop_lag_warn_ms`; rate-limited auto stack dump over `obs_loop_lag_dump_ms`.
2. **asyncio task-stack + faulthandler dump** (`dump_asyncio_tasks`, `install_sigusr1`,
   `enable_faulthandler`) — on `SIGUSR1` and on lag breach. Emits `runtime.diag.task_dump` with
   suspended coroutine frames as `file:line:func` only — **no argument/local values** (PII-safe).
3. **Threshold-gated boundary anchor** (`logging_setup.anchored`) — silent on the happy path,
   `*.slow` over threshold, `*.error` on failure; `CancelledError` re-raised silently (no
   false ERROR storm on shutdown). Applied to `AiogramSender.{send_message,edit_message_text,
   send_document}` and the audit write in `deliver_output`.
4. **Handler lifecycle** (`CorrelationMiddleware`) — `tg.update.handled` (duration_ms, failed)
   always fires + `tg.update.handler_slow` WARNING; `received` without `handled` ⇒ stuck handler.
5. **Config** (`Settings`, `AISW_` prefix): `obs_heartbeat_interval_s=20.0`,
   `obs_loop_lag_warn_ms=500`, `obs_loop_lag_dump_ms=5000`, `obs_io_slow_threshold_ms=1000`,
   `obs_handler_slow_threshold_ms=5000`, `obs_dump_min_interval_s=60.0`.
6. **Wiring** (`__main__`): `enable_faulthandler()` + `install_sigusr1(loop)` + heartbeat task at
   startup; cancel heartbeat + `remove_signal_handler(SIGUSR1)` in the shutdown block.
7. New event names centralised in `logging_events.py` (SSoT).

## Diagnostic mapping (next freeze)

- **WHEN** → last `runtime.loop.heartbeat` ts before the gap.
- **WHERE** → `*.slow`/`*.error` anchor, or a `*.start`-without-`*.done`; `tg.update.handled`
  missing for an `update_id`.
- **WHAT** → `runtime.diag.task_dump` frame (auto on lag, or `kill -USR1 <pid>` on demand) +
  faulthandler thread dump.

## Verification (evidence)

- `make lint` (ruff + ruff format + mypy --strict): **clean**.
- `grace lint --failOn errors`: **0 issues**.
- `make inv-lint`: **14/14**.
- `uv run pytest tests/unit`: **978 passed**, 1 pre-existing warning.
- New tests: 33 across `test_observability.py`, `test_logging_anchored.py`,
  `test_bot_anchors.py`, `test_middleware_lifecycle.py`, `test_output.py`.
- `ops/observability.py` coverage: **92%**.

## Review (Step 12, py-quality agent)

1 CRITICAL + 2 MAJOR + 2 MINOR + 2 NIT. Resolved:
- **CRITICAL** — `anchored` logged `CancelledError` as `.error` (false ERROR storm on graceful
  restart). Fixed: re-raise `CancelledError` silently + regression test.
- **MINOR** — `faulthandler.dump_traceback` now guarded by try/except; audit-write threshold
  wired from `settings.obs_io_slow_threshold_ms` through `_OutputDeliveryAdapter`.
- **MAJOR (heartbeat drift)** — kept relative (fixed-delay) schedule deliberately: `lag_ms`
  reports per-tick SPIKE delay (the freeze signal), not unbounded absolute drift; documented in code.
- **NIT** — `_NS_PER_MS` constant introduced (DRY).

## Out of scope (deferred)

systemd `WatchdogSec`/`sd_notify`/`Restart` (auto-recovery), aiogram session-timeout tuning,
dispatch changes, off-host metrics. `deliver_output` digest path in `scheduler/firing.py` keeps
the default audit threshold (== settings default).
