"""Tests for build_dispatcher wiring (M-TG-TEXT)."""

from __future__ import annotations

from ai_steward_wiki.auth.allowlist import Allowlist
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.tg.bot import build_dispatcher
from ai_steward_wiki.tg.middleware_auth import AllowlistMiddleware


def test_build_dispatcher_registers_allowlist_outer_middleware() -> None:
    allowlist = Allowlist(UsersConfig(schema_version=1, users=(UserRecord(telegram_id=1001),)))
    dp = build_dispatcher(allowlist)
    # aiogram stores outer middlewares on observer; we inspect by type.
    outer = list(dp.update.outer_middleware)
    assert any(isinstance(m, AllowlistMiddleware) for m in outer)
