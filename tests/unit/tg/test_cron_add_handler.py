"""M-TG-CRON-ADD /cron_add handler tests (aisw-02v).

Tests target the pure parse-and-dispatch logic at the handler entry point;
aiogram Router wiring is exercised through a minimal in-process Router.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from aiogram.filters.command import CommandObject

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.tg.cron_add import (
    CRON_ADD_USAGE_RU,
    _humanize_recurrence,
    handle_cron_add,
)


class _FakeMessage:
    def __init__(self, *, text: str | None = None) -> None:
        self.text = text
        self.from_user = type("FU", (), {"id": 100, "username": "tester"})()
        self.chat = type("C", (), {"id": 100})()
        self.answers: list[str] = []
        self.answer_calls: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kw: Any) -> None:
        self.answers.append(text)
        self.answer_calls.append((text, kw))


# ---- _humanize_recurrence pure tests ---------------------------------------


def test_humanize_daily():
    rec = Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")
    s = _humanize_recurrence(rec)
    assert "каждый день" in s
    assert "09:00" in s
    assert "Europe/Moscow" in s


def test_humanize_weekly_weekdays():
    rec = Recurrence(kind="weekly", time_hhmm="08:00", weekdays=(0, 1, 2, 3, 4), tz="UTC")
    s = _humanize_recurrence(rec)
    assert "по будням" in s
    assert "08:00" in s


def test_humanize_weekly_weekend():
    rec = Recurrence(kind="weekly", time_hhmm="12:00", weekdays=(5, 6), tz="UTC")
    s = _humanize_recurrence(rec)
    assert "по выходным" in s


def test_humanize_weekly_arbitrary():
    rec = Recurrence(kind="weekly", time_hhmm="07:30", weekdays=(0, 2, 4), tz="UTC")
    s = _humanize_recurrence(rec)
    # contains short ru names
    assert "пн" in s
    assert "ср" in s
    assert "пт" in s
    assert "07:30" in s


def test_humanize_monthly():
    rec = Recurrence(kind="monthly", time_hhmm="10:00", day_of_month=5, tz="UTC")
    s = _humanize_recurrence(rec)
    assert "5" in s
    assert "10:00" in s
    assert "число" in s


# ---- handler dispatch tests ------------------------------------------------


async def _run_handler(
    text: str,
    *,
    get_user_tz=None,
    create_job=None,
):
    msg = _FakeMessage(text=text)
    cmd = CommandObject(command="cron_add", prefix="/", args=text[len("/cron_add ") :])
    await handle_cron_add(
        msg,  # type: ignore[arg-type]
        command=cmd,
        get_user_tz=get_user_tz or AsyncMock(return_value="UTC"),
        create_cron_user_job_fn=create_job or AsyncMock(return_value=42),
    )
    return msg


async def test_no_pipe_returns_usage():
    msg = await _run_handler("/cron_add каждый день в 9")
    assert msg.answers
    assert CRON_ADD_USAGE_RU in msg.answers[0]


async def test_usage_reply_is_plain_text_parse_mode_none():
    # CRON_ADD_USAGE_RU has literal <расписание>/<команда> placeholders. Under the
    # bot's default HTML parse_mode Telegram rejects them as invalid tags and the
    # message is silently dropped (aisw-woc). The usage reply MUST go out plain text.
    msg = await _run_handler("/cron_add каждый день в 9")
    text, kw = msg.answer_calls[0]
    assert CRON_ADD_USAGE_RU in text
    assert kw.get("parse_mode") is None


async def test_no_args_returns_usage():
    msg = _FakeMessage(text="/cron_add")
    cmd = CommandObject(command="cron_add", prefix="/", args=None)
    await handle_cron_add(
        msg,  # type: ignore[arg-type]
        command=cmd,
        get_user_tz=AsyncMock(return_value="UTC"),
        create_cron_user_job_fn=AsyncMock(return_value=1),
    )
    assert msg.answers
    assert CRON_ADD_USAGE_RU in msg.answers[0]


async def test_empty_command_returns_usage():
    msg = await _run_handler("/cron_add каждый день в 9 |   ")
    assert msg.answers
    assert CRON_ADD_USAGE_RU in msg.answers[0]


async def test_empty_schedule_returns_usage():
    msg = await _run_handler("/cron_add  | run something")
    assert msg.answers
    assert CRON_ADD_USAGE_RU in msg.answers[0]


async def test_escalate_returns_hint():
    msg = await _run_handler("/cron_add как-то нерегулярно | run")
    assert msg.answers
    # Escalate path still shows the usage / hint message.
    assert CRON_ADD_USAGE_RU in msg.answers[0]


async def test_happy_path_calls_create_and_replies(monkeypatch):
    create = AsyncMock(return_value=42)
    msg = await _run_handler(
        "/cron_add каждый день в 9 утра | напомни выпить витамины",
        get_user_tz=AsyncMock(return_value="Europe/Moscow"),
        create_job=create,
    )
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["owner_telegram_id"] == 100
    assert kwargs["chat_id"] == 100
    assert kwargs["command"] == "напомни выпить витамины"
    assert kwargs["user_tz"] == "Europe/Moscow"
    assert kwargs["wiki_id"] is None
    rec = kwargs["recurrence"]
    assert isinstance(rec, Recurrence)
    assert rec.kind == "daily"
    assert rec.time_hhmm == "09:00"
    # Reply contains id + humanized recurrence + command.
    assert msg.answers
    reply = msg.answers[0]
    assert "id=42" in reply
    assert "каждый день" in reply
    assert "09:00" in reply
    assert "напомни выпить витамины" in reply


async def test_create_failure_replies_generic_error():
    create = AsyncMock(side_effect=RuntimeError("db down"))
    msg = await _run_handler(
        "/cron_add каждый день в 9 | run",
        create_job=create,
    )
    assert msg.answers
    # Russian generic-error fallback.
    assert "Что-то пошло не так" in msg.answers[0]
