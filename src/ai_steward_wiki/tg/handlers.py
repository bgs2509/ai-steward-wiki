# FILE: src/ai_steward_wiki/tg/handlers.py
# VERSION: 0.5.0
# START_MODULE_CONTRACT
#   PURPOSE: aiogram Router that adapts Telegram message/callback events to
#            the MessagePipeline Protocol (+ the bot's first slash commands).
#            Handlers stay thin: extract IDs, download bytes when needed,
#            delegate; no business logic here.
#   SCOPE: build_router(pipeline) -> Router. Message handlers: /digest_now,
#          /expand <section> (aisw-269 — first Command-filter handlers; reach
#          scheduler.firing accessors), /digest_sections (aisw-pv8 — inline
#          on/off toggles for the optional digest sections), text, voice, photo,
#          audio, video_note, document; confirm callback; digestsec: callback.
#          Audio and video_note route to on_voice (STT path); photo forwards
#          caption. Private helper _download_bytes for file-id payloads. (The
#          text handler already excludes "/"-prefixed messages, so command
#          order is irrelevant.)
#   DEPENDS: aiogram (Router, F, Bot, filters.Command, types incl.
#            InlineKeyboardButton/InlineKeyboardMarkup), structlog,
#            ai_steward_wiki.tg.pipeline.MessagePipeline,
#            ai_steward_wiki.scheduler.firing (list_owner_digest_job_ids /
#            fire_digest_job / run_section_expand / get_owner_digest_prefs /
#            set_owner_digest_section / DigestNotInitialisedError),
#            ai_steward_wiki.storage.sessions.digest_prefs (DigestPrefs /
#            TOGGLEABLE_DIGEST_SECTIONS / SECTION_DISPLAY_NAME)
#   LINKS: M-TG-HANDLERS-WIRING (chunk 19), M-SCHEDULER-FIRING, M-STORAGE-SESSIONS,
#          D-022, D-023, D-024, D-031, D-042, ADR-025, ADR-026, aisw-269, aisw-pv8
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CONFIRM_CALLBACK_PREFIX - "confirm:" callback_data prefix (D-023)
#   DIGESTSEC_CALLBACK_PREFIX - "digestsec:" callback_data prefix (ADR-026)
#   EXPAND_SECTION_KEYS - the four /expand <section> keys (mirror D-024 headers)
#   WIKIPICK_CALLBACK_PREFIX - "wikipick:" callback_data prefix (aisw-13h)
#   build_router - factory wiring the slash commands + message/callback handlers
#                  to a MessagePipeline (optional templates_dir + on_start_unknown for /start)
#   parse_confirm_callback - parse `confirm:<pending_id>:<action>` payload
#   parse_digestsec_callback - parse `digestsec:<section>:<0|1>` -> (section, target_enabled)
#   parse_wikipick_callback - parse `wikipick:<pending_id>:<idx>` -> (pending_id, wiki_index)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.5.0 - aisw-02v: build_router gains optional get_user_tz kwarg +
#                registers the /cron_add Command handler via
#                ai_steward_wiki.tg.cron_add.register_cron_add_handlers when
#                get_user_tz is provided. New routing dependency M-TG-CRON-ADD.
#   PREVIOUS:    v0.4.0 - aisw-s5i: /start, /help, /manual handlers (template-based).
#                build_router gains optional templates_dir + on_start_unknown kwargs.
#                /start branches on AllowlistMiddleware's is_pending flag; known →
#                start-known.ru.md, unknown → on_start_unknown(...) + onboarding-intro.
#                /help renders help.ru.md (D-041 verbatim WIKI-explainer); /manual
#                renders manual.ru.md (worked scenarios). New anchors
#                tg.command.start(.known/.unknown/.unknown.failed), tg.command.help,
#                tg.command.manual.
#   PREVIOUS:    v0.3.0 - aisw-163 P5: register on_reminder_card on `r:`-prefixed
#                callback_data (before confirm:/digestsec: handlers). Imports
#                REMINDER_CALLBACK_PREFIX + on_reminder_card from tg.callbacks.
#                New anchor tg.handlers.reminder_card (delegated).
#   PREVIOUS:    v0.2.0 - aisw-pv8 (Inbox-WIKI Phase-D.b.2c): /digest_sections
#                command (renders an inline keyboard of on/off toggles for the
#                optional digest sections via firing.get_owner_digest_prefs) +
#                digestsec: callback (firing.set_owner_digest_section, message
#                edited in place). New anchors tg.command.digest_sections(.shown/
#                .toggled/.bad_callback/.error). parse_digestsec_callback added.
#   PREVIOUS:    v0.1.0 - aisw-269 (Inbox-WIKI Phase-D.b.2b): the bot's first
#                slash commands — /digest_now (run the owner's enabled digest_job
#                rows via firing.fire_digest_job; 0 → ru hint; per-job errors
#                caught) and /expand <section> (today|meds|trackers|wiki →
#                firing.run_section_expand → message.answer; bad key → usage).
#                New anchors tg.command.digest_now(.empty/.job_failed/.done) +
#                tg.command.expand(.bad_section/.delivered/.failed).
#   PREVIOUS:    v0.0.4 - aisw-3dr: video_note handler stages as ext=mp4/mime=video/mp4,
#                audio handler derives ext from mime/file_name — no more .ogg for mp4.
#   PREVIOUS:    v0.0.3 - aisw-b2x: audio and document handlers also forward
#                message.caption (to on_voice / on_document) so it reaches Stage-1.
#                v0.0.2 - aisw-ahv (media chunk 3): add F.audio + F.video_note
#                handlers (route to on_voice STT path); photo handler forwards
#                message.caption to on_photo (D-022).
#                v0.0.1 - chunk 19: initial Router wiring delegating to pipeline
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.utils.chat_action import ChatActionSender

