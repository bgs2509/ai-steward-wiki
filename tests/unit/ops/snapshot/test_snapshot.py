"""Unit tests for ops.snapshot: VACUUM INTO + rolling retention."""

from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_steward_wiki.ops.snapshot import (
    SNAPSHOT_DIR_MODE,
    SNAPSHOT_RETENTION_DAYS,
    extract_sqlite_path,
    purge_old_snapshots,
    snapshot_databases,
)


def _seed_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (k, v) VALUES ('hello', 'world')")
        conn.commit()
    finally:
        conn.close()


def test_extract_sqlite_path_handles_known_schemes() -> None:
    assert extract_sqlite_path("sqlite+aiosqlite:///data/jobs.db") == Path("data/jobs.db")
    assert extract_sqlite_path("sqlite:///abs/audit.db") == Path("abs/audit.db")
    assert extract_sqlite_path("/raw/path.db") == Path("/raw/path.db")


def test_snapshot_databases_creates_valid_sqlite_copy(tmp_path: Path) -> None:
    src = tmp_path / "data" / "jobs.db"
    _seed_db(src)
    root = tmp_path / "snapshots"

    result = snapshot_databases(
        root,
        {"jobs": f"sqlite+aiosqlite:///{src}"},
        retention_days=SNAPSHOT_RETENTION_DAYS,
        now_utc=datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
    )

    snap = result.stores["jobs"]
    assert snap.exists()
    assert snap.stat().st_size > 0
    # Round-trip: snapshot is a real SQLite DB with the seeded row.
    conn = sqlite3.connect(str(snap))
    try:
        row = conn.execute("SELECT v FROM t WHERE k = ?", ("hello",)).fetchone()
    finally:
        conn.close()
    assert row == ("world",)
    # Mode 0700 on root and per-day dir, 0600 on each snapshot file.
    assert stat.S_IMODE(root.stat().st_mode) == SNAPSHOT_DIR_MODE
    assert stat.S_IMODE(result.snapshot_dir.stat().st_mode) == SNAPSHOT_DIR_MODE
    assert stat.S_IMODE(snap.stat().st_mode) == 0o600


def test_snapshot_databases_skips_missing_src(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    result = snapshot_databases(
        root,
        {"jobs": "sqlite+aiosqlite:///nonexistent/jobs.db"},
        now_utc=datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
    )
    assert result.stores == {}


def test_snapshot_databases_overwrites_same_day_rerun(tmp_path: Path) -> None:
    src = tmp_path / "audit.db"
    _seed_db(src)
    root = tmp_path / "snapshots"
    same_day = datetime(2026, 5, 11, 3, 0, tzinfo=UTC)

    snapshot_databases(root, {"audit": f"sqlite:///{src}"}, now_utc=same_day)
    # second run on same UTC day must succeed without raising
    result2 = snapshot_databases(root, {"audit": f"sqlite:///{src}"}, now_utc=same_day)
    assert result2.stores["audit"].exists()


def test_purge_old_snapshots_drops_only_stale(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    fresh = root / "2026-05-10"
    stale = root / "2026-04-01"
    keepme = root / "manual-stash"  # non-date dir — must be left alone
    for d in (fresh, stale, keepme):
        d.mkdir()
        (d / "marker").touch()

    now = datetime(2026, 5, 11, 3, 0, tzinfo=UTC)
    purged = purge_old_snapshots(root, retention_days=7, now_utc=now)

    assert stale in purged
    assert fresh not in purged
    assert keepme.exists()
    assert not stale.exists()


def test_purge_old_snapshots_no_root(tmp_path: Path) -> None:
    assert purge_old_snapshots(tmp_path / "nope") == []


def test_snapshot_uses_today_dir_name(tmp_path: Path) -> None:
    src = tmp_path / "sessions.db"
    _seed_db(src)
    fixed_now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    result = snapshot_databases(
        tmp_path / "snaps",
        {"sessions": f"sqlite:///{src}"},
        now_utc=fixed_now,
    )
    assert result.snapshot_dir.name == "2026-01-02"
    assert result.taken_at_utc == fixed_now


def test_snapshot_retention_boundary_keeps_edge(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    now = datetime(2026, 5, 11, 3, 0, tzinfo=UTC)
    # exactly retention_days old → kept (cutoff is strict <)
    edge = root / (now - timedelta(days=7)).strftime("%Y-%m-%d")
    edge.mkdir()
    purged = purge_old_snapshots(root, retention_days=7, now_utc=now)
    assert edge.exists()
    assert purged == []


@pytest.mark.parametrize("retention_days", [1, 3, 7, 30])
def test_snapshot_purges_anything_strictly_older(tmp_path: Path, retention_days: int) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    now = datetime(2026, 5, 11, tzinfo=UTC)
    too_old = root / (now - timedelta(days=retention_days + 1)).strftime("%Y-%m-%d")
    too_old.mkdir()
    purge_old_snapshots(root, retention_days=retention_days, now_utc=now)
    assert not too_old.exists()
