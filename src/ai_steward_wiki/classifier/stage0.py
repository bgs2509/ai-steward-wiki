# FILE: src/ai_steward_wiki/classifier/stage0.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Stage-0 orchestrator — load+cache prompt, invoke backend, validate, audit.
#   SCOPE: classify() public API; _PromptCache; idempotent prompt_versions audit upsert.
#   DEPENDS: pydantic, sqlalchemy.async, structlog, ai_steward_wiki.classifier.{schema,backend},
#            ai_steward_wiki.storage.audit.models
#   LINKS: M-CLASSIFIER-STAGE0, D-009, D-015
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PromptMeta - dataclass carrying (text, semver, sha256) of a loaded prompt file
#   PromptCache - per-process cache; reload() clears and re-reads
#   classify - public API; orchestrates backend call + Pydantic validation + audit + structlog
#   record_prompt_version - idempotent upsert into audit.prompt_versions (D-015)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial orchestrator with prompt cache + audit hook
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_steward_wiki.classifier.backend import ClassifierBackend
from ai_steward_wiki.classifier.schema import (
    ClassifierResult,
    ClassifierSchemaError,
)
from ai_steward_wiki.storage.audit.models import PromptVersion

__all__ = [
    "PromptCache",
    "PromptMeta",
    "classify",
    "record_prompt_version",
]

_log = structlog.get_logger("classifier.stage0")
_SEMVER_RE = re.compile(r"^semver:\s*(\d+\.\d+\.\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PromptMeta:
    text: str
    semver: str
    sha256: str
    name: str


class PromptCache:
    def __init__(self) -> None:
        self._cache: dict[Path, PromptMeta] = {}

    def get(self, path: Path) -> PromptMeta:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        raw = path.read_text(encoding="utf-8")
        match = _SEMVER_RE.search(raw)
        if match is None:
            raise ClassifierSchemaError(
                f"prompt {path} missing required `semver: X.Y.Z` frontmatter line"
            )
        meta = PromptMeta(
            text=raw,
            semver=match.group(1),
            sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            name=path.name,
        )
        self._cache[path] = meta
        return meta

    def reload(self) -> None:
        self._cache.clear()


_default_cache = PromptCache()


async def record_prompt_version(session: AsyncSession, meta: PromptMeta) -> None:
    """Idempotent upsert into audit.prompt_versions (no-op if row exists)."""
    stmt = select(PromptVersion.id).where(
        PromptVersion.name == meta.name,
        PromptVersion.semver == meta.semver,
        PromptVersion.sha256 == meta.sha256,
    )
    existing = await session.execute(stmt)
    if existing.scalar_one_or_none() is not None:
        return
    session.add(
        PromptVersion(
            name=meta.name,
            semver=meta.semver,
            sha256=meta.sha256,
            first_seen_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.flush()


async def classify(
    text: str,
    *,
    correlation_id: str,
    backend: ClassifierBackend,
    prompt_path: Path,
    audit_session: AsyncSession | None = None,
    cache: PromptCache | None = None,
) -> ClassifierResult:
    """Classify `text` via Stage-0 backend and return a validated ClassifierResult.

    Side-effects:
      - structlog event `classifier.stage0.call` (always)
      - audit.prompt_versions upsert (only when audit_session is provided)
    """
    cache = cache or _default_cache
    meta = cache.get(prompt_path)

    started = time.monotonic()
    raw = await backend.call(text=text, prompt_path=prompt_path, correlation_id=correlation_id)
    latency_ms = int((time.monotonic() - started) * 1000)

    payload = {
        **raw,
        "backend": backend.name,
        "model": backend.model,
        "prompt_semver": meta.semver,
        "prompt_sha256": meta.sha256,
        "latency_ms": latency_ms,
    }
    try:
        result = ClassifierResult.model_validate(payload)
    except ValidationError as e:
        raise ClassifierSchemaError(f"backend returned invalid ClassifierResult: {e}") from e

    if audit_session is not None:
        await record_prompt_version(audit_session, meta)

    _log.info(
        "classifier.stage0.call",
        correlation_id=correlation_id,
        backend=backend.name,
        model=backend.model,
        prompt_name=meta.name,
        prompt_semver=meta.semver,
        prompt_sha8=meta.sha256[:8],
        latency_ms=latency_ms,
        intent=result.intent.value,
        confidence=result.confidence,
    )
    return result
