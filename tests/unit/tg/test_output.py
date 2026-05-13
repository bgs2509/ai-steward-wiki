"""Tests for HtmlBalancer, ChainSplitter, deliver_output (D-024 / D-025)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.audit.models import RunOutput
from ai_steward_wiki.tg.output import (
    CHAIN_THRESHOLD,
    INLINE_THRESHOLD,
    ChainSplitter,
    HtmlBalancer,
    LengthCapSummarizer,
    deliver_output,
)
from tests.unit.tg.conftest import FakeSender

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def audit_session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


# ---------- HtmlBalancer ----------


def test_html_balancer_balanced_text_unchanged() -> None:
    b = HtmlBalancer()
    out, reopen = b.balance_segment("hello <b>world</b> ok")
    assert out == "hello <b>world</b> ok"
    assert reopen == ""


def test_html_balancer_closes_dangling_bold() -> None:
    b = HtmlBalancer()
    out, reopen = b.balance_segment("hello <b>world")
    assert out == "hello <b>world</b>"
    assert reopen == "<b>"


def test_html_balancer_nested_tags_close_in_reverse() -> None:
    b = HtmlBalancer()
    out, reopen = b.balance_segment("<b><i>foo")
    assert out == "<b><i>foo</i></b>"
    assert reopen == "<b><i>"


def test_html_balancer_ignores_unknown_tags() -> None:
    b = HtmlBalancer()
    out, _ = b.balance_segment("<script>x</script> <b>y</b>")
    assert out == "<script>x</script> <b>y</b>"


# ---------- ChainSplitter ----------


def test_chain_splitter_returns_single_part_for_small_text() -> None:
    s = ChainSplitter(part_max_chars=100, hard_cap=3)
    parts = s.split("short")
    assert len(parts) == 1
    assert parts[0].endswith("(1/1)")


def test_chain_splitter_splits_at_sentence_boundary() -> None:
    s = ChainSplitter(part_max_chars=60, hard_cap=3)
    text = "Раз. Два. Три. " * 20
    parts = s.split(text)
    assert 2 <= len(parts) <= 3
    for i, p in enumerate(parts, 1):
        assert p.endswith(f"({i}/{len(parts)})")


def test_chain_splitter_balances_html_across_boundary() -> None:
    s = ChainSplitter(part_max_chars=40, hard_cap=3)
    text = "<b>" + ("слово. " * 30) + "</b>"
    parts = s.split(text)
    assert len(parts) >= 2
    # Each part must contain a closed <b> if it opened one.
    for p in parts:
        if "<b>" in p:
            assert "</b>" in p


# ---------- deliver_output ----------


@pytest.mark.asyncio
async def test_deliver_inline_short_text(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    runs = tmp_path / "runs"
    body = "Короткий ответ"
    receipt = await deliver_output(
        sender=sender,
        chat_id=100,
        telegram_id=1001,
        wiki_id="Medical-WIKI",
        run_id="r-001",
        text=body,
        runs_dir=runs,
        audit_session_maker=audit_session_maker,
    )
    assert receipt.n_messages == 1
    assert receipt.document_sent is False
    assert len(sender.sends) == 1
    assert receipt.output_path.exists()
    assert "run_id: r-001" in receipt.output_path.read_text(encoding="utf-8")
    # audit row
    async with audit_session_maker() as s:
        rows = (await s.execute(select(RunOutput))).scalars().all()
        assert len(rows) == 1
        assert rows[0].run_id == "r-001"
        assert rows[0].kind == "reply"


@pytest.mark.asyncio
async def test_deliver_chain_split_for_mid_size(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    body = "Предложение. " * 700  # ~9100 chars → chain branch
    assert INLINE_THRESHOLD < len(body) <= CHAIN_THRESHOLD
    receipt = await deliver_output(
        sender=sender,
        chat_id=100,
        telegram_id=1001,
        wiki_id="W",
        run_id="r-mid",
        text=body,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
    )
    assert receipt.n_messages >= 2
    assert receipt.document_sent is False
    assert len(sender.sends) == receipt.n_messages
    # Footer markers
    last = sender.sends[-1]["text"]
    assert f"({receipt.n_messages}/{receipt.n_messages})" in last


@pytest.mark.asyncio
async def test_deliver_summary_plus_document_for_large(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    body = "x" * (CHAIN_THRESHOLD + 5000)  # >10000
    receipt = await deliver_output(
        sender=sender,
        chat_id=100,
        telegram_id=1001,
        wiki_id="W",
        run_id="r-big",
        text=body,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        kind="digest",
    )
    assert receipt.document_sent is True
    assert receipt.summary_chars is not None
    assert receipt.summary_chars <= 1500
    assert len(sender.sends) == 1  # summary
    assert len(sender.documents) == 1
    async with audit_session_maker() as s:
        row = (await s.execute(select(RunOutput))).scalars().one()
        assert row.kind == "digest"
        assert row.summary_chars is not None


@pytest.mark.asyncio
async def test_deliver_persists_without_tg_send(tmp_path, audit_session_maker) -> None:
    """tg_send=False: skip TG send entirely, but still persist to disk + audit."""
    sender = FakeSender()
    receipt = await deliver_output(
        sender=sender,
        chat_id=100,
        telegram_id=1001,
        wiki_id="W",
        run_id="r-skip",
        text="ответ уже доставлен стримом",
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        tg_send=False,
    )
    assert len(sender.sends) == 0
    assert len(sender.documents) == 0
    assert receipt.n_messages == 0
    assert receipt.document_sent is False
    assert receipt.output_path.exists()
    async with audit_session_maker() as s:
        rows = (await s.execute(select(RunOutput))).scalars().all()
        assert len(rows) == 1
        assert rows[0].run_id == "r-skip"


@pytest.mark.asyncio
async def test_length_cap_summarizer_truncates_with_ellipsis() -> None:
    summ = LengthCapSummarizer()
    big = "a" * 5000
    out = await summ.summarize(big)
    assert len(out) <= 1500
    assert out.endswith("\u2026")
    short = "hello"
    assert await summ.summarize(short) == short


@pytest.mark.asyncio
async def test_deliver_digest_splits_at_b_headers(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    pad = "строка наполнителя. " * 110
    text = (
        "<b>📌 TL;DR</b>\nкоротко.\n\n"
        f"<b>📅 Сегодня</b>\n{pad}\n\n"
        f"<b>💊 Лекарства</b>\n{pad}\n"
    )
    assert INLINE_THRESHOLD < len(text) <= CHAIN_THRESHOLD
    receipt = await deliver_output(
        sender=sender,
        chat_id=10,
        telegram_id=7,
        wiki_id="medical",
        run_id="digest-test1",
        text=text,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        kind="digest",
    )
    assert 2 <= receipt.n_messages <= 3
    assert receipt.document_sent is False
    msgs = [m["text"] for m in sender.sends]
    for i, m in enumerate(msgs, start=1):
        assert m.rstrip().endswith(f"({i}/{len(msgs)})")
    # The split landed on a <b>-prioritised boundary.
    assert "<b>" in msgs[1]


@pytest.mark.asyncio
async def test_deliver_digest_large_to_document(tmp_path, audit_session_maker) -> None:
    sender = FakeSender()
    text = "<b>📌 TL;DR</b>\n" + ("очень длинная сводка. " * 700)
    assert len(text) > CHAIN_THRESHOLD
    receipt = await deliver_output(
        sender=sender,
        chat_id=10,
        telegram_id=7,
        wiki_id="medical",
        run_id="digest-test2",
        text=text,
        runs_dir=tmp_path / "runs",
        audit_session_maker=audit_session_maker,
        kind="digest",
    )
    assert receipt.document_sent is True
    assert receipt.summary_chars is not None
    assert len(sender.documents) == 1
    async with audit_session_maker() as s:
        row = (await s.execute(select(RunOutput))).scalars().one()
        assert row.kind == "digest"
