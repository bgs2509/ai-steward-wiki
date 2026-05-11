# FILE: src/ai_steward_wiki/auth/admin.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Admin surface — approve/reject pending users, elevation, shadow channel.
#   SCOPE: AdminService, FailureEvent, ShadowEmitter Protocol, LoggingShadowEmitter,
#          NotAnAdmin, ApprovalResult, RejectionResult, ElevationToken.
#   DEPENDS: pydantic v2, SQLAlchemy.async, ai_steward_wiki.auth.{users_toml,allowlist,sighup,onboarding},
#            ai_steward_wiki.storage.{sessions.models.PendingConfirm, audit.models.AdminEvent}
#   LINKS: D-028, D-031, D-032, D-042, M-ONBOARD-ADMIN
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ADMIN_ELEVATION_CATEGORY - sessions.pending_confirms.category for admin elevation rows
#   DEFAULT_ELEVATION_TTL_MIN - default 30-minute window
#   FailureEvent - Pydantic strict model, metadata-only, content forbidden
#   ShadowEmitter - Protocol: emit(failure_event) -> Awaitable[None]
#   LoggingShadowEmitter - default emitter that structlog.info(...) the event
#   NotAnAdmin - raised when assert_admin fails
#   ApprovalResult - frozen dataclass: approve_pending result
#   RejectionResult - frozen dataclass: reject_pending result
#   ElevationToken - frozen dataclass: elevate result
#   AdminService - facade for approve_pending / reject_pending / elevate / demote /
#                  is_elevated / assert_admin
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 12: admin surface + shadow emitter (D-028, D-031)
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.allowlist import get_global
from ai_steward_wiki.auth.onboarding import PendingUserRepo
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig, load_users_toml
from ai_steward_wiki.storage.audit.models import AdminEvent
from ai_steward_wiki.storage.sessions.models import PendingConfirm

__all__ = [
    "ADMIN_ELEVATION_CATEGORY",
    "DEFAULT_ELEVATION_TTL_MIN",
    "AdminService",
    "ApprovalResult",
    "ElevationToken",
    "FailureEvent",
    "LoggingShadowEmitter",
    "NotAnAdmin",
    "RejectionResult",
    "ShadowEmitter",
]

_log = structlog.get_logger("auth.admin")

ADMIN_ELEVATION_CATEGORY = "admin_elevation"
DEFAULT_ELEVATION_TTL_MIN = 30

TenancyMode = Literal["single", "multi"]


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NotAnAdmin(Exception):
    """Raised when a caller lacks admin authority for the current TENANCY_MODE."""


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    user_added: bool
    already_present: bool = False


@dataclass(frozen=True)
class RejectionResult:
    ok: bool
    telegram_id: int
    reason: str


@dataclass(frozen=True)
class ElevationToken:
    admin_telegram_id: int
    expires_at_utc: datetime
    pending_id: int


