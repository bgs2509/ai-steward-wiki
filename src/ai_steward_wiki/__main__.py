# FILE: src/ai_steward_wiki/__main__.py
# VERSION: 0.5.0
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
#          _ClassifierAdapter, _TimeParserAdapter, _RecurrenceParserAdapter,
#          _on_job_missed, _WikiRunnerAdapter, _DigestRunnerAdapter,
#          _resolve_owner_wikis_factory, _OutputDeliveryAdapter,
#          _RouterAdapter, _render_raw_sidecar, _LibrarianAdapter.
#   DEPENDS: aiogram, apscheduler, alembic, structlog, sqlalchemy.async,
#            ai_steward_wiki.{settings, logging_setup, tg.bot, tg.pipeline,
#            tg.output, tg.voice, tg.photo, scheduler.core, scheduler.locks,
#            scheduler.maintenance, scheduler.firing, inbox.materialize, inbox.router,
#            inbox.route, inbox.staging, classifier.{backend,schema,stage0,time_parse,recurrence},
#            wiki.{runner,acquire,lifecycle},
#            storage.{jobs,audit,sessions}.engine, auth.{allowlist,users_toml}}
#   LINKS: M-FOUNDATION, M-STORAGE, M-AUTH-USERS, M-SCHEDULER, M-SCHEDULER-FIRING,
#          M-CLASSIFIER-STAGE0, M-CLASSIFIER-RECURRENCE, M-WIKI-RUNNER, M-WIKI-LIFECYCLE,
#          M-TG-OUTPUT, M-TG-PIPELINE-CLASSIFIER, M-TG-VOICE, M-TG-PHOTO, M-INBOX,
#          M-INBOX-ROUTER, M-INBOX-ROUTE, M-DEPLOY
#   ROLE: RUNTIME
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.5.1 - aisw-w3k (Inbox-WIKI Phase-D.b.2a): digest delivery routed
#                through tg.output.deliver_output(kind='digest') —
#                firing.set_digest_context(...) now also gets
#                audit_session_maker=audit_maker.
#   PREVIOUS:    v0.5.0 - aisw-oqq (Inbox-WIKI Phase-D.b.1): wire the recurring digest —
#                _RecurrenceParserAdapter (rule-based parse_recurrence) → DefaultPipeline
#                recurrence_parser=; _DigestRunnerAdapter (run_wiki_session over
#                prompts/wiki.md + prompts/digest.md with extra_add_dirs, 600s) +
#                _resolve_owner_wikis_factory (glob <wiki_root>/<owner>/*-WIKI minus
#                Inbox-WIKI) → firing.set_digest_context(scheduler, runner, resolver,
#                jobs_maker, sender).
#   PREVIOUS:    v0.4.0 - aisw-kcz (Inbox-WIKI Phase-D.a): wire the reminder cron
#                bridge — firing.set_firing_context(sender, jobs_maker) after the
#                scheduler starts; scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED);
#                _TimeParserAdapter (parse_time over prompts/time-parse.md) + a
#                users.toml-backed _user_tz_lookup; pass time_parser/jobs_session_maker/
#                scheduler/user_tz_lookup/default_user_tz into DefaultPipeline.
#   PREVIOUS:    v0.3.0 - aisw-zd9 (Inbox-WIKI Phase-B): add _LibrarianAdapter —
#                resolve_target_wiki (lookup-or-create the target <Domain>-WIKI;
#                AntiSpamCapError/WikiNameError → rejected) → stage_raw_into_wiki
#                (move raw + promote media into <Domain>-WIKI/raw/) →
#                run_wiki_session there (prompts/wiki.md + domain overlay) →
#                IngestOutcome(notes + summary | notes + hint). Wired into
#                DefaultPipeline as `librarian=`. New log anchors inbox.route.*.
#   PREVIOUS:    v0.2.0 - aisw-dsg (Inbox-WIKI Phase-A): add _RouterAdapter —
#                ensure_inbox_wiki → stage raw payload into Inbox-WIKI/raw/ →
#                run_wiki_session in Inbox-WIKI/ with prompts/inbox.md →
#                parse_router_reply; WikiRunnerError → RouterError. Wired into
#                DefaultPipeline as `router=`. New log anchors inbox.router.*.
#   PREVIOUS:    v0.1.5 - aisw-t0n: pass photo_vision_timeout_s into DefaultPipeline
#                and timeout_s through _WikiRunnerAdapter.run → run_wiki_session
#                (D-022 per-call vision timeout).
#   PREVIOUS:    v0.1.4 - aisw-7k0: wire register_all_retention_jobs into _amain
#                (was unwired — pending-purge / DB retention / db_snapshot never
#                ran in prod); adds jobs_sessionmaker; logs registered job ids.
#                Replaces the direct register_media_staging_sweep_job call (now
#                inside the aggregator).
#                v0.1.3 - aisw-8r9 (media chunk 4): register the daily media
#                _staging sweep job on the scheduler; _WikiRunnerAdapter.run
#                promotes staged media into <wiki>/raw/media/ after a successful
#                run (D-022 two-phase storage).
#                v0.1.2 - aisw-m2m (media chunk 2): _WikiRunnerAdapter.run
#                forwards media_paths to run_wiki_session (photo vision, D-022).
#                v0.1.1 - aisw-zny (media chunk 1): wire VoiceHandler +
#                PhotoIngestor into DefaultPipeline (D-022); runtime.media_pipeline.wired log.
#                v0.1.0 - chunk 20: wire classifier+runner+deliver_output adapters.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from apscheduler.events import EVENT_JOB_MISSED
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
from ai_steward_wiki.classifier.recurrence import RecurrenceParseResult, parse_recurrence
from ai_steward_wiki.classifier.schema import ClassifierResult, Intent, TimeParseResult
from ai_steward_wiki.classifier.stage0 import PromptCache, classify
from ai_steward_wiki.classifier.time_parse import parse_time as _parse_time_fn
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.inbox.materialize import ensure_inbox_wiki
from ai_steward_wiki.inbox.route import (
    RouteRejection,
    build_ingest_prompt,
    pick_domain_overlay,
    resolve_target_wiki,
    stage_raw_into_wiki,
)
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, parse_router_reply
from ai_steward_wiki.inbox.staging import promote_path_to_raw
from ai_steward_wiki.logging_setup import configure_logging
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.core import build_scheduler
from ai_steward_wiki.scheduler.locks import WikiLockManager
from ai_steward_wiki.scheduler.maintenance import register_all_retention_jobs
from ai_steward_wiki.settings import Settings, get_settings
from ai_steward_wiki.storage.audit.engine import build_engine, build_sessionmaker
from ai_steward_wiki.tg.bot import AiogramSender, TgSender, build_bot, build_dispatcher
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.output import deliver_output
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.pipeline import (
    DefaultPipeline,
    DefaultStreamingDelivery,
    IngestOutcome,
    WikiRunOutcome,
)
from ai_steward_wiki.tg.voice import FasterWhisperTranscriber, VoiceHandler
from ai_steward_wiki.wiki.acquire import WikiLockAdapter
from ai_steward_wiki.wiki.lifecycle import WikiLifecycleManager
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


