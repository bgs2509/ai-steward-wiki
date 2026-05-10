from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ai_steward_wiki.scheduler.locks import LOCK_FILENAME, WikiLockManager
from ai_steward_wiki.wiki.acquire import WikiLockAdapter


async def test_adapter_serialises_same_wiki(tmp_path: Path) -> None:
    adapter = WikiLockAdapter(WikiLockManager(max_concurrent_cli=4))
    wiki = tmp_path / "Health-WIKI"
    seq: list[str] = []

    async def worker(label: str, hold: float) -> None:
        async with adapter.acquire("Health-WIKI", wiki):
            seq.append(f"{label}-in")
            await asyncio.sleep(hold)
            seq.append(f"{label}-out")

    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))
    assert seq in (
        ["A-in", "A-out", "B-in", "B-out"],
        ["B-in", "B-out", "A-in", "A-out"],
    )


async def test_adapter_recovers_stale_pid(tmp_path: Path) -> None:
    wiki = tmp_path / "Stale-WIKI"
    wiki.mkdir()
    lock_file = wiki / LOCK_FILENAME
    dead_pid = 999_999
    while True:
        try:
            os.kill(dead_pid, 0)
        except ProcessLookupError:
            break
        dead_pid += 1
    lock_file.write_text(str(dead_pid))

    adapter = WikiLockAdapter(WikiLockManager(max_concurrent_cli=4))
    async with asyncio.timeout(2.0), adapter.acquire("Stale-WIKI", wiki):
        assert lock_file.read_text().strip() == str(os.getpid())


async def test_different_wikis_run_concurrently(tmp_path: Path) -> None:
    adapter = WikiLockAdapter(WikiLockManager(max_concurrent_cli=4))
    a, b = tmp_path / "A-WIKI", tmp_path / "B-WIKI"
    inside = asyncio.Event()
    other_done = asyncio.Event()

    async def first() -> None:
        async with adapter.acquire("A-WIKI", a):
            inside.set()
            await asyncio.wait_for(other_done.wait(), timeout=2.0)

    async def second() -> None:
        await inside.wait()
        async with adapter.acquire("B-WIKI", b):
            other_done.set()

    await asyncio.gather(first(), second())