class FailureEvent(BaseModel):
    """Shadow-channel admin payload — METADATA ONLY (D-031, INV: no content leakage).

    The model deliberately omits any `content`/`text`/`payload` field. Anything
    user-supplied lives outside this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str = Field(..., min_length=1, max_length=128)
    failure_kind: str = Field(..., min_length=1, max_length=64)
    wiki_id: str | None = Field(default=None, max_length=128)
    extra: dict[str, str] = Field(default_factory=dict)


class ShadowEmitter(Protocol):
    async def emit(self, event: FailureEvent) -> None: ...


class LoggingShadowEmitter:
    """Default emitter — logs via structlog. Real TG emitter wired in chunks 13/14/16."""

    async def emit(self, event: FailureEvent) -> None:
        _log.info(
            "auth.admin.shadow_emit",
            correlation_id=event.correlation_id,
            failure_kind=event.failure_kind,
            wiki_id=event.wiki_id,
            extra=event.extra,
        )


def _atomic_write_users_toml(path: Path, config: UsersConfig) -> None:
    """Render UsersConfig back to TOML and write atomically (tmp+rename)."""
    lines: list[str] = [f"schema_version = {config.schema_version}", ""]
    for u in config.users:
        lines.append("[[users]]")
        lines.append(f"telegram_id = {u.telegram_id}")
        lines.append(f"enabled = {'true' if u.enabled else 'false'}")
        lines.append(f'role = "{u.role}"')
        if u.display_name is not None:
            lines.append(f'display_name = "{u.display_name}"')
        if u.tz is not None:
            lines.append(f'tz = "{u.tz}"')
        if u.lang is not None:
            lines.append(f'lang = "{u.lang}"')
        if u.aisw_uid is not None:
            lines.append(f"aisw_uid = {u.aisw_uid}")
        lines.append("")
    body = "\n".join(lines)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    shutil.move(str(tmp), str(path))


SighupTrigger = Callable[[], Awaitable[None]]


async def _noop_sighup() -> None:
    return None


# START_CONTRACT: AdminService
#   PURPOSE: Approve/reject pending users + elevate/demote admin sessions.
#   INPUTS: { session_maker, pending_repo, sighup_trigger, tenancy_mode }
#   OUTPUTS: per-method results
#   SIDE_EFFECTS: writes users.toml, sessions.pending_confirms, audit.admin_events
#   LINKS: D-028, D-031, M-AUTH-USERS
# END_CONTRACT: AdminService
class AdminService:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        pending_repo: PendingUserRepo,
        *,
        audit_session_maker: async_sessionmaker[AsyncSession] | None = None,
        sighup_trigger: SighupTrigger = _noop_sighup,
        tenancy_mode: TenancyMode = "single",
    ) -> None:
        self._sm = session_maker
        self._audit_sm = audit_session_maker or session_maker
        self._pending = pending_repo
        self._sighup = sighup_trigger
        self._tenancy = tenancy_mode

    # ------- authority -------

    def assert_admin(self, admin_telegram_id: int) -> UserRecord:
        """Raise NotAnAdmin unless caller is an admin per TENANCY_MODE.

        single: only the FIRST admin in users.toml may approve.
        multi:  any user with role='admin' may approve.
        """
        allowlist = get_global()
        record = allowlist.get_user(admin_telegram_id)
        if record is None or record.role != "admin":
            raise NotAnAdmin(f"{admin_telegram_id} is not in the admin set")
        if self._tenancy == "single":
            admins = [u for u in allowlist.all_users() if u.role == "admin"]
            # Order is insertion-order of the MappingProxyType — stable per users.toml load.
            if not admins or admins[0].telegram_id != admin_telegram_id:
                raise NotAnAdmin(
                    f"tenancy=single: only bootstrap admin may approve, not {admin_telegram_id}"
                )
        return record

    # ------- approve / reject -------

    async def approve_pending(
        self,
        admin_telegram_id: int,
        target_telegram_id: int,
        users_toml_path: Path,
    ) -> ApprovalResult:
        self.assert_admin(admin_telegram_id)
        config = load_users_toml(users_toml_path)
        present = any(u.telegram_id == target_telegram_id for u in config.users)
        if present:
            await self._audit(
                actor=admin_telegram_id,
                target=target_telegram_id,
                action="approve",
                outcome="noop_already_present",
                reason=None,
            )
            _log.info(
                "auth.admin.approve.noop",
                actor=admin_telegram_id,
                target=target_telegram_id,
            )
            return ApprovalResult(ok=True, user_added=False, already_present=True)

        new_users = (
            *config.users,
            UserRecord(telegram_id=target_telegram_id, role="user", enabled=True),
        )
        new_config = UsersConfig(schema_version=config.schema_version, users=new_users)
        _atomic_write_users_toml(users_toml_path, new_config)
        await self._sighup()
        await self._pending.delete(target_telegram_id)
        await self._audit(
            actor=admin_telegram_id,
            target=target_telegram_id,
            action="approve",
            outcome="user_added",
            reason=None,
        )
        _log.info(
            "auth.admin.approve.ok",
            actor=admin_telegram_id,
            target=target_telegram_id,
        )
        return ApprovalResult(ok=True, user_added=True)

    async def reject_pending(
        self,
        admin_telegram_id: int,
        target_telegram_id: int,
        reason: str,
    ) -> RejectionResult:
        self.assert_admin(admin_telegram_id)
        await self._pending.delete(target_telegram_id)
        await self._audit(
            actor=admin_telegram_id,
            target=target_telegram_id,
            action="reject",
            outcome="rejected",
            reason=reason,
        )
        _log.info(
            "auth.admin.reject",
            actor=admin_telegram_id,
            target=target_telegram_id,
            reason=reason,
        )
        return RejectionResult(ok=True, telegram_id=target_telegram_id, reason=reason)

    # ------- elevation -------

    async def elevate(
        self,
        admin_telegram_id: int,
        *,
        ttl: timedelta | None = None,
        now: datetime | None = None,
    ) -> ElevationToken:
        self.assert_admin(admin_telegram_id)
        ts = now or _utcnow_naive()
        window = ttl or timedelta(minutes=DEFAULT_ELEVATION_TTL_MIN)
        expires = ts + window
        async with self._sm() as s, s.begin():
            row = PendingConfirm(
                telegram_id=admin_telegram_id,
                payload_hash=f"elev:{admin_telegram_id}:{int(ts.timestamp())}",
                expires_at_utc=expires,
                created_at_utc=ts,
                status="pending",
                category=ADMIN_ELEVATION_CATEGORY,
                chat_id=None,
                draft_json=json.dumps({"kind": "elevation"}, sort_keys=True),
            )
            s.add(row)
            await s.flush()
            pending_id = row.id
        await self._audit(
            actor=admin_telegram_id,
            target=admin_telegram_id,
            action="elevate",
            outcome="granted",
            reason=None,
            expires_at_utc=expires,
        )
        _log.info(
            "auth.admin.elevate",
            actor=admin_telegram_id,
            expires_at_utc=expires.isoformat(),
            pending_id=pending_id,
        )
        return ElevationToken(
            admin_telegram_id=admin_telegram_id,
            expires_at_utc=expires,
            pending_id=pending_id,
        )

    async def demote(self, admin_telegram_id: int) -> int:
        async with self._sm() as s, s.begin():
            result = await s.execute(
                delete(PendingConfirm).where(
                    PendingConfirm.telegram_id == admin_telegram_id,
                    PendingConfirm.category == ADMIN_ELEVATION_CATEGORY,
                )
            )
            n = result.rowcount or 0
        await self._audit(
            actor=admin_telegram_id,
            target=admin_telegram_id,
            action="demote",
            outcome="revoked" if n else "noop",
            reason=None,
        )
        _log.info("auth.admin.demote", actor=admin_telegram_id, removed=n)
        return n

    async def is_elevated(
        self,
        admin_telegram_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        ts = now or _utcnow_naive()
        async with self._sm() as s:
            row = (
                (
                    await s.execute(
                        select(PendingConfirm).where(
                            PendingConfirm.telegram_id == admin_telegram_id,
                            PendingConfirm.category == ADMIN_ELEVATION_CATEGORY,
                            PendingConfirm.expires_at_utc > ts,
                        )
                    )
                )
                .scalars()
                .first()
            )
        return row is not None

    # ------- audit -------

    async def _audit(
        self,
        *,
        actor: int,
        target: int,
        action: str,
        outcome: str,
        reason: str | None,
        expires_at_utc: datetime | None = None,
    ) -> None:
        async with self._audit_sm() as s, s.begin():
            s.add(
                AdminEvent(
                    actor_telegram_id=actor,
                    action=action,
                    target=str(target),
                    target_telegram_id=target,
                    outcome=outcome,
                    reason=reason,
                    expires_at_utc=expires_at_utc,
                    created_at_utc=_utcnow_naive(),
                )
            )
