"""Chunk-2 SSoT catalog: scheduler/storage/claude_cli stable event keys."""

from __future__ import annotations

from ai_steward_wiki import logging_events as ev


def test_chunk2_scheduler_events() -> None:
    assert ev.SCHEDULER_JOB_EXECUTED == "scheduler.job.executed"
    assert ev.SCHEDULER_JOB_ERROR == "scheduler.job.error"
    assert ev.SCHEDULER_JOB_MISSED == "scheduler.job.missed"
    assert ev.SCHEDULER_JOB_MAX_INSTANCES == "scheduler.job.max_instances"


def test_chunk2_storage_events() -> None:
    assert ev.STORAGE_SLOW_QUERY == "storage.slow_query"


def test_chunk2_claude_cli_events() -> None:
    assert ev.CLAUDE_CLI_SPAWN == "claude_cli.spawn"
    assert ev.CLAUDE_CLI_EXIT == "claude_cli.exit"
    assert ev.CLAUDE_CLI_ERROR == "claude_cli.error"
