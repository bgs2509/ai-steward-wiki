# FILE: src/ai_steward_wiki/__main__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Process entrypoint (`python -m ai_steward_wiki`). Composes Settings,
#            per-DB Alembic migrations, storage engines, allowlist sync,
#            APScheduler, classifier+runner+output adapters, aiogram
#            Bot+Dispatcher; runs long-polling; gracefully shuts down on
#            SIGINT/SIGTERM.
#   SCOPE: _amain (full async lifecycle), main (sync wrapper invoking
#          asyncio.run), private helpers _sync_url_for_jobstore,
#          _ensure_data_dirs, _run_all_migrations, _load_users_config,
#          _install_signal_handlers, _build_classifier_backend,
#          _ClassifierAdapter, _WikiRunnerAdapter, _OutputDeliveryAdapter.
#   DEPENDS: aiogram, apscheduler, alembic, structlog, sqlalchemy.async,
#            ai_steward_wiki.{settings, logging_setup, tg.bot, tg.pipeline,
#            tg.output, scheduler.core, scheduler.locks,
#            classifier.{backend,schema,stage0}, wiki.{runner,acquire},
#            storage.{jobs,audit,sessions}.engine, auth.{allowlist,users_toml}}
#   LINKS: M-FOUNDATION, M-STORAGE, M-AUTH-USERS, M-SCHEDULER,
#          M-CLASSIFIER-STAGE0, M-WIKI-RUNNER, M-TG-OUTPUT,
#          M-TG-PIPELINE-CLASSIFIER, M-DEPLOY
#   ROLE: RUNTIME
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - chunk 20: wire classifier+runner+deliver_output adapters.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import structlog
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.allowlist import replace_global, sync_to_sessions_db
from ai_steward_wiki.auth.users_toml import (
    UsersConfig,
    UsersTomlError,
    load_users_toml,
)
from ai_steward_wiki.classifier.backend import (
    AnthropicApiBackend,
    ClassifierBackend,
    ClaudeCliBackend,
)
from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.classifier.stage0 import PromptCache, classify
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.logging_setup import configure_logging
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.scheduler.core import build_scheduler
from ai_steward_wiki.scheduler.locks import WikiLockManager
from ai_steward_wiki.settings import Settings, get_settings
from ai_steward_wiki.storage.audit.engine import build_engine, build_sessionmaker
from ai_steward_wiki.tg.bot import AiogramSender, TgSender, build_bot, build_dispatcher
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.output import deliver_output
from ai_steward_wiki.tg.pipeline import (
    DefaultPipeline,
    DefaultStreamingDelivery,
    WikiRunOutcome,
)
from ai_steward_wiki.wiki.acquire import WikiLockAdapter
from ai_steward_wiki.wiki.runner import (
    AsyncioSpawner,
    WikiRunnerError,
    _RunConfig,
    aggregate_text,
    run_wiki_session,
)
from ai_steward_wiki.wiki.streaming import StreamEvent

logger = structlog.get_logger("ai_steward_wiki.runtime")

# Test seam: when set externally, _amain awaits it instead of constructing a
# fresh asyncio.Event. Production code never touches this attribute.
_STOP_EVENT_FOR_TESTS: asyncio.Event | None = None

# Repo-root-relative alembic.ini paths. Resolved against cwd at runtime.
_ALEMBIC_INIS: tuple[tuple[str, str], ...] = (
    ("jobs", "alembic/jobs/alembic.ini"),
    ("audit", "alembic/audit/alembic.ini"),
    ("sessions", "alembic/sessions/alembic.ini"),
)


def _sync_url_for_jobstore(async_url: str) -> str:
    """Strip aiosqlite driver from URL for sync SQLAlchemyJobStore.

    APScheduler's SQLAlchemyJobStore needs a synchronous URL. We only support
    sqlite in this project (D-006); reject other backends to fail fast.
    """
    if not async_url.startswith(("sqlite+aiosqlite://", "sqlite://")):
        raise ValueError(f"only sqlite URLs are supported, got: {async_url}")
    return async_url.replace("+aiosqlite", "")


