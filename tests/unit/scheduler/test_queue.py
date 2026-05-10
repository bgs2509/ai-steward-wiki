from __future__ import annotations

import pytest

from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue


@pytest.mark.asyncio
async def test_lower_lane_value_dequeues_first() -> None:
    q = PriorityJobQueue()
    await q.put(Lane.INGEST, "ingest")
    await q.put(Lane.INTERACTIVE, "interactive")
    await q.put(Lane.DIGEST, "digest")
    assert (await q.get()).payload == "interactive"
    assert (await q.get()).payload == "digest"
    assert (await q.get()).payload == "ingest"


@pytest.mark.asyncio
async def test_fifo_within_same_lane() -> None:
    q = PriorityJobQueue()
    for i in range(5):
        await q.put(Lane.USER_WRITE, f"item-{i}")
    seen = [(await q.get()).payload for _ in range(5)]
    assert seen == [f"item-{i}" for i in range(5)]


def test_lane_values_match_spec_table() -> None:
    assert Lane.INTERACTIVE == 0
    assert Lane.USER_WRITE == 1
    assert Lane.CRON_WRITE == 2
    assert Lane.DIGEST == 3
    assert Lane.INGEST == 4