class _TimeParserAdapter:
    """Bind classifier.time_parse.parse_time into the narrow TimeParser Protocol (aisw-kcz)."""

    def __init__(self, *, backend: ClassifierBackend, prompt_path: Path | None) -> None:
        self._backend = backend
        self._prompt_path = prompt_path

    async def parse_time(
        self,
        text: str,
        *,
        user_tz: ZoneInfo,
        now_utc: datetime,
        prefer_future: bool = False,
        correlation_id: str = "",
    ) -> TimeParseResult:
        return await _parse_time_fn(
            text,
            user_tz=user_tz,
            now_utc=now_utc,
            prefer_future=prefer_future,
            haiku_backend=self._backend,
            haiku_prompt_path=self._prompt_path,
            correlation_id=correlation_id,
        )


def _on_job_missed(event: object) -> None:
    """APScheduler EVENT_JOB_MISSED listener — log a missed reminder fire (aisw-kcz)."""
    logger.warning("scheduler.reminder.misfired", job_id=getattr(event, "job_id", None))


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
        media_paths: list[Path] | None = None,
        timeout_s: float | None = None,
    ) -> WikiRunOutcome:
        wiki_id = str(owner_telegram_id)
        wiki_path = self._wiki_root / wiki_id
        wiki_path.mkdir(parents=True, exist_ok=True)
        run_id = f"run-{uuid4().hex[:12]}"
        # aisw-w83: user text is delivered to claude via stdin (PIPE) by the
        # runner; do NOT mix it into the system-prompt overlay. The per-run
        # overlay stays as a stable, semver-valid placeholder until proper
        # Inbox staging lands in chunks 21+.
        scratch = self._runtime_dir / "overlays" / f"{run_id}.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("semver: 1.0.0\n\n# User turn\n", encoding="utf-8")
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
                user_input=text,
                media_paths=media_paths,
                timeout_s=timeout_s,
            )
        except WikiRunnerError:
            raise
        # D-022: on a successful run the target WIKI is known — promote staged
        # media into <wiki>/raw/media/ (immutable). Failed runs leave the file
        # in _staging for the 24h sweep job.
        for media_path in media_paths or []:
            try:
                final = promote_path_to_raw(media_path, wiki_root=wiki_path)
                logger.info(
                    "runtime.media.promoted", run_id=run_id, src=str(media_path), dest=str(final)
                )
            except FileNotFoundError:
                logger.warning("runtime.media.promote_missing", run_id=run_id, src=str(media_path))
            except OSError:
                logger.warning("runtime.media.promote_failed", run_id=run_id, src=str(media_path))
        return WikiRunOutcome(
            run_id=run_id,
            text=aggregate_text(result.events),
            latency_ms=result.latency_ms,
        )


