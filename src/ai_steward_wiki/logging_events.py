# FILE: src/ai_steward_wiki/logging_events.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: SSoT catalog of stable snake_case dotted event-key constants for structured logging.
#   SCOPE: module-level Final[str] constants only. No functions, no classes.
#   DEPENDS: -
#   LINKS: M-FOUNDATION-LOGGING
#   ROLE: TYPES
#   MAP_MODE: SUMMARY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TG_UPDATE_RECEIVED - CorrelationMiddleware entry event
#   TRACED_START_SUFFIX / TRACED_DONE_SUFFIX / TRACED_ERROR_SUFFIX - @traced lifecycle suffixes
#   TG_PIPELINE_DISPATCH / CLASSIFIER_STAGE0 / WIKI_RUN / INBOX_STAGING - canonical @traced prefixes for chunk 1 entrypoints
#   SCHEDULER_JOB_* - APScheduler lifecycle events (chunk 2)
#   STORAGE_SLOW_QUERY - SQLAlchemy slow-query log key (chunk 2)
#   CLAUDE_CLI_SPAWN / CLAUDE_CLI_EXIT / CLAUDE_CLI_ERROR - subprocess invocation anchors (chunk 2)
#   LLM_* - provider selection, failover, circuit, failure, recovery, and replay anchors
#   RUNTIME_LOOP_HEARTBEAT / RUNTIME_LOOP_LAG / RUNTIME_DIAG_TASK_DUMP - event-loop hang diagnostics (aisw-xbc)
#   TG_UPDATE_HANDLED / TG_UPDATE_HANDLER_SLOW - handler lifecycle exit + slow warn (aisw-xbc)
#   IO_ANCHOR_* - threshold-gated boundary anchors on external I/O (aisw-xbc)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: stable provider failover event catalog.
#   PREVIOUS:    v0.0.3 - aisw-xbc: event-loop hang diagnostics (heartbeat/lag/task_dump), handler lifecycle, I/O anchors
#   PREVIOUS:    v0.0.2 - chunk 2: scheduler lifecycle, storage slow_query, claude CLI spawn/exit/error
# END_CHANGE_SUMMARY
from __future__ import annotations

from typing import Final

# CorrelationMiddleware entry event
TG_UPDATE_RECEIVED: Final[str] = "tg.update.received"

# @traced lifecycle suffixes (appended to prefix by the decorator)
TRACED_START_SUFFIX: Final[str] = ".start"
TRACED_DONE_SUFFIX: Final[str] = ".done"
TRACED_ERROR_SUFFIX: Final[str] = ".error"

# anchored() over-threshold suffix (aisw-xbc) — appended to an IO_ANCHOR_* prefix.
ANCHOR_SLOW_SUFFIX: Final[str] = ".slow"

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

# Subscription-provider routing and recovery anchors (aisw-8gw; M-LLM-FAILOVER).
LLM_PROVIDER_SELECTED: Final[str] = "llm.provider.selected"
LLM_FAILOVER_TRIGGERED: Final[str] = "llm.failover.triggered"
LLM_CIRCUIT_CHANGED: Final[str] = "llm.circuit.changed"
LLM_PROVIDER_FAILED: Final[str] = "llm.provider.failed"
LLM_PROVIDER_RECOVERED: Final[str] = "llm.provider.recovered"
LLM_REPLAY_BLOCKED: Final[str] = "llm.replay.blocked"

# Event-loop hang diagnostics (aisw-xbc; M-OPS-OBSERVABILITY)
# Heartbeat is emitted every tick (its ABSENCE marks the freeze instant); lag/dump
# are threshold-gated to keep happy-path log volume near-zero (hybrid cost).
RUNTIME_LOOP_HEARTBEAT: Final[str] = "runtime.loop.heartbeat"
RUNTIME_LOOP_LAG: Final[str] = "runtime.loop.lag"
RUNTIME_DIAG_TASK_DUMP: Final[str] = "runtime.diag.task_dump"

# Handler lifecycle exit (aisw-xbc; M-FOUNDATION-LOGGING / CorrelationMiddleware).
# 'received without handled' for an update_id now means a stuck handler.
TG_UPDATE_HANDLED: Final[str] = "tg.update.handled"
TG_UPDATE_HANDLER_SLOW: Final[str] = "tg.update.handler_slow"

# Threshold-gated boundary anchors on external I/O (aisw-xbc). The decorator/ctx
# appends TRACED_DONE_SUFFIX/TRACED_ERROR_SUFFIX to these prefixes.
IO_ANCHOR_TG_SEND: Final[str] = "tg.io.send_message"
IO_ANCHOR_TG_EDIT: Final[str] = "tg.io.edit_message_text"
IO_ANCHOR_TG_DOCUMENT: Final[str] = "tg.io.send_document"
IO_ANCHOR_AUDIT_WRITE: Final[str] = "audit.io.record_run_output"

# aisw-azu: send_message retried with parse_mode=None after Telegram rejected the
# HTML payload ("can't parse entities"). Primary defence is sanitize_html; this
# logs the safety-net fallback so degraded (plain-text) delivery stays visible.
IO_SEND_PARSE_FALLBACK: Final[str] = "tg.io.send_message.parse_fallback"
