"""dry_run=True: count but don't delete; audit row marked dry_run=true."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, text

from ai_steward_wiki.ops.retention import DB_AUDIT, DB_JOBS, RetentionPolicy, run_purge
from ai_steward_wiki.storage.audit.models import AuditEvent


async def test_dry_run_keeps_rows(audit_maker, jobs_maker) -> None:
    p = RetentionPolicy(
        name="chat_log_purge",
        db=DB_AUDIT,
        table="chat_log",
        ts_column="created_at_utc",
        retention=timedelta(days=30),
        cron_hour=4,
        cron_minute=0,
    )
    now = datetime(2026, 5, 11, 12, 0, 0)
    old_ts = (now - timedelta(days=31)).isoformat()
    async with audit_maker() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO chat_log (telegram_id, chat_id, direction, kind, created_at_utc) "
                "VALUES (1, 1, 'in', 'text', :ts)"
            ),
            {"ts": old_ts},
        )
    result = await run_purge(
        p,
        db_makers={DB_AUDIT: audit_maker, DB_JOBS: jobs_maker},
        audit_maker=audit_maker,
        dry_run=True,
        now=now,
    )
    assert result.deleted == 1
    assert result.dry_run
    async with audit_maker() as s:
        count = (await s.execute(text("SELECT COUNT(*) FROM chat_log"))).scalar()
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    assert count == 1  # nothing removed
    trail = next(r for r in rows if r.kind == "retention.purge")
    assert '"dry_run":true' in (trail.payload_json or "")
