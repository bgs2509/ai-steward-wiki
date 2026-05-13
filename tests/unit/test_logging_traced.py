from __future__ import annotations

import pytest
import structlog
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import traced


def test_traced_sync_emits_start_and_done_events() -> None:
    @traced()
    def noop(x: int) -> int:
        return x + 1

    with capture_logs() as records:
        assert noop(1) == 2

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".done")
    assert all("args" not in r and "kwargs" not in r for r in records)
    done = records[-1]
    assert isinstance(done["duration_ms"], int)
    assert done["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_traced_async_emits_start_and_done_events() -> None:
    @traced()
    async def noop_async(x: int) -> int:
        return x * 2

    with capture_logs() as records:
        assert await noop_async(3) == 6

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".done")


def test_traced_sync_logs_error_and_reraises() -> None:
    @traced()
    def boom() -> None:
        raise ValueError("nope")

    with capture_logs() as records, pytest.raises(ValueError, match="nope"):
        boom()

    events = [r["event"] for r in records]
    assert events[0].endswith(".start")
    assert events[-1].endswith(".error")
    err = records[-1]
    assert err["log_level"] == "error"
    assert isinstance(err["duration_ms"], int)


def test_traced_event_prefix_override() -> None:
    @traced(event_prefix="my.thing")
    def fn() -> None:
        return None

    with capture_logs() as records:
        fn()

    events = [r["event"] for r in records]
    assert events == ["my.thing.start", "my.thing.done"]


def test_traced_bind_fields_merged_into_contextvars() -> None:
    seen: dict[str, object] = {}

    @traced(bind={"wiki_id": "W"})
    def fn() -> None:
        # bind values are merged into structlog.contextvars for the call lifetime
        seen.update(structlog.contextvars.get_contextvars())

    fn()
    assert seen.get("wiki_id") == "W"
    # cleared on exit
    assert "wiki_id" not in structlog.contextvars.get_contextvars()
