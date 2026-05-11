# FILE: src/ai_steward_wiki/auth/users_toml.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Parse and validate users.toml — SSoT of allowlist (D-042).
#   SCOPE: UserRecord, UsersConfig, load_users_toml, UsersTomlError.
#   DEPENDS: tomllib (stdlib), pydantic v2
#   LINKS: D-031, D-042, M-AUTH-USERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   SCHEMA_VERSION - integer constant; supported users.toml schema version
#   UserRecord - frozen Pydantic model for one [[users]] entry
#   UsersConfig - frozen top-level (schema_version + users list)
#   UsersTomlError - raised on parse / validation failure
#   load_users_toml - read path, parse TOML, validate, return UsersConfig
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial users.toml parser/validator (D-031/D-042)
# END_CHANGE_SUMMARY

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "SCHEMA_VERSION",
    "UserRecord",
    "UsersConfig",
    "UsersTomlError",
    "load_users_toml",
]

SCHEMA_VERSION = 1


class UsersTomlError(Exception):
    """Raised when users.toml fails to parse or validate."""


class UserRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    telegram_id: int
    enabled: bool = True
    role: Literal["admin", "user"] = "user"
    display_name: str | None = None
    tz: str | None = None
    lang: str | None = None
    aisw_uid: int | None = None

    @field_validator("telegram_id")
    @classmethod
    def _positive_telegram_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("telegram_id must be positive")
        return v


class UsersConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(...)
    users: tuple[UserRecord, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v}, expected {SCHEMA_VERSION}")
        return v

    @model_validator(mode="after")
    def _unique_telegram_ids(self) -> UsersConfig:
        seen: set[int] = set()
        for u in self.users:
            if u.telegram_id in seen:
                raise ValueError(f"duplicate telegram_id {u.telegram_id}")
            seen.add(u.telegram_id)
        return self


def load_users_toml(path: Path) -> UsersConfig:
    """Read, parse, validate. Raise UsersTomlError on any failure."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsersTomlError(f"cannot read {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise UsersTomlError(f"TOML parse error in {path}: {exc}") from exc
    try:
        return UsersConfig.model_validate(data)
    except Exception as exc:
        raise UsersTomlError(f"validation error in {path}: {exc}") from exc
