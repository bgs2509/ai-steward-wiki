"""purge_trash_sweep: tier-1/2 redactor pass on _trash content + rmtree."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.ops.retention import purge_trash_sweep
from ai_steward_wiki.storage.audit.models import AuditEvent


async def test_trash_sweep_redacts_before_rmtree(tmp_path: Path, audit_maker) -> None:
    trash = tmp_path / "_trash"
    trash.mkdir()
    entry = trash / "health-20260101"
    entry.mkdir()
    note = entry / "run.md"
    note.write_text("PAN 4111 1111 1111 1111 done", encoding="utf-8")

    # Backdate so it's older than ttl=1 day.
    old = time.time() - 86400 * 2
    os.utime(note, (old, old))
    os.utime(entry, (old, old))

    redactor = PIIRedactor(hash_secret=b"k")
    removed = await purge_trash_sweep(
        trash,
        ttl=timedelta(days=1),
        redactor=redactor,
        audit_maker=audit_maker,
        now=datetime.now(),
    )
    assert removed == 1
    assert not entry.exists()

    async with audit_maker() as s:
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    sweep_rows = [r for r in rows if r.kind == "trash_final_sweep"]
    assert len(sweep_rows) == 1
    assert '"redacted_files":1' in (sweep_rows[0].payload_json or "")
