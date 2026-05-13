# FILE: src/ai_steward_wiki/storage/sessions/engine.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Async engine + sessionmaker for sessions.db (D-006) + slow-query log listener.
#   SCOPE: Engine factory only.
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.pragmas,
#            ai_steward_wiki.storage.slow_query, ai_steward_wiki.settings
#   LINKS: M-STORAGE-SESSIONS, M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Base - DeclarativeBase for sessions.db ORM models
#   build_engine - create AsyncEngine with SQLite PRAGMAs + slow-query log listener
#   build_sessionmaker - async_sessionmaker bound to engine, expire_on_commit=False
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-nrt (chunk 2): attach slow-query log listener.
#   PREVIOUS:    v0.0.2 - sessions engine factory with PRAGMA wiring
# END_CHANGE_SUMMARY

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ai_steward_wiki.settings import get_settings
from ai_steward_wiki.storage.pragmas import apply_sqlite_pragmas
from ai_steward_wiki.storage.slow_query import attach_slow_query_logging

__all__ = [
    "Base",
    "build_engine",
    "build_sessionmaker",
]


class Base(DeclarativeBase):
    """Declarative base for sessions.db."""


def build_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, future=True)
    apply_sqlite_pragmas(engine)
    attach_slow_query_logging(
        engine,
        db_name="sessions",
        threshold_ms=get_settings().storage_slow_query_threshold_ms,
    )
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
