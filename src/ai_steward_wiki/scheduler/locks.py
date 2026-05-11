# FILE: src/ai_steward_wiki/scheduler/locks.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: 3-tier lock manager — Semaphore → in-memory asyncio.Lock per WIKI →
#            on-disk fcntl.flock advisory. Strict acquire order (D-011, D-012).
#            Stale-lock recovery via os.kill(pid, 0).
#   SCOPE: WikiLockManager async context manager only. No content I/O.
#   DEPENDS: asyncio, fcntl, os, pathlib
#   LINKS: M-SCHEDULER, M-WIKI-RUNNER (chunk 7 will consume)
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LOCK_FILENAME - on-disk advisory lock file basename
#   WikiLockManager - 3-tier acquire/release with stale-PID recovery
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - chunk 4: 3-tier WikiLockManager (D-011, D-012)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

__all__ = [
    "LOCK_FILENAME",
    "WikiLockManager",
]

LOCK_FILENAME = ".wiki.lock"


class WikiLockManager:
    """Manages three-tier locking for per-WIKI exclusive writes.

    Acquire order (strict): semaphore → in-memory lock → fcntl.flock.
    Release order is reverse. Violations of order cause deadlock under contention.
    """

    def __init__(self, max_concurrent_cli: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_cli)
        self._mem_locks: dict[str, asyncio.Lock] = {}
        self._mem_locks_guard = asyncio.Lock()

    async def _get_mem_lock(self, wiki_id: str) -> asyncio.Lock:
        async with self._mem_locks_guard:
            lock = self._mem_locks.get(wiki_id)
            if lock is None:
                lock = asyncio.Lock()
                self._mem_locks[wiki_id] = lock
            return lock

    @asynccontextmanager
    async def acquire(self, wiki_id: str, wiki_path: Path) -> AsyncIterator[None]:
        await self._semaphore.acquire()
        mem_lock = await self._get_mem_lock(wiki_id)
        await mem_lock.acquire()
        lock_path = wiki_path / LOCK_FILENAME
        fd: int | None = None
        try:
            wiki_path.mkdir(parents=True, exist_ok=True)
            fd = await asyncio.to_thread(_flock_with_recovery, lock_path)
            yield
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(_release_flock, fd, lock_path)
            mem_lock.release()
            self._semaphore.release()


def _flock_with_recovery(lock_path: Path) -> int:
    """Acquire fcntl.flock; on contention check holder PID — recover if dead."""
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder_pid = _read_pid(fd)
            if holder_pid is not None and not _pid_alive(holder_pid):
                # Stale: blocking acquire after dead holder is gone.
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                # Live holder — block until released.
                fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _release_flock(fd: int, lock_path: Path) -> None:
    try:
        os.ftruncate(fd, 0)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        # Best-effort cleanup of empty lock file.
        try:
            if lock_path.exists() and lock_path.stat().st_size == 0:
                lock_path.unlink()
        except OSError:
            pass


def _read_pid(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 32).decode("ascii", errors="ignore").strip()
        return int(data) if data else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as e:
        return e.errno != errno.ESRCH
    return True
