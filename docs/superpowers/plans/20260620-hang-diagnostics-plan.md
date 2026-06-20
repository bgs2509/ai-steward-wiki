# Implementation Plan: event-loop hang diagnostics logging (aisw-xbc)

> SSoT for execution. TDD: RED → GREEN → REFACTOR. Diagnostics-only.
> Module: M-FOUNDATION-LOGGING + new M-OPS-OBSERVABILITY.

## Task order (respects DEPENDS)

### T1 — Event-name constants (SSoT) — `logging_events.py`
- Add constants: `RUNTIME_LOOP_HEARTBEAT="runtime.loop.heartbeat"`, `RUNTIME_LOOP_LAG="runtime.loop.lag"`,
  `RUNTIME_DIAG_TASK_DUMP="runtime.diag.task_dump"`, `TG_UPDATE_HANDLED="tg.update.handled"`,
  `TG_UPDATE_HANDLER_SLOW="tg.update.handler_slow"`, `IO_ANCHOR_PREFIX="tg.io"` (or per-method).
- No test (pure constants); covered indirectly by downstream tests.

### T2 — Settings thresholds — `settings.py`
- RED: `tests/unit/test_settings.py` (or extend) asserts defaults: `obs_heartbeat_interval_s==20.0`,
  `obs_loop_lag_warn_ms==500`, `obs_loop_lag_dump_ms==5000`, `obs_io_slow_threshold_ms==1000`,
  `obs_handler_slow_threshold_ms==5000`, `obs_dump_min_interval_s==60.0`.
- GREEN: add fields to `Settings` (AISW_ prefix inherited).

### T3 — Observability module (core) — NEW `ops/observability.py` + MODULE_CONTRACT
- RED: `tests/unit/ops/test_observability.py`:
  - `compute_lag_ms(expected, now)` → int ms (pure, deterministic).
  - `run_heartbeat`: with a fake clock + injected logger, one tick emits `RUNTIME_LOOP_HEARTBEAT`;
    lag>warn emits `RUNTIME_LOOP_LAG`; lag>dump calls the dump fn (spy); dump rate-limited by `obs_dump_min_interval_s`.
  - `dump_asyncio_tasks()` returns a structured payload `[{name, frames:[...]}]` with NO arg values
    (assert frames are str file:line, assert payload contains the test coroutine name).
- GREEN: implement `compute_lag_ms`, `run_heartbeat(settings, *, dump=...)`, `dump_asyncio_tasks()`,
  `_maybe_dump` (rate-limit via monotonic), `install_sigusr1(loop, settings)`, `enable_faulthandler()`.
- KISS: each fn ≤ ~30 lines; injectable clock/logger/dump for testability (DIP).

### T4 — Boundary anchor helper — `logging_setup.py` (or observability)
- RED: `tests/unit/test_traced.py` (extend): an `anchored(event, threshold_ms)` async ctx mgr logs
  `.done` only when over threshold, ALWAYS logs `.error` on exception (re-raises). Reuse traced semantics.
- GREEN: implement thin threshold wrapper reusing perf_counter pattern from `traced`.

### T5 — AiogramSender anchors — `tg/bot.py`
- RED: `tests/unit/tg/test_bot_anchors.py`: a fake bot whose send sleeps > threshold → `.done` logged;
  fast send → no `.done`; raising send → `.error` logged + raised.
- GREEN: wrap `send_message/edit_message_text/send_document` bodies with `anchored(...)`.

### T6 — Handler lifecycle — `tg/middleware_correlation.py`
- RED: extend `tests/unit/tg/test_middleware_correlation*.py`: handler success → `TG_UPDATE_HANDLED`
  with `duration_ms` and `failed=False`; handler raising → `TG_UPDATE_HANDLED failed=True` then re-raise;
  slow handler → `TG_UPDATE_HANDLER_SLOW` warning.
- GREEN: wrap `await handler(...)` in try/except/finally measuring `perf_counter`; emit inside the
  existing `bound_contextvars` block so correlation_id/update_id are attached.

### T7 — deliver_output audit-write anchor — `tg/output.py`
- RED: assert the `_record_run_output` await is wrapped so a slow/failing DB write logs an anchor.
- GREEN: wrap with `anchored(...)`; keep existing `tg.output.delivered` final event.

### T8 — Wiring — `__main__.py`
- RED (integration-ish unit): a startup helper installs SIGUSR1 + faulthandler + creates heartbeat task;
  shutdown cancels it. Assert task created and cancelled (no leak).
- GREEN: in `_amain`: `enable_faulthandler()`, `install_sigusr1(loop, settings)`,
  `heartbeat_task = create_task(run_heartbeat(settings), name="aisw.heartbeat")`; in RUNTIME_SHUTDOWN
  finally: cancel heartbeat_task + `loop.remove_signal_handler(SIGUSR1)`.

### T9 — Full gate
- `make lint` (ruff + format + mypy + grace lint) clean.
- `uv run pytest tests/unit` green, coverage on new module ≥ 80%.
- `grace-refresh` to sync knowledge-graph + verification-plan.

## Self-review vs FR/NFR
- FR-1/2 → T3 (heartbeat+lag). FR-3 → T4/T5/T7. FR-4 → T6. FR-5/6 → T3 (dump + auto-trigger). FR-7 → T1.
- NFR-1 diagnostics-only → no restart code anywhere (verify in review). NFR-2 hybrid → thresholds (T2).
- NFR-3 no PII → T3 dump test asserts no values. NFR-4 config → T2. NFR-5 lint/cov → T9. NFR-6 DRY → T4 reuses traced.
- No placeholders. Order respects DEPENDS (T1/T2 → T3 → T4 → T5/T6/T7 → T8 → T9).
