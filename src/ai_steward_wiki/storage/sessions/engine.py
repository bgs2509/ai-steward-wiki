# FILE: src/ai_steward_wiki/storage/sessions/engine.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Async engine + sessionmaker for sessions.db (D-006).
#   SCOPE: Engine factory only.
#   DEPENDS: SQLAlchemy.asyncio, ai_steward_wiki.storage.pragmas
#   LINKS: M-STORAGE-SESSIONS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ai_steward_wiki.storage.pragmas import apply_sqlite_pragmas


class Base(DeclarativeBase):
    """Declarative base for sessions.db."""


def build_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, future=True)
    apply_sqlite_pragmas(engine)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
