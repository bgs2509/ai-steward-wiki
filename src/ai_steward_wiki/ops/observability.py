# FILE: src/ai_steward_wiki/ops/observability.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Event-loop hang diagnostics (diagnostics-only) — heartbeat+lag gauge, SIGUSR1 + lag-triggered asyncio/thread stack dumps.
#   SCOPE: compute_lag_ms, run_heartbeat, dump_asyncio_tasks, install_sigusr1, enable_faulthandler.
#   DEPENDS: asyncio, faulthandler, signal, traceback (stdlib); ai_steward_wiki.logging_setup, ai_steward_wiki.logging_events
#   LINKS: M-OPS-OBSERVABILITY, M-FOUNDATION-LOGGING, M-RUNTIME-WIRING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   compute_lag_ms - pure: scheduling drift in ms between expected and actual wake
#   run_heartbeat - background loop: emit heartbeat each tick; lag warning + rate-limited auto-dump over thresholds
#   dump_asyncio_tasks - snapshot suspended coroutine frames (file:line, NO arg values) → structlog event
#   install_sigusr1 - register SIGUSR1 → asyncio task dump + faulthandler thread dump (on-demand forensics)
#   enable_faulthandler - C-level thread-stack dumps that survive a wedged loop
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-xbc: initial hang-diagnostics observability module
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import faulthandler
import signal
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ai_steward_wiki.logging_events import (
    RUNTIME_DIAG_TASK_DUMP,
    RUNTIME_LOOP_HEARTBEAT,
    RUNTIME_LOOP_LAG,
)
from ai_steward_wiki.logging_setup import get_logger

if TYPE_CHECKING:
    from ai_steward_wiki.settings import Settings

__all__ = [
    "compute_lag_ms",
    "dump_asyncio_tasks",
    "enable_faulthandler",
    "install_sigusr1",
    "run_heartbeat",
]

_log = get_logger(__name__)


# START_CONTRACT: compute_lag_ms
#   PURPOSE: Event-loop scheduling drift (how much later than expected the loop woke).
#   INPUTS: { expected: float - planned wake time (loop clock), now: float - actual wake time }
#   OUTPUTS: { int - drift in milliseconds (>=0 in normal operation) }
#   SIDE_EFFECTS: none
# END_CONTRACT: compute_lag_ms
def compute_lag_ms(*, expected: float, now: float) -> int:
    return int((now - expected) * 1000)


# START_CONTRACT: dump_asyncio_tasks
#   PURPOSE: Snapshot every asyncio task's suspended coroutine frames for forensics.
#   INPUTS: { logger: BoundLogger - structlog sink (injectable for tests) }
#   OUTPUTS: { list[dict] - [{name, frames:[str]}] ; frames are file:line:func, never values }
#   SIDE_EFFECTS: emits RUNTIME_DIAG_TASK_DUMP at WARNING
# END_CONTRACT: dump_asyncio_tasks
def dump_asyncio_tasks(*, logger: Any = _log) -> list[dict[str, Any]]:
    # START_BLOCK_TASK_SNAPSHOT
    payload: list[dict[str, Any]] = []
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:
        # No running loop (e.g. called from a bare signal context) — nothing to dump.
        tasks = set()
    for task in tasks:
        frames: list[str] = []
        # get_stack() returns frame objects of a SUSPENDED coroutine; we render only
        # code locations (file:line + function), NEVER local/argument values (PII-safe).
        for frame in task.get_stack():
            code = frame.f_code
            frames.append(f"{code.co_filename}:{frame.f_lineno} in {code.co_name}")
        payload.append({"name": task.get_name(), "frames": frames})
    # END_BLOCK_TASK_SNAPSHOT
    logger.warning(RUNTIME_DIAG_TASK_DUMP, task_count=len(payload), tasks=payload)
    return payload


# START_CONTRACT: run_heartbeat
#   PURPOSE: Continuous proof-of-life for the event loop; its ABSENCE marks the freeze instant.
#   INPUTS: { settings: Settings - thresholds/cadence; sleep/clock/dump/monotonic/logger - injectable deps }
#   OUTPUTS: { None - runs until cancelled }
#   SIDE_EFFECTS: emits RUNTIME_LOOP_HEARTBEAT each tick; RUNTIME_LOOP_LAG + rate-limited dump over thresholds
# END_CONTRACT: run_heartbeat
async def run_heartbeat(
    settings: Settings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] | None = None,
    dump: Callable[[], Any] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    logger: Any = _log,
) -> None:
    tick_clock = asyncio.get_running_loop().time if clock is None else clock

    def _default_dump() -> Any:
        return dump_asyncio_tasks(logger=logger)

    do_dump = _default_dump if dump is None else dump

    interval = settings.obs_heartbeat_interval_s
    expected = tick_clock() + interval
    last_dump = float("-inf")
    # START_BLOCK_HEARTBEAT_LOOP
    while True:
        await sleep(interval)
        now = tick_clock()
        lag_ms = compute_lag_ms(expected=expected, now=now)
        logger.info(RUNTIME_LOOP_HEARTBEAT, lag_ms=lag_ms)
        if lag_ms > settings.obs_loop_lag_warn_ms:
            logger.warning(RUNTIME_LOOP_LAG, lag_ms=lag_ms)
        if lag_ms > settings.obs_loop_lag_dump_ms:
            stamp = monotonic()
            if stamp - last_dump >= settings.obs_dump_min_interval_s:
                do_dump()
                last_dump = stamp
        # Re-anchor to `now` (fixed-DELAY, not fixed-RATE): lag_ms reports the
        # per-tick delay since the previous wake — the signal for a freeze/blocking
        # SPIKE — instead of an ever-growing absolute drift that never recovers.
        expected = now + interval
    # END_BLOCK_HEARTBEAT_LOOP


# START_CONTRACT: install_sigusr1
#   PURPOSE: On-demand forensics — kill -USR1 <pid> dumps asyncio tasks + all thread stacks.
#   INPUTS: { loop: AbstractEventLoop - running loop, logger: BoundLogger }
#   OUTPUTS: { None }
#   SIDE_EFFECTS: registers a SIGUSR1 handler on the loop
# END_CONTRACT: install_sigusr1
def install_sigusr1(loop: asyncio.AbstractEventLoop, *, logger: Any = _log) -> None:
    def _handler() -> None:
        try:
            dump_asyncio_tasks(logger=logger)
        except Exception:
            logger.error("runtime.diag.task_dump.error", exc_info=True)
        # C-level thread dump → stderr → journald; survives a wedged loop. Guarded
        # so a closed/redirected stderr can never let the diagnostic path raise.
        try:
            faulthandler.dump_traceback(all_threads=True)
        except Exception:
            logger.error("runtime.diag.faulthandler.error", exc_info=True)

    loop.add_signal_handler(signal.SIGUSR1, _handler)


# START_CONTRACT: enable_faulthandler
#   PURPOSE: Enable C-level fault/thread dumps (independent of the asyncio loop state).
#   INPUTS: {}
#   OUTPUTS: { None }
#   SIDE_EFFECTS: faulthandler.enable() (writes to stderr on fatal signals)
# END_CONTRACT: enable_faulthandler
def enable_faulthandler() -> None:
    faulthandler.enable()
