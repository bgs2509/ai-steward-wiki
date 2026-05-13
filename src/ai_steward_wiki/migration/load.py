# FILE: src/ai_steward_wiki/migration/load.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Side-effect layer of ETL. Takes a TargetPlan and applies it to
#            the running system: writes users.toml, bootstraps WIKIs via
#            WikiLifecycleManager, copies files (shutil.copy2 preserving
#            mtime), writes generated Markdown docs, INSERTs Job rows into
#            jobs.db in a single transaction. dry_run=True makes every op a
#            no-op (only logs and counts).
#   SCOPE: LoadReport, MigrationLoader.
#   DEPENDS: tomli_w, sqlalchemy.ext.asyncio, ai_steward_wiki.auth.users_toml,
#            ai_steward_wiki.storage.jobs.models, ai_steward_wiki.storage.jobs.engine,
#            ai_steward_wiki.wiki.lifecycle,
#            ai_steward_wiki.migration.{config,transform}, sqlite3 (stdlib).
#   LINKS: M-MIGRATION-LOAD, aisw-0a5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LoadReport - frozen dataclass with per-phase counters
#   MigrationLoader - orchestrates write_users_toml, bootstrap_wikis,
#                     copy_files, write_legacy_docs, insert_jobs, snapshot_db.
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P4.*: side-effect layer
# END_CHANGE_SUMMARY

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import tomli_w
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.users_toml import SCHEMA_VERSION
from ai_steward_wiki.migration.config import USER_MAPPINGS, UserMapping
from ai_steward_wiki.migration.transform import (
    PlannedFileCopy,
    PlannedJob,
    PlannedLegacyDoc,
    PlannedWiki,
    TargetPlan,
)
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.wiki.lifecycle import WikiLifecycleManager
from ai_steward_wiki.wiki.name import normalize_wiki_name

__all__ = ["LoadReport", "MigrationLoader"]

_log = structlog.get_logger(__name__)


@dataclass(slots=True)
class LoadReport:
    users_toml_written: bool = False
    wikis_created: int = 0
    wikis_already_existed: int = 0
    files_copied: int = 0
    files_skipped_existing: int = 0
    legacy_docs_written: int = 0
    jobs_inserted: int = 0
    db_snapshot_path: Path | None = None
    profiles_dir_created: bool = False
    errors: list[str] = field(default_factory=list)