from ai_steward_wiki.auth.onboarding import format_intro_message
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.storage.sessions.digest_prefs import (
    SECTION_DISPLAY_NAME,
    TOGGLEABLE_DIGEST_SECTIONS,
    DigestPrefs,
)
from ai_steward_wiki.templates import render_template
from ai_steward_wiki.tg.aggregator import InboxAggregator
from ai_steward_wiki.tg.callbacks import REMINDER_CALLBACK_PREFIX, on_reminder_card
from ai_steward_wiki.tg.pipeline import ConfirmKeyboardAction, MessagePipeline

if TYPE_CHECKING:
    from aiogram import Bot as _Bot


# Tiny helper: yield a real ChatActionSender.typing context if bot is available,
# else an inert async-noop. Lets unit tests stay decoupled from aiogram.Bot mocks
# and lets the prod path show the "typing…" indicator throughout the pipeline.
from contextlib import asynccontextmanager


@asynccontextmanager
async def _typing_or_noop(bot: _Bot | None, chat_id: int):  # type: ignore[no-untyped-def]
    if bot is None:
        yield
        return
    async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
        yield


# Universal slow-work placeholder: kicks off a background task that, after
# PLACEHOLDER_DELAY_S, sends "⏳ Думаю…" as a real Telegram message. Whatever
# branch of pipeline.on_* eventually answers, we delete the placeholder in the
# `finally` block. This covers ALL slow branches (reminder, route, clarify,
# answer-runner) that the in-pipeline DefaultStreamingDelivery does NOT cover —
# DefaultStreamingDelivery only fires in the answer-runner branch (pipeline.py:1064).
PLACEHOLDER_DELAY_S: float = 1.0
PLACEHOLDER_TEXT_RU: str = "⏳ Думаю…"


@asynccontextmanager
async def _slow_work_placeholder(bot: _Bot | None, chat_id: int):  # type: ignore[no-untyped-def]
    if bot is None:
        yield
        return
    bot_ref = bot  # local non-None alias for closure (mypy narrowing)
    state: dict[str, int | None] = {"message_id": None}

    async def _show() -> None:
        try:
            await asyncio.sleep(PLACEHOLDER_DELAY_S)
            msg = await bot_ref.send_message(chat_id, PLACEHOLDER_TEXT_RU)
            state["message_id"] = msg.message_id
        except asyncio.CancelledError:
            raise
        except TelegramAPIError as exc:
            _log.debug("tg.handlers.placeholder.send_failed", error=str(exc))

    from contextlib import suppress

    task = asyncio.create_task(_show())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        mid = state["message_id"]
        if mid is not None:
            try:
                await bot_ref.delete_message(chat_id, mid)
            except TelegramAPIError as exc:
                _log.debug("tg.handlers.placeholder.delete_failed", error=str(exc))


