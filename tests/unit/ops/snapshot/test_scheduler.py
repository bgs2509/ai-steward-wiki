"""ops.snapshot.register_db_snapshot_job registers a daily 03:00 UTC cron."""

from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.ops.snapshot import (
    DB_SNAPSHOT_JOB_ID,
    SNAPSHOT_RETENTION_DAYS,
    register_db_snapshot_job,
)
from ai_steward_wiki.scheduler.core import build_scheduler


def test_register_db_snapshot_job_cron_at_0300_utc(tmp_path: Path) -> None:
    sched = build_scheduler(f"sqlite:///{tmp_path / 'jobs.db'}")
    job = register_db_snapshot_job(
        sched,
        snapshot_root=tmp_path / "snapshots",
        db_urls={"jobs": f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"},
    )
    assert job.id == DB_SNAPSHOT_JOB_ID
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "3"
    assert fields["minute"] == "0"
    assert str(job.trigger.timezone) == "UTC"
    assert job.args[2] == SNAPSHOT_RETENTION_DAYS


def test_register_db_snapshot_job_idempotent(tmp_path: Path) -> None:
    sched = build_scheduler(f"sqlite:///{tmp_path / 'jobs.db'}")
    args = {
        "snapshot_root": tmp_path / "snapshots",
        "db_urls": {"jobs": f"sqlite:///{tmp_path / 'jobs.db'}"},
    }
    register_db_snapshot_job(sched, **args)
    # Second registration with replace_existing=True must not raise.
    register_db_snapshot_job(sched, **args)
