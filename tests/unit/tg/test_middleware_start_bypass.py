"""AllowlistMiddleware: /start from unknown ids is allowed through (chunk 12)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_steward_wiki.auth.allowlist import Allowlist
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.tg.middleware_auth import DENY_TEXT_RU, AllowlistMiddleware


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeMessage:
    text: str | None
    from_user: FakeUser
    chat: FakeChat
    answers: list[str]

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def _allowlist_only(ids: list[int]) -> Allowlist:
    return Allowlist(
        UsersConfig(
            schema_version=1,
            users=tuple(UserRecord(telegram_id=i) for i in ids),
        )
    )


@pytest.mark.asyncio
async def test_start_command_bypasses_for_unknown_id() -> None:
    mw = AllowlistMiddleware(_allowlist_only([1]))
    seen: dict[str, object] = {}

    async def handler(event, data):
        seen["telegram_id"] = data.get("telegram_id")
        seen["is_pending"] = data.get("is_pending")
        return "OK"

    msg = FakeMessage(
        text="/start",
        from_user=FakeUser(id=999),
        chat=FakeChat(id=999),
        answers=[],
    )
    result = await mw(handler, msg, {})
    assert result == "OK"
    assert seen["telegram_id"] == 999
    assert seen["is_pending"] is True
    assert msg.answers == []  # no deny message


@pytest.mark.asyncio
async def test_non_start_blocked_for_unknown_id() -> None:
    mw = AllowlistMiddleware(_allowlist_only([1]))

    async def handler(event, data):
        return "should not run"

    msg = FakeMessage(
        text="hello",
        from_user=FakeUser(id=999),
        chat=FakeChat(id=999),
        answers=[],
    )
    result = await mw(handler, msg, {})
    assert result is None
    assert msg.answers == [DENY_TEXT_RU]


@pytest.mark.asyncio
async def test_known_id_pass_through() -> None:
    mw = AllowlistMiddleware(_allowlist_only([42]))

    async def handler(event, data):
        return data["user_record"].telegram_id

    msg = FakeMessage(
        text="hi",
        from_user=FakeUser(id=42),
        chat=FakeChat(id=42),
        answers=[],
    )
    assert await mw(handler, msg, {}) == 42


@pytest.mark.parametrize("cmd", ["/help", "/manual"])
@pytest.mark.asyncio
async def test_public_commands_bypass_for_unknown_id(cmd: str) -> None:
    # aisw-s5i Phase C.1: /help and /manual must reach handler for unknown ids.
    mw = AllowlistMiddleware(_allowlist_only([1]))
    seen: dict[str, object] = {}

    async def handler(event, data):
        seen["is_pending"] = data.get("is_pending")
        return "OK"

    msg = FakeMessage(
        text=cmd,
        from_user=FakeUser(id=999),
        chat=FakeChat(id=999),
        answers=[],
    )
    assert await mw(handler, msg, {}) == "OK"
    assert seen["is_pending"] is True
    assert msg.answers == []


@pytest.mark.asyncio
async def test_digest_command_still_blocked_for_unknown_id() -> None:
    # aisw-s5i Phase C.1 regression: /digest_now stays blocked for unknown ids.
    mw = AllowlistMiddleware(_allowlist_only([1]))

    async def handler(event, data):
        return "should not run"

    msg = FakeMessage(
        text="/digest_now",
        from_user=FakeUser(id=999),
        chat=FakeChat(id=999),
        answers=[],
    )
    assert await mw(handler, msg, {}) is None
    assert msg.answers == [DENY_TEXT_RU]


@pytest.mark.asyncio
async def test_start_with_bot_suffix_bypasses() -> None:
    mw = AllowlistMiddleware(_allowlist_only([]))
    seen: list[bool] = []

    async def handler(event, data):
        seen.append(data["is_pending"])

    msg = FakeMessage(
        text="/start@some_bot deeplink_payload",
        from_user=FakeUser(id=5),
        chat=FakeChat(id=5),
        answers=[],
    )
    await mw(handler, msg, {})
    assert seen == [True]
