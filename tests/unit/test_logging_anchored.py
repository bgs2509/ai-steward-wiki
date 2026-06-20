from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import anchored

_MS = 1_000_000  # ns per ms


def _clock(values_ns: list[int]):
    it = iter(values_ns)

    def _c() -> int:
        return next(it)

    return _c


@pytest.mark.asyncio
async def test_anchored_fast_call_is_silent() -> None:
    # 100ms < threshold 1000ms → no .slow, no .error
    with capture_logs() as records:
        async with anchored(
            "tg.io.send_message", threshold_ms=1000, clock_ns=_clock([0, 100 * _MS])
        ):
            pass
    assert records == []


@pytest.mark.asyncio
async def test_anchored_slow_call_logs_slow() -> None:
    # 1500ms > threshold 1000ms → exactly one .slow WARNING with duration_ms
    with capture_logs() as records:
        async with anchored(
            "tg.io.send_message", threshold_ms=1000, clock_ns=_clock([0, 1500 * _MS])
        ):
            pass
    slow = [r for r in records if r["event"] == "tg.io.send_message.slow"]
    assert len(slow) == 1
    assert slow[0]["duration_ms"] == 1500
    assert slow[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_anchored_exception_logs_error_and_reraises() -> None:
    with capture_logs() as records, pytest.raises(ValueError, match="boom"):
        async with anchored(
            "tg.io.send_message", threshold_ms=1000, clock_ns=_clock([0, 50 * _MS])
        ):
            raise ValueError("boom")
    err = [r for r in records if r["event"] == "tg.io.send_message.error"]
    assert len(err) == 1
    assert err[0]["duration_ms"] == 50
    assert err[0]["log_level"] == "error"


@pytest.mark.asyncio
async def test_anchored_cancellederror_is_silent_and_reraised() -> None:
    # CancelledError = normal shutdown/teardown → must NOT log .error (no false
    # ERROR storm on graceful restart) and must propagate unchanged.
    with capture_logs() as records, pytest.raises(asyncio.CancelledError):
        async with anchored("tg.io.send_message", threshold_ms=1000):
            raise asyncio.CancelledError
    assert [r for r in records if r["event"].endswith((".error", ".slow"))] == []