class _RecurrenceParserAdapter:
    """Bind classifier.recurrence.parse_recurrence into the RecurrenceParser Protocol (aisw-oqq).

    The Haiku-fallback path is a stub in the MVP; this adapter just forwards to
    the rule-based parser. ``prompts/recurrence.md`` is shipped for a later wiring.
    """

    def __call__(
        self, text: str, *, user_tz: str, correlation_id: str = ""
    ) -> RecurrenceParseResult:
        return parse_recurrence(text, user_tz=user_tz, correlation_id=correlation_id)


class _DigestRunnerAdapter:
    """Run one Stage-1 digest session against the owner's WIKIs (aisw-oqq).

    Mirrors _WikiRunnerAdapter: assembles prompts/wiki.md + prompts/digest.md,
    acquires the per-WIKI lock via run_wiki_session's own LockAcquirer, grants
    --add-dir on the other WIKIs, returns the aggregated assistant text.
    """

    def __init__(
        self,
        *,
        base_prompt_path: Path,
        digest_prompt_path: Path,
        runtime_dir: Path,
        acquirer: WikiLockAdapter,
        spawner: AsyncioSpawner,
        run_config: _RunConfig,
    ) -> None:
        self._base_prompt_path = base_prompt_path
        self._digest_prompt_path = digest_prompt_path
        self._runtime_dir = runtime_dir
        self._acquirer = acquirer
        self._spawner = spawner
        self._run_config = run_config

    async def __call__(
        self,
        *,
        wiki_id: str,
        wiki_path: Path,
        extra_add_dirs: list[Path],
        planner_context: str,
        correlation_id: str,
    ) -> str:
        wiki_path.mkdir(parents=True, exist_ok=True)
        run_id = f"digest-{uuid4().hex[:12]}"
        result = await run_wiki_session(
            wiki_id=wiki_id,
            wiki_path=wiki_path,
            base_prompt_path=self._base_prompt_path,
            overlay_prompt_path=self._digest_prompt_path,
            run_id=run_id,
            correlation_id=correlation_id,
            runtime_dir=self._runtime_dir,
            acquirer=self._acquirer,
            spawner=self._spawner,
            config=self._run_config,
            user_input=planner_context,
            extra_add_dirs=extra_add_dirs,
            timeout_s=600.0,
        )
        return aggregate_text(result.events)


