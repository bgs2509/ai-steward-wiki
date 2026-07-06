"""In-memory PriorityJobQueue message types (aisw-02v).

Pydantic v2 discriminated union — currently a single member (CronUserQueueMsg),
discriminator left in place so future kinds extend the union without widening
existing callers (NFR-5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_steward_wiki.scheduler.queue_payloads import (
    CheckInQueueMsg,
    CronUserQueueMsg,
    parse_queue_msg,
)


def _msg(**overrides: object) -> CronUserQueueMsg:
    base: dict[str, object] = {
        "kind": "cron_user",
        "job_id": 42,
        "owner_telegram_id": 100,
        "chat_id": 100,
        "command": "напомни",
        "correlation_id": "abc123",
        "scheduled_at_utc": datetime(2026, 5, 14, 9, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return CronUserQueueMsg.model_validate(base)


def test_cron_user_queue_msg_validates():
    m = _msg()
    assert m.kind == "cron_user"
    assert m.job_id == 42
    assert m.command == "напомни"
    assert m.scheduled_at_utc.tzinfo == UTC


def test_roundtrip_via_typeadapter():
    m = _msg()
    raw = m.model_dump(mode="json")
    parsed = parse_queue_msg(raw)
    assert parsed == m


def test_frozen():
    m = _msg()
    with pytest.raises(ValidationError):
        m.command = "other"  # type: ignore[misc]


def test_extra_forbidden():
    with pytest.raises(ValidationError):
        parse_queue_msg(
            {
                "kind": "cron_user",
                "job_id": 1,
                "owner_telegram_id": 1,
                "chat_id": 1,
                "command": "x",
                "correlation_id": "y",
                "scheduled_at_utc": datetime.now(UTC).isoformat(),
                "garbage": True,
            }
        )


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError):
        parse_queue_msg(
            {
                "job_id": 1,
                "owner_telegram_id": 1,
                "chat_id": 1,
                "command": "x",
                "correlation_id": "y",
                "scheduled_at_utc": datetime.now(UTC).isoformat(),
            }
        )


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        parse_queue_msg(
            {
                "kind": "future_kind",
                "job_id": 1,
                "owner_telegram_id": 1,
                "chat_id": 1,
                "command": "x",
                "correlation_id": "y",
                "scheduled_at_utc": datetime.now(UTC).isoformat(),
            }
        )


def test_check_in_queue_msg_validates() -> None:
    msg = CheckInQueueMsg(
        job_id=7,
        owner_telegram_id=1,
        chat_id=99,
        question_topic="как прошёл день",
        correlation_id="c1",
        scheduled_at_utc=datetime.now(UTC),
    )
    assert msg.kind == "check_in"
    assert msg.question_topic == "как прошёл день"


def test_parse_queue_msg_dispatches_check_in_by_discriminator() -> None:
    raw = {
        "kind": "check_in",
        "job_id": 7,
        "owner_telegram_id": 1,
        "chat_id": 99,
        "question_topic": "как прошёл день",
        "correlation_id": "c1",
        "scheduled_at_utc": datetime.now(UTC).isoformat(),
    }
    parsed = parse_queue_msg(raw)
    assert isinstance(parsed, CheckInQueueMsg)


def test_parse_queue_msg_still_dispatches_cron_user_by_discriminator() -> None:
    """FR-15-equivalent for the queue union: the pre-existing member is unaffected."""
    raw = {
        "kind": "cron_user",
        "job_id": 1,
        "owner_telegram_id": 1,
        "chat_id": 1,
        "command": "x",
        "correlation_id": "c",
        "scheduled_at_utc": datetime.now(UTC).isoformat(),
    }
    parsed = parse_queue_msg(raw)
    assert isinstance(parsed, CronUserQueueMsg)
