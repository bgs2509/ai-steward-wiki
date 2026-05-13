# FILE: src/ai_steward_wiki/storage/slow_query.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Attach a SQLAlchemy slow-query logger to an AsyncEngine.
#   SCOPE: attach_slow_query_logging(engine, *, db_name, threshold_ms).
#          Emits storage.slow_query (WARNING) when one cursor execute exceeds
#          threshold_ms. Logs db_name, statement_sha8 (sha256[:8] of the raw
#          SQL statement, parameter values are NEVER passed to the listener),
#          duration_ms. NEVER logs raw SQL or parameter values.
#   DEPENDS: sqlalchemy.event, hashlib, time, structlog
#   LINKS: M-STORAGE-JOBS, M-STORAGE-AUDIT, M-STORAGE-SESSIONS, M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   attach_slow_query_logging - attach before/after_cursor_execute listeners to one engine
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-nrt (chunk 2): slow-query log listener (sha8 only, PII-safe)
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_steward_wiki.logging_events import STORAGE_SLOW_QUERY
from ai_steward_wiki.logging_setup import get_logger

__all__ = ["attach_slow_query_logging"]

_LOG = get_logger(__name__)
_T0_ATTR = "_aisw_slow_query_t0"


def attach_slow_query_logging(
    engine: AsyncEngine,
    *,
    db_name: str,
    threshold_ms: int,
) -> None:
    """Attach before/after_cursor_execute listeners that log slow queries.

    Logs only metadata: db_name, statement_sha8 (sha256[:8] hex of the raw SQL
    statement string — parameters are passed separately by SQLAlchemy and are
    NEVER read here), duration_ms. Never logs raw SQL or parameter values.
    """
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(
        _conn: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        context: Any,
        _executemany: bool,
    ) -> None:
        setattr(context, _T0_ATTR, time.perf_counter_ns())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor_execute(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        context: Any,
        _executemany: bool,
    ) -> None:
        t0 = getattr(context, _T0_ATTR, None)
        if t0 is None:
            return
        duration_ms = (time.perf_counter_ns() - t0) // 1_000_000
        if duration_ms <= threshold_ms:
            return
        sha8 = hashlib.sha256(statement.encode("utf-8", "replace")).hexdigest()[:8]
        _LOG.warning(
            STORAGE_SLOW_QUERY,
            db_name=db_name,
            statement_sha8=sha8,
            duration_ms=int(duration_ms),
        )
