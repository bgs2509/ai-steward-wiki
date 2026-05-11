"""Unit tests for tg.handlers (M-TG-HANDLERS-WIRING)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher

from ai_steward_wiki.auth.allowlist import Allowlist
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.tg.bot import build_dispatcher
from ai_steward_wiki.tg.handlers import (
    CONFIRM_CALLBACK_PREFIX,
    _download_bytes,
    build_router,
    parse_confirm_callback,
)


def test_parse_confirm_callback_valid_confirm() -> None:
    assert parse_confirm_callback("confirm:42:confirm") == (42, "confirm")


def test_parse_confirm_callback_valid_correct() -> None:
    assert parse_confirm_callback("confirm:7:correct") == (7, "correct")


def test_parse_confirm_callback_valid_cancel() -> None:
    assert parse_confirm_callback("confirm:99:cancel") == (99, "cancel")


def test_parse_confirm_callback_wrong_prefix() -> None:
    assert parse_confirm_callback("foo:1:confirm") is None


def test_parse_confirm_callback_bad_id() -> None:
    assert parse_confirm_callback("confirm:abc:confirm") is None


def test_parse_confirm_callback_bad_action() -> None:
    assert parse_confirm_callback("confirm:1:nuke") is None


def test_parse_confirm_callback_too_many_parts() -> None:
    assert parse_confirm_callback("confirm:1:confirm:extra") is None


def test_parse_confirm_callback_too_few_parts() -> None:
    assert parse_confirm_callback("confirm:1") is None


def test_confirm_callback_prefix_constant() -> None:
    assert CONFIRM_CALLBACK_PREFIX == "confirm:"


def test_build_router_returns_router_with_handlers() -> None:
    pipeline = MagicMock()
    router = build_router(pipeline)
    # Router has at least one message handler and one callback handler.
    assert len(router.message.handlers) >= 4
    assert len(router.callback_query.handlers) >= 1


def test_build_dispatcher_with_pipeline_includes_router() -> None:
    allowlist = Allowlist(UsersConfig(schema_version=1, users=(UserRecord(telegram_id=1),)))
    pipeline = MagicMock()
    dp = build_dispatcher(allowlist, pipeline=pipeline)
    assert isinstance(dp, Dispatcher)
    # At least one sub-router was included.
    assert len(dp.sub_routers) >= 1


def test_build_dispatcher_without_pipeline_omits_router() -> None:
    allowlist = Allowlist(UsersConfig(schema_version=1, users=(UserRecord(telegram_id=1),)))
    dp = build_dispatcher(allowlist)
    assert dp.sub_routers == []


@pytest.mark.asyncio
async def test_download_bytes_reads_into_buffer() -> None:
    fake_file = object()

    async def _get_file(file_id: str) -> object:
        assert file_id == "FID"
        return fake_file

    async def _download(file: object, destination: io.BytesIO) -> None:
        assert file is fake_file
        destination.write(b"PAYLOAD")

    bot = MagicMock()
    bot.get_file = _get_file
    bot.download = _download

    out = await _download_bytes(bot, "FID")
    assert out == b"PAYLOAD"


# ---- Fake aiogram event objects for end-to-end dispatch path ----


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeVoice:
    file_id: str
    duration: int = 1


@dataclass
class FakePhotoSize:
    file_id: str
    width: int = 100
    height: int = 100
    file_unique_id: str = "u"


@dataclass
class FakeDocument:
    file_id: str
    file_name: str | None = "f.txt"
    mime_type: str | None = "text/plain"


@dataclass
class FakeMessage:
    message_id: int
    chat: FakeChat
    from_user: FakeUser
    text: str | None = None
    voice: FakeVoice | None = None
    photo: list[FakePhotoSize] | None = None
    document: FakeDocument | None = None
    bot: Any = None


@pytest.mark.asyncio
async def test_router_text_handler_dispatches_to_pipeline() -> None:
    pipeline = MagicMock()
    pipeline.on_text = AsyncMock()
    pipeline.on_voice = AsyncMock()
    pipeline.on_photo = AsyncMock()
    pipeline.on_document = AsyncMock()
    pipeline.on_confirm_callback = AsyncMock()

    router = build_router(pipeline)
    # The first handler is text; we invoke its underlying callback directly
    # with a fake Message to avoid going through aiogram dispatch validators.
    text_handler = router.message.handlers[0].callback
    msg = FakeMessage(
        message_id=555,
        chat=FakeChat(id=10),
        from_user=FakeUser(id=1),
        text="hello",
    )
    await text_handler(msg)
    pipeline.on_text.assert_awaited_once_with(
        telegram_id=1, chat_id=10, update_id=555, text="hello"
    )


@pytest.mark.asyncio
async def test_router_voice_handler_downloads_and_dispatches() -> None:
    pipeline = MagicMock()
    pipeline.on_voice = AsyncMock()
    router = build_router(pipeline)

    voice_handler = router.message.handlers[1].callback

    async def _get_file(file_id: str) -> object:
        return object()

    async def _download(file: object, destination: io.BytesIO) -> None:
        destination.write(b"OGG")

    bot = MagicMock()
    bot.get_file = _get_file
    bot.download = _download

    msg = FakeMessage(
        message_id=556,
        chat=FakeChat(id=10),
        from_user=FakeUser(id=2),
        voice=FakeVoice(file_id="VID"),
        bot=bot,
    )
    await voice_handler(msg)
    pipeline.on_voice.assert_awaited_once_with(
        telegram_id=2, chat_id=10, update_id=556, audio_bytes=b"OGG"
    )


@pytest.mark.asyncio
async def test_router_callback_handler_parses_and_dispatches() -> None:
    pipeline = MagicMock()
    pipeline.on_confirm_callback = AsyncMock()
    router = build_router(pipeline)

    cb_handler = router.callback_query.handlers[0].callback

    @dataclass
    class FakeCallback:
        data: str
        from_user: FakeUser
        message: FakeMessage
        answers: list[Any] = field(default_factory=list)

        async def answer(self, *a: Any, **kw: Any) -> None:
            self.answers.append((a, kw))

    msg = FakeMessage(message_id=1, chat=FakeChat(id=11), from_user=FakeUser(id=7), text="recap")
    cb = FakeCallback(data="confirm:42:cancel", from_user=FakeUser(id=7), message=msg)

    await cb_handler(cb)
    pipeline.on_confirm_callback.assert_awaited_once_with(
        telegram_id=7, chat_id=11, pending_id=42, action="cancel"
    )
    assert cb.answers  # was acknowledged


@pytest.mark.asyncio
async def test_router_callback_handler_malformed_data_just_answers() -> None:
    pipeline = MagicMock()
    pipeline.on_confirm_callback = AsyncMock()
    router = build_router(pipeline)
    cb_handler = router.callback_query.handlers[0].callback

    @dataclass
    class FakeCallback:
        data: str
        from_user: FakeUser
        message: FakeMessage
        answers: list[Any] = field(default_factory=list)

        async def answer(self, *a: Any, **kw: Any) -> None:
            self.answers.append(1)

    msg = FakeMessage(message_id=1, chat=FakeChat(id=11), from_user=FakeUser(id=7), text="r")
    cb = FakeCallback(data="confirm:bad:confirm", from_user=FakeUser(id=7), message=msg)
    await cb_handler(cb)
    pipeline.on_confirm_callback.assert_not_awaited()
    assert cb.answers == [1]


# Stamp Bot / Dispatcher imports used so linter doesn't drop them.
def test_aiogram_imports_alive() -> None:
    assert Bot
    assert Dispatcher
