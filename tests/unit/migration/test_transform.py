"""Pure-function tests for the transform layer (aisw-0a5 P3.*)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.migration.config import find_user_mapping
from ai_steward_wiki.migration.extract import SourcePlannerItem
from ai_steward_wiki.migration.transform import (
    build_recurrence,
    daily_weekly_to_cron,
    extract_chat_id,
    is_planner_active,
    map_category,
    monthly_to_cron,
    msk_to_utc,
    planner_to_jobs,
)

# ----------------- is_planner_active -----------------


def _mk_item(**over: object) -> SourcePlannerItem:
    defaults: dict[str, object] = {
        "id": "uuid-1",
        "title": "T",
        "category": "task",
        "date": "2026-05-20",
        "time_start": "10:00:00",
        "remind_before": [0],
        "repeat": None,
        "recipients": [763463467],
        "status": "pending",
    }
    defaults.update(over)
    return SourcePlannerItem.model_validate({**defaults, "raw": defaults})


def test_active_pending_future() -> None:
    assert is_planner_active(_mk_item(date="2026-12-31"), date(2026, 5, 13)) is True


def test_inactive_pending_past_oneshot() -> None:
    assert is_planner_active(_mk_item(date="2025-01-01"), date(2026, 5, 13)) is False


def test_active_pending_past_with_repeat() -> None:
    assert (
        is_planner_active(
            _mk_item(date="2025-01-01", repeat={"type": "daily", "time": "09:00"}),
            date(2026, 5, 13),
        )
        is True
    )


def test_inactive_done() -> None:
    assert is_planner_active(_mk_item(date="2026-06-01", status="done"), date(2026, 5, 13)) is False


def test_inactive_cancelled() -> None:
    assert (
        is_planner_active(_mk_item(date="2026-06-01", status="cancelled"), date(2026, 5, 13))
        is False
    )


# ----------------- msk_to_utc -----------------


def test_msk_to_utc_winter() -> None:
    got = msk_to_utc("2026-01-15", "12:00:00")
    # MSK is UTC+3 year-round
    assert got == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def test_msk_to_utc_default_time() -> None:
    got = msk_to_utc("2026-01-15", None)
    assert got == datetime(2026, 1, 14, 21, 0, tzinfo=UTC)


# ----------------- map_category -----------------


@pytest.mark.parametrize(
    ("orig", "new"),
    [
        ("medication", "medication"),
        ("event", "event"),
        ("task", "generic"),
        ("reminder", "generic"),
        ("block", "generic"),
        ("todo", "generic"),
        ("garbage", "generic"),
    ],
)
def test_map_category(orig: str, new: str) -> None:
    got_new, got_orig = map_category(orig)
    assert got_new == new
    assert got_orig == orig


# ----------------- extract_chat_id -----------------


def test_extract_chat_id_ok() -> None:
    assert extract_chat_id([763463467]) == 763463467


def test_extract_chat_id_empty_fails() -> None:
    with pytest.raises(ValueError, match="exactly 1"):
        extract_chat_id([])


def test_extract_chat_id_multi_fails() -> None:
    with pytest.raises(ValueError, match="exactly 1"):
        extract_chat_id([1, 2])


# ----------------- cron builders -----------------


def test_monthly_to_cron_basic() -> None:
    expr = monthly_to_cron({"type": "monthly", "day": 1, "time": "09:00"})
    assert expr == "0 9 1 * *"


def test_monthly_to_cron_day_31_validates() -> None:
    # APScheduler accepts day=31 (even though some months don't have 31).
    expr = monthly_to_cron({"type": "monthly", "day": 31, "time": "23:59"})
    assert expr == "59 23 31 * *"


def test_monthly_to_cron_invalid_time() -> None:
    with pytest.raises(ValueError, match="invalid monthly time"):
        monthly_to_cron({"type": "monthly", "day": 1, "time": "9"})


def test_daily_to_cron() -> None:
    assert daily_weekly_to_cron({"type": "daily", "time": "08:30"}) == "30 8 * * *"


def test_weekly_to_cron() -> None:
    expr = daily_weekly_to_cron({"type": "weekly", "time": "07:00", "days": ["mon", "fri"]})
    assert expr == "0 7 * * mon,fri"


def test_weekly_needs_days() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        daily_weekly_to_cron({"type": "weekly", "time": "07:00"})


def test_build_recurrence_daily() -> None:
    r = build_recurrence({"type": "daily", "time": "08:30"}, user_tz="Europe/Moscow")
    assert isinstance(r, Recurrence)
    assert r.kind == "daily"
    assert r.time_hhmm == "08:30"
    assert r.weekdays == ()


def test_build_recurrence_weekly() -> None:
    r = build_recurrence(
        {"type": "weekly", "time": "07:00", "days": ["mon", "wed", "fri"]},
        user_tz="Europe/Moscow",
    )
    assert r.kind == "weekly"
    assert set(r.weekdays) == {0, 2, 4}


# ----------------- planner_to_jobs -----------------


def _gena() -> object:
    um = find_user_mapping(763463467)
    assert um is not None
    return um


def test_planner_to_jobs_oneshot_single_lead() -> None:
    item = _mk_item(date="2026-12-31", time_start="09:00:00", remind_before=[0])
    jobs = planner_to_jobs(
        item, user_mapping=_gena(), now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    )
    assert len(jobs) == 1
    j = jobs[0]
    assert j.kind == "reminder_job"
    assert j.owner_telegram_id == 763463467
    assert j.chat_id == 763463467
    assert j.payload["category"] == "generic"
    assert j.payload["legacy_item_id"] == "uuid-1"
    assert j.payload["legacy_category"] == "task"


def test_planner_to_jobs_oneshot_fan_out() -> None:
    item = _mk_item(date="2026-12-31", time_start="09:00:00", remind_before=[1440, 60, 0])
    jobs = planner_to_jobs(
        item, user_mapping=_gena(), now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    )
    assert len(jobs) == 3
    # Shared legacy_item_id
    assert len({j.payload["legacy_item_id"] for j in jobs}) == 1
    # Different scheduled_at_utc
    assert len({j.scheduled_at_utc for j in jobs}) == 3


def test_planner_to_jobs_monthly_to_cron_user() -> None:
    item = _mk_item(
        category="reminder",
        date="2026-04-01",
        time_start="09:00:00",
        repeat={"type": "monthly", "day": 1, "time": "09:00"},
    )
    jobs = planner_to_jobs(
        item, user_mapping=_gena(), now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    )
    assert len(jobs) == 1
    j = jobs[0]
    assert j.kind == "cron_user"
    assert j.scheduled_at_utc is None
    assert j.payload["cron_expr"] == "0 9 1 * *"
    assert j.payload["legacy_source"] == "planner.json:monthly"


def test_planner_to_jobs_weekly_cron_user_with_recurrence_check() -> None:
    item = _mk_item(
        category="event",
        date="2026-04-01",
        time_start="08:00:00",
        repeat={"type": "weekly", "time": "08:00", "days": ["mon", "fri"]},
    )
    jobs = planner_to_jobs(
        item, user_mapping=_gena(), now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    )
    assert len(jobs) == 1
    assert jobs[0].kind == "cron_user"
    assert jobs[0].payload["cron_expr"] == "0 8 * * mon,fri"
    assert jobs[0].payload["legacy_source"] == "planner.json:weekly"


def test_planner_to_jobs_medication_keeps_category() -> None:
    item = _mk_item(
        category="medication",
        date="2026-06-01",
        time_start="09:00:00",
        remind_before=[0],
    )
    jobs = planner_to_jobs(
        item, user_mapping=_gena(), now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    )
    assert jobs[0].payload["category"] == "medication"


def test_planner_to_jobs_multi_recipient_fails_fast() -> None:
    item = _mk_item(recipients=[1, 2])
    with pytest.raises(ValueError, match="exactly 1"):
        planner_to_jobs(
            item,
            user_mapping=_gena(),
            now_utc=datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
        )
