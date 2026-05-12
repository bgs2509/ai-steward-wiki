"""Unit tests for M-RUNTIME-WIRING (src/ai_steward_wiki/__main__.py)."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_steward_wiki import __main__ as runtime
from ai_steward_wiki.scheduler.maintenance import (
    MEDIA_STAGING_SWEEP_JOB_ID,
    PURGE_PENDING_JOB_ID,
)


def test_sync_url_strips_aiosqlite() -> None:
    assert runtime._sync_url_for_jobstore("sqlite+aiosqlite:///data/jobs.db") == (
        "sqlite:///data/jobs.db"
    )


def test_sync_url_passthrough_for_plain_sqlite() -> None:
    assert runtime._sync_url_for_jobstore("sqlite:///x.db") == "sqlite:///x.db"


def test_sync_url_rejects_non_sqlite() -> None:
    with pytest.raises(ValueError, match="only sqlite"):
        runtime._sync_url_for_jobstore("postgresql+asyncpg://x/y")


def test_ensure_data_dirs_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "jobs.db"
    runtime._ensure_data_dirs(
        [
            f"sqlite+aiosqlite:///{target}",
        ]
    )
    assert target.parent.is_dir()


def test_ensure_data_dirs_ignores_non_file_urls(tmp_path: Path) -> None:
    runtime._ensure_data_dirs(["sqlite+aiosqlite:///:memory:"])


def test_load_allowlist_none_path_returns_empty(tmp_path: Path) -> None:
    cfg = runtime._load_users_config(None)
    assert cfg.users == ()


def test_load_allowlist_missing_file_returns_empty(tmp_path: Path) -> None:
    cfg = runtime._load_users_config(tmp_path / "missing.toml")
    assert cfg.users == ()


def test_load_allowlist_reads_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "users.toml"
    path.write_text(
        "schema_version = 1\n" "[[users]]\n" "telegram_id = 12345\n" 'role = "admin"\n',
        encoding="utf-8",
    )
    cfg = runtime._load_users_config(path)
    assert len(cfg.users) == 1
    assert cfg.users[0].telegram_id == 12345


def test_install_signal_handlers_sets_event_on_sigterm() -> None:
    async def scenario() -> bool:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        runtime._install_signal_handlers(loop, stop)
        # Emulate SIGTERM by invoking the handler the same way asyncio would.
        loop.call_soon(stop.set)
        await asyncio.wait_for(stop.wait(), timeout=1.0)
        return stop.is_set()

    assert asyncio.run(scenario()) is True


def test_amain_composes_and_shuts_down_cleanly(tmp_path: Path) -> None:
    """_amain composes modules, blocks on stop_event, shuts down on signal."""

    fake_bot = MagicMock()
    fake_bot.session = MagicMock()
    fake_bot.session.close = AsyncMock()

    fake_dp = MagicMock()
    fake_dp.start_polling = AsyncMock()
    fake_dp.stop_polling = AsyncMock()

    fake_scheduler = MagicMock()
    fake_scheduler.start = MagicMock()
    fake_scheduler.shutdown = MagicMock()

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_sessionmaker = MagicMock()

    settings = MagicMock()
    settings.log_level = "INFO"
    settings.env = "local"
    settings.tg_bot_token = MagicMock()
    settings.tg_bot_token.get_secret_value = MagicMock(return_value="TEST")
    settings.jobs_db_url = f"sqlite+aiosqlite:///{tmp_path}/jobs.db"
    settings.audit_db_url = f"sqlite+aiosqlite:///{tmp_path}/audit.db"
    settings.sessions_db_url = f"sqlite+aiosqlite:///{tmp_path}/sessions.db"
    settings.users_toml_path = None

    async def trigger_stop() -> None:
        await asyncio.sleep(0.05)
        runtime._STOP_EVENT_FOR_TESTS.set()

    with (
        patch.object(runtime, "get_settings", return_value=settings),
        patch.object(runtime, "configure_logging"),
        patch.object(runtime, "_run_all_migrations", new=AsyncMock()),
        patch.object(runtime, "build_engine", return_value=fake_engine),
        patch.object(runtime, "build_sessionmaker", return_value=fake_sessionmaker),
        patch.object(runtime, "sync_to_sessions_db", new=AsyncMock()),
        patch.object(runtime, "build_scheduler", return_value=fake_scheduler),
        patch.object(runtime, "build_bot", return_value=fake_bot),
        patch.object(runtime, "build_dispatcher", return_value=fake_dp),
    ):

        async def driver() -> None:
            runtime._STOP_EVENT_FOR_TESTS = asyncio.Event()
            await asyncio.gather(runtime._amain(), trigger_stop())

        asyncio.run(driver())

    fake_scheduler.start.assert_called_once()
    fake_scheduler.shutdown.assert_called_once()
    fake_dp.start_polling.assert_awaited_once()
    fake_bot.session.close.assert_awaited()
    assert fake_engine.dispose.await_count >= 3
    # aisw-7k0: all retention/maintenance jobs are registered (pending purge,
    # DB retention, db_snapshot, media _staging sweep).
    registered_job_ids = {c.kwargs.get("id") for c in fake_scheduler.add_job.call_args_list}
    assert PURGE_PENDING_JOB_ID in registered_job_ids
    assert MEDIA_STAGING_SWEEP_JOB_ID in registered_job_ids
    assert fake_scheduler.add_job.call_count >= 5


def test_amain_requires_active_tg_token(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.log_level = "INFO"
    settings.env = "local"
    settings.tg_bot_token = None
    settings.jobs_db_url = f"sqlite+aiosqlite:///{tmp_path}/jobs.db"
    settings.audit_db_url = f"sqlite+aiosqlite:///{tmp_path}/audit.db"
    settings.sessions_db_url = f"sqlite+aiosqlite:///{tmp_path}/sessions.db"
    settings.users_toml_path = None

    with (
        patch.object(runtime, "get_settings", return_value=settings),
        patch.object(runtime, "configure_logging"),
        patch.object(runtime, "_run_all_migrations", new=AsyncMock()),
        pytest.raises(RuntimeError, match="tg_bot_token"),
    ):
        asyncio.run(runtime._amain())


def test_main_invokes_asyncio_run() -> None:
    with patch.object(runtime.asyncio, "run") as run_mock:
        runtime.main()
    run_mock.assert_called_once()


def test_signal_constants_available() -> None:
    assert hasattr(signal, "SIGINT")
    assert hasattr(signal, "SIGTERM")
