"""AdminService: approve / reject / elevate / demote / assert_admin / shadow_emit."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.auth.admin import (
    AdminService,
    FailureEvent,
    LoggingShadowEmitter,
    NotAnAdmin,
)
from ai_steward_wiki.auth.allowlist import replace_global
from ai_steward_wiki.auth.onboarding import PendingUserRepo, start_unknown_user
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig, load_users_toml
from ai_steward_wiki.storage.audit.models import AdminEvent

REPO_ROOT = Path(__file__).resolve().parents[3]

ADMIN_TG = 100
OTHER_ADMIN_TG = 101
USER_TG = 200
PENDING_TG = 999


@pytest.fixture
async def sessions_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def audit_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_global():
    # Bootstrap admin = ADMIN_TG; OTHER_ADMIN_TG is also admin but secondary.
    cfg = UsersConfig(
        schema_version=1,
        users=(
            UserRecord(telegram_id=ADMIN_TG, role="admin"),
            UserRecord(telegram_id=OTHER_ADMIN_TG, role="admin"),
            UserRecord(telegram_id=USER_TG, role="user"),
        ),
    )
    replace_global(cfg)
    yield
    replace_global(UsersConfig(schema_version=1, users=()))


@pytest.fixture
def users_toml(tmp_path) -> Path:
    p = tmp_path / "users.toml"
    p.write_text(
        f"""schema_version = 1
[[users]]
telegram_id = {ADMIN_TG}
role = "admin"
[[users]]
telegram_id = {OTHER_ADMIN_TG}
role = "admin"
[[users]]
telegram_id = {USER_TG}
role = "user"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def service_single(sessions_maker, audit_maker):
    repo = PendingUserRepo(sessions_maker)
    return AdminService(
        sessions_maker,
        repo,
        audit_session_maker=audit_maker,
        tenancy_mode="single",
    )


@pytest.fixture
def service_multi(sessions_maker, audit_maker):
    repo = PendingUserRepo(sessions_maker)
    return AdminService(
        sessions_maker,
        repo,
        audit_session_maker=audit_maker,
        tenancy_mode="multi",
    )


# --- assert_admin ---


def test_assert_admin_accepts_bootstrap_in_single(service_single) -> None:
    rec = service_single.assert_admin(ADMIN_TG)
    assert rec.role == "admin"


def test_assert_admin_rejects_secondary_in_single(service_single) -> None:
    with pytest.raises(NotAnAdmin):
        service_single.assert_admin(OTHER_ADMIN_TG)


def test_assert_admin_rejects_non_admin(service_single) -> None:
    with pytest.raises(NotAnAdmin):
        service_single.assert_admin(USER_TG)


def test_assert_admin_multi_accepts_any_admin(service_multi) -> None:
    service_multi.assert_admin(ADMIN_TG)
    service_multi.assert_admin(OTHER_ADMIN_TG)


def test_assert_admin_multi_rejects_user(service_multi) -> None:
    with pytest.raises(NotAnAdmin):
        service_multi.assert_admin(USER_TG)


# --- approve_pending ---


async def test_approve_pending_adds_user_and_triggers_sighup(
    service_single, users_toml, sessions_maker
) -> None:
    repo = PendingUserRepo(sessions_maker)
    await start_unknown_user(repo, telegram_id=PENDING_TG)
    called: list[bool] = []

    async def _sighup() -> None:
        called.append(True)

    service_single._sighup = _sighup  # test wiring of private hook
    result = await service_single.approve_pending(ADMIN_TG, PENDING_TG, users_toml)
    assert result.ok
    assert result.user_added
    cfg = load_users_toml(users_toml)
    assert any(u.telegram_id == PENDING_TG for u in cfg.users)
    assert called == [True]
    assert await repo.get(PENDING_TG) is None  # pending row deleted


async def test_approve_pending_idempotent_when_already_present(service_single, users_toml) -> None:
    result = await service_single.approve_pending(ADMIN_TG, USER_TG, users_toml)
    assert result.ok
    assert result.already_present
    assert not result.user_added


async def test_approve_pending_rejects_non_admin(service_single, users_toml) -> None:
    with pytest.raises(NotAnAdmin):
        await service_single.approve_pending(USER_TG, PENDING_TG, users_toml)


# --- reject_pending ---


async def test_reject_pending_deletes_and_audits(
    service_single, sessions_maker, audit_maker
) -> None:
    repo = PendingUserRepo(sessions_maker)
    await start_unknown_user(repo, telegram_id=PENDING_TG)
    result = await service_single.reject_pending(ADMIN_TG, PENDING_TG, reason="spam")
    assert result.ok
    assert result.reason == "spam"
    assert await repo.get(PENDING_TG) is None
    async with audit_maker() as s:
        rows = (await s.execute(select(AdminEvent))).scalars().all()
    assert any(r.action == "reject" and r.target_telegram_id == PENDING_TG for r in rows)


# --- elevate / demote / is_elevated ---


async def test_elevate_creates_pending_confirm(service_single) -> None:
    now = datetime(2026, 5, 11, 12, 0, 0)
    tok = await service_single.elevate(ADMIN_TG, ttl=timedelta(minutes=30), now=now)
    assert tok.admin_telegram_id == ADMIN_TG
    assert tok.expires_at_utc == now + timedelta(minutes=30)
    assert await service_single.is_elevated(ADMIN_TG, now=now + timedelta(minutes=10))
    assert not await service_single.is_elevated(ADMIN_TG, now=now + timedelta(minutes=31))


async def test_demote_removes_elevation(service_single) -> None:
    now = datetime(2026, 5, 11, 12, 0, 0)
    await service_single.elevate(ADMIN_TG, ttl=timedelta(minutes=30), now=now)
    n = await service_single.demote(ADMIN_TG)
    assert n == 1
    assert not await service_single.is_elevated(ADMIN_TG, now=now + timedelta(minutes=10))


async def test_elevate_rejects_non_admin(service_single) -> None:
    with pytest.raises(NotAnAdmin):
        await service_single.elevate(USER_TG)


# --- shadow emitter ---


def test_failure_event_forbids_content_field() -> None:
    with pytest.raises(ValueError, match="content"):
        FailureEvent(
            correlation_id="cid",
            failure_kind="cli_crash",
            content="leak",  # type: ignore[call-arg]
        )


def test_failure_event_accepts_metadata_only() -> None:
    ev = FailureEvent(
        correlation_id="cid-1",
        failure_kind="cli_timeout",
        wiki_id="medical",
        extra={"exit_code": "137"},
    )
    assert ev.wiki_id == "medical"
    assert ev.extra["exit_code"] == "137"


async def test_logging_shadow_emitter_runs() -> None:
    em = LoggingShadowEmitter()
    await em.emit(FailureEvent(correlation_id="c", failure_kind="x"))
