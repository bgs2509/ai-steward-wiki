"""Digest-control NL detection helpers (#2, aisw-578).

Pure-function coverage for the rule-based action classifier and HH:MM extractor
used by the digest fast-path (`переноси/выключи сводку`).
"""

from __future__ import annotations

import pytest

from ai_steward_wiki.tg.pipeline import _detect_digest_action, _extract_hhmm


@pytest.mark.parametrize(
    "text",
    [
        "выключи ежедневную сводку",
        "отключи сводку",
        "не присылай сводку",
        "больше не делай сводку",
        "убери сводку",
    ],
)
def test_detect_digest_action_disable(text: str) -> None:
    assert _detect_digest_action(text) == "disable"


@pytest.mark.parametrize(
    "text",
    [
        "переноси сводку на 7:30",
        "перенеси сводку на 9",
        "поменяй время сводки на 8:00",
        "сдвинь сводку на 10",
    ],
)
def test_detect_digest_action_reschedule(text: str) -> None:
    assert _detect_digest_action(text) == "reschedule"


@pytest.mark.parametrize(
    "text",
    [
        "делай сводку каждый день в 9",
        "делай мне сводку каждое утро в 9",
        "хочу еженедельную сводку по понедельникам в 8",
    ],
)
def test_detect_digest_action_create(text: str) -> None:
    assert _detect_digest_action(text) == "create"


def test_detect_reschedule_without_time_is_not_reschedule() -> None:
    # A reschedule verb but no parseable time must not be treated as reschedule.
    assert _detect_digest_action("перенеси сводку") == "create"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("переноси сводку на 7:30", "07:30"),
        ("на 07.05", "07:05"),
        ("в 9 утра", "09:00"),
        ("на 7", "07:00"),
        ("в 23:59", "23:59"),
        ("без времени", None),
        ("на 25:00", None),
        ("в 9:70", None),
    ],
)
def test_extract_hhmm(text: str, expected: str | None) -> None:
    assert _extract_hhmm(text) == expected
