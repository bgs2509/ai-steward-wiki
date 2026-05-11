"""Each purge run produces an audit_events row."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from ai_steward_wiki.ops.retention import DB_AUDIT, DB_JOBS, run_purge
from ai_steward_wiki.storage.audit.models import AuditEvent


async def _make_policy():
    from ai_steward_wiki.ops.retention import RetentionPolicy

    return RetentionPolicy(
        name="chat_log_purge",
        db=DB_AUDIT,
        table="chat_log",
        ts_column="created_at_utc",
        retention=timedelta(days=30),
        cron_hour=4,
        cron_minute=0,
    )


async def test_purge_writes_audit_row(audit_maker, jobs_maker) -> None:
    p = await _make_policy()
    now = datetime(2026, 5, 11, 12, 0, 0)
    result = await run_purge(
        p,
        db_makers={DB_AUDIT: audit_maker, DB_JOBS: jobs_maker},
        audit_maker=audit_maker,
        dry_run=False,
        now=now,
    )
    assert result.deleted == 0
    async with audit_maker() as s:
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    trail = [r for r in rows if r.kind == "retention.purge"]
    assert len(trail) == 1
    assert trail[0].target == "audit.chat_log"
    assert '"deleted":0' in (trail[0].payload_json or "")
