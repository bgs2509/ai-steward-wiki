# FILE: src/ai_steward_wiki/auth/sighup.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Hot-reload coordinator — SIGHUP primary + watchdog 500ms debounce (D-031).
#   SCOPE: AllowlistReloader, install_sighup_handler, build_watchdog_observer.
#   DEPENDS: asyncio, signal, hashlib, watchdog, ai_steward_wiki.auth.{users_toml,allowlist}
#   LINKS: D-031, M-AUTH-USERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   AdminAlert - Protocol for admin_alert(message) async callable
#   DEBOUNCE_SECONDS - watchdog debounce window (0.5s)
#   logger - module-level structlog logger
#   AllowlistReloader - guarded async reload coroutine; sha256 short-circuit; admin_alert hook
#   install_sighup_handler - bind SIGHUP to reloader.schedule()
#   build_watchdog_observer - watchdog.Observer triggering reloader.schedule_debounced()
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial SIGHUP + watchdog reload coordinator (D-031)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ai_steward_wiki.auth.allowlist import replace_global, sync_to_sessions_db
from ai_steward_wiki.auth.users_toml import UsersTomlError, load_users_toml

logger = structlog.get_logger(__name__)

DEBOUNCE_SECONDS = 0.5

AdminAlert = Callable[[str], Awaitable[None]]


async def _default_admin_alert(message: str) -> None:
    logger.warning("allowlist.admin_alert", message=message)


class AllowlistReloader:
    """Single-flight reload of users.toml → cache + sessions.db sync."""

    def __init__(
        self,
        path: Path,
        session_maker: async_sessionmaker[AsyncSession],
        admin_alert: AdminAlert | None = None,
    ) -> None:
        self._path = path
        self._session_maker = session_maker
        self._admin_alert: AdminAlert = admin_alert or _default_admin_alert
        self._lock = asyncio.Lock()
        self._last_sha: str | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def reload(self) -> bool:
        """Reload once. Return True if cache replaced; False on noop or error."""
        async with self._lock:
            try:
                raw = self._path.read_bytes()
            except OSError as exc:
                await self._admin_alert(f"users.toml read failed: {exc}")
                logger.error("allowlist.read_failed", error=str(exc))
                return False

            sha = hashlib.sha256(raw).hexdigest()
            if sha == self._last_sha:
                logger.debug("allowlist.reload_noop", sha=sha[:12])
                return False

            try:
                config = load_users_toml(self._path)
            except UsersTomlError as exc:
                await self._admin_alert(f"users.toml invalid, keeping prior cache: {exc}")
                logger.error("allowlist.validate_failed", error=str(exc))
                return False

            replace_global(config)
            try:
                await sync_to_sessions_db(config, self._session_maker)
            except Exception as exc:
                logger.exception("allowlist.db_sync_failed", error=str(exc))
                await self._admin_alert(f"sessions.users sync failed: {exc}")
                # cache already replaced — DB will re-sync on next reload.
                return True

            self._last_sha = sha
            logger.info(
                "allowlist.reloaded",
                user_count=len(config.users),
                sha=sha[:12],
            )
            return True

    def __init_tasks(self) -> set[asyncio.Task[bool]]:
        return getattr(self, "_tasks", set())

    def schedule(self) -> None:
        """Fire-and-forget reload (used by SIGHUP)."""
        loop = asyncio.get_running_loop()
        tasks = self.__init_tasks()
        task = loop.create_task(self.reload())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        self._tasks = tasks

    def schedule_debounced(self) -> None:
        """Reset 500ms timer; reload runs after quiet period (used by watchdog)."""
        loop = asyncio.get_running_loop()
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        self._debounce_handle = loop.call_later(DEBOUNCE_SECONDS, self.schedule)


def install_sighup_handler(loop: asyncio.AbstractEventLoop, reloader: AllowlistReloader) -> None:
    try:
        loop.add_signal_handler(signal.SIGHUP, reloader.schedule)
    except (NotImplementedError, AttributeError):
        # Windows / restricted env — SIGHUP unavailable, watchdog fallback only.
        logging.getLogger(__name__).warning("SIGHUP not available; relying on watchdog")


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, reloader: AllowlistReloader, loop: asyncio.AbstractEventLoop) -> None:
        self._reloader = reloader
        self._loop = loop
        self._target = reloader.path.resolve()

    def on_any_event(self, event: FileSystemEvent) -> None:
        try:
            src = Path(str(event.src_path)).resolve()
        except OSError:
            return
        if src != self._target:
            return
        self._loop.call_soon_threadsafe(self._reloader.schedule_debounced)


def build_watchdog_observer(reloader: AllowlistReloader, loop: asyncio.AbstractEventLoop) -> Any:
    """Return a watchdog Observer wired to call reloader.schedule_debounced on changes."""
    observer = Observer()
    observer.schedule(
        _WatchdogHandler(reloader, loop),
        str(reloader.path.parent),
        recursive=False,
    )
    return observer
