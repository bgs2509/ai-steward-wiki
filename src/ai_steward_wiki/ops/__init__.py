# FILE: src/ai_steward_wiki/ops/__init__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Operational hygiene — PII redaction, retention purge, GDPR purge.
#   SCOPE: Re-export public surface of ops.pii, ops.retention, ops.gdpr.
#   DEPENDS: ai_steward_wiki.ops.{pii,retention,gdpr}
#   LINKS: M-OPS-PII, D-034, D-035
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
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 13: M-OPS-PII initial barrel
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

__all__ = [
    "RETENTION_POLICIES",
    "PIIRedactor",
    "PurgeResult",
    "RetentionPolicy",
    "make_structlog_processor",
    "purge_staging",
    "purge_trash_sweep",
    "purge_user",
    "redact",
    "register_retention_jobs",
    "run_purge",
]
