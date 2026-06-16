from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ai_steward_wiki.tg.aggregator import FakeLoaderControl, InboxAggregator

_DELAY = 0.02  # tiny debounce so tests stay fast but still exercise the timer


@dataclass
class _RecordingProcess:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def __call__(self, *, telegram_id: int, chat_id: int, update_id: int, text: str) -> None:
        self.calls.append(
            {"telegram_id": telegram_id, "chat_id": chat_id, "update_id": update_id, "text": text}
        )


def _agg(process: _RecordingProcess, loader: FakeLoaderControl) -> InboxAggregator:
    return InboxAggregator(process=process, loader=loader, delay_s=_DELAY)


async def test_single_message_flushes_once() -> None:
    proc, loader = _RecordingProcess(), FakeLoaderControl()
    agg = _agg(proc, loader)
    await agg.submit(telegram_id=42, chat_id=7, update_id=1, text="привет")
    await asyncio.sleep(_DELAY * 3)
    assert len(proc.calls) == 1
    assert proc.calls[0]["text"] == "привет"
    assert proc.calls[0]["update_id"] == 1
    assert len(loader.posted) == 1
    assert loader.deleted == loader.posted  # loader removed before processing


async def test_burst_aggregates_into_one_call_in_order() -> None:
    proc, loader = _RecordingProcess(), FakeLoaderControl()
    agg = _agg(proc, loader)
    # three parts arriving within the window, last one out of message-id order
    await agg.submit(telegram_id=42, chat_id=7, update_id=10, text="часть1")
    await asyncio.sleep(_DELAY / 2)
    await agg.submit(telegram_id=42, chat_id=7, update_id=11, text="часть2")
    await asyncio.sleep(_DELAY / 2)
    await agg.submit(telegram_id=42, chat_id=7, update_id=12, text="часть3")
    await asyncio.sleep(_DELAY * 3)

    assert len(proc.calls) == 1  # ONE classify/route for the whole burst
    assert proc.calls[0]["text"] == "часть1\n\nчасть2\n\nчасть3"
    assert proc.calls[0]["update_id"] == 10  # first part's id
    # loader reposted on each message (3 posts), removed each repost + final flush (3 deletes)
    assert len(loader.posted) == 3
    assert len(loader.deleted) == 3


async def test_debounce_resets_on_each_message() -> None:
    proc, loader = _RecordingProcess(), FakeLoaderControl()
    agg = _agg(proc, loader)
    await agg.submit(telegram_id=1, chat_id=9, update_id=1, text="a")
    # keep resetting just under the window — must NOT flush yet
    for i in range(4):
        await asyncio.sleep(_DELAY * 0.6)
        await agg.submit(telegram_id=1, chat_id=9, update_id=2 + i, text=f"b{i}")
    assert proc.calls == []  # still buffering
    await asyncio.sleep(_DELAY * 3)
    assert len(proc.calls) == 1  # single flush after quiet window


async def test_out_of_order_arrival_sorted_by_update_id() -> None:
    proc, loader = _RecordingProcess(), FakeLoaderControl()
    agg = _agg(proc, loader)
    await agg.submit(telegram_id=1, chat_id=3, update_id=20, text="второй")
    await agg.submit(telegram_id=1, chat_id=3, update_id=19, text="первый")
    await asyncio.sleep(_DELAY * 3)
    assert proc.calls[0]["text"] == "первый\n\nвторой"


async def test_separate_chats_do_not_mix() -> None:
    proc, loader = _RecordingProcess(), FakeLoaderControl()
    agg = _agg(proc, loader)
    await agg.submit(telegram_id=1, chat_id=100, update_id=1, text="chatA")
    await agg.submit(telegram_id=2, chat_id=200, update_id=1, text="chatB")
    await asyncio.sleep(_DELAY * 3)
    texts = {c["chat_id"]: c["text"] for c in proc.calls}
    assert texts == {100: "chatA", 200: "chatB"}


async def test_loader_failure_does_not_block_flush() -> None:
    class BrokenLoader(FakeLoaderControl):
        async def post(self, chat_id: int) -> int | None:
            raise RuntimeError("telegram down")

    proc = _RecordingProcess()
    agg = _agg(proc, BrokenLoader())
    await agg.submit(telegram_id=1, chat_id=5, update_id=1, text="still works")
    await asyncio.sleep(_DELAY * 3)
    assert len(proc.calls) == 1  # loader is cosmetic; aggregation proceeds
