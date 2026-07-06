"""HH:MM extractor used as a parameter validator (aisw-xi8: promoted from the
deleted digest-control fast-path to Phase-C.2's reschedule-time-only merge
path; #2/aisw-578's classifying regex sibling _detect_digest_action was
deleted per FR-2)."""

from __future__ import annotations

import pytest

from ai_steward_wiki.tg.pipeline import _extract_hhmm


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
