from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_steward_wiki.classifier import FakeClaudeRunner, parse_time

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)


async def test_parse_dateparser_hit_ru() -> None:
    res = await parse_time("через 30 минут", user_tz=MSK, now_utc=NOW)
    assert res.source == "dateparser"
    assert res.escalate is False
    assert res.when_utc is not None
    assert res.when_utc.tzinfo == UTC


async def test_parse_dateparser_hit_en() -> None:
    res = await parse_time("tomorrow at 9am", user_tz=MSK, now_utc=NOW)
    assert res.source == "dateparser"
    assert res.when_utc is not None
    assert res.when_utc.tzinfo == UTC


async def test_parse_escalate_without_backend() -> None:
    res = await parse_time("когда-нибудь потом наверное", user_tz=MSK, now_utc=NOW)
    assert res.escalate is True
    assert res.when_utc is None
    assert res.source == "escalate"


async def test_parse_haiku_fallback_resolves(tmp_path: Path) -> None:
    runner = FakeClaudeRunner(
        responses=[{"when_iso": "2026-05-11T09:00:00+03:00", "tz": "Europe/Moscow"}]
    )
    prompt = tmp_path / "time.md"
    prompt.write_text("---\nsemver: 1.0.0\n---\n", encoding="utf-8")
    res = await parse_time(
        "qwerty asdf zzz unparseable",
        user_tz=MSK,
        now_utc=NOW,
        haiku_backend=runner,
        haiku_prompt_path=prompt,
    )
    assert res.source == "haiku_fallback"
    assert res.escalate is False
    assert res.when_utc is not None
    assert res.when_utc.tzinfo == UTC


async def test_prefer_future_rolls_past_wall_clock_forward() -> None:
    # NOW = 15:00 MSK; a bare "at 6am" is in the past today → prefer_future rolls it forward.
    res = await parse_time("at 6am", user_tz=MSK, now_utc=NOW, prefer_future=True)
    assert res.escalate is False
    assert res.when_utc is not None
    assert res.when_utc > NOW
    assert res.when_utc.tzinfo == UTC


async def test_prefer_future_keeps_explicit_future() -> None:
    res = await parse_time("tomorrow at 9am", user_tz=MSK, now_utc=NOW, prefer_future=True)
    assert res.escalate is False
    assert res.when_utc is not None
    assert res.when_utc > NOW


async def test_parse_haiku_ambiguous_escalates(tmp_path: Path) -> None:
    runner = FakeClaudeRunner(responses=[{"ambiguous": True}])
    prompt = tmp_path / "time.md"
    prompt.write_text("---\nsemver: 1.0.0\n---\n", encoding="utf-8")
    res = await parse_time(
        "qwerty asdf zzz unparseable",
        user_tz=MSK,
        now_utc=NOW,
        haiku_backend=runner,
        haiku_prompt_path=prompt,
    )
    assert res.escalate is True
    assert res.source == "escalate"
