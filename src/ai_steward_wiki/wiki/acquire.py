# FILE: src/ai_steward_wiki/wiki/acquire.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Lock-acquirer Protocol seam for the runner. Default adapter delegates
#            to scheduler.locks.WikiLockManager which already enforces the strict
#            acquire order semaphore → memlock → flock with stale-PID recovery.
#   SCOPE: LockAcquirer Protocol + WikiLockAdapter implementation.
#   DEPENDS: ai_steward_wiki.scheduler.locks
#   LINKS: M-WIKI-RUNNER, M-SCHEDULER, D-011, D-012
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LockAcquirer - Protocol; .acquire(wiki_id, wiki_path) -> async ctx mgr
#   WikiLockAdapter - default impl wrapping scheduler.locks.WikiLockManager
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 7: lock-acquirer adapter
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol

from ai_steward_wiki.scheduler.locks import WikiLockManager

__all__ = [
    "LockAcquirer",
    "WikiLockAdapter",
]


class LockAcquirer(Protocol):
    def acquire(self, wiki_id: str, wiki_path: Path) -> AbstractAsyncContextManager[None]: ...


class WikiLockAdapter:
    """Default LockAcquirer — delegates to scheduler.locks.WikiLockManager.

    The wrapped manager already implements semaphore → in-memory asyncio.Lock →
    fcntl.flock with PID-based stale recovery. Reverse-order release is built
    into its async context manager; nothing else to do here.
    """

    def __init__(self, manager: WikiLockManager) -> None:
        self._manager = manager

    @asynccontextmanager
    async def acquire(self, wiki_id: str, wiki_path: Path) -> AsyncIterator[None]:
        async with self._manager.acquire(wiki_id, wiki_path):
            yield
