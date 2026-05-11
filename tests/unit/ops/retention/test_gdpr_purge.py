"""GDPR purge_user: admin-gated; allow-listed stores; records admin_events row."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select, text

from ai_steward_wiki.auth.admin import AdminService, NotAnAdmin
from ai_steward_wiki.auth.allowlist import replace_global
from ai_steward_wiki.auth.onboarding import PendingUserRepo
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig
from ai_steward_wiki.ops.gdpr import purge_user
from ai_steward_wiki.storage.audit.models import AdminEvent
from ai_steward_wiki.storage.sessions.models import PendingUser

ADMIN_TG = 100
USER_TG = 200
TARGET_TG = 555


@pytest.fixture(autouse=True)
def _reset_global():
    replace_global(
        UsersConfig(
            schema_version=1,
            users=(
                UserRecord(telegram_id=ADMIN_TG, role="admin"),
                UserRecord(telegram_id=USER_TG, role="user"),
            ),
        )
    )
    yield
    replace_global(UsersConfig(schema_version=1, users=()))


@pytest.fixture
def admin_svc(sessions_maker, audit_maker):
    repo = PendingUserRepo(sessions_maker)
    return AdminService(
        sessions_maker,
        repo,
        audit_session_maker=audit_maker,
        tenancy_mode="single",
    )


async def test_purge_user_removes_chat_log_and_pending(
    sessions_maker, audit_maker, admin_svc
) -> None:
    # Seed chat_log with one row for TARGET and one for someone else.
    async with audit_maker() as s, s.begin():
        for tg, kind in [(TARGET_TG, "text"), (999, "text")]:
            await s.execute(
                text(
                    "INSERT INTO chat_log (telegram_id, chat_id, direction, kind, created_at_utc) "
                    "VALUES (:tg, 1, 'in', :k, :ts)"
                ),
                {"tg": tg, "k": kind, "ts": datetime(2026, 5, 1).isoformat()},
            )
    # Seed pending_users with TARGET.
    async with sessions_maker() as s, s.begin():
        s.add(
            PendingUser(
                telegram_id=TARGET_TG,
                requested_at_utc=datetime(2026, 5, 1),
                candidate_payload_json=None,
            )
        )

    counts = await purge_user(
        TARGET_TG,
        actor_telegram_id=ADMIN_TG,
        admin_svc=admin_svc,
        audit_maker=audit_maker,
        sessions_maker=sessions_maker,
    )
    assert counts["audit.chat_log"] == 1
    assert counts["sessions.pending_users"] == 1

    # Verify untouched row remains in chat_log.
    async with audit_maker() as s:
        remaining = (
            await s.execute(text("SELECT COUNT(*) FROM chat_log WHERE telegram_id = 999"))
        ).scalar()
    assert remaining == 1

    # Verify admin_events row exists.
    async with audit_maker() as s:
        events = (
            (await s.execute(select(AdminEvent).where(AdminEvent.action == "gdpr_purge")))
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].target_telegram_id == TARGET_TG


async def test_purge_user_rejects_non_admin(sessions_maker, audit_maker, admin_svc) -> None:
    with pytest.raises(NotAnAdmin):
        await purge_user(
            TARGET_TG,
            actor_telegram_id=USER_TG,
            admin_svc=admin_svc,
            audit_maker=audit_maker,
            sessions_maker=sessions_maker,
        )


async def test_purge_user_does_not_touch_prompt_versions_or_run_outputs(
    sessions_maker, audit_maker, admin_svc
) -> None:
    async with audit_maker() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO prompt_versions (name, semver, sha256, first_seen_at_utc) "
                "VALUES ('p', '1.0.0', 'abc', :ts)"
            ),
            {"ts": datetime(2020, 1, 1).isoformat()},
        )
        await s.execute(
            text(
                "INSERT INTO run_outputs "
                "(run_id, wiki_id, owner_telegram_id, started_at_utc, output_path, "
                "output_bytes, kind) "
                "VALUES ('r1', 'w', :tg, :ts, '/x', 0, 'reply')"
            ),
            {"tg": TARGET_TG, "ts": datetime(2020, 1, 1).isoformat()},
        )
    await purge_user(
        TARGET_TG,
        actor_telegram_id=ADMIN_TG,
        admin_svc=admin_svc,
        audit_maker=audit_maker,
        sessions_maker=sessions_maker,
    )
    async with audit_maker() as s:
        pv = (await s.execute(text("SELECT COUNT(*) FROM prompt_versions"))).scalar()
        ro = (await s.execute(text("SELECT COUNT(*) FROM run_outputs"))).scalar()
    # D-025/D-015 invariant: indefinite retention regardless of GDPR purge.
    assert pv == 1
    assert ro == 1
