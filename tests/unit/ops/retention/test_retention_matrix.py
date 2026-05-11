"""Parametrised over RETENTION_POLICIES — every §10.4 row purges as designed."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from ai_steward_wiki.ops.retention import (
    DB_AUDIT,
    DB_JOBS,
    RETENTION_POLICIES,
    RetentionPolicy,
    run_purge,
)


# We seed each (table, ts_column) with two rows: one older than cutoff, one
# newer. After purge: deleted=1, surviving row's timestamp ≥ cutoff.
async def _seed(maker, table: str, ts_column: str, old_ts: datetime, new_ts: datetime) -> None:
    # Minimal-cols inserts: rely on default NULLs/zeros for non-NOT-NULL fields
    # by inserting only required columns per table. Use raw SQL to avoid coupling.
    REQUIRED_INSERTS: dict[str, list[tuple[str, str | int]]] = {
        "chat_log": [("telegram_id", 1), ("chat_id", 1), ("direction", "in"), ("kind", "text")],
        "tg_updates": [("update_id", 0)],  # PK, will override per-row
        "seen_files": [
            ("content_sha256", ""),
            ("kind", "text"),
            ("owner_telegram_id", 1),
        ],
        "dedup_hits": [
            ("content_sha256", "x"),
            ("owner_telegram_id", 1),
            ("action", "use_old"),
        ],
        "audit_events": [("kind", "test")],
        "admin_events": [("actor_telegram_id", 1), ("action", "test")],
        "job_outputs": [
            ("job_id", 1),
            ("run_id", "r"),
            ("status", "ok"),
            ("notify_policy", "always"),
        ],
        "run_outputs": [
            ("run_id", "r1"),
            ("wiki_id", "w"),
            ("owner_telegram_id", 1),
            ("output_path", "/x"),
            ("output_bytes", 0),
            ("kind", "reply"),
        ],
        "onboarding_events": [("telegram_id", 1), ("slug", "x")],
        "tracker_answers": [
            ("owner_telegram_id", 1),
            ("question_key", "k"),
            ("answer", "a"),
        ],
    }

    base = REQUIRED_INSERTS[table]

    async with maker() as s, s.begin():
        for i, ts in enumerate((old_ts, new_ts)):
            cols = list(base)
            if table == "tg_updates":
                cols = [("update_id", i + 1)]
            cols.append((ts_column, ts.isoformat()))
            if table == "seen_files":
                cols[0] = ("content_sha256", f"hash{i}")
            if table == "run_outputs":
                cols[0] = ("run_id", f"r{i}")
            col_names = ", ".join(c for c, _ in cols)
            placeholders = ", ".join(f":{c}" for c, _ in cols)
            params = dict(cols)
            await s.execute(
                text(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"),
                params,
            )


@pytest.mark.parametrize("policy", RETENTION_POLICIES, ids=lambda p: p.name)
async def test_purge_deletes_only_expired_rows(
    policy: RetentionPolicy, audit_maker, jobs_maker
) -> None:
    now = datetime(2026, 5, 11, 12, 0, 0)
    old_ts = now - policy.retention - timedelta(seconds=10)
    new_ts = now - policy.retention + timedelta(seconds=10)

    target_maker = audit_maker if policy.db == DB_AUDIT else jobs_maker
    await _seed(target_maker, policy.table, policy.ts_column, old_ts, new_ts)

    result = await run_purge(
        policy,
        db_makers={DB_AUDIT: audit_maker, DB_JOBS: jobs_maker},
        audit_maker=audit_maker,
        dry_run=False,
        now=now,
    )
    assert result.deleted == 1
    # Verify surviving row newer than cutoff. audit_events purges itself but
    # run_purge writes a fresh audit row at `now` → 1 surviving seed + 1 trail.
    async with target_maker() as s:
        kept = (await s.execute(text(f"SELECT COUNT(*) FROM {policy.table}"))).scalar()
    expected_kept = 2 if policy.table == "audit_events" else 1
    assert kept == expected_kept
