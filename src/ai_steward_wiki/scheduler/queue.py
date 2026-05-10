# FILE: src/ai_steward_wiki/scheduler/queue.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: 5-lane asyncio.PriorityQueue with FIFO tiebreaker for job dispatch.
#   SCOPE: Lane enum (D-011 §3), QueueItem, PriorityJobQueue wrapper.
#   DEPENDS: asyncio, itertools
#   LINKS: M-SCHEDULER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Lane - priority lane enum (0..4)
#   QueueItem - (lane, sequence, payload) ordered tuple
#   PriorityJobQueue - asyncio.PriorityQueue[QueueItem] with auto-incrementing seq
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - chunk 4: 5-lane PriorityJobQueue (D-011 §3)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Lane(IntEnum):
    INTERACTIVE = 0
    USER_WRITE = 1
    CRON_WRITE = 2
    DIGEST = 3
    INGEST = 4


@dataclass(order=True)
class QueueItem:
    lane: int
    sequence: int
    payload: Any = field(compare=False)


class PriorityJobQueue:
    """Lane-priority + FIFO-within-lane wrapper around asyncio.PriorityQueue."""

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
        self._counter = itertools.count()

    async def put(self, lane: Lane, payload: Any) -> None:
        item = QueueItem(lane=int(lane), sequence=next(self._counter), payload=payload)
        await self._queue.put(item)

    async def get(self) -> QueueItem:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
