# FILE: src/ai_steward_wiki/storage/sessions/models.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: ORM models for sessions.db — runtime TG state and hot-path caches.
#   SCOPE: users, pending_users, pending_confirms, inbox_hint_cache, fsm.
#   DEPENDS: SQLAlchemy.orm, ai_steward_wiki.storage.sessions.engine.Base
#   LINKS: M-STORAGE-SESSIONS, D-031, D-042, D-030, D-023
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   User - sync-snapshot from users.toml (D-031/D-042)
#   PendingUser - /start applicant pre-approve (D-030, retention 14d)
#   PendingConfirm - explicit-confirm 10min TTL (D-023)
#   InboxHintCache - per-user runtime catalog of "## Inbox hint" (D-006 §"Структура")
#   FsmState - aiogram FSM persistence
# END_MODULE_MAP

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_steward_wiki.storage.sessions.engine import Base


class User(Base):
    """The ONLY table holding both user_id (surrogate) and telegram_id (canonical) — D-042."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(nullable=False)


class PendingUser(Base):
    __tablename__ = "pending_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    requested_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)
    candidate_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PendingConfirm(Base):
    __tablename__ = "pending_confirms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False)


class InboxHintCache(Base):
    __tablename__ = "inbox_hint_cache"
    __table_args__ = (UniqueConstraint("user_id", "wiki_path", name="uq_inbox_hint_user_wiki"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    wiki_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ctime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hint_text: Mapped[str] = mapped_column(Text, nullable=False)
    refreshed_at_utc: Mapped[datetime] = mapped_column(nullable=False)


class FsmState(Base):
    """Generic key-value FSM storage backing aiogram. Kept independent of aiogram internals."""

    __tablename__ = "fsm"
    __table_args__ = (UniqueConstraint("chat_id", "user_id_tg", name="uq_fsm_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id_tg: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at_utc: Mapped[datetime] = mapped_column(nullable=False)


Index(
    "ix_pending_confirms_telegram_expires",
    PendingConfirm.telegram_id,
    PendingConfirm.expires_at_utc,
)