def _resolve_owner_wikis_factory(
    wiki_root: Path,
) -> Callable[[int], Awaitable[list[tuple[str, Path]]]]:
    """Return an async resolver: owner_telegram_id → [(wiki_dir_name, path), …] minus Inbox-WIKI."""

    async def _resolve(owner_telegram_id: int) -> list[tuple[str, Path]]:
        owner_dir = wiki_root / str(owner_telegram_id)
        if not owner_dir.is_dir():
            return []
        out: list[tuple[str, Path]] = []
        for entry in sorted(owner_dir.iterdir()):
            if not entry.is_dir() or not entry.name.endswith("-WIKI"):
                continue
            if entry.name == "Inbox-WIKI":
                continue
            out.append((entry.name, entry))
        return out

    return _resolve


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

    async def deliver(
        self,
        *,
        chat_id: int,
        telegram_id: int,
        run_id: str,
        text: str,
        tg_send: bool = True,
    ) -> None:
        await deliver_output(
            sender=self._sender,
            chat_id=chat_id,
            telegram_id=telegram_id,
            wiki_id=str(telegram_id),
            run_id=run_id,
            text=text,
            runs_dir=self._runs_dir,
            audit_session_maker=self._audit_session_maker,
            tg_send=tg_send,
        )


# START_BLOCK_INBOX_ROUTER_ADAPTER (aisw-dsg, Inbox-WIKI Phase-A)
_RawSource = Literal["text", "voice", "document", "photo"]


