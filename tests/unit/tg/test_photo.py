"""Unit tests for ai_steward_wiki.tg.photo — D-022 / §9 tech-spec."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.tg.photo import PHOTO_MIME_TO_EXT, PhotoIngestor


def test_jpeg_is_staged_as_jpg(tmp_path: Path) -> None:
    ingestor = PhotoIngestor(inbox_root=tmp_path / "Inbox-WIKI")
    ref = ingestor.handle(b"\xff\xd8\xff\xe0fake-jpeg", run_id="r1", mime="image/jpeg")
    assert ref.ext == "jpg"
    assert ref.staging_path.suffix == ".jpg"
    assert ref.staging_path.exists()


def test_png_and_webp_supported(tmp_path: Path) -> None:
    ingestor = PhotoIngestor(inbox_root=tmp_path / "Inbox-WIKI")
    png = ingestor.handle(b"\x89PNG\r\n", run_id="r2", mime="image/png")
    webp = ingestor.handle(b"RIFF????WEBP", run_id="r3", mime="image/webp")
    assert png.ext == "png"
    assert webp.ext == "webp"


def test_unsupported_mime_raises(tmp_path: Path) -> None:
    ingestor = PhotoIngestor(inbox_root=tmp_path / "Inbox-WIKI")
    with pytest.raises(ValueError, match="unsupported photo mime"):
        ingestor.handle(b"x", run_id="r", mime="image/gif")


def test_per_call_inbox_root_overrides(tmp_path: Path) -> None:
    ingestor = PhotoIngestor()  # no constructor root
    inbox = tmp_path / "222" / "Inbox-WIKI"
    ref = ingestor.handle(b"\xff\xd8\xff", run_id="r1", mime="image/jpeg", inbox_root=inbox)
    assert ref.staging_path.parent == inbox / "raw" / "media" / "_staging"


def test_no_root_anywhere_raises() -> None:
    with pytest.raises(ValueError, match="inbox_root required"):
        PhotoIngestor().handle(b"\xff\xd8\xff", run_id="r1", mime="image/jpeg")


def test_mime_to_ext_mapping_is_closed() -> None:
    # Defensive: ensure no accidental new entries (PII tier-2: closed allowlist).
    assert set(PHOTO_MIME_TO_EXT) == {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
