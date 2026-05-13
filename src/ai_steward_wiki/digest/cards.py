# FILE: src/ai_steward_wiki/digest/cards.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Render actionable reminder cards for the digest (±2h pending window, aisw-163).
#   SCOPE: BUTTON_TEMPLATES (per-category labels), _render_card (text + keyboard),
#          emit_reminder_cards (window query + per-card send, cap=8 by default).
#   DEPENDS: aiogram.types (InlineKeyboardButton/InlineKeyboardMarkup), SQLAlchemy.asyncio,
#            structlog, ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads.ReminderPayload, ai_steward_wiki.tg.bot.TgSender
#   LINKS: M-DIGEST-CARDS, M-STORAGE-JOBS, M-TG-TEXT, ADR-026, aisw-163
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   BUTTON_TEMPLATES - per-category list of (label, action) for the 3 inline buttons
#   emit_reminder_cards - send up to `cap` cards for one owner's ±2h pending reminders
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-163 P3: initial render + ±2h window query (no callbacks yet)
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload

if TYPE_CHECKING:
    from aiogram.types import InlineKeyboardMarkup

    from ai_steward_wiki.tg.bot import TgSender

__all__ = [
    "BUTTON_TEMPLATES",
    "emit_reminder_cards",
]

_log = structlog.get_logger(__name__)

# ±2h window — D-024 / ADR-026.
_WINDOW = timedelta(hours=2)

# Per-category button labels. Actions are stable across categories (done/snz/skp)
# so the callback parser stays category-agnostic. Order is the canonical render
# order — done leftmost, skip rightmost.
BUTTON_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "medication": [
        ("💊 Принял", "done"),
        ("⏰ Отложить 30 мин", "snz"),
        ("❌ Пропустил", "skp"),
    ],
    "event": [
        ("✅ Сделано", "done"),
        ("⏰ Отложить 30 мин", "snz"),
        ("❌ Пропустил", "skp"),
    ],
    "generic": [
        ("✅ Готово", "done"),
        ("⏰ Отложить 30 мин", "snz"),
        ("❌ Пропустил", "skp"),
    ],
}


def _category(payload: dict[str, Any]) -> str:
    cat = payload.get("category", "generic")
    return cat if cat in BUTTON_TEMPLATES else "generic"


# START_CONTRACT: _render_card
#   PURPOSE: Build the text body + 3-button keyboard for one reminder job.
#   INPUTS: { job_id: int, payload: dict[str, Any] (already-validated reminder payload) }
#   OUTPUTS: { tuple[str, InlineKeyboardMarkup] - HTML-safe text + aiogram keyboard }
#   SIDE_EFFECTS: none (pure)
#   LINKS: M-DIGEST-CARDS, BUTTON_TEMPLATES
# END_CONTRACT: _render_card
def _render_card(*, job_id: int, payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    category = _category(payload)
    message = str(payload.get("message", "")).strip() or "(без описания)"
    text = f"🔔 <b>Напоминание</b>\n{message}"
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"r:{job_id}:{action}")
        for label, action in BUTTON_TEMPLATES[category]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=[buttons])


# START_CONTRACT: emit_reminder_cards
#   PURPOSE: For one owner, send up to `cap` actionable cards for reminder_jobs
#            with user_state='pending' scheduled within ±_WINDOW of now_utc.
#   INPUTS: { sender: TgSender, owner_telegram_id: int, chat_id: int,
#             now_utc: datetime (naive UTC),
#             jobs_session_maker: async_sessionmaker[AsyncSession], cap: int = 8 }
#   OUTPUTS: { tuple[int, int] - (emitted, total_in_window) }
#   SIDE_EFFECTS: TG send_message per card; one read-only DB transaction.
#   LINKS: M-DIGEST-CARDS, M-STORAGE-JOBS, ADR-026, aisw-163
# END_CONTRACT: emit_reminder_cards
async def emit_reminder_cards(
    *,
    sender: TgSender,
    owner_telegram_id: int,
    chat_id: int,
    now_utc: datetime,
    jobs_session_maker: async_sessionmaker[AsyncSession],
    cap: int = 8,
) -> tuple[int, int]:
    # START_BLOCK_QUERY_WINDOW
    window_lo = now_utc - _WINDOW
    window_hi = now_utc + _WINDOW
    async with jobs_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(Job.id, Job.payload)
                    .where(
                        Job.owner_telegram_id == owner_telegram_id,
                        Job.kind == "reminder_job",
                        Job.user_state == "pending",
                        Job.scheduled_at_utc.is_not(None),
                        Job.scheduled_at_utc >= window_lo,
                        Job.scheduled_at_utc <= window_hi,
                    )
                    .order_by(Job.scheduled_at_utc.asc(), Job.id.asc())
                )
            )
            .tuples()
            .all()
        )
    # END_BLOCK_QUERY_WINDOW

    total = len(rows)
    to_emit = rows[:cap]
    by_category: dict[str, int] = {}
    # START_BLOCK_EMIT_CARDS
    for job_id, payload in to_emit:
        try:
            validated = ReminderPayload.model_validate(payload)
            payload_dict = validated.model_dump()
        except Exception:
            # Malformed legacy row — fall back to a generic card with raw fields.
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
        text, keyboard = _render_card(job_id=job_id, payload=payload_dict)
        await sender.send_message(chat_id, text, reply_markup=keyboard)
        cat = _category(payload_dict)
        by_category[cat] = by_category.get(cat, 0) + 1
    # END_BLOCK_EMIT_CARDS

    _log.info(
        "digest.cards.emitted",
        owner_telegram_id=owner_telegram_id,
        emitted=len(to_emit),
        total=total,
        by_category=by_category,
    )
    return len(to_emit), total
