"""Restore smoke: take a snapshot, copy to a fresh path, verify roundtrip.

This is the pytest target referenced from docs/runbook/restore.md §1.2 step 6.
It also runs as part of the default unit pipeline so regressions are caught
without manual intervention; the runbook documents the manual rehearsal flow.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ai_steward_wiki.ops.snapshot import snapshot_databases


def _seed(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO kv (k, v) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _read(path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(path))
    try:
        return dict(conn.execute("SELECT k, v FROM kv").fetchall())
    finally:
        conn.close()


def test_snapshot_restore_roundtrip(tmp_path: Path) -> None:
    # 1. Seed three live DBs.
    live = tmp_path / "data"
    seeds = {
        "jobs": [("a", "1"), ("b", "2")],
        "audit": [("x", "10")],
        "sessions": [("user", "alice")],
    }
    db_urls = {}
    for name, rows in seeds.items():
        p = live / f"{name}.db"
        _seed(p, rows)
        db_urls[name] = f"sqlite+aiosqlite:///{p}"

    # 2. Take a snapshot.
    snap_root = tmp_path / "state" / "snapshots"
    result = snapshot_databases(
        snap_root,
        db_urls,
        now_utc=datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
    )
    assert set(result.stores) == set(seeds)

    # 3. Restore into a clean directory per runbook §1.2 steps 3-4.
    restore_dir = tmp_path / "state-restore-test" / "data"
    restore_dir.mkdir(parents=True)
    for name, snap_path in result.stores.items():
        shutil.copy(snap_path, restore_dir / f"{name}.db")

    # 4. Smoke: every seeded row is intact and readable.
    for name, expected in seeds.items():
        assert _read(restore_dir / f"{name}.db") == dict(expected)
