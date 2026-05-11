"""Tests for IdempotencyService (D-018 — L1 tg_updates + L2 seen_files dedup)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.inbox.idempotency import (
    IdempotencyService,
    compute_content_hash,
    normalize_text,
)
from ai_steward_wiki.storage.audit.models import DedupHit, SeenFile, TgUpdate

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
async def session_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


# ---------- pure helpers ----------


def test_normalize_text_nfkc_strip_lower_collapse() -> None:
    # Full-width digits → ASCII via NFKC; trim; lower; whitespace collapse.
    assert normalize_text("  Hello\u00a0WORLD\n\tfoo  ") == "hello world foo"
    assert normalize_text("\uff21\uff22\uff23  \uff11\uff12\uff13") == "abc 123"
    # Whitespace-noise invariance, Cyrillic input.
    assert normalize_text("Привет   Мир") == normalize_text("\nпривет мир\n")


def test_compute_content_hash_text_is_normalization_invariant() -> None:
    a = compute_content_hash("text", "Hello   World")
    b = compute_content_hash("text", "hello world")
    c = compute_content_hash("text", "  HELLO\nWORLD\t")
    assert a == b == c
    assert len(a) == 64


def test_compute_content_hash_bytes_kinds() -> None:
    blob = b"\x89PNG\r\n\x1a\nFOO"
    for kind in ("voice", "photo", "file"):
        h = compute_content_hash(kind, blob)  # type: ignore[arg-type]
        assert len(h) == 64
    # Same blob → same hash regardless of kind label.
    assert (
        compute_content_hash("voice", blob)
        == compute_content_hash("photo", blob)
        == compute_content_hash("file", blob)
    )


def test_compute_content_hash_type_mismatch_raises() -> None:
    with pytest.raises(TypeError):
        compute_content_hash("text", b"bytes for text kind")
    with pytest.raises(TypeError):
        compute_content_hash("file", "string for file kind")


# ---------- L1: TG update_id ----------


async def test_l1_first_sight_returns_true_second_returns_false(session_maker) -> None:
    svc = IdempotencyService(session_maker)
    assert await svc.check_update_id(42) is True
    assert await svc.check_update_id(42) is False
    # Different update_id is independent.
    assert await svc.check_update_id(43) is True

    async with session_maker() as s:
        rows = (
            (await s.execute(select(TgUpdate.update_id).order_by(TgUpdate.update_id)))
            .scalars()
            .all()
        )
        assert list(rows) == [42, 43]


# ---------- L2: content hash ----------


async def test_l2_first_sight_registers_and_returns_none(session_maker) -> None:
    svc = IdempotencyService(session_maker)
    sha, match = await svc.check_content(1001, "text", "lab results A1c 6.2")
    assert match is None
    assert len(sha) == 64
    async with session_maker() as s:
        row = (await s.execute(select(SeenFile).where(SeenFile.content_sha256 == sha))).scalar_one()
        assert row.owner_telegram_id == 1001
        assert row.kind == "text"


async def test_l2_duplicate_returns_match_with_first_owner(session_maker) -> None:
    svc = IdempotencyService(session_maker)
    sha1, m1 = await svc.check_content(1001, "text", "Hello World")
    assert m1 is None
    # Different surface form → same normalized hash → match.
    sha2, m2 = await svc.check_content(2002, "text", "  HELLO\nworld  ")
    assert sha2 == sha1
    assert m2 is not None
    assert m2.content_sha256 == sha1
    # First-sight owner preserved (1001), even though current caller is 2002.
    assert m2.owner_telegram_id == 1001
    assert m2.kind == "text"
    assert m2.first_seen_at_utc is not None


async def test_l2_bytes_kinds_independent_of_text_collision(session_maker) -> None:
    svc = IdempotencyService(session_maker)
    # Same hash space — collisions across kinds detected when raw bytes coincide,
    # which is acceptable per D-018 (SHA-256 is content-identity, kind is metadata only).
    sha_voice, m1 = await svc.check_content(1001, "voice", b"OPUS-FRAME")
    assert m1 is None
    sha_photo, m2 = await svc.check_content(1001, "photo", b"OPUS-FRAME")
    # Same payload bytes → same hash → collision recorded.
    assert sha_photo == sha_voice
    assert m2 is not None


# ---------- dedup_hits audit log ----------


async def test_record_dedup_choice_writes_audit_row(session_maker) -> None:
    svc = IdempotencyService(session_maker)
    sha, _ = await svc.check_content(1001, "text", "duplicate me")
    # Simulate a second send → user picks "use_old".
    sha2, match = await svc.check_content(1001, "text", "duplicate me")
    assert match is not None
    await svc.record_dedup_choice(sha2, 1001, "use_old")

    async with session_maker() as s:
        rows = (
            (await s.execute(select(DedupHit).where(DedupHit.content_sha256 == sha)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].action == "use_old"
        assert rows[0].owner_telegram_id == 1001


# ---------- no auto-block invariant (D-018 §"Последствия") ----------


async def test_l2_collision_does_not_block_ingest(session_maker) -> None:
    """Caller can proceed after L2 collision — UX-policy lives at TG layer, not here."""
    svc = IdempotencyService(session_maker)
    await svc.check_content(1001, "text", "x")
    sha, match = await svc.check_content(1001, "text", "x")
    assert match is not None
    # The service does not raise; caller is free to ingest again.
