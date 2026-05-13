"""tg.bot.register_bot_commands publishes the native TG menu (aisw-s5i Phase D)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.types import BotCommand

from ai_steward_wiki.tg.bot import register_bot_commands


@pytest.mark.asyncio
async def test_register_bot_commands_calls_set_my_commands_once() -> None:
    bot = AsyncMock()
    await register_bot_commands(bot)
    assert bot.set_my_commands.await_count == 1


@pytest.mark.asyncio
async def test_register_bot_commands_publishes_all_six_commands() -> None:
    bot = AsyncMock()
    await register_bot_commands(bot)
    args, kwargs = bot.set_my_commands.call_args
    cmds = args[0] if args else kwargs["commands"]
    names = {c.command for c in cmds}
    assert names == {"start", "help", "manual", "digest_now", "expand", "digest_sections"}


@pytest.mark.asyncio
async def test_register_bot_commands_all_descriptions_are_non_empty_ru() -> None:
    bot = AsyncMock()
    await register_bot_commands(bot)
    args, _ = bot.set_my_commands.call_args
    cmds = args[0]
    for c in cmds:
        assert isinstance(c, BotCommand)
        assert c.description
        assert len(c.description) > 0
        # Heuristic: at least one Cyrillic letter — confirms RU (D-032).
        assert any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in c.description), c.command
