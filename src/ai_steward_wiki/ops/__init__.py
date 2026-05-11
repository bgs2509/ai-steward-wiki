# FILE: src/ai_steward_wiki/ops/__init__.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Operational hygiene — PII redaction, retention purge, GDPR purge,
#            db snapshot backup, per-WIKI git auto-commit.
#   SCOPE: Re-export public surface of ops.{pii,retention,gdpr,snapshot,wiki_git}.
#   DEPENDS: ai_steward_wiki.ops.{pii,retention,gdpr,snapshot,wiki_git}
#   LINKS: M-OPS-PII, M-OPS-BACKUP, D-034, D-035, D-037
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PIIRedactor - tiered redactor (tier-1 DROP, tier-2 MASK, tier-3 plaintext)
#   redact - module-level convenience using a default redactor
#   make_structlog_processor - adapter for structlog processor chain
#   RetentionPolicy - Pydantic model declaring one retention rule
#   RETENTION_POLICIES - canonical list of retention rules (§10.4)
#   PurgeResult - Pydantic model carrying counts from a purge run
#   run_purge - execute a single RetentionPolicy
#   register_retention_jobs - register all RETENTION_POLICIES with APScheduler
#   purge_staging - inbox _staging sweep wrapper
#   purge_trash_sweep - final tier-1/tier-2 sweep over _trash content
#   purge_user - GDPR admin endpoint
#   SnapshotResult - outcome of one db_snapshot run
#   DB_SNAPSHOT_JOB_ID - stable APScheduler id for the daily snapshot job
#   SNAPSHOT_RETENTION_DAYS - 7d rolling retention constant
#   snapshot_databases - run VACUUM INTO for each configured DB
#   purge_old_snapshots - rolling 7d retention sweep
#   register_db_snapshot_job - register cron 03:00 UTC daily
#   COMMIT_FMT - canonical per-WIKI commit template
#   GITIGNORE_ENTRIES - lines written into each WIKI .gitignore
#   WikiGitError - raised when local git CLI fails
#   init_wiki_git - idempotent git init + .gitignore writer
#   format_commit_message - render canonical commit message
#   auto_commit - stage-all + commit; no-op when nothing staged
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - chunk 14: M-OPS-BACKUP barrel additions
# END_CHANGE_SUMMARY

from ai_steward_wiki.ops.gdpr import purge_user
from ai_steward_wiki.ops.pii import (
    PIIRedactor,
    make_structlog_processor,
    redact,
)
from ai_steward_wiki.ops.retention import (
    RETENTION_POLICIES,
    PurgeResult,
    RetentionPolicy,
    purge_staging,
    purge_trash_sweep,
    register_retention_jobs,
    run_purge,
)
from ai_steward_wiki.ops.snapshot import (
    DB_SNAPSHOT_JOB_ID,
    SNAPSHOT_RETENTION_DAYS,
    SnapshotResult,
    purge_old_snapshots,
    register_db_snapshot_job,
    snapshot_databases,
)
from ai_steward_wiki.ops.wiki_git import (
    COMMIT_FMT,
    GITIGNORE_ENTRIES,
    WikiGitError,
    auto_commit,
    format_commit_message,
    init_wiki_git,
)

__all__ = [
    "COMMIT_FMT",
    "DB_SNAPSHOT_JOB_ID",
    "GITIGNORE_ENTRIES",
    "RETENTION_POLICIES",
    "SNAPSHOT_RETENTION_DAYS",
    "PIIRedactor",
    "PurgeResult",
    "RetentionPolicy",
    "SnapshotResult",
    "WikiGitError",
    "auto_commit",
    "format_commit_message",
    "init_wiki_git",
    "make_structlog_processor",
    "purge_old_snapshots",
    "purge_staging",
    "purge_trash_sweep",
    "purge_user",
    "redact",
    "register_db_snapshot_job",
    "register_retention_jobs",
    "run_purge",
    "snapshot_databases",
]
