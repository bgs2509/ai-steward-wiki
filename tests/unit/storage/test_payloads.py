"""Pydantic discriminated union for job payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_steward_wiki.storage.jobs.payloads import (
    CronUserPayload,
    PurgePayload,
    ReminderPayload,
    WikiRunPayload,
    parse_job_payload,
)


def test_wiki_run_round_trip():
    p = parse_job_payload(
        {"kind": "wiki_run", "wiki_id": "Health-WIKI", "prompt_text": "x", "correlation_id": "c1"}
    )
    assert isinstance(p, WikiRunPayload)
    assert p.wiki_id == "Health-WIKI"


def test_digest_window_bounds():
    parse_job_payload({"kind": "digest", "wiki_id": "Health-WIKI", "window_hours": 24})
    with pytest.raises(ValidationError):
        parse_job_payload({"kind": "digest", "wiki_id": "Health-WIKI", "window_hours": 0})


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        parse_job_payload({"kind": "nope", "wiki_id": "x"})


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        parse_job_payload(
            {"kind": "purge", "target": "audit.chat_log", "older_than_hours": 720, "extra": 1}
        )


def test_reminder_round_trip():
    p = parse_job_payload({"kind": "reminder_job", "message": "позвонить врачу"})
    assert isinstance(p, ReminderPayload)
    assert p.message == "позвонить врачу"
    assert p.lead_time_min == 0
    # round-trips through model_dump(mode="json")
    again = parse_job_payload(p.model_dump(mode="json"))
    assert again == p


def test_reminder_lead_time():
    p = parse_job_payload({"kind": "reminder_job", "message": "x", "lead_time_min": 30})
    assert isinstance(p, ReminderPayload)
    assert p.lead_time_min == 30
    with pytest.raises(ValidationError):
        parse_job_payload({"kind": "reminder_job", "message": "x", "lead_time_min": -1})


def test_reminder_extra_field_forbidden():
    with pytest.raises(ValidationError):
        parse_job_payload({"kind": "reminder_job", "message": "x", "extra": 1})


def test_reminder_frozen():
    p = parse_job_payload({"kind": "reminder_job", "message": "x"})
    with pytest.raises(ValidationError):
        p.message = "y"  # type: ignore[misc]


def test_cron_and_purge_basic():
    assert isinstance(
        parse_job_payload(
            {
                "kind": "cron_user",
                "wiki_id": "Health-WIKI",
                "cron_expr": "0 9 * * *",
                "user_text": "напомни",
            }
        ),
        CronUserPayload,
    )
    assert isinstance(
        parse_job_payload({"kind": "purge", "target": "audit.tg_updates", "older_than_hours": 24}),
        PurgePayload,
    )
