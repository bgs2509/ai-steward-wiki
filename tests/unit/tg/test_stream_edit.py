"""Tests for StreamEditor (D-026 streaming edits)."""

from __future__ import annotations

import pytest

from ai_steward_wiki.tg.stream_edit import StreamEditor
from tests.unit.tg.conftest import FakeClock, FakeSender


def _make_editor(
    *, tick_s: float = 1.5, delta_chars: int = 50, chain_threshold: int = 4000
) -> tuple[StreamEditor, FakeSender, FakeClock]:
    sender = FakeSender()
    clock = FakeClock(now=100.0)
    ed = StreamEditor(
        sender=sender,
        chat_id=900,
        first_message_id=500,
        tick_s=tick_s,
        delta_chars=delta_chars,
        chain_threshold=chain_threshold,
        clock=clock,
    )
    return ed, sender, clock


@pytest.mark.asyncio
async def test_small_feed_below_delta_and_tick_does_not_edit() -> None:
    ed, sender, _ = _make_editor()
    await ed.feed("hi")
    assert sender.edits == []


@pytest.mark.asyncio
async def test_delta_threshold_triggers_edit() -> None:
    ed, sender, _ = _make_editor(delta_chars=10)
    await ed.feed("0123456789ABC")  # >= 10 chars
    assert len(sender.edits) == 1
    assert sender.edits[0]["message_id"] == 500


@pytest.mark.asyncio
async def test_tick_alone_triggers_edit() -> None:
    ed, sender, clock = _make_editor(tick_s=1.5, delta_chars=1000)
    await ed.feed("ab")
    assert sender.edits == []
    clock.advance(1.6)
    await ed.feed("c")  # still <1000 delta, but tick elapsed
    assert len(sender.edits) == 1


@pytest.mark.asyncio
async def test_chain_split_at_threshold() -> None:
    ed, sender, _ = _make_editor(delta_chars=1, chain_threshold=100)
    await ed.feed("X" * 150)
    # 1 edit for finalized head + 1 send_message placeholder
    assert any(s["text"].startswith("\u23f3 (") for s in sender.sends)
    assert len(sender.edits) >= 1
    # Current message id updated to the new message
    assert ed.current_message_id == sender.sends[-1]["message_id"]
    assert ed.segment_idx == 2


@pytest.mark.asyncio
async def test_finalize_emits_final_state_idempotent() -> None:
    ed, sender, _ = _make_editor(delta_chars=10)
    await ed.feed("hello world this is a test")
    await ed.finalize()
    await ed.finalize()  # idempotent
    # last edit must reflect final flush
    assert any("hello world this is a test" in e["text"] for e in sender.edits)


@pytest.mark.asyncio
async def test_finalize_skips_edit_when_text_unchanged() -> None:
    """If the last tick already sent the canonical text, finalize() is a no-op
    edit-wise (no spurious 'message is not modified' round-trip)."""
    ed, sender, _ = _make_editor(delta_chars=10)
    await ed.feed("hello world unchanged buffer")  # triggers one tick edit
    n_edits = len(sender.edits)
    assert n_edits == 1
    await ed.finalize()
    assert len(sender.edits) == n_edits  # no extra edit
    assert ed._finalized is True


@pytest.mark.asyncio
async def test_finalize_balances_html_tags() -> None:
    ed, sender, _ = _make_editor(delta_chars=10000)
    await ed.feed("<b>unclosed bold")
    await ed.finalize()
    final = sender.edits[-1]["text"]
    assert "</b>" in final


@pytest.mark.asyncio
async def test_finalize_swallows_sender_errors() -> None:
    ed, sender, _ = _make_editor()
    sender.fail_edits = 99  # always fail
    await ed.feed("hello")
    # Should not raise even though edit fails.
    await ed.finalize()
    assert ed._finalized  # internal flag flipped


@pytest.mark.asyncio
async def test_feed_after_finalize_raises() -> None:
    ed, _, _ = _make_editor()
    await ed.finalize()
    with pytest.raises(RuntimeError, match="finalized"):
        await ed.feed("nope")
