# FILE: src/ai_steward_wiki/storage/pragmas.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Apply SQLite PRAGMAs (D-006) to every connection of any engine.
#   SCOPE: WAL, synchronous=NORMAL, foreign_keys=ON, busy_timeout=5000.
#   DEPENDS: SQLAlchemy
#   LINKS: M-STORAGE
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   apply_sqlite_pragmas - attach a "connect" listener that runs PRAGMAs on every new connection
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - initial SQLite PRAGMA event listener (D-006)
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

# WAL retains journal between connections; the others must be set per-connection.
_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
)


def apply_sqlite_pragmas(engine: AsyncEngine | Engine) -> None:
    """Attach PRAGMA listener on connect. Idempotent across calls per engine."""
    sync_engine: Engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine

    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_conn: Any, _conn_record: Any) -> None:  # pragma: no cover - trivial
        cursor = dbapi_conn.cursor()
        try:
            for name, value in _PRAGMAS:
                cursor.execute(f"PRAGMA {name} = {value}")
        finally:
            cursor.close()
