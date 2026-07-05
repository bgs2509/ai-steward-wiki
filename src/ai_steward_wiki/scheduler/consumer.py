# FILE: src/ai_steward_wiki/scheduler/consumer.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Single async drain loop over PriorityJobQueue — executes each cron-user
#            item through Claude with safe Codex fallback,
#            captures stdout (timeout 600s), delivers via aiogram.Bot
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
#            ai_steward_wiki.llm.{failover,codex},
#            ai_steward_wiki.tg.output.ChainSplitter,
#            ai_steward_wiki.storage.jobs.models.Job
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER, M-STORAGE-JOBS, M-TG-TEXT,
#          M-CLAUDE-CLI-COMMON, M-LLM-FAILOVER, M-LLM-CODEX,
#          aisw-02v, aisw-0j4, aisw-abc, aisw-8gw, D-011 §3, D-021,
#          ADR-010
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Spawner - Protocol seam: async spawn(argv, *, cwd, env) -> _Killable + communicate
#   CronConsumer - constructor-DI drain loop; Claude-first text generation; one delivery
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: normalize Claude JSON text envelopes and
#                execute safe gpt-5.5 medium fallback before one Telegram delivery.
#   PREVIOUS:    v0.0.5 - aisw-8gw: contract-only plan for safe Codex text fallback.
#   PREVIOUS:    v0.0.4 - aisw-abc: drop the `systemd-run --scope --collect` wrapper
#                from _build_argv — run the Claude CLI directly, matching
#                wiki/runner._build_argv. The wrapper was D-038 per-UID-isolation
#                infra; ADR-010 deferred that model for the life-MVP (simple
#                single-user service under `bgs`). On the active deployment the
#                wrapper failed with "Failed to start transient scope unit:
#                Interactive authentication required" (polkit denies non-root
#                manage-units without a session), breaking ALL cron-user jobs.
#                Removed the now-dead `slice_name` constructor param,
#                `cron_user_slice_name` setting, and `unit=`/`slice=` log fields.
#                CLAUDE_CONFIG_DIR still reaches the child via build_env().
#   PREVIOUS:    v0.0.3 - aisw-o3m: bring the cron path to defense-in-depth parity
#                with wiki/runner._build_argv. _build_argv now adds, BEFORE the
#                aisw-0j4 "--" command separator: --setting-sources "" (drop default
#                settings + default Claude Code system prompt under OAuth),
#                --disable-slash-commands, --permission-mode dontAsk (matches runner),
#                --disallowedTools WebFetch (matches runner read-only deny surface;
#                no --allowedTools, like runner read-only runs). FORK DECISION kept:
#                the positional-prompt + direct stdout-capture invocation model is
#                unchanged (NOT switched to -p/stream-json/stdin) — cron-regression
#                risk out of scope. Flag spelling verified against `claude --help`
#                (claude 2.1.175). Closes the parity follow-up from v0.0.2.
#   PREVIOUS:    v0.0.2 - aisw-0j4: insert a literal "--" end-of-options separator
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
import json
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
    parse_claude_subscription_limit,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)
from ai_steward_wiki.llm.codex import CodexCliAdapter, CodexRequest, CodexRunKind
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverPolicy,
    ProviderLimitError,
    ProvidersUnavailableError,
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
_PROVIDERS_UNAVAILABLE_RU = (
    "Claude и Codex сейчас недоступны. Исходная команда сохранена — повторите позже."
)


