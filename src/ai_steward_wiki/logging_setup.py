# FILE: src/ai_steward_wiki/logging_setup.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: structlog JSON-lines logging with correlation_id contextvar propagation, a boundary @traced decorator, and a threshold-gated anchored() I/O context manager.
#   SCOPE: configure_logging(level), bind_correlation_id(value),
#          get_correlation_id(), get_logger(name), traced(event_prefix, bind), anchored(event, threshold_ms).
#   DEPENDS: structlog, ai_steward_wiki.logging_events
#   LINKS: M-FOUNDATION-SETTINGS (reads log_level), M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   configure_logging - one-shot structlog setup, JSON renderer, ISO UTC timestamps
#   bind_correlation_id - set the correlation_id contextvar; returns Token for reset
#   reset_correlation_id - reset via Token returned from bind_correlation_id
#   get_correlation_id - read current correlation_id (None if unset)
#   get_logger - structlog.get_logger thin wrapper for typing
#   traced - PII-safe boundary decorator (sync+async); emits .start/.done/.error w/ duration_ms
#   anchored - threshold-gated I/O boundary ctx mgr; silent unless slow/failed (aisw-xbc)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-xbc: add threshold-gated anchored() I/O boundary context manager
#   PREVIOUS:    v0.0.2 - add @traced PII-safe boundary decorator (sync+async)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, MutableMapping
from contextvars import ContextVar, Token
from typing import Any, ParamSpec, TypeVar, cast

import structlog

from ai_steward_wiki.logging_events import ANCHOR_SLOW_SUFFIX, TRACED_ERROR_SUFFIX

__all__ = [
    "anchored",
    "bind_correlation_id",
    "configure_logging",
    "get_correlation_id",
    "get_logger",
    "reset_correlation_id",
    "traced",
]

_correlation_id: ContextVar[str | None] = ContextVar("aisw_correlation_id", default=None)

# Shared by traced() and anchored() to convert perf_counter_ns deltas to ms.
_NS_PER_MS = 1_000_000


def bind_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def _inject_correlation_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    if "correlation_id" not in event_dict:
        event_dict["correlation_id"] = _correlation_id.get()
    return event_dict


def configure_logging(level: str = "INFO", *, pii_processor: Any = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _inject_correlation_id,
    ]
    if pii_processor is not None:
        # M-OPS-PII (chunk 13): tier-1 DROP / tier-2 MASK BEFORE renderer.
        processors.append(pii_processor)
    processors += [
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


_P = ParamSpec("_P")
_R = TypeVar("_R")


def traced(
    *,
    event_prefix: str | None = None,
    bind: Mapping[str, Any] | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Boundary-observability decorator (PII-safe).

    Emits ``f"{prefix}.start"`` (INFO) on entry, ``f"{prefix}.done"`` (INFO,
    ``duration_ms: int``) on success, and ``f"{prefix}.error"`` (ERROR,
    ``duration_ms`` + ``exc_info``) on exception; the exception is re-raised.

    Does NOT introspect args, kwargs, or return values — never logs them.
    Callers may pass ``bind={...}`` for non-PII contextual fields; bind values
    are merged into ``structlog.contextvars`` for the call's lifetime so that
    inner logs inherit them.

    Default prefix is ``f"{func.__module__}.{func.__qualname__}"``.
    """
    bind_dict: dict[str, Any] = dict(bind) if bind else {}

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        prefix = event_prefix or f"{func.__module__}.{func.__qualname__}"
        log = structlog.get_logger(func.__module__)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
                t0 = time.perf_counter_ns()
                with structlog.contextvars.bound_contextvars(**bind_dict):
                    log.info(f"{prefix}.start")
                    try:
                        result = await cast(Any, func)(*args, **kwargs)
                    except BaseException:
                        dur = (time.perf_counter_ns() - t0) // _NS_PER_MS
                        log.error(f"{prefix}.error", duration_ms=int(dur), exc_info=True)
                        raise
                    dur = (time.perf_counter_ns() - t0) // _NS_PER_MS
                    log.info(f"{prefix}.done", duration_ms=int(dur))
                    return cast(_R, result)

            return cast(Callable[_P, _R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            t0 = time.perf_counter_ns()
            with structlog.contextvars.bound_contextvars(**bind_dict):
                log.info(f"{prefix}.start")
                try:
                    result = func(*args, **kwargs)
                except BaseException:
                    dur = (time.perf_counter_ns() - t0) // _NS_PER_MS
                    log.error(f"{prefix}.error", duration_ms=int(dur), exc_info=True)
                    raise
                dur = (time.perf_counter_ns() - t0) // _NS_PER_MS
                log.info(f"{prefix}.done", duration_ms=int(dur))
                return result

        return sync_wrapper

    return decorator


@contextlib.asynccontextmanager
async def anchored(
    event: str,
    *,
    threshold_ms: int,
    logger: Any = None,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> AsyncIterator[None]:
    """Threshold-gated boundary anchor for an external I/O call (aisw-xbc).

    Reuses the ``traced`` timing semantics but stays SILENT on the happy path
    (hybrid cost): emits ``f"{event}{ANCHOR_SLOW_SUFFIX}"`` (WARNING, ``duration_ms``)
    only when the call exceeds ``threshold_ms``, and always emits
    ``f"{event}{TRACED_ERROR_SUFFIX}"`` (ERROR, ``duration_ms`` + ``exc_info``) on
    exception before re-raising. A call that hangs forever logs NEITHER — that case
    is covered by the loop heartbeat gap + ``dump_asyncio_tasks`` frame.

    Logs only timing and the static ``event`` name — never args or payloads (PII-safe).
    """
    log = logger if logger is not None else structlog.get_logger("ai_steward_wiki.anchor")
    t0 = clock_ns()
    try:
        yield
    except asyncio.CancelledError:
        # Cancellation is normal shutdown / task teardown, NOT a request failure.
        # Logging it as .error would flood the journal on every graceful restart
        # and bury the genuine diagnostic events this anchor exists for.
        raise
    except BaseException:
        dur = (clock_ns() - t0) // _NS_PER_MS
        log.error(f"{event}{TRACED_ERROR_SUFFIX}", duration_ms=dur, exc_info=True)
        raise
    dur = (clock_ns() - t0) // _NS_PER_MS
    if dur > threshold_ms:
        log.warning(f"{event}{ANCHOR_SLOW_SUFFIX}", duration_ms=dur)
