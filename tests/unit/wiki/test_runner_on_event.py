"""Tests for chunk-21 on_event callback hook in run_wiki_session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_steward_wiki.wiki.runner import _RunConfig, run_wiki_session
from ai_steward_wiki.wiki.streaming import StreamEvent
from tests.unit.wiki.conftest import FakeAcquirer, FakeSpawner


def _lines() -> list[bytes]:
    return [
        json.dumps({"type": "assistant", "text": "a"}).encode() + b"\n",
        json.dumps({"type": "assistant", "text": "b"}).encode() + b"\n",
        json.dumps({"type": "result", "stop_reason": "end_turn"}).encode() + b"\n",
    ]


async def test_on_event_called_per_event(
    tmp_path: Path, prompts_dir: Path, fake_acquirer: FakeAcquirer
) -> None:
    received: list[StreamEvent] = []

    async def cb(ev: StreamEvent) -> None:
        received.append(ev)

    spawner = FakeSpawner(lines=_lines(), exit_code=0)
    await run_wiki_session(
        wiki_id="W",
        wiki_path=tmp_path / "W",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="r1",
        correlation_id="c1",
        runtime_dir=tmp_path / "rt",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_RunConfig(timeout_s=2.0, term_grace_s=0.1),
        on_event=cb,
    )
    assert len(received) == 3
    assert [e.type for e in received] == ["assistant_chunk", "assistant_chunk", "final"]


async def test_on_event_exception_swallowed(
    tmp_path: Path, prompts_dir: Path, fake_acquirer: FakeAcquirer
) -> None:
    calls = 0

    async def cb(ev: StreamEvent) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    spawner = FakeSpawner(lines=_lines(), exit_code=0)
    result = await run_wiki_session(
        wiki_id="W",
        wiki_path=tmp_path / "W",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="r2",
        correlation_id="c2",
        runtime_dir=tmp_path / "rt",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_RunConfig(timeout_s=2.0, term_grace_s=0.1),
        on_event=cb,
    )
    assert result.exit_code == 0
    assert calls == 3


async def test_no_callback_back_compat(
    tmp_path: Path, prompts_dir: Path, fake_acquirer: FakeAcquirer
) -> None:
    spawner = FakeSpawner(lines=_lines(), exit_code=0)
    result = await run_wiki_session(
        wiki_id="W",
        wiki_path=tmp_path / "W",
        base_prompt_path=prompts_dir / "wiki.md",
        overlay_prompt_path=prompts_dir / "domain-default.md",
        run_id="r3",
        correlation_id="c3",
        runtime_dir=tmp_path / "rt",
        acquirer=fake_acquirer,
        spawner=spawner,
        config=_RunConfig(timeout_s=2.0, term_grace_s=0.1),
    )
    assert result.exit_code == 0
    assert len(result.events) == 3


# Mark all async tests for asyncio
pytestmark = pytest.mark.asyncio
