# FILE: src/ai_steward_wiki/scheduler/consumer.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Single async drain loop over PriorityJobQueue — spawns
#            `systemd-run --scope --collect`-wrapped Claude CLI per cron-user
#            item, captures stdout (timeout 600s), delivers via aiogram.Bot
#            (chunked via ChainSplitter). Mutates jobs.Job status through the
#            'queued' → 'running' → 'finished'|'failed' arc.
#   SCOPE: CronConsumer (constructor-DI bot/queue/jobs_session_maker — R-1 mitigation);
#          Spawner Protocol seam for unit tests; default _AsyncioSpawner wraps
#          asyncio.create_subprocess_exec; .run() blocking drain loop; ._execute_one
#          per-item executor.
#   DEPENDS: asyncio, contextlib, pydantic v2, aiogram (Bot, exceptions.TelegramAPIError),
#            sqlalchemy.ext.asyncio (AsyncSession, async_sessionmaker), structlog,
#            ai_steward_wiki.scheduler.queue.PriorityJobQueue,
#            ai_steward_wiki.scheduler.queue_payloads.CronUserQueueMsg,
#            ai_steward_wiki.scheduler.core.kill_with_sequence/DEFAULT_TERM_GRACE_SECONDS,
#            ai_steward_wiki.claude_cli.common.{resolve_binary,build_env,neutral_cwd,
#                                               system_prompt_argv,truncate_stderr},
#            ai_steward_wiki.tg.output.ChainSplitter,
#            ai_steward_wiki.storage.jobs.models.Job
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER, M-STORAGE-JOBS, M-TG-TEXT,
#          M-CLAUDE-CLI-COMMON, aisw-02v, aisw-0j4, D-011 §3, D-021
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Spawner - Protocol seam: async spawn(argv, *, cwd, env) -> _Killable + communicate
#   CronConsumer - constructor-DI drain loop; .run() blocking; ._execute_one per-item
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-0j4: insert a literal "--" end-of-options separator
#                immediately before msg.command in _build_argv. The pre-existing "--"
#                only terminates systemd-run option parsing, not claude's, so a
#                "-"-prefixed user command was parsed as a Claude CLI flag
#                (--dangerously-skip-permissions, --add-dir /, …), bypassing the
#                per-job sandbox. The new separator forces msg.command to be a
#                positional prompt. Full parity with runner argv hardening
#                (permission-mode / allow-deny tools / setting-sources) tracked
#                separately as a follow-up.
#   PREVIOUS:    v0.0.1 - aisw-02v: initial PriorityJobQueue consumer (single drain
#                loop, systemd-run --scope --collect wrapper, ChainSplitter delivery)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog
from aiogram.exceptions import TelegramAPIError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.claude_cli.common import (
    build_env,
    neutral_cwd,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)
from ai_steward_wiki.scheduler.core import (
    DEFAULT_TERM_GRACE_SECONDS,
    kill_with_sequence,
)
from ai_steward_wiki.scheduler.queue import PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CronUserQueueMsg
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.tg.output import ChainSplitter

if TYPE_CHECKING:
    from aiogram import Bot

__all__ = ["CronConsumer", "Spawner"]

_log = structlog.get_logger("scheduler.consumer")

# ru user-facing strings (D-032, NFR-4).
# ruff: noqa: RUF001 — Cyrillic letters in user-visible strings are intentional.
_TIMEOUT_MSG_RU = "❌ Тайм-аут: команда выполнялась дольше {seconds} с."
_ERROR_MSG_RU = "❌ Ошибка ({code}): {stderr}"
_EMPTY_OUTPUT_RU = "✅ Команда выполнена (без вывода)."


class _Killable(Protocol):
    """Subset of asyncio.subprocess.Process we depend on."""

    returncode: int | None

    async def communicate(self) -> tuple[bytes, bytes]: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


class Spawner(Protocol):
    """Subprocess spawn seam — overridable in tests (R-2 / D-021)."""

    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> _Killable: ...