def _ensure_data_dirs(async_urls: list[str]) -> None:
    """Create parent directories for sqlite file URLs (no-op for :memory:)."""
    for url in async_urls:
        sync_url = url.replace("+aiosqlite", "")
        if not sync_url.startswith("sqlite:///"):
            continue
        target = sync_url[len("sqlite:///") :]
        if target in {"", ":memory:"}:
            continue
        Path(target).parent.mkdir(parents=True, exist_ok=True)


def _load_users_config(path: Path | None) -> UsersConfig:
    """Load users.toml; return empty config when path is None or missing."""
    if path is None or not path.exists():
        logger.info("runtime.allowlist.loaded", users_count=0, path_present=False)
        return UsersConfig(schema_version=1, users=())
    try:
        cfg = load_users_toml(path)
    except UsersTomlError:
        logger.exception("runtime.allowlist.parse_error", path=str(path))
        raise
    logger.info("runtime.allowlist.loaded", users_count=len(cfg.users), path_present=True)
    return cfg


# START_BLOCK_TEXT_PIPELINE_ADAPTERS
class _ClassifierAdapter:
    """Bind Stage-0 classify(...) into the narrow Classifier Protocol used by DefaultPipeline."""

    def __init__(
        self,
        *,
        backend: ClassifierBackend,
        prompt_path: Path,
        audit_session_maker: async_sessionmaker[AsyncSession],
        cache: PromptCache,
    ) -> None:
        self._backend = backend
        self._prompt_path = prompt_path
        self._audit_session_maker = audit_session_maker
        self._cache = cache

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult:
        async with self._audit_session_maker() as session:
            return await classify(
                text,
                correlation_id=correlation_id,
                backend=self._backend,
                prompt_path=self._prompt_path,
                audit_session=session,
                cache=self._cache,
            )


class _WikiRunnerAdapter:
    """Bind Stage-1a/1b run_wiki_session into the narrow WikiRunner Protocol."""

    def __init__(
        self,
        *,
        wiki_root: Path,
        base_prompt_path: Path,
        overlay_prompt_path: Path,
        runtime_dir: Path,
        acquirer: WikiLockAdapter,
        spawner: AsyncioSpawner,
        run_config: _RunConfig,
    ) -> None:
        self._wiki_root = wiki_root
        self._base_prompt_path = base_prompt_path
        self._overlay_prompt_path = overlay_prompt_path
        self._runtime_dir = runtime_dir
        self._acquirer = acquirer
        self._spawner = spawner
        self._run_config = run_config

    async def run(
        self,
        *,
        text: str,
        owner_telegram_id: int,
        correlation_id: str,
        intent: Intent,
        on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> WikiRunOutcome:
        wiki_id = str(owner_telegram_id)
        wiki_path = self._wiki_root / wiki_id
        wiki_path.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid4().hex[:12]}"
        # Write the user turn into the overlay (a single-turn prompt) so the
        # CLI sees the prompt material. The overlay path is stable; we append
        # the freshly-staged user text to a per-run scratch file referenced
        # from the overlay header. MVP simplification: feed the text in place
        # of the overlay so the user input reaches the model without extra
        # plumbing. Future chunks (21+) will replace with proper Inbox staging.
        scratch = self._runtime_dir / "overlays" / f"{run_id}.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        header = "semver: 1.0.0\n\n# User turn\n\n"
        scratch.write_text(header + text, encoding="utf-8")
        try:
            result = await run_wiki_session(
                wiki_id=wiki_id,
                wiki_path=wiki_path,
                base_prompt_path=self._base_prompt_path,
                overlay_prompt_path=scratch,
                run_id=run_id,
                correlation_id=correlation_id,
                runtime_dir=self._runtime_dir,
                acquirer=self._acquirer,
                spawner=self._spawner,
                config=self._run_config,
                on_event=on_event,
            )
        except WikiRunnerError:
            raise
        return WikiRunOutcome(
            run_id=run_id,
            text=aggregate_text(result.events),
            latency_ms=result.latency_ms,
        )


