# FILE: src/ai_steward_wiki/tg/cron_add.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: aiogram Router add-on — /cron_add <NL recurrence> | <command>
#            Command handler. Splits args on the first '|'; calls
#            classifier.recurrence.parse_recurrence; on parse-success calls
#            scheduler.cron_user.create_cron_user_job; replies with humanized
#            recurrence + job_id (AD-02: no interactive confirm in walking
#            skeleton — parser is rule-based + reply renders parsed cron in ru).
#   SCOPE: register_cron_add_handlers(router, *, get_user_tz, create_cron_user_job_fn?);
#          handle_cron_add (DI-friendly handler); _humanize_recurrence pure ru;
#          CRON_ADD_USAGE_RU + CRON_ADD_USAGE_HINT_RU + _GENERIC_ERR_RU constants.
#   DEPENDS: aiogram (Router, Command, CommandObject, Message), structlog,
#            ai_steward_wiki.classifier.recurrence (parse_recurrence, Recurrence,
#            RecurrenceParseResult),
#            ai_steward_wiki.scheduler.cron_user.create_cron_user_job
#   LINKS: M-TG-CRON-ADD, M-CLASSIFIER-RECURRENCE, M-SCHEDULER-CRON-USER, aisw-02v, R-4
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CRON_ADD_USAGE_RU - ru usage string + examples (R-4 mitigation)
#   CRON_ADD_USAGE_HINT_RU - alias kept for backward-compat with external readers
#   register_cron_add_handlers - factory: install Command('cron_add') on a Router
#   handle_cron_add - thin async handler (DI for get_user_tz + create-fn) — testable in isolation
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-02v: initial /cron_add handler
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from ai_steward_wiki.classifier.recurrence import Recurrence, parse_recurrence
from ai_steward_wiki.scheduler.cron_user import create_cron_user_job

if TYPE_CHECKING:
    from aiogram import Router
    from aiogram.filters.command import CommandObject
    from aiogram.types import Message

# ruff: noqa: RUF001, RUF003 — Cyrillic letters in user-facing strings are intentional (D-032).

__all__ = [
    "CRON_ADD_USAGE_HINT_RU",
    "CRON_ADD_USAGE_RU",
    "handle_cron_add",
    "register_cron_add_handlers",
]

_log = structlog.get_logger("tg.cron_add")

CRON_ADD_USAGE_RU = (
    "Используй: /cron_add <расписание> | <команда>\n"
    "Примеры:\n"
    "  /cron_add каждый день в 9 | напомни выпить витамины\n"
    "  /cron_add каждую среду в 14:00 | сделай сводку\n"
    "  /cron_add каждого 5-го в 10:00 | сводка за месяц"
)
CRON_ADD_USAGE_HINT_RU = CRON_ADD_USAGE_RU  # alias

_GENERIC_ERR_RU = "Что-то пошло не так. Попробуй ещё раз чуть позже."

_WEEKDAY_RU: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Type aliases for DI seams (handler test points).
_GetUserTz = Callable[[int], Awaitable[str]]
_CreateCronUserJob = Callable[..., Awaitable[int]]


def _humanize_recurrence(rec: Recurrence) -> str:
    if rec.kind == "daily":
        return f"каждый день в {rec.time_hhmm} ({rec.tz})"
    if rec.kind == "monthly":
        return f"каждое {rec.day_of_month}-е число в {rec.time_hhmm} ({rec.tz})"
    # weekly
    wds = sorted(set(rec.weekdays))
    if tuple(wds) == (0, 1, 2, 3, 4):
        period = "по будням"
    elif tuple(wds) == (5, 6):
        period = "по выходным"
    else:
        names = ", ".join(_WEEKDAY_RU[d] for d in wds)
        period = f"по {names}"
    return f"{period} в {rec.time_hhmm} ({rec.tz})"


