# FILE: src/ai_steward_wiki/logging_events.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: SSoT catalog of stable snake_case dotted event-key constants for structured logging.
#   SCOPE: module-level Final[str] constants only. No functions, no classes.
#   DEPENDS: -
#   LINKS: M-FOUNDATION-LOGGING
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TG_UPDATE_RECEIVED - CorrelationMiddleware entry event
#   TRACED_START_SUFFIX / TRACED_DONE_SUFFIX / TRACED_ERROR_SUFFIX - @traced lifecycle suffixes
#   TG_PIPELINE_DISPATCH / CLASSIFIER_STAGE0 / WIKI_RUN / INBOX_STAGING - canonical @traced prefixes for chunk 1 entrypoints
#   SCHEDULER_JOB_* - APScheduler lifecycle events (chunk 2)
#   STORAGE_SLOW_QUERY - SQLAlchemy slow-query log key (chunk 2)
#   CLAUDE_CLI_SPAWN / CLAUDE_CLI_EXIT / CLAUDE_CLI_ERROR - subprocess invocation anchors (chunk 2)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - chunk 2: scheduler lifecycle, storage slow_query, claude CLI spawn/exit/error
#   PREVIOUS:    v0.0.1 - initial SSoT catalog (chunk 1 entrypoints + correlation middleware)
# END_CHANGE_SUMMARY
from __future__ import annotations

from typing import Final

# CorrelationMiddleware entry event
TG_UPDATE_RECEIVED: Final[str] = "tg.update.received"

# @traced lifecycle suffixes (appended to prefix by the decorator)
TRACED_START_SUFFIX: Final[str] = ".start"
TRACED_DONE_SUFFIX: Final[str] = ".done"
TRACED_ERROR_SUFFIX: Final[str] = ".error"

# Canonical @traced prefixes for chunk-1 entrypoints
TG_PIPELINE_DISPATCH: Final[str] = "tg.pipeline.dispatch"
CLASSIFIER_STAGE0: Final[str] = "classifier.stage0"
WIKI_RUN: Final[str] = "wiki.run"
INBOX_STAGING: Final[str] = "inbox.staging"

# APScheduler lifecycle (chunk 2; M-SCHEDULER)
SCHEDULER_JOB_EXECUTED: Final[str] = "scheduler.job.executed"
SCHEDULER_JOB_ERROR: Final[str] = "scheduler.job.error"
SCHEDULER_JOB_MISSED: Final[str] = "scheduler.job.missed"
SCHEDULER_JOB_MAX_INSTANCES: Final[str] = "scheduler.job.max_instances"

# Storage slow-query log (chunk 2; M-STORAGE-*)
STORAGE_SLOW_QUERY: Final[str] = "storage.slow_query"

# Claude CLI subprocess anchors (chunk 2; M-CLASSIFIER-STAGE0 + M-WIKI-RUNNER)
CLAUDE_CLI_SPAWN: Final[str] = "claude_cli.spawn"
CLAUDE_CLI_EXIT: Final[str] = "claude_cli.exit"
CLAUDE_CLI_ERROR: Final[str] = "claude_cli.error"