class _CronExecutionError(RuntimeError):
    def __init__(self, *, user_message: str | None, last_error: str) -> None:
        super().__init__(last_error)
        self.user_message = user_message
        self.last_error = last_error


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
        spawner: Spawner | None = None,
        failover_policy: FailoverPolicy | None = None,
        codex_adapter: CodexCliAdapter | None = None,
    ) -> None:
        self._queue = queue
        self._bot = bot
        self._claude_binary = claude_binary
        self._claude_config_dir = claude_config_dir
        self._prompt_path = prompt_path
        self._jobs_session_maker = jobs_session_maker
        self._timeout_s = timeout_s
        self._spawner: Spawner = spawner if spawner is not None else _AsyncioSpawner()
        self._failover_policy = failover_policy
        self._codex_adapter = codex_adapter

    # ---- public lifecycle -------------------------------------------------

    async def run(self) -> None:
        """Drain loop — `await queue.get()` per iteration, dispatch to _execute_one.

        Cancellation: CancelledError propagates so __main__ shutdown can await
        the task cleanly. Unexpected exceptions inside _execute_one are caught,
        logged, and the loop continues (one bad item must not crash the drain).
        """
        _log.info("scheduler.consumer.started")
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

        try:
            text = await self._execute_text(msg)
        except ProvidersUnavailableError:
            await self._deliver(
                msg,
                _PROVIDERS_UNAVAILABLE_RU,
                status="failed",
                last_error="providers_unavailable",
            )
            return
        except _CronExecutionError as exc:
            if exc.user_message is None:
                await self._set_status(
                    msg.job_id,
                    status="failed",
                    finished=True,
                    last_error=exc.last_error,
                )
            else:
                await self._deliver(
                    msg,
                    exc.user_message,
                    status="failed",
                    last_error=exc.last_error,
                )
            return
        await self._deliver(msg, text, status="finished")

    async def _execute_text(self, msg: CronUserQueueMsg) -> str:
        policy = self._failover_policy
        codex = self._codex_adapter
        if policy is None or codex is None:
            return await self._execute_claude_text(msg)
        system_prompt = self._prompt_path.read_text(encoding="utf-8")
        return await policy.execute(
            run_kind="text",
            correlation_id=msg.correlation_id,
            claude=lambda: self._execute_claude_text(msg),
            codex=lambda: codex.run_text(
                CodexRequest(
                    prompt=(
                        f"{system_prompt}\n\n"
                        "<cron_user_command>\n"
                        f"{msg.command}\n"
                        "</cron_user_command>"
                    ),
                    model=codex.complex_model,
                    reasoning=codex.complex_reasoning,
                    run_kind=CodexRunKind.TEXT,
                    correlation_id=msg.correlation_id,
                    timeout_s=self._timeout_s,
                    cwd=codex.neutral_cwd,
                )
            ),
        )

    async def _execute_claude_text(self, msg: CronUserQueueMsg) -> str:
        argv = self._build_argv(msg)
        env = build_env(self._claude_config_dir)
        cwd = neutral_cwd(self._claude_config_dir)
        _log.info(
            "scheduler.consumer.exec.started",
            job_id=msg.job_id,
            correlation_id=msg.correlation_id,
            chat_id=msg.chat_id,
        )
        start_t = datetime.now(UTC)
        try:
            proc = await self._spawner.spawn(argv, cwd=cwd, env=env)
        except Exception as exc:
            _log.warning(
                "scheduler.consumer.exec.failed",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                error_class=type(exc).__name__,
                reason="spawn",
            )
            raise _CronExecutionError(
                user_message=None,
                last_error=f"spawn: {type(exc).__name__}",
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_s)
        except TimeoutError as exc:
            await kill_with_sequence(proc, grace_seconds=DEFAULT_TERM_GRACE_SECONDS)
            _log.warning(
                "scheduler.consumer.exec.timeout",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                timeout_s=self._timeout_s,
            )
            raise _CronExecutionError(
                user_message=_TIMEOUT_MSG_RU.format(seconds=int(self._timeout_s)),
                last_error=f"timeout after {self._timeout_s}s",
            ) from exc
        except Exception as exc:
            _log.warning(
                "scheduler.consumer.exec.failed",
                job_id=msg.job_id,
                correlation_id=msg.correlation_id,
                error_class=type(exc).__name__,
                reason="communicate",
            )
            raise _CronExecutionError(
                user_message=None,
                last_error=f"communicate: {type(exc).__name__}",
            ) from exc

        duration_ms = int((datetime.now(UTC) - start_t).total_seconds() * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1
        try:
            envelope = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            envelope = None
        if isinstance(envelope, dict):
            limit = parse_claude_subscription_limit(envelope)
            if limit is not None:
                raise ProviderLimitError(
                    provider="claude",
                    reset_at=limit.reset_at,
                    evidence=AttemptEvidence(EvidenceKind.READ_ONLY, "cron text generation"),
                )
        if exit_code != 0:
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
            raise _CronExecutionError(
                user_message=err_text,
                last_error=f"exit={exit_code}; stderr={truncate_stderr(stderr, limit=200)}",
            )
        if isinstance(envelope, dict):
            result = envelope.get("result")
            if envelope.get("subtype") != "success" or not isinstance(result, str):
                raise _CronExecutionError(
                    user_message=_ERROR_MSG_RU.format(code=exit_code, stderr="invalid output"),
                    last_error="invalid claude JSON envelope",
                )
            text = result.strip()
        else:
            text = stdout.decode("utf-8", "replace").strip()
        _log.info(
            "scheduler.consumer.exec.done",
            job_id=msg.job_id,
            correlation_id=msg.correlation_id,
            duration_ms=duration_ms,
            bytes_stdout=len(stdout),
        )
        return text or _EMPTY_OUTPUT_RU

    # ---- argv builder ------------------------------------------------------

    def _build_argv(self, msg: CronUserQueueMsg) -> list[str]:
        # START_BLOCK_CONSUMER_ARGV
        # aisw-abc: run the Claude CLI DIRECTLY — no `systemd-run --scope` wrapper.
        # The wrapper was D-038 per-UID-isolation infrastructure; ADR-010 deferred
        # that model for the life-MVP (simple single-user service under `bgs`, no
        # CAP_SETUID, no polkit rule). On the active deployment `systemd-run --scope`
        # failed with "Failed to start transient scope unit: Interactive
        # authentication required" (polkit denies non-root manage-units without a
        # session). The interactive path (wiki/runner._build_argv) already spawns
        # claude directly; this brings the cron path to parity. CLAUDE_CONFIG_DIR
        # reaches the child via build_env() (claude_cli.common), no longer via
        # systemd-run --setenv.
        binary = resolve_binary(self._claude_binary)
        argv: list[str] = [
            binary,
            "-p",
            "--output-format",
            "json",
            *system_prompt_argv(self._prompt_path),
            # aisw-o3m: defense-in-depth parity with wiki/runner._build_argv.
            # Flag names verified against `claude --help` (claude 2.1.175):
            #   --permission-mode dontAsk  -> explicit mode, matches runner.
            #   --setting-sources ""       -> drop default user/project settings &
            #                                 the default Claude Code system prompt
            #                                 loaded under subscription OAuth (aisw-0mg).
            #   --disable-slash-commands   -> no skills/slash-commands from user input.
            #   --disallowedTools WebFetch -> match runner's default deny surface; the
            #                                 cron path is a read-only generic command,
            #                                 so (like runner read-only runs) it sets no
            #                                 --allowedTools.
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--disallowedTools",
            "WebFetch",
            # aisw-0j4: literal end-of-options separator BEFORE the user command.
            # Without it a msg.command starting with "-" (e.g.
            # "--dangerously-skip-permissions", "--add-dir /") is parsed by the
            # Claude CLI as a FLAG, widening the per-job tool/permission surface.
            # This "--" forces claude to treat msg.command as a positional prompt,
            # never an option. (runner.py avoids this by routing user input via
            # stdin under -p; the consumer path uses a positional prompt, so the
            # separator is the correct minimal guard here.)
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
