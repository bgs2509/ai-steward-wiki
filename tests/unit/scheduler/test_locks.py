from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from ai_steward_wiki.scheduler.locks import LOCK_FILENAME, WikiLockManager


async def test_acquire_order_serializes_same_wiki(tmp_path: Path) -> None:
    mgr = WikiLockManager(max_concurrent_cli=4)
    wiki = tmp_path / "Health-WIKI"
    sequence: list[str] = []

    async def worker(label: str, hold: float) -> None:
        async with mgr.acquire("Health-WIKI", wiki):
            sequence.append(f"{label}-in")
            await asyncio.sleep(hold)
            sequence.append(f"{label}-out")

    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))
    # Both got in, but never interleaved (deadlock-order test).
    assert sequence in (
        ["A-in", "A-out", "B-in", "B-out"],
        ["B-in", "B-out", "A-in", "A-out"],
    )


async def test_different_wikis_run_concurrently(tmp_path: Path) -> None:
    mgr = WikiLockManager(max_concurrent_cli=4)
    a, b = tmp_path / "A-WIKI", tmp_path / "B-WIKI"
    inside = asyncio.Event()
    other_done = asyncio.Event()

    async def first() -> None:
        async with mgr.acquire("A-WIKI", a):
            inside.set()
            await asyncio.wait_for(other_done.wait(), timeout=2.0)

    async def second() -> None:
        await inside.wait()
        async with mgr.acquire("B-WIKI", b):
            other_done.set()

    await asyncio.gather(first(), second())


async def test_stale_pid_lock_recovered(tmp_path: Path) -> None:
    """Lock file with dead PID must not block new acquirer."""
    wiki = tmp_path / "Stale-WIKI"
    wiki.mkdir()
    lock_file = wiki / LOCK_FILENAME
    # PID 999999 is almost certainly dead.
    dead_pid = 999_999
    while True:
        try:
            os.kill(dead_pid, 0)
        except ProcessLookupError:
            break
        dead_pid += 1
    lock_file.write_text(str(dead_pid))

    mgr = WikiLockManager(max_concurrent_cli=4)
    # Should acquire without hanging — flock not held by anyone, only stale pid in file.
    async with asyncio.timeout(2.0):
        async with mgr.acquire("Stale-WIKI", wiki):
            current = lock_file.read_text().strip()
            assert current == str(os.getpid())


@pytest.mark.parametrize("capacity", [1, 2])
async def test_semaphore_caps_concurrency(tmp_path: Path, capacity: int) -> None:
    mgr = WikiLockManager(max_concurrent_cli=capacity)
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(idx: int) -> None:
        nonlocal in_flight, peak
        wiki = tmp_path / f"W{idx}-WIKI"
        async with mgr.acquire(f"W{idx}-WIKI", wiki):
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1

    await asyncio.gather(*(worker(i) for i in range(capacity * 3)))
    assert peak <= capacity
