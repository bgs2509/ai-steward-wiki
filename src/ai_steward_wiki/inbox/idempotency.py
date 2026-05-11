# FILE: src/ai_steward_wiki/inbox/idempotency.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Two-layer ingest dedup (D-018): L1 TG update_id (24h) + L2 SHA-256 content hash (30d).
#   SCOPE: normalize_text, compute_content_hash, IdempotencyService (L1+L2 + dedup-hit logging).
#   DEPENDS: SQLAlchemy.async, ai_steward_wiki.storage.audit.models (TgUpdate, SeenFile, DedupHit),
#            ai_steward_wiki.logging_setup
#   LINKS: D-018 (amended 2026-05-10), M-INGEST-IDEM, M-STORAGE-AUDIT, INV-4
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ContentKind - Literal alias for {text, voice, photo, file}
#   normalize_text - NFKC + strip + lower + collapse-whitespace for L2 text hashing
#   compute_content_hash - SHA-256 hex of normalized text or raw bytes per kind
#   SeenFileMatch - dataclass returned on L2 collision (hash + first-seen ts + owner)
#   IdempotencyService - check_update_id (L1), check_content (L2), record_dedup_choice
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - L1+L2 dedup per D-018 (chunk 9)
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.logging_setup import get_logger
from ai_steward_wiki.storage.audit.models import DedupHit, SeenFile, TgUpdate

_log = get_logger(__name__)

ContentKind = Literal["text", "voice", "photo", "file"]

_WS_RE = re.compile(r"\s+")


# START_CONTRACT: normalize_text
#   PURPOSE: Canonicalize text for stable SHA-256 (D-018 §"Hash вычисление" — text).
#   INPUTS: { value: str }
#   OUTPUTS: { str - NFKC-normalized, stripped, lower-cased, whitespace-collapsed }
#   SIDE_EFFECTS: none
# END_CONTRACT: normalize_text
def normalize_text(value: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", value).strip().lower())


# START_CONTRACT: compute_content_hash
#   PURPOSE: SHA-256 hex digest per D-018 hashing rules.
#   INPUTS: { kind: ContentKind, payload: str | bytes }
#   OUTPUTS: { str - 64-char lowercase hex }
#   SIDE_EFFECTS: none
# END_CONTRACT: compute_content_hash
def compute_content_hash(kind: ContentKind, payload: str | bytes) -> str:
    # START_BLOCK_HASH_BY_KIND
    if kind == "text":
        if not isinstance(payload, str):
            raise TypeError("text kind requires str payload")
        raw = normalize_text(payload).encode("utf-8")
    else:
        if not isinstance(payload, bytes | bytearray):
            raise TypeError(f"{kind} kind requires bytes payload")
        raw = bytes(payload)
    # END_BLOCK_HASH_BY_KIND
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class SeenFileMatch:
    """L2 collision result — content already seen for this owner."""

    content_sha256: str
    owner_telegram_id: int
    kind: ContentKind
    first_seen_at_utc: datetime


def _utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IdempotencyService:
    """Two-layer dedup repo over audit.db (D-018).

    L1 (check_update_id): TG webhook `update_id` INSERT OR IGNORE.
        Returns True iff the update is new (was inserted). Existing → False (skip).
    L2 (check_content): content SHA-256 lookup; returns SeenFileMatch on collision OR
        registers the new content and returns None. Does NOT block — caller decides UX.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = session_maker

    # START_CONTRACT: check_update_id
    #   PURPOSE: L1 dedup — atomic INSERT OR IGNORE on tg_updates.update_id.
    #   INPUTS: { update_id: int }
    #   OUTPUTS: { bool - True if new (proceed), False if duplicate (skip silently) }
    #   SIDE_EFFECTS: writes one row in audit.db.tg_updates on first sight
    # END_CONTRACT: check_update_id
    async def check_update_id(self, update_id: int) -> bool:
        now = _utc_naive()
        stmt = (
            sqlite_insert(TgUpdate)
            .values(update_id=update_id, received_at_utc=now)
            .on_conflict_do_nothing(index_elements=[TgUpdate.update_id])
        )
        async with self._sm() as session, session.begin():
            result = await session.execute(stmt)
        inserted = (result.rowcount or 0) > 0
        if not inserted:
            _log.info("inbox.idempotency.l1_duplicate", update_id=update_id)
        return inserted

    # START_CONTRACT: check_content
    #   PURPOSE: L2 dedup — compute hash, lookup seen_files for owner, register if new.
    #   INPUTS: { owner_telegram_id: int, kind: ContentKind, payload: str | bytes }
    #   OUTPUTS: { tuple[str, SeenFileMatch | None] - (sha256, match-or-None) }
    #   SIDE_EFFECTS: writes one row in audit.db.seen_files on first sight; emits structlog
    # END_CONTRACT: check_content
    async def check_content(
        self,
        owner_telegram_id: int,
        kind: ContentKind,
        payload: str | bytes,
    ) -> tuple[str, SeenFileMatch | None]:
        sha256 = compute_content_hash(kind, payload)
        now = _utc_naive()
        stmt = (
            sqlite_insert(SeenFile)
            .values(
                content_sha256=sha256,
                kind=kind,
                owner_telegram_id=owner_telegram_id,
                first_seen_at_utc=now,
            )
            .on_conflict_do_nothing(index_elements=[SeenFile.content_sha256])
        )
        async with self._sm() as session, session.begin():
            result = await session.execute(stmt)
            inserted = (result.rowcount or 0) > 0
            if inserted:
                _log.info(
                    "inbox.idempotency.l2_new",
                    owner_telegram_id=owner_telegram_id,
                    kind=kind,
                    sha256=sha256,
                )
                return sha256, None
            # START_BLOCK_FETCH_EXISTING
            from sqlalchemy import select

            row = (
                await session.execute(select(SeenFile).where(SeenFile.content_sha256 == sha256))
            ).scalar_one()
            # END_BLOCK_FETCH_EXISTING
        _log.info(
            "inbox.idempotency.l2_duplicate",
            owner_telegram_id=owner_telegram_id,
            kind=kind,
            sha256=sha256,
            first_seen_owner=row.owner_telegram_id,
        )
        return sha256, SeenFileMatch(
            content_sha256=row.content_sha256,
            owner_telegram_id=row.owner_telegram_id,
            kind=row.kind,  # type: ignore[arg-type]
            first_seen_at_utc=row.first_seen_at_utc,
        )

    # START_CONTRACT: record_dedup_choice
    #   PURPOSE: Append audit row for user's inline-confirm action on an L2 collision.
    #   INPUTS: { content_sha256: str, owner_telegram_id: int, action: str }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: writes one row in audit.db.dedup_hits
    # END_CONTRACT: record_dedup_choice
    async def record_dedup_choice(
        self,
        content_sha256: str,
        owner_telegram_id: int,
        action: str,
    ) -> None:
        row = DedupHit(
            content_sha256=content_sha256,
            owner_telegram_id=owner_telegram_id,
            action=action,
            created_at_utc=_utc_naive(),
        )
        async with self._sm() as session, session.begin():
            session.add(row)
        _log.info(
            "inbox.idempotency.dedup_choice",
            owner_telegram_id=owner_telegram_id,
            sha256=content_sha256,
            action=action,
        )
