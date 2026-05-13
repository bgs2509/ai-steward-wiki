"""APScheduler lifecycle listener — emits canonical events for the 4 job codes (chunk 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from structlog.testing import capture_logs

from ai_steward_wiki.scheduler.core import (
    _scheduler_event_listener,
    attach_lifecycle_logging,
)

_SRT = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)


def _exec_event(code: int, *, exception: BaseException | None = None) -> JobExecutionEvent:
    return JobExecutionEvent(
        code,
        "job-42",
        "default",
        _SRT,
        retval=None,
        exception=exception,
        traceback="Traceback (most recent call last):\n  fake\n" if exception else None,
    )


def test_executed_event_logs_info_with_duration() -> None:
    with capture_logs() as logs:
        _scheduler_event_listener(_exec_event(EVENT_JOB_EXECUTED))
    assert len(logs) == 1
    rec = logs[0]
    assert rec["event"] == "scheduler.job.executed"
    assert rec["log_level"] == "info"
    assert rec["job_id"] == "job-42"
    assert rec["jobstore"] == "default"
    assert rec["scheduled_run_time"] == _SRT.isoformat()
    assert isinstance(rec["duration_ms"], int)
    assert rec["duration_ms"] >= 0


def test_error_event_logs_error_with_traceback() -> None:
    err = RuntimeError("boom")
    with capture_logs() as logs:
        _scheduler_event_listener(_exec_event(EVENT_JOB_ERROR, exception=err))
    assert len(logs) == 1
    rec = logs[0]
    assert rec["event"] == "scheduler.job.error"
    assert rec["log_level"] == "error"
    assert rec["job_id"] == "job-42"
    assert rec["jobstore"] == "default"
    assert "traceback" in rec
    # We do NOT serialize the exception .args / .message body.
    assert "boom" not in str(rec.get("event", ""))


def test_missed_event_logs_warning() -> None:
    with capture_logs() as logs:
        _scheduler_event_listener(_exec_event(EVENT_JOB_MISSED))
    assert len(logs) == 1
    rec = logs[0]
    assert rec["event"] == "scheduler.job.missed"
    assert rec["log_level"] == "warning"
    assert rec["job_id"] == "job-42"
    assert rec["jobstore"] == "default"


def test_max_instances_event_logs_warning() -> None:
    ev = JobSubmissionEvent(
        EVENT_JOB_MAX_INSTANCES, "job-42", "default", scheduled_run_times=[_SRT]
    )
    with capture_logs() as logs:
        _scheduler_event_listener(ev)
    assert len(logs) == 1
    rec = logs[0]
    assert rec["event"] == "scheduler.job.max_instances"
    assert rec["log_level"] == "warning"
    assert rec["job_id"] == "job-42"
    assert rec["jobstore"] == "default"


def test_unknown_event_code_is_ignored() -> None:
    # Listener registered with a mask, but defensive: dispatch on unknown code → no log.
    ev = MagicMock()
    ev.code = 0  # not in the canonical 4
    with capture_logs() as logs:
        _scheduler_event_listener(ev)
    assert logs == []


def test_attach_lifecycle_logging_registers_listener_with_correct_mask() -> None:
    scheduler = AsyncIOScheduler()
    attach_lifecycle_logging(scheduler)
    listeners = scheduler._listeners
    assert listeners, "expected one listener registered"
    callback, mask = listeners[0]
    expected = EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
    assert mask == expected
    assert callback is _scheduler_event_listener


def test_build_scheduler_attaches_listener() -> None:
    from ai_steward_wiki.scheduler.core import build_scheduler

    scheduler = build_scheduler("sqlite:///:memory:")
    assert any(
        cb is _scheduler_event_listener for cb, _mask in scheduler._listeners
    ), "build_scheduler must attach the lifecycle listener"
