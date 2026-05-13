# FILE: src/ai_steward_wiki/migration/__main__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: CLI entrypoint for the migration ETL. Parses argv, dispatches
#            extract -> transform -> (dry-run report | execute + report).
#            Fail-Fast: any unexpected exception aborts the run; pre-step
#            jobs.db snapshot is the rollback artefact.
#   SCOPE: main, _build_parser. Module-level usage:
#            `uv run python -m ai_steward_wiki.migration --help`
#   DEPENDS: argparse (stdlib), structlog, sqlalchemy.ext.asyncio,
#            ai_steward_wiki.migration.{extract,transform,load,report,config}
#   LINKS: M-MIGRATION-CLI, aisw-0a5
#   ROLE: SCRIPT
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   main - async entrypoint (asyncio.run from __main__ block)
#   _build_parser - argparse.ArgumentParser factory (exposed for tests)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5 P5.3: CLI entrypoint
# END_CHANGE_SUMMARY

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_steward_wiki.migration.extract import extract_all
from ai_steward_wiki.migration.load import MigrationLoader
from ai_steward_wiki.migration.report import render_report
from ai_steward_wiki.migration.transform import build_plan_all

__all__ = ["_build_parser", "main"]

_log = structlog.get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai_steward_wiki.migration",
        description="ETL: legacy ai-steward (vpn-0) -> ai-steward-wiki (vpn-gpu-1).",
    )
    p.add_argument(
        "--snapshot-root",
        required=True,
        type=Path,
        help="rsync target on local machine (e.g. /tmp/migration-snapshot)",
    )
    p.add_argument(
        "--target-wiki-root",
        type=Path,
        default=Path("/home/bgs/.local/share/ai-steward-wiki/workspace/wikis"),
    )
    p.add_argument(
        "--jobs-db",
        type=str,
        default="sqlite+aiosqlite:////home/bgs/works/ai-steward-wiki/data/jobs.db",
    )
    p.add_argument(
        "--jobs-db-path",
        type=Path,
        default=Path("/home/bgs/works/ai-steward-wiki/data/jobs.db"),
        help="filesystem path to jobs.db (used for sqlite3 .backup snapshot)",
    )
    p.add_argument(
        "--users-toml",
        type=Path,
        default=Path("/home/bgs/works/ai-steward-wiki/data/users.toml"),
    )
    p.add_argument(
        "--profiles-dir",
        type=Path,
        default=Path("/home/bgs/.local/share/ai-steward-wiki/data/profiles"),
    )
    p.add_argument("--report-out", type=Path, default=None)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) plan but do not write",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="actually apply the plan to DB + FS",
    )
    return p


async def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    dry_run = not args.execute  # --execute flips off dry-run

    snapshot_root: Path = args.snapshot_root
    if not snapshot_root.exists():
        _log.error("migration.cli.snapshot_missing", path=str(snapshot_root))
        return 2

    now_utc = datetime.now(tz=UTC)
    snapshot_date = now_utc.strftime("%Y-%m-%d")

    _log.info(
        "migration.cli.start",
        snapshot_root=str(snapshot_root),
        mode="dry-run" if dry_run else "execute",
    )

    # Phase E + T (no IO to target).
    source_data = extract_all(snapshot_root)
    plan = build_plan_all(source_data, now_utc=now_utc, snapshot_date=snapshot_date)

    # Phase L (gated by dry_run inside the loader).
    session_maker: async_sessionmaker[AsyncSession] | None = None
    if not dry_run:
        engine = create_async_engine(args.jobs_db, future=True)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

    loader = MigrationLoader(
        target_wiki_root=args.target_wiki_root,
        users_toml_path=args.users_toml,
        profiles_dir=args.profiles_dir,
        jobs_db_path=args.jobs_db_path,
        jobs_session_maker=session_maker,
        dry_run=dry_run,
    )
    load_report = await loader.execute(plan)

    # Render report
    report_md = render_report(
        plan,
        mode="dry-run" if dry_run else "execute",
        snapshot_root=str(snapshot_root),
        snapshot_date=snapshot_date,
        load_report=load_report if not dry_run else None,
    )
    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report_md, encoding="utf-8")
        _log.info("migration.cli.report_written", path=str(args.report_out))
    else:
        sys.stdout.write(report_md)

    _log.info("migration.cli.done")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
