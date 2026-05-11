"""Tests for AllowlistMiddleware (D-031/D-042 gate)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
class FakeMessageEvent:
    from_user: FakeUser
    chat: FakeChat
    answered: list[str] = field(default_factory=list)

    async def answer(self, text: str) -> None:
        self.answered.append(text)


def _allowlist(ids: list[int]) -> Allowlist:
    return Allowlist(
        UsersConfig(
            schema_version=1,
            users=tuple(UserRecord(telegram_id=i, role="user") for i in ids),
        )
    )


async def _call(mw: AllowlistMiddleware, event: Any) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def handler(ev: Any, data: dict[str, Any]) -> str:
        captured.update(data)
        return "OK"

    result = await mw(handler, event, {})
    return result, captured


@pytest.mark.asyncio
async def test_allows_allowlisted_user_and_injects_user_record() -> None:
    mw = AllowlistMiddleware(_allowlist([1001]))
    ev = FakeMessageEvent(from_user=FakeUser(id=1001), chat=FakeChat(id=900))
    result, data = await _call(mw, ev)
    assert result == "OK"
    assert data["telegram_id"] == 1001
    assert isinstance(data["user_record"], UserRecord)
    assert ev.answered == []


@pytest.mark.asyncio
async def test_denies_stranger_and_sends_russian_one_liner() -> None:
    mw = AllowlistMiddleware(_allowlist([1001]))
    ev = FakeMessageEvent(from_user=FakeUser(id=2002), chat=FakeChat(id=900))
    result, data = await _call(mw, ev)
    assert result is None
    assert ev.answered == [DENY_TEXT_RU]
    assert "user_record" not in data


@pytest.mark.asyncio
async def test_passes_through_events_without_from_user() -> None:
    mw = AllowlistMiddleware(_allowlist([1001]))

    @dataclass
    class ServiceEvent:
        pass

    result, _ = await _call(mw, ServiceEvent())
    assert result == "OK"


@pytest.mark.asyncio
async def test_deny_swallows_reply_errors() -> None:
    mw = AllowlistMiddleware(_allowlist([1001]))

    @dataclass
    class BrokenEvent:
        from_user: FakeUser
        chat: FakeChat

        async def answer(self, text: str) -> None:
            raise RuntimeError("network down")

    ev = BrokenEvent(from_user=FakeUser(id=99), chat=FakeChat(id=1))
    result, _ = await _call(mw, ev)
    assert result is None  # denied, error swallowed