class MigrationLoader:
    """Apply a TargetPlan to the target system.

    Side-effect topology (all gated by `dry_run`):
        1. snapshot jobs.db (sqlite3 .backup) -> /tmp/jobs.db.pre-migration.<ts>
        2. write users.toml (tomli_w)
        3. mkdir profiles_dir
        4. bootstrap WIKIs (WikiLifecycleManager.create_wiki — idempotent)
        5. copy files (shutil.copy2, skip if dst exists with same size)
        6. write legacy docs (atomic write)
        7. PRAGMA wal_checkpoint(TRUNCATE) on jobs.db
        8. INSERT all Job rows in a single transaction

    On any exception in steps 4-8, the loader logs and raises — caller
    (CLI) is responsible for rollback awareness (the pre-step-1 DB snapshot
    is the rollback artifact).
    """

    def __init__(
        self,
        *,
        target_wiki_root: Path,
        users_toml_path: Path,
        profiles_dir: Path,
        jobs_db_path: Path,
        jobs_session_maker: async_sessionmaker[AsyncSession] | None,
        dry_run: bool,
    ) -> None:
        self._wiki_root = target_wiki_root
        self._users_toml_path = users_toml_path
        self._profiles_dir = profiles_dir
        self._jobs_db_path = jobs_db_path
        self._session_maker = jobs_session_maker
        self._dry_run = dry_run
        self._lifecycle = WikiLifecycleManager(target_wiki_root)
        self._report = LoadReport()

    # ---------- public ----------

    async def execute(self, plan: TargetPlan) -> LoadReport:
        self._snapshot_db()
        self._write_users_toml()
        self._ensure_profiles_dir()
        self._bootstrap_wikis(plan.wikis)
        self._copy_files(plan.files)
        self._write_legacy_docs(plan.legacy_docs)
        await self._insert_jobs(plan.jobs)
        return self._report

    # ---------- 1. snapshot ----------

    def _snapshot_db(self) -> None:
        if self._dry_run:
            _log.info("migration.load.snapshot.skip_dry_run")
            return
        if not self._jobs_db_path.exists():
            _log.warning(
                "migration.load.snapshot.no_db",
                path=str(self._jobs_db_path),
            )
            return
        from datetime import datetime as _dt

        ts = _dt.now().strftime("%Y%m%dT%H%M%S")
        snap = Path(f"/tmp/jobs.db.pre-migration.{ts}")
        # sqlite3 .backup is the safe way (handles WAL).
        with (
            sqlite3.connect(str(self._jobs_db_path)) as src,
            sqlite3.connect(str(snap)) as dst,
        ):
            src.backup(dst)
        self._report.db_snapshot_path = snap
        _log.info("migration.load.snapshot.done", path=str(snap))

    # ---------- 2. users.toml ----------

    def _write_users_toml(self) -> None:
        doc: dict[str, object] = {"schema_version": SCHEMA_VERSION}
        users_block: list[dict[str, object]] = []
        for um in USER_MAPPINGS:
            entry: dict[str, object] = {
                "telegram_id": um.telegram_id,
                "role": um.role,
                "display_name": um.display_name,
                "tz": um.tz,
                "lang": um.lang,
            }
            users_block.append(entry)
        doc["users"] = users_block

        if self._dry_run:
            _log.info(
                "migration.load.users_toml.skip_dry_run",
                entries=len(users_block),
            )
            self._report.users_toml_written = False
            return
        self._users_toml_path.parent.mkdir(parents=True, exist_ok=True)
        with self._users_toml_path.open("wb") as fh:
            tomli_w.dump(doc, fh)
        self._report.users_toml_written = True
        _log.info(
            "migration.load.users_toml.done",
            path=str(self._users_toml_path),
            entries=len(users_block),
        )

    # ---------- 3. profiles dir ----------

    def _ensure_profiles_dir(self) -> None:
        if self._dry_run:
            _log.info(
                "migration.load.profiles_dir.skip_dry_run",
                path=str(self._profiles_dir),
            )
            return
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._report.profiles_dir_created = True
        _log.info(
            "migration.load.profiles_dir.done",
            path=str(self._profiles_dir),
        )

    # ---------- 4. WIKIs ----------

    def _bootstrap_wikis(self, planned: list[PlannedWiki]) -> None:
        for pw in planned:
            primary = self._wiki_primary(pw.raw_name)
            existing = self._lifecycle.lookup(pw.owner_telegram_id, primary)
            if existing is not None:
                self._report.wikis_already_existed += 1
                continue
            if self._dry_run:
                _log.info(
                    "migration.load.wiki.skip_dry_run",
                    owner=pw.owner_telegram_id,
                    primary=primary,
                    template_id=pw.template_id,
                )
                continue
            self._lifecycle.create_wiki(pw.owner_telegram_id, pw.raw_name, pw.template_id)
            self._report.wikis_created += 1

    def _wiki_primary(self, raw_name: str) -> str:
        return normalize_wiki_name(raw_name).primary

    def _wiki_dir(self, owner_telegram_id: int, wiki_raw_name: str) -> Path:
        return self._wiki_root / str(owner_telegram_id) / self._wiki_primary(wiki_raw_name)

    # ---------- 5. file copy ----------

    def _copy_files(self, planned: list[PlannedFileCopy]) -> None:
        for fc in planned:
            dst = self._wiki_dir(fc.owner_telegram_id, fc.wiki_raw_name) / fc.target_rel
            if dst.exists() and dst.stat().st_size == fc.src.stat().st_size:
                self._report.files_skipped_existing += 1
                continue
            if self._dry_run:
                _log.info(
                    "migration.load.file.skip_dry_run",
                    src=str(fc.src),
                    dst=str(dst),
                )
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fc.src, dst)
            self._report.files_copied += 1

    # ---------- 6. legacy docs ----------

    def _write_legacy_docs(self, planned: list[PlannedLegacyDoc]) -> None:
        for doc in planned:
            dst = self._wiki_dir(doc.owner_telegram_id, doc.wiki_raw_name) / doc.target_rel
            if self._dry_run:
                _log.info(
                    "migration.load.legacy_doc.skip_dry_run",
                    dst=str(dst),
                    bytes=len(doc.content),
                )
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: tmp then rename.
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            tmp.write_text(doc.content, encoding="utf-8")
            tmp.replace(dst)
            self._report.legacy_docs_written += 1

    # ---------- 7-8. jobs.db ----------

    async def _insert_jobs(self, planned: list[PlannedJob]) -> None:
        if not planned:
            return
        if self._dry_run:
            _log.info("migration.load.jobs.skip_dry_run", count=len(planned))
            return
        if self._session_maker is None:
            raise RuntimeError("jobs_session_maker is required for --execute")

        # WAL checkpoint to release WAL pages before bulk insert.
        if self._jobs_db_path.exists():
            with sqlite3.connect(str(self._jobs_db_path)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        async with self._session_maker() as session, session.begin():
            for pj in planned:
                session.add(
                    Job(
                        owner_telegram_id=pj.owner_telegram_id,
                        chat_id=pj.chat_id,
                        kind=pj.kind,
                        status=pj.status,
                        priority=pj.priority,
                        scheduled_at_utc=pj.scheduled_at_utc,
                        payload=pj.payload,
                        created_at_utc=pj.created_at_utc,
                        user_state=pj.user_state,
                        snooze_count=pj.snooze_count,
                    )
                )
        self._report.jobs_inserted = len(planned)
        _log.info("migration.load.jobs.done", count=len(planned))


# Re-export for tests that don't want to touch lifecycle.
_ = (UserMapping,)
