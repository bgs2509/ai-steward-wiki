# FILE: src/ai_steward_wiki/ops/gdpr.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: GDPR purge_user admin endpoint with explicit allow-list of stores.
#   SCOPE: purge_user(target_telegram_id, *, actor_telegram_id, scope, ...).
#   DEPENDS: sqlalchemy.async, ai_steward_wiki.auth.admin.AdminService,
#            ai_steward_wiki.storage.{audit,sessions} models.
#   LINKS: D-034 §10.4, M-OPS-PII, M-ONBOARD-ADMIN
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PurgeUserResult - dict alias mapping store name → deleted row count
#   purge_user - GDPR endpoint; admin-gated; allow-listed stores per D-034 §10.4
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 13: GDPR purge_user (admin-gated)
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.audit.models import AdminEvent, ChatLog
from ai_steward_wiki.storage.sessions.models import PendingUser

if TYPE_CHECKING:
    from ai_steward_wiki.auth.admin import AdminService

__all__ = [
    "PurgeUserResult",
    "purge_user",
]

PurgeUserResult = dict[str, int]


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# START_CONTRACT: purge_user
#   PURPOSE: Delete target user's content from allow-listed stores; record admin_events row.
#   INPUTS: { target_telegram_id, actor_telegram_id, scope='all', admin_svc, makers }
#   OUTPUTS: { PurgeUserResult — store_name → deleted_count }
#   SIDE_EFFECTS: DELETE rows in audit.chat_log, sessions.pending_users; INSERT admin_events.
#                 Does NOT touch prompt_versions, run_outputs, users.toml (D-025 invariant).
#   LINKS: D-034 §10.4
# END_CONTRACT: purge_user
async def purge_user(
    target_telegram_id: int,
    *,
    actor_telegram_id: int,
    scope: str = "all",
    admin_svc: AdminService,
    audit_maker: async_sessionmaker[AsyncSession],
    sessions_maker: async_sessionmaker[AsyncSession],
) -> PurgeUserResult:
    """GDPR data subject erasure for one telegram_id. Raises NotAnAdmin if not authorised."""
    admin_svc.assert_admin(actor_telegram_id)
    counts: PurgeUserResult = {}

    # 1. audit.chat_log
    async with audit_maker() as s, s.begin():
        res = await s.execute(delete(ChatLog).where(ChatLog.telegram_id == target_telegram_id))
        counts["audit.chat_log"] = res.rowcount or 0

    # 2. sessions.pending_users
    async with sessions_maker() as s, s.begin():
        res = await s.execute(
            delete(PendingUser).where(PendingUser.telegram_id == target_telegram_id)
        )
        counts["sessions.pending_users"] = res.rowcount or 0

    # 3. admin_events: append-only — record action, never erase actor trail.
    async with audit_maker() as s, s.begin():
        s.add(
            AdminEvent(
                actor_telegram_id=actor_telegram_id,
                action="gdpr_purge",
                target=str(target_telegram_id),
                target_telegram_id=target_telegram_id,
                outcome="ok",
                reason=f"scope={scope}",
                created_at_utc=_utcnow_naive(),
            )
        )
    return counts