class BotLoaderControl:
    """aisw-378: real LoaderControl — posts/removes the "⏳ Думаю…" loader via the Bot.

    Used by InboxAggregator to keep the loader as the chat's latest message while a
    burst of split messages is buffered, and to remove it once processing finishes.
    """

    def __init__(self, bot: _Bot) -> None:
        self._bot = bot

    async def post(self, chat_id: int) -> int | None:
        try:
            msg = await self._bot.send_message(chat_id, PLACEHOLDER_TEXT_RU)
            return int(msg.message_id)
        except TelegramAPIError as exc:
            _log.debug("tg.aggregator.loader.send_failed", error=str(exc))
            return None

    async def delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(chat_id, message_id)
        except TelegramAPIError as exc:
            _log.debug("tg.aggregator.loader.delete_failed", error=str(exc))


__all__ = [
    "CONFIRM_CALLBACK_PREFIX",
    "DIGESTSEC_CALLBACK_PREFIX",
    "EXPAND_SECTION_KEYS",
    "WIKIPICK_CALLBACK_PREFIX",
    "build_router",
    "parse_confirm_callback",
    "parse_digestsec_callback",
    "parse_wikipick_callback",
]

if TYPE_CHECKING:
    from aiogram import Router
    from aiogram.types import InlineKeyboardMarkup

_log = structlog.get_logger("tg.handlers")

CONFIRM_CALLBACK_PREFIX = "confirm:"
_VALID_ACTIONS: frozenset[str] = frozenset({"confirm", "correct", "cancel"})

# WIKI-picker on the route-confirm card — `wikipick:<pending_id>:<idx>` (aisw-13h).
WIKIPICK_CALLBACK_PREFIX = "wikipick:"

# /digest_sections — inline-toggle callbacks `digestsec:<section>:<0|1>` (ADR-026).
DIGESTSEC_CALLBACK_PREFIX = "digestsec:"

# /expand <section> — the four keys mirror the D-024 <b>-headers in prompts/digest.md.
EXPAND_SECTION_KEYS: tuple[str, ...] = ("today", "meds", "trackers", "wiki")

# ru user-facing strings for the slash commands (D-032: ru-only, no i18n).
_DIGEST_NOW_NONE_RU = (
    "У тебя пока нет настроенной сводки. Создай, например: «делай сводку каждый день в 9»."  # noqa: RUF001
)
_DIGEST_UNAVAILABLE_RU = "Сводки сейчас недоступны — попробуй позже."
_EXPAND_USAGE_RU = "Используй: /expand <раздел> — today | meds | trackers | wiki"
_EXPAND_NO_WIKI_RU = "У тебя пока нет ни одной WIKI для детализации."  # noqa: RUF001
_EXPAND_EMPTY_RU = "По этому разделу за период ничего нет."
_GENERIC_ERR_RU = "Что-то пошло не так. Попробуй ещё раз чуть позже."

# /digest_sections ru strings (D-032: ru-only, no i18n).
_DIGEST_SECTIONS_HEADER_RU = "Разделы твоей сводки — нажми, чтобы включить/выключить:"
_DIGESTSEC_DONE_RU = "Готово"
_DIGESTSEC_BAD_RU = "Не понял кнопку."  # noqa: RUF001

# /start, /help, /manual (aisw-s5i Phase C.2). Wording lives in templates/*.ru.md;
# we only hold the slug allowlists here so a typo in a template fails fast at render.
_BOT_NAME = "ai-steward-wiki"
_START_KNOWN_SLUGS: frozenset[str] = frozenset(
    {"greeting", "how-to-start", "commands-hint", "pointers"}
)
_HELP_SLUGS: frozenset[str] = frozenset(
    {"intro", "wiki-explainer", "lazy-domains", "scenarios", "commands", "next-steps"}
)
_MANUAL_SLUGS: frozenset[str] = frozenset(
    {
        "intro",
        "scenario-note",
        "scenario-wiki",
        "scenario-reminder",
        "scenario-digest",
        "scenario-expand-toggle",
        "voice-photo",
        "privacy-note",
    }
)
# Default templates dir for tests and standalone build_router callers; production
# wiring (__main__.py) passes Settings.wiki_template_dir explicitly.
_DEFAULT_TEMPLATES_DIR: Path = Path(__file__).resolve().parents[3] / "templates"

