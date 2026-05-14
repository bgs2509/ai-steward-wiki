from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_steward_wiki.classifier.recurrence import (
    Recurrence,
    RecurrenceParseResult,
    parse_recurrence,
)


def test_daily_to_cron() -> None:
    r = Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")
    assert r.to_cron() == {"hour": 9, "minute": 0}


def test_weekly_to_cron_orders_weekdays() -> None:
    r = Recurrence(kind="weekly", time_hhmm="19:05", weekdays=(4, 0), tz="Europe/Moscow")
    assert r.to_cron() == {"day_of_week": "mon,fri", "hour": 19, "minute": 5}


def test_recurrence_frozen_and_extra_forbidden() -> None:
    r = Recurrence(kind="daily", time_hhmm="08:00", tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="08:00", tz="UTC", junk=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        r.time_hhmm = "07:00"  # type: ignore[misc]


def test_invalid_time_hhmm_rejected() -> None:
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="9:00", tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="25:00", tz="UTC")


def test_invalid_weekday_rejected() -> None:
    with pytest.raises(ValidationError):
        Recurrence(kind="weekly", time_hhmm="09:00", weekdays=(7,), tz="UTC")


# --- parse_recurrence ------------------------------------------------------


def test_parse_daily() -> None:
    res = parse_recurrence("каждый день в 9 утра сводка", user_tz="Europe/Moscow")
    assert res.recurrence == Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


def test_parse_daily_explicit_minutes() -> None:
    res = parse_recurrence("присылай дайджест каждый день в 21:30", user_tz="UTC")
    assert res.recurrence == Recurrence(kind="daily", time_hhmm="21:30", tz="UTC")


def test_parse_daily_evening_hour() -> None:
    res = parse_recurrence("каждый вечер в 8 вечера сводка", user_tz="UTC")
    assert res.recurrence == Recurrence(kind="daily", time_hhmm="20:00", tz="UTC")


def test_parse_weekly_weekdays_word() -> None:
    res = parse_recurrence("сводка по будням в 19:00", user_tz="Europe/Moscow")
    assert res.recurrence == Recurrence(
        kind="weekly", time_hhmm="19:00", weekdays=(0, 1, 2, 3, 4), tz="Europe/Moscow"
    )


def test_parse_weekly_named_days() -> None:
    res = parse_recurrence("еженедельно по понедельникам и пятницам в 8", user_tz="UTC")
    assert res.recurrence == Recurrence(kind="weekly", time_hhmm="08:00", weekdays=(0, 4), tz="UTC")


def test_parse_weekend() -> None:
    res = parse_recurrence("по выходным в 10 утра присылай сводку", user_tz="UTC")
    assert res.recurrence == Recurrence(kind="weekly", time_hhmm="10:00", weekdays=(5, 6), tz="UTC")


def test_parse_no_time_escalates() -> None:
    res = parse_recurrence("каждый день сводка", user_tz="UTC")
    assert res.recurrence is None
    assert res.escalate is True


# --- monthly (aisw-r2k) ----------------------------------------------------


def test_monthly_to_cron() -> None:
    r = Recurrence(kind="monthly", time_hhmm="09:00", day_of_month=15, tz="Europe/Moscow")
    assert r.to_cron() == {"day": 15, "hour": 9, "minute": 0}


def test_monthly_requires_day_of_month() -> None:
    with pytest.raises(ValidationError):
        Recurrence(kind="monthly", time_hhmm="09:00", tz="UTC")


def test_monthly_day_of_month_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Recurrence(kind="monthly", time_hhmm="09:00", day_of_month=0, tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="monthly", time_hhmm="09:00", day_of_month=32, tz="UTC")


def test_day_of_month_forbidden_for_daily_weekly() -> None:
    with pytest.raises(ValidationError):
        Recurrence(kind="daily", time_hhmm="09:00", day_of_month=5, tz="UTC")
    with pytest.raises(ValidationError):
        Recurrence(kind="weekly", time_hhmm="09:00", weekdays=(0,), day_of_month=5, tz="UTC")


def test_monthly_forbids_weekdays() -> None:
    with pytest.raises(ValidationError):
        Recurrence(
            kind="monthly",
            time_hhmm="09:00",
            day_of_month=1,
            weekdays=(0,),
            tz="UTC",
        )


@pytest.mark.parametrize(
    ("text", "expected_dom", "expected_time"),
    [
        ("1 числа в 9 утра отчёт", 1, "09:00"),
        ("каждого первого месяца в 10:00 сводка", 1, "10:00"),
        ("ежемесячно 5-го в 8 утра", 5, "08:00"),
        ("по 15 числам в 21:30 присылай дайджест", 15, "21:30"),
        ("1-го каждого месяца в 7 утра", 1, "07:00"),
        ("28 числа в 18:00 платёж", 28, "18:00"),
    ],
)
def test_parse_monthly_variants(text: str, expected_dom: int, expected_time: str) -> None:
    res = parse_recurrence(text, user_tz="Europe/Moscow")
    assert res.recurrence == Recurrence(
        kind="monthly",
        time_hhmm=expected_time,
        day_of_month=expected_dom,
        tz="Europe/Moscow",
    )


def test_parse_monthly_no_time_escalates() -> None:
    res = parse_recurrence("15 числа каждого месяца отчёт", user_tz="UTC")
    assert res.recurrence is None
    assert res.escalate is True


def test_parse_monthly_day_out_of_range_escalates() -> None:
    res = parse_recurrence("32 числа в 9 утра", user_tz="UTC")
    assert res.recurrence is None
    assert res.escalate is True


def test_parse_unrelated_escalates() -> None:
    res = parse_recurrence("просто текст без расписания", user_tz="UTC")
    assert isinstance(res, RecurrenceParseResult)
    assert res.recurrence is None
    assert res.escalate is True
