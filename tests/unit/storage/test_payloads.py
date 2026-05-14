"""Pydantic discriminated union for job payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.storage.jobs.payloads import (
    CronUserPayload,
    DigestPayload,
    PurgePayload,
    ReminderPayload,
    WikiRunPayload,
    parse_job_payload,
)


def _rec() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


def test_wiki_run_round_trip():
    p = parse_job_payload(
        {"kind": "wiki_run", "wiki_id": "Health-WIKI", "prompt_text": "x", "correlation_id": "c1"}
    )
    assert isinstance(p, WikiRunPayload)
    assert p.wiki_id == "Health-WIKI"


def test_digest_payload_roundtrip():
    p = DigestPayload(recurrence=_rec())
    d = p.model_dump(mode="json")
    assert d["kind"] == "digest"
    assert d["wiki_scope"] == "all"
    assert d["window_hours"] == 24
    parsed = parse_job_payload(d)
    assert isinstance(parsed, DigestPayload)
    assert parsed.recurrence == _rec()


def test_digest_wiki_scope_named_subset():
    # aisw-269 — wiki_scope widened to 'all' | list[str] (non-empty).
    p = DigestPayload(recurrence=_rec(), wiki_scope=["Health", "Money"])
    assert p.wiki_scope == ["Health", "Money"]
    again = parse_job_payload(p.model_dump(mode="json"))
    assert isinstance(again, DigestPayload)
    assert again.wiki_scope == ["Health", "Money"]
    # 'all' still valid (no jobs.db migration; existing rows keep validating).
    assert DigestPayload(recurrence=_rec(), wiki_scope="all").wiki_scope == "all"
    assert (
        parse_job_payload(
            {"kind": "digest", "wiki_scope": "all", "recurrence": _rec().model_dump(mode="json")}
        ).wiki_scope
        == "all"
    )
    # empty list rejected.
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), wiki_scope=[])


def test_digest_window_bounds():
    DigestPayload(recurrence=_rec(), window_hours=168)
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), window_hours=0)
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), window_hours=169)


def test_digest_extra_field_forbidden():
    with pytest.raises(ValidationError):
        DigestPayload(recurrence=_rec(), junk=1)  # type: ignore[call-arg]


def test_digest_frozen():
    p = DigestPayload(recurrence=_rec())
    with pytest.raises(ValidationError):
        p.window_hours = 12  # type: ignore[misc]


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


# aisw-163: category lets the digest cards module pick per-category buttons.
def test_reminder_category_default_generic():
    p = parse_job_payload({"kind": "reminder_job", "message": "x"})
    assert isinstance(p, ReminderPayload)
    assert p.category == "generic"


def test_reminder_category_explicit_round_trip():
    for cat in ("medication", "event", "generic"):
        p = parse_job_payload({"kind": "reminder_job", "message": "x", "category": cat})
        assert isinstance(p, ReminderPayload)
        assert p.category == cat
        again = parse_job_payload(p.model_dump(mode="json"))
        assert again == p


def test_reminder_category_invalid_rejected():
    with pytest.raises(ValidationError):
        parse_job_payload({"kind": "reminder_job", "message": "x", "category": "gibberish"})


def test_reminder_legacy_payload_still_parses():
    # aisw-163: a row persisted before the category field exists must keep validating.
    legacy = {"kind": "reminder_job", "message": "x", "lead_time_min": 5}
    p = parse_job_payload(legacy)
    assert isinstance(p, ReminderPayload)
    assert p.category == "generic"


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
    # aisw-02v: CronUserPayload widened to typed Recurrence + free-form command.
    assert isinstance(
        parse_job_payload(
            {
                "kind": "cron_user",
                "recurrence": {
                    "kind": "daily",
                    "time_hhmm": "09:00",
                    "tz": "Europe/Moscow",
                },
                "command": "напомни",
                "wiki_id": "Health-WIKI",
            }
        ),
        CronUserPayload,
    )
    assert isinstance(
        parse_job_payload({"kind": "purge", "target": "audit.tg_updates", "older_than_hours": 24}),
        PurgePayload,
    )