def _render_raw_sidecar(
    *, source: _RawSource, text: str, media_paths: list[Path] | None
) -> tuple[str, str]:
    """Return (filename, content) for the Inbox-WIKI/raw/<ts>_<source>.<ext> entry.

    text → a plain .md with the message body; media → a .md sidecar with a YAML
    front-matter (source, received_utc, staged_path[s]) plus the carried text
    (voice transcript / synthetic photo prompt / extracted document text). The
    binary itself stays in media_staging_root — its move into the target WIKI
    is Phase-B/Phase-E (aisw-zd9 / aisw-12t).
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{ts}_{source}.md"
    if source == "text":
        return filename, text if text.endswith("\n") else text + "\n"
    staged = [str(p) for p in (media_paths or [])]
    lines = [
        "---",
        f"source: {source}",
        f"received_utc: {ts}",
        f"staged_path: {staged[0] if staged else 'null'}",
    ]
    if len(staged) > 1:
        lines.append("staged_paths:")
        lines.extend(f"  - {p}" for p in staged)
    lines += ["---", "", "## Содержимое", "", text.rstrip("\n"), ""]
    return filename, "\n".join(lines)


class _RouterAdapter:
    """Inbox-WIKI Stage-1a router: materialise Inbox-WIKI, stage the raw payload,
    run Claude inside it with prompts/inbox.md, parse the reply (M-INBOX-ROUTER)."""

    def __init__(
        self,
        *,
        wiki_root: Path,
        inbox_template_path: Path,
        base_prompt_path: Path,
        inbox_overlay_path: Path,
        runtime_dir: Path,
        acquirer: WikiLockAdapter,
        spawner: AsyncioSpawner,
        run_config: _RunConfig,
    ) -> None:
        self._wiki_root = wiki_root
        self._inbox_template_path = inbox_template_path
        self._base_prompt_path = base_prompt_path
        self._inbox_overlay_path = inbox_overlay_path
        self._runtime_dir = runtime_dir
        self._acquirer = acquirer
        self._spawner = spawner
        self._run_config = run_config

    async def route(
        self,
        *,
        text: str,
        telegram_id: int,
        correlation_id: str,
        source: _RawSource,
        media_paths: list[Path] | None = None,
        timeout_s: float | None = None,
    ) -> RouterDecision:
        inbox_dir = await ensure_inbox_wiki(
            telegram_id,
            wiki_root=self._wiki_root,
            template_path=self._inbox_template_path,
        )
        raw_path = await asyncio.to_thread(
            self._write_raw, inbox_dir, source=source, text=text, media_paths=media_paths
        )
        logger.info(
            "inbox.router.staged_raw",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            source=source,
            raw_path=str(raw_path),
        )
        wiki_id = f"{telegram_id}/Inbox-WIKI"
        run_id = f"router-{uuid4().hex[:12]}"
        logger.info(
            "inbox.router.run.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            wiki_id=wiki_id,
            run_id=run_id,
            source=source,
            media_count=len(media_paths) if media_paths else 0,
        )
        try:
            result = await run_wiki_session(
                wiki_id=wiki_id,
                wiki_path=inbox_dir,
                base_prompt_path=self._base_prompt_path,
                overlay_prompt_path=self._inbox_overlay_path,
                run_id=run_id,
                correlation_id=correlation_id,
                runtime_dir=self._runtime_dir,
                acquirer=self._acquirer,
                spawner=self._spawner,
                config=self._run_config,
                user_input=text,
                media_paths=media_paths,
                timeout_s=timeout_s,
            )
        except WikiRunnerError as e:
            raise RouterError(str(e)) from e
        reply_text = aggregate_text(result.events)
        logger.info(
            "inbox.router.run.done",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            wiki_id=wiki_id,
            run_id=run_id,
            latency_ms=result.latency_ms,
            chars=len(reply_text),
        )
        decision = parse_router_reply(reply_text)
        if not decision.parsed_ok:
            logger.info(
                "inbox.router.parse_error",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                run_id=run_id,
                raw_preview=reply_text[:200],
            )
        logger.info(
            "inbox.router.parsed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=run_id,
            intent=decision.intent.value,
            target_wiki=decision.target_wiki,
            parsed_ok=decision.parsed_ok,
        )
        return decision

    def _write_raw(
        self,
        inbox_dir: Path,
        *,
        source: _RawSource,
        text: str,
        media_paths: list[Path] | None,
    ) -> Path:
        raw_dir = inbox_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        filename, content = _render_raw_sidecar(source=source, text=text, media_paths=media_paths)
        target = raw_dir / filename
        tmp = raw_dir / f"{filename}.tmp"
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
        return target


# END_BLOCK_INBOX_ROUTER_ADAPTER


# START_BLOCK_INBOX_LIBRARIAN_ADAPTER (aisw-zd9, Inbox-WIKI Phase-B)
class _LibrarianAdapter:
    """Inbox-WIKI Stage-1b librarian: resolve/create the target <Domain>-WIKI from a
    RouterDecision, move the raw payload into it, run Claude there (prompts/wiki.md +
    a domain overlay) to ingest, and compose the user reply (M-INBOX-ROUTE)."""

    def __init__(
        self,
        *,
        wiki_root: Path,
        prompts_dir: Path,
        lifecycle: WikiLifecycleManager,
        runtime_dir: Path,
        acquirer: WikiLockAdapter,
        spawner: AsyncioSpawner,
        run_config: _RunConfig,
    ) -> None:
        self._wiki_root = wiki_root
        self._prompts_dir = prompts_dir
        self._lifecycle = lifecycle
        self._runtime_dir = runtime_dir
        self._acquirer = acquirer
        self._spawner = spawner
        self._run_config = run_config

    async def ingest(
        self,
        decision: RouterDecision,
        *,
        telegram_id: int,
        user_text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None = None,
        correlation_id: str,
    ) -> IngestOutcome:
        def _on_route_missing() -> None:
            logger.warning(
                "inbox.route.route_target_was_missing",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                target_wiki=decision.target_wiki,
            )

        target = resolve_target_wiki(
            decision,
            lifecycle=self._lifecycle,
            owner=telegram_id,
            wiki_root=self._wiki_root,
            default_template_id="_default",
            on_route_missing=_on_route_missing,
        )
        if isinstance(target, RouteRejection):
            logger.info(
                f"inbox.route.{'cap_reached' if target.reason == 'cap' else 'bad_name'}",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                target_wiki=decision.target_wiki,
            )
            return IngestOutcome(
                status="rejected",
                reply=f"{decision.notes}\n\n{target.hint}",
                run_id=None,
                target_wiki=None,
                created=False,
            )
        logger.info(
            "inbox.route.target_resolved",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            target_wiki=target.wiki_name.primary,
            created=target.created,
        )
        staged = await asyncio.to_thread(
            stage_raw_into_wiki,
            target.wiki_dir,
            source=source,
            user_text=user_text,
            media_paths=media_paths,
        )
        overlay = pick_domain_overlay(self._prompts_dir, target.wiki_name.slug)
        prompt = build_ingest_prompt(user_text, staged)
        run_id = f"ingest-{uuid4().hex[:12]}"
        wiki_id = f"{telegram_id}/{target.wiki_name.primary}"
        logger.info(
            "inbox.route.ingest.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            wiki_id=wiki_id,
            run_id=run_id,
            source=source,
            media_count=len(staged.media_abs),
        )
        try:
            result = await run_wiki_session(
                wiki_id=wiki_id,
                wiki_path=target.wiki_dir,
                base_prompt_path=self._prompts_dir / "wiki.md",
                overlay_prompt_path=overlay,
                run_id=run_id,
                correlation_id=correlation_id,
                runtime_dir=self._runtime_dir,
                acquirer=self._acquirer,
                spawner=self._spawner,
                config=self._run_config,
                user_input=prompt,
                media_paths=staged.media_abs or None,
                timeout_s=None,
            )
        except WikiRunnerError:
            logger.exception(
                "inbox.route.ingest_failed",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                wiki_id=wiki_id,
                run_id=run_id,
                error_class="WikiRunnerError",
            )
            return IngestOutcome(
                status="run_failed",
                reply=f"{decision.notes}\n\nНе удалось разложить по полочкам — попробую позже.",  # noqa: RUF001
                run_id=run_id,
                target_wiki=target.wiki_name.primary,
                created=target.created,
            )
        summary = aggregate_text(result.events)
        logger.info(
            "inbox.route.ingest.done",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            wiki_id=wiki_id,
            run_id=run_id,
            latency_ms=result.latency_ms,
            chars=len(summary),
        )
        return IngestOutcome(
            status="ok",
            reply=f"{decision.notes}\n\n{summary or '(WIKI обновлена)'}",
            run_id=run_id,
            target_wiki=target.wiki_name.primary,
            created=target.created,
        )


# END_BLOCK_INBOX_LIBRARIAN_ADAPTER


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
    jobs_maker = build_sessionmaker(jobs_engine)
    audit_maker = build_sessionmaker(audit_engine)
    sessions_maker = build_sessionmaker(sessions_engine)

    users_cfg = _load_users_config(settings.users_toml_path)
    await sync_to_sessions_db(users_cfg, sessions_maker)
    allowlist = replace_global(users_cfg)

    scheduler = build_scheduler(_sync_url_for_jobstore(settings.jobs_db_url))
    scheduler.start()
    scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
    retention_jobs = register_all_retention_jobs(
        scheduler,
        audit_maker=audit_maker,
        jobs_maker=jobs_maker,
        sessions_maker=sessions_maker,
        dry_run=settings.retention_dry_run,
        snapshot_root=settings.snapshot_dir,
        db_urls_for_snapshot={
            "jobs": settings.jobs_db_url,
            "audit": settings.audit_db_url,
            "sessions": settings.sessions_db_url,
        },
        snapshot_retention_days=settings.snapshot_retention_days,
        media_staging_root=settings.media_staging_root,
    )
    logger.info(
        "runtime.scheduler.started",
        jobs_url=settings.jobs_db_url,
        media_staging_root=str(settings.media_staging_root),
        retention_job_ids=[getattr(j, "id", None) for j in retention_jobs],
    )

    bot = build_bot(settings.tg_bot_token.get_secret_value())
    sender = AiogramSender(bot)
    # aisw-kcz: install the reminder-firing context (picklable int-arg fire_job
    # reads the bot-sender + jobs sessionmaker from here at fire time).
    firing.set_firing_context(sender=sender, jobs_session_maker=jobs_maker)

    # START_BLOCK_TEXT_PIPELINE_WIRING (chunk 20 M-TG-PIPELINE-CLASSIFIER)
    classifier_backend = _build_classifier_backend(settings)
    classifier_adapter = _ClassifierAdapter(
        backend=classifier_backend,
        prompt_path=settings.prompts_dir / "classifier.md",
        audit_session_maker=audit_maker,
        cache=PromptCache(),
    )
    # aisw-kcz: NL-time parser for the reminder fast-path. Uses prompts/time-parse.md
    # as the Haiku-fallback prompt when dateparser misses; if absent the parser just
    # escalates (the file is shipped in prompts/, so this path is normally taken).
    time_parse_prompt = settings.prompts_dir / "time-parse.md"
    time_parser_adapter = _TimeParserAdapter(
        backend=classifier_backend,
        prompt_path=time_parse_prompt if time_parse_prompt.exists() else None,
    )
    _users_by_id = {u.telegram_id: u for u in users_cfg.users}

    def _user_tz_lookup(telegram_id: int) -> str | None:
        u = _users_by_id.get(telegram_id)
        return u.tz if u is not None else None

    runtime_dir = settings.workspace_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_manager = WikiLockManager()
    if settings.claude_config_dir is None:
        raise RuntimeError(
            "claude_config_dir is required for wiki runner; set "
            "AISW_CLAUDE_CONFIG_DIR_LOCAL or AISW_CLAUDE_CONFIG_DIR_VPS"
        )
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
    # aisw-oqq: recurring-digest fast-path parser + digest firing context.
    recurrence_parser_adapter = _RecurrenceParserAdapter()
    digest_runner_adapter = _DigestRunnerAdapter(
        base_prompt_path=settings.prompts_dir / "wiki.md",
        digest_prompt_path=settings.prompts_dir / "digest.md",
        runtime_dir=runtime_dir,
        acquirer=WikiLockAdapter(lock_manager),
        spawner=AsyncioSpawner(),
        run_config=_RunConfig(
            model=settings.wiki_runner_model,
            timeout_s=600.0,
            term_grace_s=settings.wiki_runner_term_grace_s,
            claude_config_dir=settings.claude_config_dir,
        ),
    )
    firing.set_digest_context(
        scheduler=scheduler,
        runner=digest_runner_adapter,
        resolve_owner_wikis=_resolve_owner_wikis_factory(settings.wiki_root),
        jobs_session_maker=jobs_maker,
        audit_session_maker=audit_maker,
        sender=sender,
    )
    router_adapter = _RouterAdapter(
        wiki_root=settings.wiki_root,
        inbox_template_path=settings.wiki_template_dir / "inbox-wiki" / "CLAUDE.md",
        base_prompt_path=settings.prompts_dir / "wiki.md",
        inbox_overlay_path=settings.prompts_dir / "inbox.md",
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
    librarian_adapter = _LibrarianAdapter(
        wiki_root=settings.wiki_root,
        prompts_dir=settings.prompts_dir,
        lifecycle=WikiLifecycleManager(
            settings.wiki_root,
            max_per_user=settings.wiki_max_per_user,
            retention_days=settings.wiki_trash_retention_days,
        ),
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

    # START_BLOCK_MEDIA_PIPELINE_WIRING (aisw-zny, media chunk 1, D-022)
    voice_handler: VoiceHandler | None = None
    if settings.voice_enabled:
        voice_handler = VoiceHandler(
            FasterWhisperTranscriber(model_size=settings.voice_whisper_model_size),
            inbox_root=settings.media_staging_root,
        )
    photo_ingestor: PhotoIngestor | None = (
        PhotoIngestor(inbox_root=settings.media_staging_root) if settings.photo_enabled else None
    )
    logger.info(
        "runtime.media_pipeline.wired",
        voice=voice_handler is not None,
        photo=photo_ingestor is not None,
        whisper_model=settings.voice_whisper_model_size if voice_handler is not None else None,
    )
    # END_BLOCK_MEDIA_PIPELINE_WIRING

    streaming_delivery = DefaultStreamingDelivery(sender=sender)
    pipeline = DefaultPipeline(
        sender=sender,
        idempotency=IdempotencyService(audit_maker),
        confirmation=ConfirmationService(sender, sessions_maker),
        voice=voice_handler,
        photo=photo_ingestor,
        classifier=classifier_adapter,
        runner=runner_adapter,
        output=output_adapter,
        streaming=streaming_delivery,
        router=router_adapter,
        librarian=librarian_adapter,
        pii=PIIRedactor(hash_secret=settings.pii_hash_secret.get_secret_value().encode("utf-8")),
        photo_vision_timeout_s=settings.photo_vision_timeout_s,
        time_parser=time_parser_adapter,
        recurrence_parser=recurrence_parser_adapter,
        jobs_session_maker=jobs_maker,
        scheduler=scheduler,
        user_tz_lookup=_user_tz_lookup,
        default_user_tz=settings.default_user_tz,
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
