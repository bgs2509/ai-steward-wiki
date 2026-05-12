# FILE: src/ai_steward_wiki/tg/photo.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Photo ingestion — stage bytes to _staging/, return a MediaRef. The
#            actual Claude vision call is performed by the wiki pipeline, which
#            passes the staged path to the runner via media_paths (D-022, aisw-m2m).
#   SCOPE: PhotoIngestor.handle(photo_bytes, run_id) -> MediaRef; ext sniff via
#          mime mapping (jpeg/png/webp).
#   DEPENDS: ai_steward_wiki.inbox.staging, ai_steward_wiki.logging_setup
#   LINKS: D-022, §9 tech-spec, M-TG-MEDIA
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PHOTO_MIME_TO_EXT - closed mapping of allowed photo MIME types → extension
#   PhotoIngestor - stages photo bytes; returns MediaRef for the wiki pipeline
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-m2m (media chunk 2): contract clarified — vision
#                call is owned by the wiki pipeline (media_paths); no code change.
#   PREVIOUS:    v0.0.1 - chunk 11: initial photo staging (D-022)
# END_CHANGE_SUMMARY

from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.inbox.staging import MediaRef, stage_media
from ai_steward_wiki.logging_setup import get_logger

__all__ = [
    "PHOTO_MIME_TO_EXT",
    "PhotoIngestor",
]

_log = get_logger(__name__)

PHOTO_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class PhotoIngestor:
    """Stages photo bytes and returns a MediaRef for downstream Sonnet vision."""

    def __init__(self, *, inbox_root: Path) -> None:
        self._inbox_root = inbox_root

    def handle(
        self,
        photo_bytes: bytes,
        *,
        run_id: str,
        mime: str,
    ) -> MediaRef:
        ext = PHOTO_MIME_TO_EXT.get(mime.lower())
        if ext is None:
            raise ValueError(f"unsupported photo mime: {mime!r}")
        ref = stage_media(
            photo_bytes,
            ext=ext,
            run_id=run_id,
            inbox_root=self._inbox_root,
            mime=mime,
        )
        _log.info(
            "photo.ingested",
            run_id=run_id,
            sha256=ref.sha256,
            ext=ext,
            size=ref.size,
        )
        return ref
