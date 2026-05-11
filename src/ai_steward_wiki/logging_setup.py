# FILE: src/ai_steward_wiki/logging_setup.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: structlog JSON-lines logging with correlation_id contextvar propagation.
#   SCOPE: configure_logging(level), bind_correlation_id(value),
#          get_correlation_id(), get_logger(name).
#   DEPENDS: structlog
#   LINKS: M-FOUNDATION-SETTINGS (reads log_level)
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
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial structlog JSON setup with correlation_id contextvar
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextvars import ContextVar, Token
from typing import Any

import structlog

__all__ = [
    "bind_correlation_id",
    "configure_logging",
    "get_correlation_id",
    "get_logger",
    "reset_correlation_id",
]

_correlation_id: ContextVar[str | None] = ContextVar("aisw_correlation_id", default=None)


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