# Map common audio MIME types to a staging-file extension (D-022). Unknown →
# fall back to the file_name suffix, else "mp3".
_AUDIO_MIME_TO_EXT: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


def _audio_ext_for(mime: str | None, file_name: str | None) -> str:
    """Pick a sane staging extension for an audio file (alnum, ≤8 chars)."""
    if mime:
        ext = _AUDIO_MIME_TO_EXT.get(mime.lower())
        if ext is not None:
            return ext
    if file_name and "." in file_name:
        suffix = file_name.rsplit(".", 1)[-1].lower()
        if suffix.isalnum() and 1 <= len(suffix) <= 8:
            return suffix
    return "mp3"


def parse_confirm_callback(data: str) -> tuple[int, ConfirmKeyboardAction] | None:
    """Parse ``confirm:<pending_id>:<action>``; return None on malformed input."""
    if not data.startswith(CONFIRM_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        pending_id = int(parts[1])
    except ValueError:
        return None
    action = parts[2]
    if action not in _VALID_ACTIONS:
        return None
    return pending_id, cast(ConfirmKeyboardAction, action)


def parse_wikipick_callback(data: str) -> tuple[int, int] | None:
    """Parse ``wikipick:<pending_id>:<idx>`` → ``(pending_id, wiki_index)`` or None (aisw-13h)."""
    if not data.startswith(WIKIPICK_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def parse_digestsec_callback(data: str) -> tuple[str, bool] | None:
    """Parse ``digestsec:<section>:<0|1>`` → ``(section, target_enabled)`` or None.

    The flag is the TARGET state (what tapping the button sets it to), so a tap
    on a stale message is idempotent.
    """
    if not data.startswith(DIGESTSEC_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, section, flag = parts
    if section not in TOGGLEABLE_DIGEST_SECTIONS or flag not in ("0", "1"):
        return None
    return section, flag == "1"


def _build_digest_sections_kb(prefs: DigestPrefs) -> InlineKeyboardMarkup:
    """Render the on/off toggle keyboard for the optional digest sections (ADR-026)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    for key in TOGGLEABLE_DIGEST_SECTIONS:
        on = bool(getattr(prefs, f"{key}_enabled"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{SECTION_DISPLAY_NAME[key]}: {'вкл ✅' if on else 'выкл ⬜'}",
                    callback_data=f"{DIGESTSEC_CALLBACK_PREFIX}{key}:{0 if on else 1}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _download_bytes(bot: object, file_id: str) -> bytes:
    """Download a file by file_id and return its raw bytes.

    aiogram 3: `bot.get_file(file_id)` → `bot.download(file)` returns a
    BinaryIO-like; we read it into memory (MVP — small media only).
    """
    file = await bot.get_file(file_id)  # type: ignore[attr-defined]
    buf = io.BytesIO()
    await bot.download(file, destination=buf)  # type: ignore[attr-defined]
    return buf.getvalue()


def build_router(
    pipeline: MessagePipeline,
    *,
    templates_dir: Path | None = None,
    on_start_unknown: Callable[..., Awaitable[None]] | None = None,
    get_user_tz: Callable[[int], Awaitable[str]] | None = None,
    aggregator: InboxAggregator | None = None,
) -> Router:
    """Construct an aiogram Router wired to ``pipeline``.

    ``templates_dir`` — directory holding ``start-known.ru.md`` / ``help.ru.md`` /
    ``manual.ru.md`` / ``onboarding-intro.ru.md`` (aisw-s5i). Defaults to the
    repo-level ``templates/`` for tests; production passes
    ``Settings.wiki_template_dir`` from ``__main__.py``.

    ``on_start_unknown`` — optional async callable invoked on ``/start`` from
    an unknown telegram_id (when ``is_pending=True`` reaches the handler via
    AllowlistMiddleware). Signature: ``(telegram_id: int, username: str | None) -> None``.
    If ``None`` the handler still answers with the intro template — useful for
    tests and graceful degradation.
    """
    from aiogram import F, Router
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, Message

    _templates_dir: Path = templates_dir if templates_dir is not None else _DEFAULT_TEMPLATES_DIR
    router = Router(name="m-tg-handlers-wiring")

    if get_user_tz is not None:
        from ai_steward_wiki.tg.cron_add import register_cron_add_handlers

        register_cron_add_handlers(router, get_user_tz=get_user_tz)

    @router.message(F.text & ~F.text.startswith("/"))
    async def _on_text(message: Message) -> None:
        # START_BLOCK_HANDLER_TEXT
        if message.from_user is None or message.text is None or message.chat is None:
            _log.debug("tg.handlers.text.skip_missing_fields")
            return
        # aisw-378: when an aggregator is wired, buffer the message and return — a
        # burst of split messages is debounced into ONE classify/route, and the
        # aggregator owns the "⏳ Думаю…" loader (so the typing/placeholder wrap is
        # skipped here, it would fight the loader and end on return anyway).
        if aggregator is not None:
            await aggregator.submit(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                text=message.text,
            )
            return
        # Keep Telegram's "typing…" indicator alive for the whole pipeline
        # (Stage-0 + router + answer); aiogram re-sends sendChatAction ~every 5s.
        # message.bot may be None in unit-test fixtures — fall through without the
        # indicator in that case.
        _bot = getattr(message, "bot", None)
        async with (
            _typing_or_noop(_bot, message.chat.id),
            _slow_work_placeholder(_bot, message.chat.id),
        ):
            await pipeline.on_text(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                text=message.text,
            )
        # END_BLOCK_HANDLER_TEXT

    @router.message(Command("start"))
    async def _on_start(message: Message, **data: object) -> None:
        # START_BLOCK_HANDLER_START
        # aisw-s5i: /start branches on AllowlistMiddleware's `is_pending` flag.
        # Known user → render templates/start-known.ru.md.
        # Unknown user → optionally call on_start_unknown (records pending row,
        # D-030) + render existing onboarding-intro.ru.md via format_intro_message.
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.start.skip_missing_fields")
            return
        owner = message.from_user.id
        is_pending = bool(data.get("is_pending", False))
        if not is_pending:
            text = render_template(
                _templates_dir / "start-known.ru.md",
                required_slugs=_START_KNOWN_SLUGS,
                bot_name=_BOT_NAME,
            )
            _log.info("tg.command.start.known", owner_telegram_id=owner)
            try:
                await message.answer(text)
            except TelegramBadRequest as exc:
                _log.warning(
                    "tg.command.start.send_failed",
                    owner_telegram_id=owner,
                    chat_id=message.chat.id,
                    error=str(exc),
                )
            return
        # Unknown id reached us via the public-command bypass.
        if on_start_unknown is not None:
            try:
                await on_start_unknown(
                    telegram_id=owner,
                    username=message.from_user.username,
                )
            except Exception as exc:  # defensive: never crash the handler
                _log.warning(
                    "tg.command.start.unknown.failed",
                    owner_telegram_id=owner,
                    error_class=type(exc).__name__,
                )
        text = format_intro_message(
            _templates_dir / "onboarding-intro.ru.md",
            bot_name=_BOT_NAME,
        )
        _log.info("tg.command.start.unknown", owner_telegram_id=owner)
        try:
            await message.answer(text)
        except TelegramBadRequest as exc:
            _log.warning(
                "tg.command.start.send_failed",
                owner_telegram_id=owner,
                chat_id=message.chat.id,
                error=str(exc),
            )
        # END_BLOCK_HANDLER_START

    @router.message(Command("help"))
    async def _on_help(message: Message, **data: object) -> None:
        # START_BLOCK_HANDLER_HELP
        # aisw-s5i: static help — D-041 mandatory WIKI-explainer + command cheat-sheet.
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.help.skip_missing_fields")
            return
        text = render_template(
            _templates_dir / "help.ru.md",
            required_slugs=_HELP_SLUGS,
            bot_name=_BOT_NAME,
        )
        _log.info(
            "tg.command.help",
            owner_telegram_id=message.from_user.id,
            is_allowed=not bool(data.get("is_pending", False)),
        )
        try:
            await message.answer(text)
        except TelegramBadRequest as exc:
            _log.warning(
                "tg.command.help.send_failed",
                owner_telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                error=str(exc),
            )
        # END_BLOCK_HANDLER_HELP

    @router.message(Command("manual"))
    async def _on_manual(message: Message, **data: object) -> None:
        # START_BLOCK_HANDLER_MANUAL
        # aisw-s5i: extended scenarios — worked examples for primary NL usage.
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.manual.skip_missing_fields")
            return
        text = render_template(
            _templates_dir / "manual.ru.md",
            required_slugs=_MANUAL_SLUGS,
            bot_name=_BOT_NAME,
        )
        _log.info(
            "tg.command.manual",
            owner_telegram_id=message.from_user.id,
            is_allowed=not bool(data.get("is_pending", False)),
        )
        try:
            await message.answer(text)
        except TelegramBadRequest as exc:
            _log.warning(
                "tg.command.manual.send_failed",
                owner_telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                error=str(exc),
            )
        # END_BLOCK_HANDLER_MANUAL

    @router.message(Command("digest_now"))
    async def _on_digest_now(message: Message) -> None:
        # START_BLOCK_HANDLER_DIGEST_NOW
        # aisw-269: ad-hoc digest — run each of the owner's enabled digest_job rows
        # through the existing fire_digest_job pipeline (lock/audit/3-strike reused).
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.digest_now.skip_missing_fields")
            return
        owner = message.from_user.id
        try:
            ids = await firing.list_owner_digest_job_ids(owner)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.digest_now.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_UNAVAILABLE_RU)
            return
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.digest_now.failed",
                owner_telegram_id=owner,
                error_class=type(exc).__name__,
            )
            await message.answer(_GENERIC_ERR_RU)
            return
        if not ids:
            _log.info("tg.command.digest_now.empty", owner_telegram_id=owner)
            await message.answer(_DIGEST_NOW_NONE_RU)
            return
        _log.info("tg.command.digest_now", owner_telegram_id=owner, n_jobs=len(ids))
        for job_id in ids:
            try:
                await firing.fire_digest_job(job_id)
            except Exception as exc:  # one bad job must not stop the rest
                _log.warning(
                    "tg.command.digest_now.job_failed",
                    job_id=job_id,
                    error_class=type(exc).__name__,
                )
        _log.info("tg.command.digest_now.done", owner_telegram_id=owner, n_jobs=len(ids))
        # END_BLOCK_HANDLER_DIGEST_NOW

    @router.message(Command("expand"))
    async def _on_expand(message: Message) -> None:
        # START_BLOCK_HANDLER_EXPAND
        # aisw-269: on-demand section detail — re-run Claude scoped to one section.
        if message.from_user is None or message.chat is None or message.text is None:
            _log.debug("tg.handlers.expand.skip_missing_fields")
            return
        owner = message.from_user.id
        parts = message.text.split(maxsplit=1)
        section = parts[1].strip().lower() if len(parts) > 1 else ""
        if section not in EXPAND_SECTION_KEYS:
            _log.info("tg.command.expand.bad_section", owner_telegram_id=owner, got=section)
            await message.answer(_EXPAND_USAGE_RU)
            return
        try:
            text = await firing.run_section_expand(owner, section)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.expand.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_UNAVAILABLE_RU)
            return
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.expand.failed",
                owner_telegram_id=owner,
                section=section,
                error_class=type(exc).__name__,
            )
            await message.answer(_GENERIC_ERR_RU)
            return
        if text is None:
            await message.answer(_EXPAND_NO_WIKI_RU)
            return
        body = text.strip() or _EXPAND_EMPTY_RU
        await message.answer(body)
        _log.info(
            "tg.command.expand.delivered",
            owner_telegram_id=owner,
            section=section,
            chars=len(body),
        )
        # END_BLOCK_HANDLER_EXPAND

    @router.message(Command("digest_sections"))
    async def _on_digest_sections(message: Message) -> None:
        # START_BLOCK_HANDLER_DIGEST_SECTIONS
        # aisw-pv8: show the owner's per-section digest toggles as an inline keyboard.
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.digest_sections.skip_missing_fields")
            return
        owner = message.from_user.id
        try:
            prefs = await firing.get_owner_digest_prefs(owner)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.digest_sections.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_UNAVAILABLE_RU)
            return
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.digest_sections.error",
                owner_telegram_id=owner,
                error_class=type(exc).__name__,
            )
            await message.answer(_GENERIC_ERR_RU)
            return
        await message.answer(
            _DIGEST_SECTIONS_HEADER_RU,
            reply_markup=_build_digest_sections_kb(prefs),
        )
        _log.info(
            "tg.command.digest_sections.shown",
            owner_telegram_id=owner,
            trackers_enabled=prefs.trackers_enabled,
            wiki_enabled=prefs.wiki_enabled,
        )
        # END_BLOCK_HANDLER_DIGEST_SECTIONS

    @router.message(F.voice)
    async def _on_voice(message: Message) -> None:
        # START_BLOCK_HANDLER_VOICE
        if (
            message.from_user is None
            or message.voice is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.voice.skip_missing_fields")
            return
        audio = await _download_bytes(message.bot, message.voice.file_id)
        async with (
            ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id),
            _slow_work_placeholder(message.bot, message.chat.id),
        ):
            await pipeline.on_voice(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                audio_bytes=audio,
            )
        # END_BLOCK_HANDLER_VOICE

    @router.message(F.photo)
    async def _on_photo(message: Message) -> None:
        # START_BLOCK_HANDLER_PHOTO
        if (
            message.from_user is None
            or not message.photo
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.photo.skip_missing_fields")
            return
        # Telegram delivers photo as a list of sizes — take the largest (last).
        photo = message.photo[-1]
        data = await _download_bytes(message.bot, photo.file_id)
        async with (
            ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id),
            _slow_work_placeholder(message.bot, message.chat.id),
        ):
            await pipeline.on_photo(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                photo_bytes=data,
                mime="image/jpeg",  # TG re-encodes photos to JPEG (D-022)
                caption=message.caption,
            )
        # END_BLOCK_HANDLER_PHOTO

    @router.message(F.audio)
    async def _on_audio(message: Message) -> None:
        # START_BLOCK_HANDLER_AUDIO
        # Audio files (mp3/ogg/m4a) take the same STT path as voice messages (D-022).
        if (
            message.from_user is None
            or message.audio is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.audio.skip_missing_fields")
            return
        data = await _download_bytes(message.bot, message.audio.file_id)
        audio_mime = message.audio.mime_type or "audio/mpeg"
        async with (
            ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id),
            _slow_work_placeholder(message.bot, message.chat.id),
        ):
            await pipeline.on_voice(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                audio_bytes=data,
                caption=message.caption,
                ext=_audio_ext_for(message.audio.mime_type, message.audio.file_name),
                mime=audio_mime,
            )
        # END_BLOCK_HANDLER_AUDIO

    @router.message(F.video_note)
    async def _on_video_note(message: Message) -> None:
        # START_BLOCK_HANDLER_VIDEO_NOTE
        # Video notes (mp4) carry an audio track; faster-whisper/pyav demuxes it,
        # so they route through the same STT path as voice (D-022).
        if (
            message.from_user is None
            or message.video_note is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.video_note.skip_missing_fields")
            return
        data = await _download_bytes(message.bot, message.video_note.file_id)
        async with (
            ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id),
            _slow_work_placeholder(message.bot, message.chat.id),
        ):
            await pipeline.on_voice(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                audio_bytes=data,
                ext="mp4",
                mime="video/mp4",
            )
        # END_BLOCK_HANDLER_VIDEO_NOTE

    @router.message(F.document)
    async def _on_document(message: Message) -> None:
        # START_BLOCK_HANDLER_DOCUMENT
        if (
            message.from_user is None
            or message.document is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.document.skip_missing_fields")
            return
        doc = message.document
        data = await _download_bytes(message.bot, doc.file_id)
        async with (
            ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id),
            _slow_work_placeholder(message.bot, message.chat.id),
        ):
            await pipeline.on_document(
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                update_id=message.message_id,
                doc_bytes=data,
                mime=doc.mime_type or "application/octet-stream",
                filename=doc.file_name or "unnamed",
                caption=message.caption,
            )
        # END_BLOCK_HANDLER_DOCUMENT

    @router.callback_query(F.data.startswith(REMINDER_CALLBACK_PREFIX))
    async def _on_reminder_card_cb(callback: CallbackQuery) -> None:
        # START_BLOCK_HANDLER_REMINDER_CARD_CB
        # aisw-163 P5: register on_reminder_card before confirm: / digestsec:
        # callbacks. Distinct "r:" prefix keeps routing unambiguous.
        await on_reminder_card(callback)
        # END_BLOCK_HANDLER_REMINDER_CARD_CB

    @router.callback_query(F.data.startswith(CONFIRM_CALLBACK_PREFIX))
    async def _on_confirm(callback: CallbackQuery) -> None:
        # START_BLOCK_HANDLER_CONFIRM_CB
        if callback.from_user is None or callback.data is None or callback.message is None:
            _log.debug("tg.handlers.callback.skip_missing_fields")
            await callback.answer()
            return
        parsed = parse_confirm_callback(callback.data)
        if parsed is None:
            _log.info("tg.handlers.callback.malformed", data=callback.data)
            await callback.answer()
            return
        pending_id, action = parsed
        await pipeline.on_confirm_callback(
            telegram_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            pending_id=pending_id,
            action=action,
        )
        await callback.answer()
        # END_BLOCK_HANDLER_CONFIRM_CB

    @router.callback_query(F.data.startswith(WIKIPICK_CALLBACK_PREFIX))
    async def _on_wikipick(callback: CallbackQuery) -> None:
        # START_BLOCK_HANDLER_WIKIPICK_CB (aisw-13h)
        if callback.from_user is None or callback.data is None or callback.message is None:
            _log.debug("tg.handlers.callback.skip_missing_fields")
            await callback.answer()
            return
        parsed = parse_wikipick_callback(callback.data)
        if parsed is None:
            _log.info("tg.handlers.callback.malformed", data=callback.data)
            await callback.answer()
            return
        pending_id, wiki_index = parsed
        await pipeline.on_wikipick_callback(
            telegram_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            pending_id=pending_id,
            wiki_index=wiki_index,
        )
        await callback.answer()
        # END_BLOCK_HANDLER_WIKIPICK_CB

    @router.callback_query(F.data.startswith(DIGESTSEC_CALLBACK_PREFIX))
    async def _on_digestsec_callback(cb: CallbackQuery) -> None:
        # START_BLOCK_HANDLER_DIGESTSEC_CB
        # aisw-pv8: flip one digest section on/off and re-render the keyboard in place.
        owner = cb.from_user.id if cb.from_user else 0
        parsed = parse_digestsec_callback(cb.data or "")
        if parsed is None:
            _log.info(
                "tg.command.digest_sections.bad_callback", owner_telegram_id=owner, data=cb.data
            )
            await cb.answer(_DIGESTSEC_BAD_RU)
            return
        section, target = parsed
        try:
            prefs = await firing.set_owner_digest_section(owner, section=section, enabled=target)
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.digest_sections.error",
                owner_telegram_id=owner,
                section=section,
                error_class=type(exc).__name__,
            )
            await cb.answer(_GENERIC_ERR_RU)
            return
        if cb.message is not None:
            try:
                await cb.message.edit_reply_markup(reply_markup=_build_digest_sections_kb(prefs))  # type: ignore[union-attr]
            except Exception:  # stale/unmodified message — ignore, the write already happened
                _log.debug("tg.command.digest_sections.edit_skipped", owner_telegram_id=owner)
        await cb.answer(_DIGESTSEC_DONE_RU)
        _log.info(
            "tg.command.digest_sections.toggled",
            owner_telegram_id=owner,
            section=section,
            enabled=target,
        )
        # END_BLOCK_HANDLER_DIGESTSEC_CB

    return router
