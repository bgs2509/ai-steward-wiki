# FILE: src/ai_steward_wiki/__main__.py
# VERSION: 0.5.8
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
#          _resolve_owner_wikis_factory, make_hint_catalog_resolver,
#          _OutputDeliveryAdapter, _RouterAdapter, _render_raw_sidecar,
#          _LibrarianAdapter.
#   DEPENDS: aiogram, apscheduler, alembic, structlog, sqlalchemy.async,
#            ai_steward_wiki.{settings, logging_setup, tg.bot, tg.pipeline,
#            tg.output, tg.voice, tg.photo, scheduler.core, scheduler.locks,
#            scheduler.maintenance, scheduler.firing, inbox.materialize, inbox.router,
#            inbox.route, inbox.staging, inbox.hint_cache,
#            classifier.{backend,schema,stage0,time_parse,recurrence},
#            wiki.{runner,acquire,lifecycle},
#            storage.{jobs,audit,sessions}.engine, storage.sessions.users, auth.{allowlist,users_toml}}
#   LINKS: M-FOUNDATION, M-STORAGE, M-AUTH-USERS, M-SCHEDULER, M-SCHEDULER-FIRING,
#          M-CLASSIFIER-STAGE0, M-CLASSIFIER-RECURRENCE, M-WIKI-RUNNER, M-WIKI-LIFECYCLE,
#          M-TG-OUTPUT, M-TG-PIPELINE-CLASSIFIER, M-TG-VOICE, M-TG-PHOTO, M-INBOX,
#          M-INBOX-ROUTER, M-INBOX-ROUTE, M-DEPLOY
#   ROLE: RUNTIME
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.5.8 - aisw-02v (walking skeleton): wire the cron-user vertical
#                slice. Create one PriorityJobQueue shared between the
#                APScheduler-fired producer callback
#                (scheduler.cron_user.fire_cron_user_job; context installed via
#                cron_user.set_cron_user_context(scheduler, queue, jobs_maker))
#                and the single-task async consumer
#                (scheduler.consumer.CronConsumer.run() spawned as
#                'aisw.cron_consumer' next to dp.start_polling, cancelled on
#                shutdown before scheduler.shutdown). build_dispatcher now
#                takes get_user_tz: telegram_id → IANA tz (users.toml entry's
#                tz with default_user_tz fallback) and forwards it into
#                build_router → tg.cron_add.register_cron_add_handlers for the
#                /cron_add Command handler.
#   PREVIOUS:    v0.5.7 - aisw-6mi: boot-time _purge_legacy_maintenance_jobs runs
#                after scheduler.start() and before register_all_retention_jobs to
#                drop pre-fix maintenance rows from jobs.db (legacy ids land in
#                the new "memory" jobstore instead). Eliminates the
#                AttributeError: Can't get local object 'create_engine.<locals>.connect'
#                crash on boot.
#   PREVIOUS:    v0.5.6 - aisw-163 P5: install reminder-card callback context via
#                tg.callbacks.set_callback_context(CallbackContext(scheduler,
#                jobs_session_maker=jobs_maker)) right after set_firing_context.
#                Enables `r:<id>:{done|snz|skp}` button taps to mutate state.
#   PREVIOUS:    v0.5.5 - aisw-pv8 (Inbox-WIKI Phase-D.b.2c): pass the sessions
#                sessionmaker into firing.set_digest_context(sessions_session_maker=
#                sessions_maker) so the digest firing path can read/write
#                user_digest_prefs (per-user digest section toggles).
#   PREVIOUS:    v0.5.4 - aisw-5sd (Inbox-WIKI Phase-E.b): wire the '## Inbox hint'
#                fast-path — make_hint_catalog_resolver (telegram_id → {stem: hint_text}
#                via InboxHintCacheRepo + get_or_refresh_hint per domain WIKI; surrogate
#                user_id via storage.sessions.users.resolve_user_id) → DefaultPipeline
#                hint_catalog_resolver=.
#   PREVIOUS:    v0.5.3 - aisw-12t (Inbox-WIKI Phase-E.a): per-user media staging —
#                VoiceHandler/PhotoIngestor built without a fixed inbox_root;
#                DefaultPipeline(wiki_root=settings.wiki_root) so on_voice/on_photo
#                stage under <wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging;
#                register_all_retention_jobs(wiki_root_for_media_sweep=settings.wiki_root)
#                — the daily media sweep now iterates every per-user Inbox-WIKI.
#   PREVIOUS:    v0.5.2 - aisw-269 (Inbox-WIKI Phase-D.b.2b): _DigestRunnerAdapter
#                gains digest_expand_prompt_path + a section: str|None=None arg —
#                section None ⇒ prompts/digest.md (byte-identical), a section key ⇒
#                prompts/digest_expand.md with the section name in user_input (for
#                /expand); the firing slash-command accessors read the same digest
#                context, so no extra wiring beyond the new prompt path;
#                owner_wikis_resolver (= _resolve_owner_wikis_factory, reused from
#                set_digest_context) is also passed into DefaultPipeline for the
#                digest fast-path's named-subset WIKI extraction.
#   PREVIOUS:    v0.5.1 - aisw-w3k (Inbox-WIKI Phase-D.b.2a): digest delivery routed
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
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.jobstores.base import JobLookupError
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
from ai_steward_wiki.claude_cli.common import default_claude_config_dir
from ai_steward_wiki.inbox.hint_cache import InboxHintCacheRepo, get_or_refresh_hint
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.inbox.materialize import ensure_inbox_wiki
from ai_steward_wiki.inbox.route import (
    RouteRejection,
    build_ingest_prompt,
    pick_domain_overlay,
    resolve_target_wiki,
    stage_raw_into_wiki,
)
from ai_steward_wiki.inbox.router import (
    RouterDecision,
    RouterError,
    build_router_input,
    parse_router_reply,
)
from ai_steward_wiki.inbox.staging import promote_path_to_raw
from ai_steward_wiki.logging_setup import configure_logging
from ai_steward_wiki.ops.observability import (
    enable_faulthandler,
    install_sigusr1,
    run_heartbeat,
)
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.scheduler import cron_user as cron_user_mod
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.consumer import CronConsumer
from ai_steward_wiki.scheduler.core import build_scheduler
from ai_steward_wiki.scheduler.locks import WikiLockManager
from ai_steward_wiki.scheduler.maintenance import (
    MEDIA_STAGING_SWEEP_JOB_ID,
    PURGE_PENDING_JOB_ID,
    register_all_retention_jobs,
)
from ai_steward_wiki.scheduler.queue import PriorityJobQueue
from ai_steward_wiki.settings import Settings, get_settings
from ai_steward_wiki.storage.audit.engine import build_engine, build_sessionmaker
from ai_steward_wiki.storage.sessions.users import resolve_user_id
from ai_steward_wiki.tg.aggregator import InboxAggregator
from ai_steward_wiki.tg.bot import (
    AiogramSender,
    TgSender,
    build_bot,
    build_dispatcher,
    register_bot_commands,
)
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.handlers import BotLoaderControl
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
    WRITE_TOOLS,
    AsyncioSpawner,
    WikiRunnerError,
    WikiRunnerTimeoutError,
    _RunConfig,
    aggregate_text,
    run_wiki_session,
)
from ai_steward_wiki.wiki.schema_gen import (
    ClaudeCliSchemaGenerator,
    SchemaGenerator,
    apply_generated_schema,
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


# START_BLOCK_PURGE_LEGACY_MAINTENANCE
def _purge_legacy_maintenance_jobs(scheduler: object) -> int:
    """Drop maintenance/retention/snapshot jobs leftover in the default jobstore.

    Before aisw-6mi maintenance cron was (attempted to be) registered in the
    SQLAlchemyJobStore (``default``). Once we move them to the in-memory
    jobstore (``memory``), any pre-existing rows in ``jobs.db`` would shadow
    the new registrations by id. This one-time cleanup runs after
    ``scheduler.start()`` and before re-registration, removing the legacy ids
    from ``default`` only. User reminder jobs (any id not in the allowlist or
    matching the ``retention.`` prefix) remain untouched.
    """
    legacy_ids: frozenset[str] = frozenset(
        {PURGE_PENDING_JOB_ID, MEDIA_STAGING_SWEEP_JOB_ID, "ops.db_snapshot"}
    )
    removed = 0
    # mypy-friendly duck-typing — APScheduler exposes get_jobs/remove_job.
    get_jobs = getattr(scheduler, "get_jobs")  # noqa: B009
    remove_job = getattr(scheduler, "remove_job")  # noqa: B009
    for job in list(get_jobs(jobstore="default")):
        job_id = getattr(job, "id", "")
        if job_id in legacy_ids or job_id.startswith("retention."):
            try:
                remove_job(job_id, jobstore="default")
                removed += 1
            except JobLookupError:
                continue
    if removed:
        logger.info("scheduler.bootstrap.legacy_maintenance_purged", removed=removed)
    return removed


# END_BLOCK_PURGE_LEGACY_MAINTENANCE


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
        digest_expand_prompt_path: Path,
        runtime_dir: Path,
        acquirer: WikiLockAdapter,
        spawner: AsyncioSpawner,
        run_config: _RunConfig,
    ) -> None:
        self._base_prompt_path = base_prompt_path
        self._digest_prompt_path = digest_prompt_path
        self._digest_expand_prompt_path = digest_expand_prompt_path
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
        section: str | None = None,
    ) -> str:
        # section is None ⇒ the full D-024 digest (prompts/digest.md, byte-identical
        # to aisw-oqq/w3k); a section key ⇒ on-demand detail (prompts/digest_expand.md,
        # the section name passed in user_input) — aisw-269 /expand.
        if section is None:
            overlay_prompt_path = self._digest_prompt_path
            user_input = planner_context
            run_prefix = "digest"
        else:
            overlay_prompt_path = self._digest_expand_prompt_path
            user_input = f"Детализируй раздел сводки: {section}"
            run_prefix = "expand"
        wiki_path.mkdir(parents=True, exist_ok=True)
        run_id = f"{run_prefix}-{uuid4().hex[:12]}"
        result = await run_wiki_session(
            wiki_id=wiki_id,
            wiki_path=wiki_path,
            base_prompt_path=self._base_prompt_path,
            overlay_prompt_path=overlay_prompt_path,
            run_id=run_id,
            correlation_id=correlation_id,
            runtime_dir=self._runtime_dir,
            acquirer=self._acquirer,
            spawner=self._spawner,
            config=self._run_config,
            user_input=user_input,
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


def make_hint_catalog_resolver(
    *,
    hint_repo: InboxHintCacheRepo,
    owner_wikis_resolver: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
    surrogate_id_of: Callable[[int], Awaitable[int | None]],
) -> Callable[[int], Awaitable[dict[str, str]]]:
    """telegram_id → {wiki_stem: hint_text} from each domain WIKI's cached '## Inbox hint' (aisw-5sd).

    Reuses inbox.hint_cache.get_or_refresh_hint per domain (stat→cache-hit, so a hot
    message does zero filesystem reads). Empty dict if the sender has no users row yet
    or no domain WIKIs — the fast-path then just falls through to the heavy router.
    """

    async def _resolve(telegram_id: int) -> dict[str, str]:
        uid = await surrogate_id_of(telegram_id)
        if uid is None:
            return {}
        catalog: dict[str, str] = {}
        for stem, dir_path in await owner_wikis_resolver(telegram_id):
            hint = await get_or_refresh_hint(hint_repo, uid, dir_path / "CLAUDE.md")
            if hint:
                catalog[stem] = hint
        return catalog

    return _resolve


class _OutputDeliveryAdapter:
    """Bind tg.output.deliver_output into the narrow OutputDelivery Protocol."""

    def __init__(
        self,
        *,
        sender: TgSender,
        runs_dir: Path,
        audit_session_maker: async_sessionmaker[AsyncSession],
        audit_io_threshold_ms: int = 1000,
    ) -> None:
        self._sender = sender
        self._runs_dir = runs_dir
        self._audit_session_maker = audit_session_maker
        self._audit_io_threshold_ms = audit_io_threshold_ms

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
            audit_io_threshold_ms=self._audit_io_threshold_ms,
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
    binary itself stays in the sender's Inbox-WIKI/raw/media/_staging until
    promotion into the target WIKI on a successful run (aisw-8r9 / aisw-12t).
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

    def _list_existing_wikis(self, telegram_id: int) -> list[str]:
        """Owner's existing <Domain>-WIKI dir names (minus Inbox-WIKI) for router context (aisw-2co)."""
        owner_dir = self._wiki_root / str(telegram_id)
        if not owner_dir.is_dir():
            return []
        return [
            entry.name
            for entry in sorted(owner_dir.iterdir())
            if entry.is_dir() and entry.name.endswith("-WIKI") and entry.name != "Inbox-WIKI"
        ]

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
        existing_wikis = self._list_existing_wikis(telegram_id)
        router_input = build_router_input(text, existing_wikis)
        logger.info(
            "inbox.router.existing_wikis",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            count=len(existing_wikis),
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
                user_input=router_input,
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


_INGEST_META_FILES = frozenset({"CLAUDE.md", "log.md", "index.md", "README.md", ".gitkeep"})
_INGEST_SKIP_DIRS = frozenset({"raw", "runs"})


def _wiki_has_ingested_content(wiki_dir: Path) -> bool:
    """True if the WIKI holds ingested data beyond scaffold/meta (aisw-zpn).

    Used after an ingest timeout to tell partial-success from total failure. Ignores
    meta files (CLAUDE.md/log.md/index.md/README.md/.gitkeep) and the raw/ + runs/
    staging dirs — any other file means the model wrote real content before the kill.
    """
    for path in wiki_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(wiki_dir)
        if rel.parts and rel.parts[0] in _INGEST_SKIP_DIRS:
            continue
        if path.name in _INGEST_META_FILES:
            continue
        return True
    return False


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
        schema_generator: SchemaGenerator | None = None,
        ingest_timeout_s: float | None = None,
    ) -> None:
        self._wiki_root = wiki_root
        self._prompts_dir = prompts_dir
        self._lifecycle = lifecycle
        self._runtime_dir = runtime_dir
        self._acquirer = acquirer
        self._spawner = spawner
        self._run_config = run_config
        # aisw-b50: generates a tailored schema for unknown-domain WIKIs at create.
        # None disables generation (tests / known-domain-only deployments).
        self._schema_generator = schema_generator
        # aisw-zpn: per-run timeout for the (heavier) create+ingest path. None →
        # fall back to run_config.timeout_s (the general 300s query budget).
        self._ingest_timeout_s = ingest_timeout_s

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
        # aisw-b50: a freshly-created WIKI of an UNKNOWN domain (no static preset)
        # gets an LLM-generated, topic-tailored schema before the ingest run reads
        # CLAUDE.md. Known domains already carry their preset; failures keep _default.
        if (
            target.created
            and self._schema_generator is not None
            and self._lifecycle.resolve_template_id(decision.target_wiki or "") == "_default"
        ):
            await apply_generated_schema(
                claude_md=target.wiki_dir / "CLAUDE.md",
                wiki_name=target.wiki_name.primary,
                first_content=user_text,
                correlation_id=correlation_id,
                generator=self._schema_generator,
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
                timeout_s=self._ingest_timeout_s,
            )
        except WikiRunnerTimeoutError:
            # aisw-zpn: a large document can exceed the ingest budget AFTER writing
            # part of the data (Write files persist through the kill). Tell the user
            # honestly and let a re-send complete it (soft-resume), instead of the
            # generic "failed" that hides the partial data.
            partial = await asyncio.to_thread(_wiki_has_ingested_content, target.wiki_dir)
            logger.warning(
                "inbox.route.ingest_timeout",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                wiki_id=wiki_id,
                run_id=run_id,
                partial=partial,
            )
            if partial:
                return IngestOutcome(
                    status="partial",
                    reply=(
                        f"{decision.notes}\n\n"
                        "Документ большой — занёс частично. "
                        "Пришли его ещё раз, чтобы дозанести остальное."  # noqa: RUF001
                    ),
                    run_id=run_id,
                    target_wiki=target.wiki_name.primary,
                    created=target.created,
                )
            return IngestOutcome(
                status="run_failed",
                reply=f"{decision.notes}\n\nНе удалось разложить по полочкам — попробую позже.",  # noqa: RUF001
                run_id=run_id,
                target_wiki=target.wiki_name.primary,
                created=target.created,
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
    return ClaudeCliBackend(
        claude_config_dir=default_claude_config_dir(),
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


def _require_claude_config_dir() -> None:
    """Fail fast at startup if the Claude CLI config dir is missing (ADR-009).

    The bot uses the run user's default ~/.claude, which holds the subscription
    auth (credentials.json) read by every Stage-1 CLI run. A missing/unauthenticated
    dir means the CLI cannot authenticate and the bot would fail silently on the
    first classification, so we stop here with an actionable message instead.
    """
    config_dir = default_claude_config_dir()
    if not config_dir.is_dir():
        raise RuntimeError(
            f"Claude config dir does not exist: {config_dir}. Authenticate the "
            f"run user once: `claude login` (creates {config_dir})."
        )


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

    _require_claude_config_dir()

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
    _purge_legacy_maintenance_jobs(scheduler)
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
        wiki_root_for_media_sweep=settings.wiki_root,
    )
    logger.info(
        "runtime.scheduler.started",
        jobs_url=settings.jobs_db_url,
        wiki_root=str(settings.wiki_root),
        retention_job_ids=[getattr(j, "id", None) for j in retention_jobs],
    )

    bot = build_bot(settings.tg_bot_token.get_secret_value())
    sender = AiogramSender(bot, io_slow_threshold_ms=settings.obs_io_slow_threshold_ms)
    # aisw-kcz: install the reminder-firing context (picklable int-arg fire_job
    # reads the bot-sender + jobs sessionmaker from here at fire time).
    firing.set_firing_context(sender=sender, jobs_session_maker=jobs_maker)

    # aisw-163 P5: install the reminder-card callback context. The on_reminder_card
    # handler (registered in tg.handlers) reads the scheduler + jobs sessionmaker
    # from here when a `r:<id>:{done|snz|skp}` button is tapped.
    from ai_steward_wiki.tg.callbacks import CallbackContext, set_callback_context

    set_callback_context(CallbackContext(scheduler=scheduler, jobs_session_maker=jobs_maker))

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
            claude_config_dir=default_claude_config_dir(),
            allowed_tools=WRITE_TOOLS,  # aisw-t6w: ingest/wiki edits must write under dontAsk
        ),
    )
    # aisw-oqq: recurring-digest fast-path parser + digest firing context.
    recurrence_parser_adapter = _RecurrenceParserAdapter()
    digest_runner_adapter = _DigestRunnerAdapter(
        base_prompt_path=settings.prompts_dir / "wiki.md",
        digest_prompt_path=settings.prompts_dir / "digest.md",
        digest_expand_prompt_path=settings.prompts_dir / "digest_expand.md",
        runtime_dir=runtime_dir,
        acquirer=WikiLockAdapter(lock_manager),
        spawner=AsyncioSpawner(),
        run_config=_RunConfig(
            model=settings.wiki_runner_model,
            timeout_s=600.0,
            term_grace_s=settings.wiki_runner_term_grace_s,
            claude_config_dir=default_claude_config_dir(),
            allowed_tools=WRITE_TOOLS,  # aisw-t6w: digest expand writes into WIKIs
        ),
    )
    owner_wikis_resolver = _resolve_owner_wikis_factory(settings.wiki_root)
    firing.set_digest_context(
        scheduler=scheduler,
        runner=digest_runner_adapter,
        resolve_owner_wikis=owner_wikis_resolver,
        jobs_session_maker=jobs_maker,
        audit_session_maker=audit_maker,
        sender=sender,
        sessions_session_maker=sessions_maker,
    )

    # aisw-02v (walking skeleton): cron-user producer + queue consumer.
    # The PriorityJobQueue is shared between the APScheduler-fired producer
    # callback (scheduler.cron_user.fire_cron_user_job) and the single-task
    # async consumer (scheduler.consumer.CronConsumer.run()).
    cron_user_queue = PriorityJobQueue()
    cron_user_mod.set_cron_user_context(scheduler, cron_user_queue, jobs_maker)
    cron_consumer = CronConsumer(
        queue=cron_user_queue,
        bot=bot,
        claude_binary=settings.claude_cli_binary,
        claude_config_dir=default_claude_config_dir(),
        prompt_path=settings.cron_user_prompt_path,
        jobs_session_maker=jobs_maker,
        timeout_s=settings.cron_user_timeout_s,
        slice_name=settings.cron_user_slice_name,
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
            # aisw-t6w: router is read-only (classify/route only) — no allowed_tools,
            # so dontAsk keeps Write/Edit denied here by design.
            model=settings.wiki_runner_model,
            timeout_s=settings.wiki_runner_timeout_s,
            term_grace_s=settings.wiki_runner_term_grace_s,
            claude_config_dir=default_claude_config_dir(),
        ),
    )
    librarian_adapter = _LibrarianAdapter(
        wiki_root=settings.wiki_root,
        prompts_dir=settings.prompts_dir,
        lifecycle=WikiLifecycleManager(
            settings.wiki_root,
            max_per_user=settings.wiki_max_per_user,
            retention_days=settings.wiki_trash_retention_days,
            templates_dir=settings.wiki_template_dir,  # aisw-db6: render schema into managed zone
        ),
        runtime_dir=runtime_dir,
        acquirer=WikiLockAdapter(lock_manager),
        spawner=AsyncioSpawner(),
        run_config=_RunConfig(
            model=settings.wiki_runner_model,
            timeout_s=settings.wiki_runner_timeout_s,
            term_grace_s=settings.wiki_runner_term_grace_s,
            claude_config_dir=default_claude_config_dir(),
            allowed_tools=WRITE_TOOLS,  # aisw-t6w: librarian creates/edits WIKI files
        ),
        schema_generator=ClaudeCliSchemaGenerator(  # aisw-b50: tailored schema for unknown domains
            claude_config_dir=default_claude_config_dir(),
            prompt_path=settings.prompts_dir / "schema-gen.md",
            model=settings.wiki_runner_model,
        ),
        ingest_timeout_s=settings.wiki_ingest_timeout_s,  # aisw-zpn: larger budget for big docs
    )
    runs_dir = settings.workspace_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_adapter = _OutputDeliveryAdapter(
        sender=sender,
        runs_dir=runs_dir,
        audit_session_maker=audit_maker,
        audit_io_threshold_ms=settings.obs_io_slow_threshold_ms,
    )
    logger.info(
        "runtime.text_pipeline.wired",
        backend=classifier_backend.name,
        model=classifier_backend.model,
        wiki_root=str(settings.wiki_root),
    )
    # END_BLOCK_TEXT_PIPELINE_WIRING

    # START_BLOCK_MEDIA_PIPELINE_WIRING (aisw-zny, media chunk 1, D-022)
    # aisw-12t (Phase-E.a): the staging root is per-sender and resolved by the
    # pipeline at message time (DefaultPipeline(wiki_root=…) → inbox_wiki_path);
    # the handlers are built without a fixed inbox_root.
    voice_handler: VoiceHandler | None = None
    if settings.voice_enabled:
        voice_handler = VoiceHandler(
            FasterWhisperTranscriber(model_size=settings.voice_whisper_model_size),
        )
    photo_ingestor: PhotoIngestor | None = PhotoIngestor() if settings.photo_enabled else None
    logger.info(
        "runtime.media_pipeline.wired",
        voice=voice_handler is not None,
        photo=photo_ingestor is not None,
        whisper_model=settings.voice_whisper_model_size if voice_handler is not None else None,
    )
    # END_BLOCK_MEDIA_PIPELINE_WIRING

    streaming_delivery = DefaultStreamingDelivery(sender=sender)
    hint_catalog_resolver = make_hint_catalog_resolver(
        hint_repo=InboxHintCacheRepo(sessions_maker),
        owner_wikis_resolver=owner_wikis_resolver,
        surrogate_id_of=lambda tid: resolve_user_id(sessions_maker, tid),
    )
    pipeline = DefaultPipeline(
        sender=sender,
        idempotency=IdempotencyService(
            audit_maker,
            ttl_text_seconds=settings.l2_ttl_text_seconds,
            ttl_binary_seconds=settings.l2_ttl_binary_seconds,
        ),
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
        owner_wikis_resolver=owner_wikis_resolver,
        hint_catalog_resolver=hint_catalog_resolver,
        jobs_session_maker=jobs_maker,
        scheduler=scheduler,
        user_tz_lookup=_user_tz_lookup,
        default_user_tz=settings.default_user_tz,
        wiki_root=settings.wiki_root,
    )
    # aisw-s5i: wire /start unknown-id callback (records pending_users row).
    from ai_steward_wiki.auth.onboarding import PendingUserRepo, start_unknown_user

    _pending_repo = PendingUserRepo(sessions_maker)

    async def _on_start_unknown_cb(*, telegram_id: int, username: str | None) -> None:
        await start_unknown_user(_pending_repo, telegram_id, username=username)

    # aisw-02v: user_tz resolver for /cron_add — reads users.toml entry's tz
    # (loaded above into _users_by_id), falls back to Settings.default_user_tz.
    async def _resolve_user_tz(telegram_id: int) -> str:
        tz = _user_tz_lookup(telegram_id)
        return tz if tz is not None else settings.default_user_tz

    # aisw-378: debounce-aggregate a burst of split text messages into one
    # classify/route (fixes Telegram splitting a long paste across messages).
    message_aggregator = InboxAggregator(
        process=pipeline.on_text,
        loader=BotLoaderControl(bot),
        delay_s=settings.tg_aggregate_delay_s,
    )
    dp = build_dispatcher(
        allowlist,
        pipeline=pipeline,
        templates_dir=settings.wiki_template_dir,
        on_start_unknown=_on_start_unknown_cb,
        get_user_tz=_resolve_user_tz,
        aggregator=message_aggregator,
        handler_slow_threshold_ms=settings.obs_handler_slow_threshold_ms,
    )
    logger.info("runtime.handlers.registered")

    # aisw-s5i: publish the bot's command list so Telegram clients show
    # the native `≡` menu next to the message input.
    try:
        await register_bot_commands(bot)
    except Exception as exc:  # non-fatal: bot can run without the native menu
        logger.warning(
            "runtime.bot.commands.register_failed",
            error_class=type(exc).__name__,
        )

    loop = asyncio.get_running_loop()
    stop_event = _STOP_EVENT_FOR_TESTS if _STOP_EVENT_FOR_TESTS is not None else asyncio.Event()
    _install_signal_handlers(loop, stop_event)

    # aisw-xbc: event-loop hang diagnostics (diagnostics-only). faulthandler gives
    # C-level thread dumps that survive a wedged loop; SIGUSR1 dumps asyncio task
    # frames on demand; the heartbeat task is the proof-of-life whose ABSENCE in the
    # journal marks the freeze instant (and whose lag spike auto-triggers a dump).
    enable_faulthandler()
    install_sigusr1(loop)
    heartbeat_task = asyncio.create_task(run_heartbeat(settings), name="aisw.heartbeat")
    # END_BLOCK_RUNTIME_BOOTSTRAP

    # START_BLOCK_RUNTIME_POLLING
    logger.info("runtime.polling.start")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    stop_task = asyncio.create_task(stop_event.wait())
    # aisw-02v: cron-user queue consumer runs alongside polling; cancelled in
    # the shutdown block below.
    consumer_task = asyncio.create_task(cron_consumer.run(), name="aisw.cron_consumer")

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
        # aisw-xbc: stop the diagnostics heartbeat and drop the SIGUSR1 handler.
        if not heartbeat_task.done():
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat_task
        with contextlib.suppress(Exception):
            loop.remove_signal_handler(signal.SIGUSR1)
        # aisw-02v: cancel the cron-user consumer; CancelledError propagates
        # out of CronConsumer.run() so the await below completes cleanly.
        if not consumer_task.done():
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await consumer_task
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
