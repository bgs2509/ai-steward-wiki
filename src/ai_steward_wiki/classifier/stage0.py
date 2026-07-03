# FILE: src/ai_steward_wiki/classifier/stage0.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Stage-0 orchestrator — load+cache prompt, invoke backend, validate, audit.
#   SCOPE: classify() public API (with graceful timeout/transient-error fallback to
#          intent=unknown); _PromptCache; idempotent prompt_versions audit upsert.
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
#   LAST_CHANGE: v0.0.3 - aisw-l3h: retry a bare ClassifierError (CLI rc!=0, e.g. a transient
#                `.claude.json` write race — see 2026-07-02T19:03 prod incident) once before
#                falling back to intent=unknown, same shape as the timeout fallback.
#                ClassifierSchemaError (permanent) still propagates unretried. New log anchor
#                classifier.stage0.error_fallback; distilled_payload fallback marker
#                "stage0_error".
#   PREVIOUS:    v0.0.2 - aisw-32p: graceful Stage-0 timeout fallback. classify() catches
#                ClassifierTimeoutError from backend.call() and returns a safe
#                intent=unknown ClassifierResult (confidence 0.0, distilled_payload
#                {"fallback":"stage0_timeout"}) instead of raising — unknown is routable so
#                the tg pipeline forwards to the Inbox router rather than dropping the message.
#                New log anchor classifier.stage0.timeout_fallback.
#   PREVIOUS:    v0.0.1 - initial orchestrator with prompt cache + audit hook
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_steward_wiki.classifier.backend import ClassifierBackend
from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierResult,
    ClassifierSchemaError,
    ClassifierTimeoutError,
    Intent,
)
from ai_steward_wiki.logging_events import CLASSIFIER_STAGE0
from ai_steward_wiki.logging_setup import traced
from ai_steward_wiki.storage.audit.models import PromptVersion

__all__ = [
    "PromptCache",
    "PromptMeta",
    "classify",
    "record_prompt_version",
]

_log = structlog.get_logger("classifier.stage0")
_SEMVER_RE = re.compile(r"^semver:\s*(\d+\.\d+\.\d+)\s*$", re.MULTILINE)
# aisw-l3h: pause before the single retry of a bare ClassifierError (CLI rc!=0). Observed
# prod failures (2026-07-02T13:18, 19:03) were config-file write races that self-resolve in
# well under a second — this gives the race a chance to clear without adding user-visible lag.
_ERROR_RETRY_DELAY_S: float = 0.5


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


async def _call_with_error_retry(
    *, backend: ClassifierBackend, text: str, prompt_path: Path, correlation_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Invoke `backend.call`, retrying a bare ClassifierError once.

    Returns (raw, None) on success, or (None, fallback_reason) when the backend never
    produced usable output. TIMEOUT degrades on the first hit — retrying a 30s timeout would
    double the user's wait. A bare ClassifierError (CLI rc!=0, e.g. a `.claude.json` write
    race — aisw-l3h, prod incident 2026-07-02T19:03) gets one retry, since observed failures
    resolve in a few seconds. ClassifierSchemaError (malformed model output) is a PERMANENT
    fault — re-raised unretried, so the pipeline's existing error ack stays correct for it.
    """
    attempts_left = 2
    while True:
        attempts_left -= 1
        try:
            raw = await backend.call(
                text=text, prompt_path=prompt_path, correlation_id=correlation_id
            )
            return raw, None
        except ClassifierTimeoutError:
            return None, "stage0_timeout"
        except ClassifierError as e:
            if type(e) is not ClassifierError:
                raise
            if attempts_left <= 0:
                return None, "stage0_error"
            await asyncio.sleep(_ERROR_RETRY_DELAY_S)


def _fallback_result(
    *, reason: str, backend: ClassifierBackend, meta: PromptMeta, latency_ms: int
) -> ClassifierResult:
    return ClassifierResult.model_validate(
        {
            "intent": Intent.UNKNOWN.value,
            "confidence": 0.0,
            "distilled_payload": {"fallback": reason},
            "backend": backend.name,
            "model": backend.model,
            "prompt_semver": meta.semver,
            "prompt_sha256": meta.sha256,
            "latency_ms": latency_ms,
        }
    )


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


@traced(event_prefix=CLASSIFIER_STAGE0)
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
    # START_BLOCK_TRANSIENT_FALLBACK (aisw-32p, aisw-l3h)
    # Never let a transient Stage-0 Haiku failure drop the user's message — degrade to a
    # safe intent=unknown result instead of propagating an exception (which the tg pipeline
    # turns into a dead-end "не удалось распознать" ack). `unknown` is a ROUTABLE intent
    # (tg.pipeline._ROUTABLE_INTENTS), so the message still reaches the Inbox router (or the
    # generic answer runner). See _call_with_error_retry for the retry/fallback policy per
    # exception type. ClassifierSchemaError is NOT degraded — it is a permanent fault.
    raw, fallback_reason = await _call_with_error_retry(
        backend=backend, text=text, prompt_path=prompt_path, correlation_id=correlation_id
    )
    if fallback_reason is not None:
        latency_ms = int((time.monotonic() - started) * 1000)
        log_kwargs: dict[str, Any] = {
            "correlation_id": correlation_id,
            "backend": backend.name,
            "model": backend.model,
            "prompt_name": meta.name,
            "prompt_semver": meta.semver,
            "prompt_sha8": meta.sha256[:8],
            "latency_ms": latency_ms,
            "intent": Intent.UNKNOWN.value,
        }
        if fallback_reason == "stage0_timeout":
            _log.warning("classifier.stage0.timeout_fallback", **log_kwargs)
        else:
            _log.warning("classifier.stage0.error_fallback", **log_kwargs)
        return _fallback_result(
            reason=fallback_reason, backend=backend, meta=meta, latency_ms=latency_ms
        )
    # END_BLOCK_TRANSIENT_FALLBACK
    assert raw is not None
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