class _OutputDeliveryAdapter:
    """Bind tg.output.deliver_output into the narrow OutputDelivery Protocol."""

    def __init__(
        self,
        *,
        sender: TgSender,
        runs_dir: Path,
        audit_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sender = sender
        self._runs_dir = runs_dir
        self._audit_session_maker = audit_session_maker

    async def deliver(self, *, chat_id: int, telegram_id: int, run_id: str, text: str) -> None:
        await deliver_output(
            sender=self._sender,
            chat_id=chat_id,
            telegram_id=telegram_id,
            wiki_id=str(telegram_id),
            run_id=run_id,
            text=text,
            runs_dir=self._runs_dir,
            audit_session_maker=self._audit_session_maker,
        )


def _build_classifier_backend(settings: Settings) -> ClassifierBackend:
    """Construct the configured Stage-0 backend; raise on misconfiguration."""
    if settings.stage0_backend == "anthropic_api":
        if settings.stage0_api_credential_path is None:
            raise RuntimeError("stage0_api_credential_path is required for anthropic_api backend")
        return AnthropicApiBackend(
            credential_path=settings.stage0_api_credential_path,
        )
    if settings.claude_config_dir is None:
        raise RuntimeError(
            "claude_config_dir is required for claude_cli backend; set "
            "AISW_CLAUDE_CONFIG_DIR_LOCAL or AISW_CLAUDE_CONFIG_DIR_VPS"
        )
    return ClaudeCliBackend(
        claude_config_dir=settings.claude_config_dir,
        timeout_s=settings.classifier_stage0_timeout_s,
    )


# END_BLOCK_TEXT_PIPELINE_ADAPTERS


def _run_single_migration(name: str, ini_path: str, async_url: str) -> None:
    """Run `alembic upgrade head` for one database (sync, run inside to_thread)."""
    sync_url = _sync_url_for_jobstore(async_url)
    cfg = AlembicConfig(ini_path)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    alembic_command.upgrade(cfg, "head")
    logger.info("runtime.migrations.done", db_name=name)


async def _run_all_migrations(settings: Settings) -> None:
    """Run upgrade head on jobs.db, audit.db, sessions.db in order."""
    # START_BLOCK_RUNTIME_MIGRATIONS
    urls = {
        "jobs": settings.jobs_db_url,
        "audit": settings.audit_db_url,
        "sessions": settings.sessions_db_url,
    }
    for name, ini in _ALEMBIC_INIS:
        logger.info("runtime.migrations.begin", db_name=name)
        await asyncio.to_thread(_run_single_migration, name, ini, urls[name])
    # END_BLOCK_RUNTIME_MIGRATIONS


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Wire SIGINT and SIGTERM to set the stop event."""

    def _handler(sig_name: str) -> None:
        logger.info("runtime.signal.received", signal=sig_name)
        stop.set()

    for sig, name in ((signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handler, name)


async def _amain() -> None:
    # START_BLOCK_RUNTIME_BOOTSTRAP
    correlation_id = f"proc-{uuid4().hex[:8]}"
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "runtime.start",
        correlation_id=correlation_id,
        env=settings.env,
        log_level=settings.log_level,
    )

    if settings.tg_bot_token is None:
        raise RuntimeError(
            f"tg_bot_token missing for env={settings.env!r}; "
            f"set AISW_TG_BOT_TOKEN_LOCAL or AISW_TG_BOT_TOKEN_PROD"
        )

    db_urls = [settings.jobs_db_url, settings.audit_db_url, settings.sessions_db_url]
    _ensure_data_dirs(db_urls)
    await _run_all_migrations(settings)

    jobs_engine = build_engine(settings.jobs_db_url)
    audit_engine = build_engine(settings.audit_db_url)
    sessions_engine = build_engine(settings.sessions_db_url)
    audit_maker = build_sessionmaker(audit_engine)
    sessions_maker = build_sessionmaker(sessions_engine)

    users_cfg = _load_users_config(settings.users_toml_path)
    await sync_to_sessions_db(users_cfg, sessions_maker)
    allowlist = replace_global(users_cfg)

    scheduler = build_scheduler(_sync_url_for_jobstore(settings.jobs_db_url))
    scheduler.start()
    logger.info("runtime.scheduler.started", jobs_url=settings.jobs_db_url)

    bot = build_bot(settings.tg_bot_token.get_secret_value())
    sender = AiogramSender(bot)

    # START_BLOCK_TEXT_PIPELINE_WIRING (chunk 20 M-TG-PIPELINE-CLASSIFIER)
    classifier_backend = _build_classifier_backend(settings)
    classifier_adapter = _ClassifierAdapter(
        backend=classifier_backend,
        prompt_path=settings.prompts_dir / "classifier.md",
        audit_session_maker=audit_maker,
        cache=PromptCache(),
    )
    runtime_dir = settings.workspace_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_manager = WikiLockManager()
    runner_adapter = _WikiRunnerAdapter(
        wiki_root=settings.wiki_root,
        base_prompt_path=settings.prompts_dir / "wiki.md",
        overlay_prompt_path=settings.prompts_dir / "inbox.md",
        runtime_dir=runtime_dir,
        acquirer=WikiLockAdapter(lock_manager),
        spawner=AsyncioSpawner(),
        run_config=_RunConfig(
            model=settings.wiki_runner_model,
            timeout_s=settings.wiki_runner_timeout_s,
            term_grace_s=settings.wiki_runner_term_grace_s,
            claude_config_dir=settings.claude_config_dir,
        ),
    )
    runs_dir = settings.workspace_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_adapter = _OutputDeliveryAdapter(
        sender=sender,
        runs_dir=runs_dir,
        audit_session_maker=audit_maker,
    )
    logger.info(
        "runtime.text_pipeline.wired",
        backend=classifier_backend.name,
        model=classifier_backend.model,
        wiki_root=str(settings.wiki_root),
    )
    # END_BLOCK_TEXT_PIPELINE_WIRING

    streaming_delivery = DefaultStreamingDelivery(sender=sender)
    pipeline = DefaultPipeline(
        sender=sender,
        idempotency=IdempotencyService(audit_maker),
        confirmation=ConfirmationService(sender, sessions_maker),
        classifier=classifier_adapter,
        runner=runner_adapter,
        output=output_adapter,
        streaming=streaming_delivery,
        pii=PIIRedactor(hash_secret=settings.pii_hash_secret.get_secret_value().encode("utf-8")),
    )
    dp = build_dispatcher(allowlist, pipeline=pipeline)
    logger.info("runtime.handlers.registered")

    loop = asyncio.get_running_loop()
    stop_event = _STOP_EVENT_FOR_TESTS if _STOP_EVENT_FOR_TESTS is not None else asyncio.Event()
    _install_signal_handlers(loop, stop_event)
    # END_BLOCK_RUNTIME_BOOTSTRAP

    # START_BLOCK_RUNTIME_POLLING
    logger.info("runtime.polling.start")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, _pending = await asyncio.wait(
            {polling_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if polling_task in done:
            polling_task.result()
    finally:
        # START_BLOCK_RUNTIME_SHUTDOWN
        if not polling_task.done():
            stop = getattr(dp, "stop_polling", None)
            if stop is not None:
                try:
                    await stop()
                except Exception:
                    logger.exception("runtime.shutdown.stop_polling_failed")
            polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await polling_task
        if not stop_task.done():
            stop_task.cancel()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("runtime.shutdown.scheduler_failed")
        for engine in (jobs_engine, audit_engine, sessions_engine):
            try:
                await engine.dispose()
            except Exception:
                logger.exception("runtime.shutdown.engine_dispose_failed")
        try:
            await bot.session.close()
        except Exception:
            logger.exception("runtime.shutdown.bot_close_failed")
        logger.info("runtime.shutdown.done", correlation_id=correlation_id)
        # END_BLOCK_RUNTIME_SHUTDOWN
    # END_BLOCK_RUNTIME_POLLING


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
