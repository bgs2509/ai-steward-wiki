from __future__ import annotations

import asyncio
import faulthandler
import signal
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from ai_steward_wiki.ops import observability as obs


def _settings(**overrides: Any) -> Any:
    base = {
        "obs_heartbeat_interval_s": 1.0,
        "obs_loop_lag_warn_ms": 500,
        "obs_loop_lag_dump_ms": 5000,
        "obs_dump_min_interval_s": 60.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Stop(Exception):
    """Sentinel to break run_heartbeat's infinite loop in tests."""


def _sleep_for(n_ticks: int):
    state = {"i": 0}

    async def _sleep(_seconds: float) -> None:
        state["i"] += 1
        if state["i"] > n_ticks:
            raise _Stop

    return _sleep


def _clock_from(values: list[float]):
    it = iter(values)

    def _clock() -> float:
        return next(it)

    return _clock


def test_compute_lag_ms() -> None:
    # 0.6s of drift → 600ms
    assert obs.compute_lag_ms(expected=1.0, now=1.6) == 600
    # on-time → 0; never negative-rounds surprisingly
    assert obs.compute_lag_ms(expected=2.0, now=2.0) == 0


@pytest.mark.asyncio
async def test_heartbeat_emits_each_tick_with_lag() -> None:
    # init clock=0 → expected=1.0; tick1 now=1.0 (lag 0); tick2 now=2.6 (lag 600)
    clock = _clock_from([0.0, 1.0, 2.6])
    with capture_logs() as records, pytest.raises(_Stop):
        await obs.run_heartbeat(
            _settings(),
            sleep=_sleep_for(2),
            clock=clock,
            dump=lambda: None,
            monotonic=lambda: 0.0,
        )
    beats = [r for r in records if r["event"] == obs.RUNTIME_LOOP_HEARTBEAT]
    assert [b["lag_ms"] for b in beats] == [0, 600]


@pytest.mark.asyncio
async def test_heartbeat_lag_warning_over_threshold() -> None:
    # tick1 lag 600ms > warn(500) → one runtime.loop.lag WARNING
    clock = _clock_from([0.0, 1.6])
    with capture_logs() as records, pytest.raises(_Stop):
        await obs.run_heartbeat(
            _settings(),
            sleep=_sleep_for(1),
            clock=clock,
            dump=lambda: None,
            monotonic=lambda: 0.0,
        )
    lags = [r for r in records if r["event"] == obs.RUNTIME_LOOP_LAG]
    assert len(lags) == 1
    assert lags[0]["lag_ms"] == 600
    assert lags[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_heartbeat_auto_dump_rate_limited() -> None:
    # two consecutive ticks both lag > dump(5000ms); within min_interval → dump once
    clock = _clock_from([0.0, 7.0, 13.0])  # lag1=6000, lag2=5000
    calls = {"n": 0}

    def _dump() -> None:
        calls["n"] += 1

    with pytest.raises(_Stop):
        await obs.run_heartbeat(
            _settings(obs_dump_min_interval_s=60.0),
            sleep=_sleep_for(2),
            clock=clock,
            dump=_dump,
            monotonic=lambda: 0.0,  # same monotonic → second dump suppressed
        )
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_dump_asyncio_tasks_frames_no_values() -> None:
    async def _sleeper() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_sleeper(), name="aisw.test.sleeper")
    await asyncio.sleep(0)  # let it suspend on the inner sleep
    try:
        with capture_logs() as records:
            payload = obs.dump_asyncio_tasks()
    finally:
        task.cancel()

    dumps = [r for r in records if r["event"] == obs.RUNTIME_DIAG_TASK_DUMP]
    assert len(dumps) == 1
    names = [t["name"] for t in payload]
    assert "aisw.test.sleeper" in names
    entry = next(t for t in payload if t["name"] == "aisw.test.sleeper")
    assert isinstance(entry["frames"], list)
    assert all(isinstance(f, str) for f in entry["frames"])
    # frames are file:line / source-line strings, never argument values like "3600"
    assert any("observability" in f or ".py:" in f for f in entry["frames"])


def test_install_sigusr1_registers_handler_that_dumps() -> None:
    captured: dict[str, Any] = {}

    class _FakeLoop:
        def add_signal_handler(self, sig: int, cb: Any) -> None:
            captured["sig"] = sig
            captured["cb"] = cb

    obs.install_sigusr1(_FakeLoop())  # type: ignore[arg-type]
    assert captured["sig"] == signal.SIGUSR1
    # invoking the registered handler dumps without raising (no running loop here →
    # all_tasks() RuntimeError is swallowed, count 0, still emits the event)
    with capture_logs() as records:
        captured["cb"]()
    assert any(r["event"] == obs.RUNTIME_DIAG_TASK_DUMP for r in records)


def test_enable_faulthandler() -> None:
    obs.enable_faulthandler()
    assert faulthandler.is_enabled()
