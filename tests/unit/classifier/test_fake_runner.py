from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.classifier import ClassifierError, FakeClaudeRunner


async def test_fake_runner_pops_responses() -> None:
    runner = FakeClaudeRunner(responses=[{"intent": "reminder"}, {"intent": "wiki_query"}])
    a = await runner.call(text="t1", prompt_path=Path("p"), correlation_id="c1")
    b = await runner.call(text="t2", prompt_path=Path("p"), correlation_id="c2")
    assert a == {"intent": "reminder"}
    assert b == {"intent": "wiki_query"}
    assert len(runner.calls) == 2
    assert runner.calls[0]["text"] == "t1"


async def test_fake_runner_callable() -> None:
    runner = FakeClaudeRunner(responses=lambda text: {"intent": "unknown", "echo": text})
    out = await runner.call(text="hello", prompt_path=Path("p"), correlation_id="c")
    assert out == {"intent": "unknown", "echo": "hello"}


async def test_fake_runner_exhausted_raises() -> None:
    runner = FakeClaudeRunner(responses=[])
    with pytest.raises(ClassifierError):
        await runner.call(text="t", prompt_path=Path("p"), correlation_id="c")
