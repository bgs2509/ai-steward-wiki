# FILE: src/ai_steward_wiki/inbox/staging.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Media staging+promotion for voice/photo ingest (D-022, §9 tech-spec).
#   SCOPE: stage_media (bytes → _staging/<run_id>_<sha8>.<ext>), promote_to_raw
#          (atomic move to <wiki>/raw/media/<ISO8601>_<sha8>.<ext>), sweep_staging
#          (24h TTL retention sweep).
#   DEPENDS: hashlib, os, datetime (stdlib), ai_steward_wiki.logging_setup
#   LINKS: D-022, §9 tech-spec, INV-4, M-TG-MEDIA
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   STAGING_DIRNAME - canonical _staging subdir name
#   RAW_MEDIA_SUBPATH - raw/media subpath under a wiki root
#   DEFAULT_STAGING_TTL_S - 24h retention (D-022)
#   MediaRef - dataclass (staging_path, sha256, mime, ext, size, run_id)
#   stage_media - atomically write bytes to _staging/<run_id>_<sha8>.<ext>
#   promote_to_raw - atomic move staging→raw/media/<ISO8601>_<sha8>.<ext>
#   sweep_staging - delete staging files older than ttl_s; returns removed count
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 11: initial staging+promote+sweep (D-022)
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ai_steward_wiki.logging_setup import get_logger

__all__ = [
    "DEFAULT_STAGING_TTL_S",
    "RAW_MEDIA_SUBPATH",
    "STAGING_DIRNAME",
    "MediaRef",
    "promote_to_raw",
    "stage_media",
    "sweep_staging",
]

_log = get_logger(__name__)

STAGING_DIRNAME = "_staging"
RAW_MEDIA_SUBPATH = "raw/media"
DEFAULT_STAGING_TTL_S = 24 * 60 * 60  # 24h per D-022 §"staging retention"


@dataclass(frozen=True)
class MediaRef:
    """Reference to a staged media artifact."""

    staging_path: Path
    sha256: str
    mime: str
    ext: str
    size: int
    run_id: str


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sanitize_ext(ext: str) -> str:
    e = ext.lower().lstrip(".")
    # Whitelist: alnum only, max 8 chars (PII tier-2: never embed user-name).
    if not e.isalnum() or len(e) > 8:
        raise ValueError(f"invalid media extension: {ext!r}")
    return e


# START_CONTRACT: stage_media
#   PURPOSE: Atomically write media bytes to <inbox_root>/raw/media/_staging/<run_id>_<sha8>.<ext>.
#   INPUTS: { data: bytes, ext: str, run_id: str, inbox_root: Path, mime: str }
#   OUTPUTS: { MediaRef - frozen dataclass with staging path + content hash }
#   SIDE_EFFECTS: creates _staging dir (parents=True); writes tmp file then os.replace.
#   LINKS: D-022 §"_staging path"
# END_CONTRACT: stage_media
def stage_media(
    data: bytes,
    *,
    ext: str,
    run_id: str,
    inbox_root: Path,
    mime: str,
) -> MediaRef:
    safe_ext = _sanitize_ext(ext)
    if not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError(f"invalid run_id: {run_id!r}")
    sha = _sha256_hex(data)
    sha8 = sha[:8]
    staging_dir = inbox_root / RAW_MEDIA_SUBPATH / STAGING_DIRNAME
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / f"{run_id}_{sha8}.{safe_ext}"
    # START_BLOCK_STAGING_ATOMIC_WRITE
    tmp = staging_dir / f".{run_id}_{sha8}.{safe_ext}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
    # END_BLOCK_STAGING_ATOMIC_WRITE
    _log.info(
        "media.staged",
        run_id=run_id,
        sha256=sha,
        ext=safe_ext,
        size=len(data),
        path=str(target),
    )
    return MediaRef(
        staging_path=target,
        sha256=sha,
        mime=mime,
        ext=safe_ext,
        size=len(data),
        run_id=run_id,
    )


# START_CONTRACT: promote_to_raw
#   PURPOSE: Atomic move staging file → <wiki_root>/raw/media/<ISO8601>_<sha8>.<ext>.
#   INPUTS: { ref: MediaRef, wiki_root: Path, now: datetime | None }
#   OUTPUTS: { Path - final raw/media path; idempotent if target already exists }
#   SIDE_EFFECTS: creates raw/media dir; os.replace; emits media.promoted log.
#   LINKS: D-022 §"atomic move"
# END_CONTRACT: promote_to_raw
def promote_to_raw(
    ref: MediaRef,
    *,
    wiki_root: Path,
    now: datetime | None = None,
) -> Path:
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    sha8 = ref.sha256[:8]
    raw_dir = wiki_root / RAW_MEDIA_SUBPATH
    raw_dir.mkdir(parents=True, exist_ok=True)
    final = raw_dir / f"{ts}_{sha8}.{ref.ext}"
    if final.exists():
        # Idempotent: if same target already exists, drop staging (if present).
        if ref.staging_path.exists():
            with contextlib.suppress(OSError):
                ref.staging_path.unlink()
        _log.debug("media.promote_idempotent", path=str(final), sha256=ref.sha256)
        return final
    if not ref.staging_path.exists():
        # Already moved or swept — surface as a hard error rather than silent miss.
        raise FileNotFoundError(f"staging file missing: {ref.staging_path}")
    os.replace(ref.staging_path, final)
    _log.info(
        "media.promoted",
        run_id=ref.run_id,
        sha256=ref.sha256,
        ext=ref.ext,
        path=str(final),
    )
    return final


# START_CONTRACT: sweep_staging
#   PURPOSE: Delete staging files older than ttl_s; default 24h (D-022).
#   INPUTS: { inbox_root: Path, now: datetime | None, ttl_s: int }
#   OUTPUTS: { int - number of files removed }
#   SIDE_EFFECTS: unlinks stale files; logs media.staging_swept per file.
#   LINKS: D-022 §"staging retention"
# END_CONTRACT: sweep_staging
def sweep_staging(
    inbox_root: Path,
    *,
    now: datetime | None = None,
    ttl_s: int = DEFAULT_STAGING_TTL_S,
) -> int:
    staging_dir = inbox_root / RAW_MEDIA_SUBPATH / STAGING_DIRNAME
    if not staging_dir.exists():
        return 0
    cutoff = (now or datetime.now(UTC)).timestamp() - ttl_s
    removed = 0
    for path in staging_dir.iterdir():
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
            _log.info("media.staging_swept", path=str(path))
    if removed:
        _log.info("media.staging_sweep_done", removed=removed, dir=str(staging_dir))
    return removed