class _AsyncioSpawner:
    """Default Spawner: asyncio.create_subprocess_exec with stdout/stderr pipes."""

    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> _Killable:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        return proc  # type: ignore[return-value]


class CronConsumer:
    """Single-task async drain loop for cron-user PriorityJobQueue items.

    Constructor-DI keeps the consumer testable (R-1 mitigation: bot is not a
    module-global); .run() is awaited as a task in the bot lifecycle; cancellation
    propagates out of .run() so __main__ shutdown can await the task.
    """

    def __init__(
        self,
        *,
        queue: PriorityJobQueue,
        bot: Bot,
        claude_binary: str,
        claude_config_dir: Path,
        prompt_path: Path,
        jobs_session_maker: async_sessionmaker[AsyncSession],
        timeout_s: float = 600.0,
        slice_name: str = "aisw-cli.slice",
        spawner: Spawner | None = None,
    ) -> None:
        self._queue = queue
        self._bot = bot
        self._claude_binary = claude_binary
        self._claude_config_dir = claude_config_dir
        self._prompt_path = prompt_path
        self._jobs_session_maker = jobs_session_maker
        self._timeout_s = timeout_s
        self._slice_name = slice_name
        self._spawner: Spawner = spawner if spawner is not None else _AsyncioSpawner()

    # ---- public lifecycle -------------------------------------------------

    async def run(self) -> None:
        """Drain loop — `await queue.get()` per iteration, dispatch to _execute_one.

        Cancellation: CancelledError propagates so __main__ shutdown can await
        the task cleanly. Unexpected exceptions inside _execute_one are caught,
        logged, and the loop continues (one bad item must not crash the drain).
        """
        _log.info("scheduler.consumer.started", slice=self._slice_name)
        try:
            while True:
                item = await self._queue.get()
                _log.info(
                    "scheduler.consumer.drained",
                    lane=item.lane,
                    sequence=item.sequence,
                )
                try:
                    await self._execute_one(item.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log.warning(
                        "scheduler.consumer.unexpected",
                        error_class=type(exc).__name__,
                    )
        except asyncio.CancelledError:
            _log.info("scheduler.consumer.cancelled")
            raise

    # ---- per-item executor (also called directly from tests) --------------

    async def _execute_one(self, payload: object) -> None:
        # START_BLOCK_CONSUMER_VALIDATE
        if not isinstance(payload, CronUserQueueMsg):
            try:
                msg = CronUserQueueMsg.model_validate(payload)
            except ValidationError as exc:
                _log.warning(
                    "scheduler.consumer.unexpected",
                    reason="payload_invalid",
                    error_class=type(exc).__name__,
                )
                return
        else:
            msg = payload
        # END_BLOCK_CONSUMER_VALIDATE

        await self._set_status(msg.job_id, status="running", started=True)

        argv = self._build_argv(msg)
        env = build_env(self._claude_config_dir)
        cwd = neutral_cwd(self._claude_config_dir)
        _log.info(
            "scheduler.consumer.exec.started",
            job_id=msg.job_id,
            correlation_id=msg.correlation_id,
            chat_id=msg.chat_id,
            unit=f"cli-{msg.job_id}",
        )
        start_t = datetime.now(UTC)

        proc = await self._spawner.spawn(argv, cwd=cwd, env=env)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_s)
        except TimeoutError:
            await kill_with_sequence(proc, grace_seconds=DEFAULT_TERM_GRACE_SECONDS)
            _log.warning(
                "scheduler.consumer.exec.timeout",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                timeout_s=self._timeout_s,
            )
            await self._deliver(
                msg,
                _TIMEOUT_MSG_RU.format(seconds=int(self._timeout_s)),
                status="failed",
                last_error=f"timeout after {self._timeout_s}s",
            )
            return
        except Exception as exc:
            _log.warning(
                "scheduler.consumer.exec.failed",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                error_class=type(exc).__name__,
                reason="spawn_or_communicate",
            )
            await self._set_status(
                msg.job_id,
                status="failed",
                finished=True,
                last_error=f"spawn/communicate: {type(exc).__name__}",
            )
            return

        duration_ms = int((datetime.now(UTC) - start_t).total_seconds() * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1

        if exit_code == 0:
            _log.info(
                "scheduler.consumer.exec.done",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                duration_ms=duration_ms,
                bytes_stdout=len(stdout),
            )
            text = stdout.decode("utf-8", "replace").strip() or _EMPTY_OUTPUT_RU
            await self._deliver(msg, text, status="finished")
        else:
            _log.warning(
                "scheduler.consumer.exec.failed",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
            err_text = _ERROR_MSG_RU.format(
                code=exit_code,
                stderr=truncate_stderr(stderr, limit=512),
            )
            await self._deliver(
                msg,
                err_text,
                status="failed",
                last_error=f"exit={exit_code}; stderr={truncate_stderr(stderr, limit=200)}",
            )

    # ---- argv builder ------------------------------------------------------

    def _build_argv(self, msg: CronUserQueueMsg) -> list[str]:
        # START_BLOCK_CONSUMER_ARGV
        binary = resolve_binary(self._claude_binary)
        argv: list[str] = [
            "systemd-run",
            "--scope",
            "--collect",
            f"--slice={self._slice_name}",
            f"--unit=cli-{msg.job_id}",
            f"--setenv=CLAUDE_CONFIG_DIR={self._claude_config_dir}",
            "--",
            binary,
            *system_prompt_argv(self._prompt_path),
            # aisw-0j4: literal end-of-options separator BEFORE the user command.
            # The "--" at index ~7 only terminates systemd-run's own option parsing,
            # not claude's. Without this separator a msg.command starting with "-"
            # (e.g. "--dangerously-skip-permissions", "--add-dir /") is parsed by the
            # Claude CLI as a FLAG, widening the per-job tool/permission surface and
            # bypassing the sandbox. This "--" forces claude to treat msg.command as a
            # positional prompt, never an option. (runner.py avoids this by routing
            # user input via stdin under -p; the consumer path uses a positional prompt,
            # so the separator is the correct minimal guard here.)
            "--",
            msg.command,
        ]
        # END_BLOCK_CONSUMER_ARGV
        return argv

    # ---- delivery (chunked) -----------------------------------------------

    async def _deliver(
        self,
        msg: CronUserQueueMsg,
        text: str,
        *,
        status: str,
        last_error: str | None = None,
    ) -> None:
        # START_BLOCK_CONSUMER_DELIVER
        chunks = ChainSplitter().split(text) if text else [_EMPTY_OUTPUT_RU]
        n_sent = 0
        delivery_failed = False
        for chunk in chunks:
            try:
                await self._bot.send_message(msg.chat_id, chunk)
                n_sent += 1
            except TelegramAPIError as exc:
                _log.warning(
                    "scheduler.consumer.deliver_failed",
                    job_id=msg.job_id,
                    correlation_id=msg.correlation_id,
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                )
                delivery_failed = True
                break
        final_status = "failed" if delivery_failed else status
        final_last_error = (
            last_error if not delivery_failed else (last_error or "telegram_api_error")
        )
        await self._set_status(
            msg.job_id,
            status=final_status,
            finished=True,
            last_error=final_last_error,
        )
        if not delivery_failed:
            _log.info(
                "scheduler.consumer.delivered",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                chat_id=msg.chat_id,
                n_chunks=n_sent,
            )
        # END_BLOCK_CONSUMER_DELIVER

    # ---- status mutation --------------------------------------------------

    async def _set_status(
        self,
        job_id: int,
        *,
        status: str,
        started: bool = False,
        finished: bool = False,
        last_error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._jobs_session_maker() as session, session.begin():
            row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
            if row is None:
                _log.warning(
                    "scheduler.consumer.row_missing",
                    job_id=job_id,
                    intended_status=status,
                )
                return
            row.status = status
            if started:
                row.started_at_utc = now
            if finished:
                row.finished_at_utc = now
            if last_error is not None:
                row.last_error = last_error


# Suppress unused-import warnings on optional contextlib (kept for future).
_ = contextlib
