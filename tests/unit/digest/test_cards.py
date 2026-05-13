"""M-DIGEST-CARDS unit tests (aisw-163 P3).

Render + ±2h window query. Pure read-side over jobs.db.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.digest.cards import (
    BUTTON_TEMPLATES,
    _render_card,
    emit_reminder_cards,
)
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload


@pytest.fixture
async def jobs_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@dataclass
class _Sent:
    chat_id: int
    text: str
    reply_markup: Any
    parse_mode: str | None


@dataclass
class _FakeSender:
    sent: list[_Sent] = field(default_factory=list)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ):
        self.sent.append(_Sent(chat_id, text, reply_markup, parse_mode))

        class _M:
            message_id = len(self.sent)

        return _M()


def _payload(category: str = "generic", message: str = "do thing") -> dict[str, Any]:
    return ReminderPayload(message=message, category=category).model_dump()  # type: ignore[arg-type]


async def _add_job(
    maker, *, owner: int, scheduled_at_utc: datetime, payload: dict[str, Any], user_state="pending"
) -> int:
    async with maker() as s:
        j = Job(
            owner_telegram_id=owner,
            chat_id=owner,
            kind="reminder_job",
            status="scheduled",
            priority=2,
            scheduled_at_utc=scheduled_at_utc,
            payload=payload,
            user_state=user_state,
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(j)
        await s.commit()
        return j.id


def test_button_templates_have_three_per_category():
    assert set(BUTTON_TEMPLATES) == {"medication", "event", "generic"}
    for cat, buttons in BUTTON_TEMPLATES.items():
        actions = [a for _, a in buttons]
        assert actions == ["done", "snz", "skp"], f"{cat}: {actions}"


def test_render_medication_card():
    text, kb = _render_card(job_id=42, payload=_payload("medication", "Принять аспирин"))
    assert "аспирин" in text.lower()
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert len(rows[0]) == 3
    assert [b.callback_data for b in rows[0]] == ["r:42:done", "r:42:snz", "r:42:skp"]
    assert "Принял" in rows[0][0].text  # medication-specific label


def test_render_event_card():
    text, kb = _render_card(job_id=7, payload=_payload("event", "Встреча с врачом"))
    rows = kb.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["r:7:done", "r:7:snz", "r:7:skp"]
    # event uses 'Сделано', medication uses 'Принял' — must differ.
    assert rows[0][0].text != BUTTON_TEMPLATES["medication"][0][0]


def test_render_generic_card():
    text, kb = _render_card(job_id=9, payload=_payload("generic", "позвонить маме"))
    rows = kb.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["r:9:done", "r:9:snz", "r:9:skp"]
    assert rows[0][0].text == BUTTON_TEMPLATES["generic"][0][0]


async def test_cap_at_8(jobs_session_maker):
    now = datetime(2026, 5, 13, 12, 0, 0)
    for i in range(12):
        await _add_job(
            jobs_session_maker,
            owner=111,
            scheduled_at_utc=now + timedelta(minutes=i),
            payload=_payload("generic", f"msg-{i}"),
        )
    sender = _FakeSender()
    emitted, total = await emit_reminder_cards(
        sender=sender,
        owner_telegram_id=111,
        chat_id=111,
        now_utc=now,
        jobs_session_maker=jobs_session_maker,
    )
    assert (emitted, total) == (8, 12)
    assert len(sender.sent) == 8


async def test_only_pending_in_window(jobs_session_maker):
    now = datetime(2026, 5, 13, 12, 0, 0)
    in_window_pending = await _add_job(
        jobs_session_maker, owner=222, scheduled_at_utc=now, payload=_payload()
    )
    # done state — excluded
    await _add_job(
        jobs_session_maker,
        owner=222,
        scheduled_at_utc=now,
        payload=_payload(),
        user_state="done",
    )
    # skipped — excluded
    await _add_job(
        jobs_session_maker,
        owner=222,
        scheduled_at_utc=now,
        payload=_payload(),
        user_state="skipped",
    )
    # snoozed — excluded (back in queue, not actionable now)
    await _add_job(
        jobs_session_maker,
        owner=222,
        scheduled_at_utc=now,
        payload=_payload(),
        user_state="snoozed",
    )
    # outside window (>2h ahead)
    await _add_job(
        jobs_session_maker,
        owner=222,
        scheduled_at_utc=now + timedelta(hours=3),
        payload=_payload(),
    )
    # outside window (>2h behind)
    await _add_job(
        jobs_session_maker,
        owner=222,
        scheduled_at_utc=now - timedelta(hours=3),
        payload=_payload(),
    )
    # other owner — excluded
    await _add_job(jobs_session_maker, owner=999, scheduled_at_utc=now, payload=_payload())

    sender = _FakeSender()
    emitted, total = await emit_reminder_cards(
        sender=sender,
        owner_telegram_id=222,
        chat_id=222,
        now_utc=now,
        jobs_session_maker=jobs_session_maker,
    )
    assert (emitted, total) == (1, 1)
    assert len(sender.sent) == 1
    # The single card must reference the one in-window pending job.
    assert f"r:{in_window_pending}:done" in str(sender.sent[0].reply_markup.inline_keyboard)


async def test_empty_window_emits_nothing(jobs_session_maker):
    now = datetime(2026, 5, 13, 12, 0, 0)
    sender = _FakeSender()
    emitted, total = await emit_reminder_cards(
        sender=sender,
        owner_telegram_id=333,
        chat_id=333,
        now_utc=now,
        jobs_session_maker=jobs_session_maker,
    )
    assert (emitted, total) == (0, 0)
    assert sender.sent == []