async def handle_cron_add(
    message: Message,
    *,
    command: CommandObject,
    get_user_tz: _GetUserTz,
    create_cron_user_job_fn: _CreateCronUserJob | None = None,
) -> None:
    """Handle one /cron_add invocation.

    DI'd via kwargs so the test suite can supply mocks without monkey-patching:
    - get_user_tz: telegram_id → IANA tz
    - create_cron_user_job_fn: defaults to scheduler.cron_user.create_cron_user_job
    """
    if message.from_user is None or message.chat is None:
        _log.debug("tg.command.cron_add.skip_missing_fields")
        return
    owner = message.from_user.id
    chat = message.chat.id
    args = command.args or ""
    create_fn: _CreateCronUserJob = (
        create_cron_user_job_fn if create_cron_user_job_fn is not None else create_cron_user_job
    )

    # START_BLOCK_CRON_ADD_PARSE_INPUT
    if "|" not in args:
        _log.info("tg.command.cron_add.usage", owner_telegram_id=owner, reason="no_pipe")
        await message.answer(CRON_ADD_USAGE_RU)
        return
    schedule_text, _, command_text = args.partition("|")
    schedule_text = schedule_text.strip()
    command_text = command_text.strip()
    if not schedule_text or not command_text:
        _log.info(
            "tg.command.cron_add.usage",
            owner_telegram_id=owner,
            reason="empty_part",
            empty_schedule=not schedule_text,
            empty_command=not command_text,
        )
        await message.answer(CRON_ADD_USAGE_RU)
        return
    # END_BLOCK_CRON_ADD_PARSE_INPUT

    # START_BLOCK_CRON_ADD_PARSE_RECURRENCE
    try:
        user_tz = await get_user_tz(owner)
    except Exception as exc:
        _log.warning(
            "tg.command.cron_add.failed",
            owner_telegram_id=owner,
            stage="resolve_tz",
            error_class=type(exc).__name__,
        )
        await message.answer(_GENERIC_ERR_RU)
        return
    result = parse_recurrence(schedule_text, user_tz=user_tz)
    if result.escalate or result.recurrence is None:
        _log.info(
            "tg.command.cron_add.escalate",
            owner_telegram_id=owner,
            reason=result.reason,
        )
        await message.answer(CRON_ADD_USAGE_RU)
        return
    rec = result.recurrence
    _log.info(
        "tg.command.cron_add.parsed",
        owner_telegram_id=owner,
        recurrence_kind=rec.kind,
        time=rec.time_hhmm,
        tz=rec.tz,
    )
    # END_BLOCK_CRON_ADD_PARSE_RECURRENCE

    # START_BLOCK_CRON_ADD_SCHEDULE
    try:
        job_id = await create_fn(
            owner_telegram_id=owner,
            chat_id=chat,
            recurrence=rec,
            command=command_text,
            user_tz=user_tz,
            wiki_id=None,
        )
    except Exception as exc:
        _log.warning(
            "tg.command.cron_add.failed",
            owner_telegram_id=owner,
            stage="create_job",
            error_class=type(exc).__name__,
        )
        await message.answer(_GENERIC_ERR_RU)
        return
    _log.info(
        "tg.command.cron_add.scheduled",
        owner_telegram_id=owner,
        job_id=job_id,
        chat_id=chat,
    )
    preview = command_text if len(command_text) <= 120 else command_text[:117] + "..."
    await message.answer(
        f"✅ Запланировано (id={job_id}): {_humanize_recurrence(rec)}. Команда: {preview}"
    )
    # END_BLOCK_CRON_ADD_SCHEDULE


def register_cron_add_handlers(
    router: Router,
    *,
    get_user_tz: _GetUserTz,
    create_cron_user_job_fn: _CreateCronUserJob | None = None,
) -> None:
    """Register Command('cron_add') on ``router``.

    The handler closes over ``get_user_tz`` (telegram_id → IANA tz) and the
    optional ``create_cron_user_job_fn`` DI seam (defaults to the production
    scheduler.cron_user.create_cron_user_job).
    """
    from aiogram.filters import Command

    @router.message(Command("cron_add"))
    async def _on_cron_add(message: Message, command: CommandObject) -> None:
        # START_BLOCK_HANDLER_CRON_ADD
        await handle_cron_add(
            message,
            command=command,
            get_user_tz=get_user_tz,
            create_cron_user_job_fn=create_cron_user_job_fn,
        )
        # END_BLOCK_HANDLER_CRON_ADD
