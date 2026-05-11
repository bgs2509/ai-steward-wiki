"""Unit tests for ai_steward_wiki.inbox.staging — D-022 / §9 tech-spec."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_steward_wiki.inbox.staging import (
    DEFAULT_STAGING_TTL_S,
    MediaRef,
    promote_to_raw,
    stage_media,
    sweep_staging,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_stage_media_writes_to_staging_with_run_id_and_sha8(tmp_path: Path) -> None:
    data = b"hello voice"
    inbox = tmp_path / "Inbox-WIKI"
    ref = stage_media(data, ext="ogg", run_id="run42", inbox_root=inbox, mime="audio/ogg")

    expected = inbox / "raw" / "media" / "_staging" / f"run42_{_sha(data)[:8]}.ogg"
    assert ref.staging_path == expected
    assert expected.read_bytes() == data
    assert ref.sha256 == _sha(data)
    assert ref.ext == "ogg"
    assert ref.size == len(data)
    assert ref.mime == "audio/ogg"


def test_stage_media_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    data = b"x" * 32
    inbox = tmp_path / "Inbox-WIKI"
    stage_media(data, ext="jpg", run_id="r1", inbox_root=inbox, mime="image/jpeg")
    staging = inbox / "raw" / "media" / "_staging"
    leftovers = [p for p in staging.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_stage_media_rejects_bad_ext(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid media extension"):
        stage_media(b"x", ext="../etc/passwd", run_id="r", inbox_root=tmp_path, mime="image/jpeg")


def test_stage_media_rejects_path_traversal_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid run_id"):
        stage_media(b"x", ext="ogg", run_id="../escape", inbox_root=tmp_path, mime="audio/ogg")


def test_promote_to_raw_moves_to_iso_named_file(tmp_path: Path) -> None:
    data = b"voice payload"
    inbox = tmp_path / "Inbox-WIKI"
    ref = stage_media(data, ext="ogg", run_id="r", inbox_root=inbox, mime="audio/ogg")
    wiki = tmp_path / "Health-WIKI"
    moment = datetime(2026, 5, 11, 12, 34, 56, tzinfo=UTC)
    final = promote_to_raw(ref, wiki_root=wiki, now=moment)

    assert final.exists()
    assert not ref.staging_path.exists()
    assert final.parent == wiki / "raw" / "media"
    assert final.name == f"20260511T123456Z_{_sha(data)[:8]}.ogg"
    assert final.read_bytes() == data


def test_promote_to_raw_idempotent_when_target_exists(tmp_path: Path) -> None:
    data = b"abc"
    inbox = tmp_path / "Inbox-WIKI"
    wiki = tmp_path / "Health-WIKI"
    moment = datetime(2026, 5, 11, 0, 0, 0, tzinfo=UTC)

    ref1 = stage_media(data, ext="ogg", run_id="r1", inbox_root=inbox, mime="audio/ogg")
    final1 = promote_to_raw(ref1, wiki_root=wiki, now=moment)
    assert final1.exists()

    # Second time: same sha, same moment → same final path; staging cleaned.
    ref2 = stage_media(data, ext="ogg", run_id="r2", inbox_root=inbox, mime="audio/ogg")
    final2 = promote_to_raw(ref2, wiki_root=wiki, now=moment)
    assert final2 == final1
    assert not ref2.staging_path.exists()


def test_promote_raises_when_staging_missing(tmp_path: Path) -> None:
    ref = MediaRef(
        staging_path=tmp_path / "does_not_exist.ogg",
        sha256="0" * 64,
        mime="audio/ogg",
        ext="ogg",
        size=0,
        run_id="r",
    )
    with pytest.raises(FileNotFoundError):
        promote_to_raw(ref, wiki_root=tmp_path / "wiki")


def test_sweep_staging_removes_only_old_files(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox-WIKI"
    fresh = stage_media(b"fresh", ext="ogg", run_id="fresh", inbox_root=inbox, mime="audio/ogg")
    old = stage_media(b"old", ext="ogg", run_id="old", inbox_root=inbox, mime="audio/ogg")

    # Backdate `old` 48h.
    past = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(old.staging_path, (past, past))

    removed = sweep_staging(inbox)
    assert removed == 1
    assert fresh.staging_path.exists()
    assert not old.staging_path.exists()


def test_sweep_staging_default_ttl_is_24h() -> None:
    assert DEFAULT_STAGING_TTL_S == 24 * 60 * 60


def test_sweep_staging_missing_dir_is_noop(tmp_path: Path) -> None:
    assert sweep_staging(tmp_path / "nope") == 0
