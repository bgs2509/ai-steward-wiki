"""Tests for ConfirmationService (D-023 graduated confirmations)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.sessions.models import PendingConfirm
from ai_steward_wiki.tg.confirm import (
    ConfirmationService,
    PendingConfirmDraft,
    build_route_confirm_keyboard,
    compute_payload_hash,
)
from tests.unit.tg.conftest import FakeSender

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_route_confirm_keyboard_no_wikis_is_confirm_cancel() -> None:
    kb = build_route_confirm_keyboard(7)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["confirm:7:confirm", "confirm:7:cancel"]


def test_route_confirm_keyboard_wiki_picker_two_columns() -> None:
    wikis = ["Medical-WIKI", "Budget-WIKI", "Career-WIKI", "Investment-WIKI", "Default-WIKI"]
    kb = build_route_confirm_keyboard(7, wikis)
    rows = kb.inline_keyboard
    # 5 WIKIs → three picker rows (2,2,1) + confirm row + cancel row
    pick_rows = rows[:-2]
    assert [len(r) for r in pick_rows] == [2, 2, 1]  # two-column layout
    pick_cbs = [b.callback_data for r in pick_rows for b in r]
    assert pick_cbs == [f"wikipick:7:{i}" for i in range(5)]
    pick_labels = [b.text for r in pick_rows for b in r]
    assert pick_labels == wikis
    assert rows[-2][0].callback_data == "confirm:7:confirm"
    assert rows[-1][0].callback_data == "confirm:7:cancel"


@pytest.fixture
async def session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def test_compute_payload_hash_is_canonical_and_stable() -> None:
    a = compute_payload_hash({"b": 2, "a": 1})
    b = compute_payload_hash({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


@pytest.mark.asyncio
async def test_auto_ack_sends_single_message(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    msg_id = await svc.auto_ack(chat_id=100, line="Готово")
    assert msg_id > 0
    assert len(sender.sends) == 1
    assert sender.sends[0]["text"] == "Готово"


@pytest.mark.asyncio
async def test_implicit_ack_sends_recap_with_optional_keyboard(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    msg_id = await svc.implicit_ack(chat_id=100, recap="Покажу сегодня")
    assert msg_id > 0
    assert sender.sends[0]["reply_markup"] is None


@pytest.mark.asyncio
async def test_request_explicit_persists_row_and_sends_3_button_keyboard(
    session_maker,
) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)

    draft = PendingConfirmDraft(
        telegram_id=1001,
        chat_id=900,
        category="reminder.create",
        draft={"title": "Лекарство", "time": "08:00"},
        recap_text="Создать напоминание Лекарство в 08:00?",
    )
    rec = await svc.request_explicit(draft)

    assert rec.pending_id > 0
    assert rec.recap_message_id > 0
    assert rec.expires_at_utc > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=9)

    # DB row created
    async with session_maker() as s:
        row = await s.get(PendingConfirm, rec.pending_id)
        assert row is not None
        assert row.status == "pending"
        assert row.category == "reminder.create"
        assert row.chat_id == 900
        assert row.recap_message_id == rec.recap_message_id
        assert row.draft_json is not None

    # Keyboard has exactly 3 buttons
    sent = sender.sends[0]
    kb = sent["reply_markup"]
    assert kb is not None
    assert len(kb.inline_keyboard) == 3
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert callbacks == [
        f"confirm:{rec.pending_id}:confirm",
        f"confirm:{rec.pending_id}:correct",
        f"confirm:{rec.pending_id}:cancel",
    ]


@pytest.mark.asyncio
async def test_request_explicit_idempotent_on_duplicate(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    draft = PendingConfirmDraft(
        telegram_id=1001, chat_id=900, category="x", draft={"k": 1}, recap_text="recap"
    )
    rec1 = await svc.request_explicit(draft)
    rec2 = await svc.request_explicit(draft)
    assert rec1.pending_id == rec2.pending_id
    # Only one recap sent
    assert len(sender.sends) == 1


@pytest.mark.asyncio
async def test_resolve_transitions_status_under_pending_only(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    rec = await svc.request_explicit(
        PendingConfirmDraft(telegram_id=1, chat_id=2, category="c", draft={"a": 1}, recap_text="r")
    )
    status = await svc.resolve(1, rec.pending_id, "confirm")
    assert status == "confirmed"

    # Second resolve is no-op (status != pending anymore).
    status2 = await svc.resolve(1, rec.pending_id, "cancel")
    assert status2 is None


@pytest.mark.asyncio
async def test_resolve_rejects_wrong_telegram_id(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    rec = await svc.request_explicit(
        PendingConfirmDraft(telegram_id=1, chat_id=2, category="c", draft={"a": 1}, recap_text="r")
    )
    status = await svc.resolve(999, rec.pending_id, "confirm")
    assert status is None


@pytest.mark.asyncio
async def test_expire_due_flips_stale_rows(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    rec = await svc.request_explicit(
        PendingConfirmDraft(
            telegram_id=1,
            chat_id=2,
            category="c",
            draft={"a": 1},
            recap_text="r",
            ttl_sec=1,
        )
    )
    future = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=120)
    n = await svc.expire_due(future)
    assert n == 1

    async with session_maker() as s:
        row = await s.get(PendingConfirm, rec.pending_id)
        assert row is not None
        assert row.status == "expired"


@pytest.mark.asyncio
async def test_expire_due_does_not_touch_resolved(session_maker) -> None:
    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    rec = await svc.request_explicit(
        PendingConfirmDraft(telegram_id=1, chat_id=2, category="c", draft={"a": 1}, recap_text="r")
    )
    await svc.resolve(1, rec.pending_id, "cancel")
    future = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1200)
    n = await svc.expire_due(future)
    assert n == 0

    async with session_maker() as s:
        row = (await s.execute(select(PendingConfirm))).scalars().one()
        assert row.status == "cancelled"


# ---------- Phase-C: route confirm keyboard + custom keyboard_factory (aisw-e45) ----------


def test_build_route_confirm_keyboard_has_two_buttons() -> None:
    from ai_steward_wiki.tg.confirm import BTN_CANCEL, BTN_CONFIRM, build_route_confirm_keyboard

    kb = build_route_confirm_keyboard(77)
    rows = kb.inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].text == BTN_CONFIRM
    assert rows[0][0].callback_data == "confirm:77:confirm"
    assert rows[1][0].text == BTN_CANCEL
    assert rows[1][0].callback_data == "confirm:77:cancel"


@pytest.mark.asyncio
async def test_request_explicit_uses_custom_keyboard_factory(session_maker) -> None:
    from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard

    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    draft = PendingConfirmDraft(
        telegram_id=1,
        chat_id=2,
        category="route_ingest",
        draft={"decision": {"intent": "route"}},
        recap_text="Положу в Travel-WIKI. Подтверждаешь?",
    )
    rec = await svc.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)

    kb = sender.sends[0]["reply_markup"]
    assert len(kb.inline_keyboard) == 2  # route keyboard, not the 3-button default
    assert kb.inline_keyboard[0][0].callback_data == f"confirm:{rec.pending_id}:confirm"
