# FILE: src/ai_steward_wiki/storage/audit/models.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: ORM models for audit.db — append-only audit + dedup + observability.
#   SCOPE: 10 tables per D-006 amended 2026-05-10.
#   DEPENDS: SQLAlchemy.orm, ai_steward_wiki.storage.audit.engine.Base
#   LINKS: M-STORAGE-AUDIT, D-018, D-020, D-025, D-028, D-030, D-033, D-034
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ChatLog - plaintext dialog (D-033, retention 30d)
#   AuditEvent - generic action-log row (retention 90d)
#   AdminEvent - admin-elevation trail (D-028, retention 90d)
#   TgUpdate - L1 dedup TG update_id (D-018, TTL 24h)
#   SeenFile - L2 content-hash dedup (D-018, TTL 30d)
#   DedupHit - outcome of L2 collision (D-018)
#   JobOutput - cron result routing/delivery metadata (D-020, retention 90d)
#   RunOutput - index of full-output files in <wiki>/data/runs/ (D-025, retention 180d)
#   PromptVersion - semver+sha256 of system prompts (D-015, indefinite)
#   OnboardingEvent - intro-element measure-show per user (D-030, retention 180d)
# END_MODULE_MAP

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_steward_wiki.storage.audit.engine import Base


class ChatLog(Base):
    __tablename__ = "chat_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # text|voice|photo|...
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # redacted JSON string
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class AdminEvent(Base):
    __tablename__ = "admin_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # elevate|grant|revoke|...
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at_utc: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class TgUpdate(Base):
    """L1 idempotency — Telegram update_id PK (D-018, TTL 24h)."""

    __tablename__ = "tg_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    received_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class SeenFile(Base):
    """L2 idempotency — content-hash SHA-256 PK (D-018 amended, TTL 30d)."""

    __tablename__ = "seen_files"

    content_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # text|voice|photo|file
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    first_seen_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class DedupHit(Base):
    __tablename__ = "dedup_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # use_old|recreate|...
    created_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


class JobOutput(Base):
    """Routing/delivery metadata for scheduled job results (D-020). Retention 90d.

    Note: cross-DB FK to jobs.db.jobs.id is logical only — SQLite FKs do not span files.
    """

    __tablename__ = "job_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fired_at_utc: Mapped[datetime] = mapped_column(nullable=False)
    finished_at_utc: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok|fail|timeout|killed
    notify_policy: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # always|on_output|silent
    delivered_to_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RunOutput(Base):
    """Index of full-output files written under <wiki>/data/runs/ (D-025). Retention 180d."""

    __tablename__ = "run_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    wiki_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    started_at_utc: Mapped[datetime] = mapped_column(nullable=False)
    finished_at_utc: Mapped[datetime | None] = mapped_column(nullable=True)
    output_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # reply|digest|ingest_report


class PromptVersion(Base):
    """Semver + sha256 of every system prompt invoked (D-015). Indefinite retention."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "semver", "sha256", name="uq_prompt_versions"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at_utc: Mapped[datetime] = mapped_column(nullable=False)


class OnboardingEvent(Base):
    """Measure-show of mandatory intro elements per user (D-030). Retention 180d."""

    __tablename__ = "onboarding_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    shown_at_utc: Mapped[datetime] = mapped_column(nullable=False, index=True)


Index(
    "ix_audit_events_kind_created",
    AuditEvent.kind,
    AuditEvent.created_at_utc,
)
