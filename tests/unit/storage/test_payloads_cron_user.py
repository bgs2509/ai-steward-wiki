"""CronUserPayload widening tests (aisw-02v).

Covers the post-widening shape — typed Recurrence + free-form command +
optional wiki_id — and ensures the old field names (cron_expr, user_text)
are rejected so stale dicts surface as ValidationError.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.storage.jobs.payloads import CronUserPayload, parse_job_payload


def _daily_9_msk() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


def test_cron_user_new_shape_validates():
    rec = _daily_9_msk()
    p = CronUserPayload(kind="cron_user", recurrence=rec, command="напомни выпить витамины")
    assert p.kind == "cron_user"
    assert p.recurrence == rec
    assert p.command == "напомни выпить витамины"
    assert p.wiki_id is None


def test_cron_user_optional_wiki_id_accepts_str():
    p = CronUserPayload(
        kind="cron_user",
        recurrence=_daily_9_msk(),
        command="run",
        wiki_id="Health",
    )
    assert p.wiki_id == "Health"


def test_cron_user_parse_job_payload_roundtrip():
    raw = {
        "kind": "cron_user",
        "recurrence": _daily_9_msk().model_dump(mode="json"),
        "command": "hi",
    }
    p = parse_job_payload(raw)
    assert isinstance(p, CronUserPayload)
    assert p.command == "hi"
    assert p.wiki_id is None
    # Round-trip through model_dump → parse_job_payload again.
    dumped = p.model_dump(mode="json")
    p2 = parse_job_payload(dumped)
    assert p2 == p


def test_cron_user_missing_command_rejected():
    with pytest.raises(ValidationError):
        CronUserPayload(kind="cron_user", recurrence=_daily_9_msk())  # type: ignore[call-arg]


def test_cron_user_missing_recurrence_rejected():
    with pytest.raises(ValidationError):
        CronUserPayload(kind="cron_user", command="hi")  # type: ignore[call-arg]


def test_cron_user_extra_field_forbidden():
    with pytest.raises(ValidationError):
        parse_job_payload(
            {
                "kind": "cron_user",
                "recurrence": _daily_9_msk().model_dump(mode="json"),
                "command": "hi",
                "extra_garbage": 42,
            }
        )


def test_cron_user_legacy_cron_expr_rejected():
    """Stale dict with the old field names must fail (extra='forbid' guards us)."""
    with pytest.raises(ValidationError):
        parse_job_payload(
            {
                "kind": "cron_user",
                "wiki_id": "Health",
                "cron_expr": "0 9 * * *",
                "user_text": "напомни",
            }
        )


def test_cron_user_frozen():
    p = CronUserPayload(kind="cron_user", recurrence=_daily_9_msk(), command="hi")
    with pytest.raises(ValidationError):
        p.command = "hello"  # type: ignore[misc]
