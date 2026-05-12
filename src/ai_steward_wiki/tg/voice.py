# FILE: src/ai_steward_wiki/tg/voice.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Voice (ogg/opus) ingestion — faster-whisper CPU STT + staging hand-off.
#   SCOPE: VoiceTranscriber Protocol, Transcript dataclass, FasterWhisperTranscriber
#          (lazy-loaded CTranslate2 int8 CPU model, ru/en, RTF ≤ 0.5), VoiceHandler
#          wiring stage_media + transcribe.
#   DEPENDS: faster_whisper (lazy import — optional dep), ai_steward_wiki.inbox.staging,
#            ai_steward_wiki.logging_setup
#   LINKS: D-022, §9 tech-spec, M-TG-MEDIA
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   VoiceUnavailableError - raised when STT backend (faster-whisper) is missing
#   Transcript - frozen dataclass (text, lang, duration_s, model, rtf)
#   VoiceTranscriber - Protocol with transcribe(audio_bytes, hint_lang) -> Transcript
#   FasterWhisperTranscriber - default impl, lazy model load, fallback lang="ru"
#   VoiceHandler - stages bytes + transcribes; returns (MediaRef, Transcript)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-12t (Phase-E.a): per-call inbox_root override on
#                VoiceHandler.handle (per-user Inbox-WIKI media staging, D-022);
#                __init__ inbox_root is now an optional default.
#   PREVIOUS:    v0.0.2 - aisw-zny (media chunk 1): _load_model raises
#                VoiceUnavailableError on missing faster-whisper so the runtime
#                can degrade gracefully instead of a silent generic ack.
#   PREVIOUS:    v0.0.1 - chunk 11: initial voice STT + staging (D-022)
# END_CHANGE_SUMMARY

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_steward_wiki.inbox.staging import MediaRef, stage_media
from ai_steward_wiki.logging_setup import get_logger

__all__ = [
    "FasterWhisperTranscriber",
    "Transcript",
    "VoiceHandler",
    "VoiceTranscriber",
    "VoiceUnavailableError",
]

_log = get_logger(__name__)


class VoiceUnavailableError(RuntimeError):
    """STT backend is not installed/usable (e.g. ``faster-whisper`` missing).

    Raised lazily on first transcription attempt; the pipeline maps it to a
    user-facing ru message instead of the silent generic ack.
    """


_FALLBACK_LANG = "ru"
_SUPPORTED_LANGS = ("ru", "en")


@dataclass(frozen=True)
class Transcript:
    text: str
    lang: str
    duration_s: float
    model: str
    rtf: float  # real-time factor: processing_time / duration


class VoiceTranscriber(Protocol):
    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        hint_lang: str | None = None,
    ) -> Transcript: ...


class FasterWhisperTranscriber:
    """Default transcriber — CTranslate2 int8 CPU model, lazy-loaded.

    Note: the real model is heavy; we never instantiate it at import time so
    unit tests can monkeypatch ``_load_model`` to inject a fake.
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: object | None = None

    @property
    def model_name(self) -> str:
        return f"faster-whisper/{self._model_size}-{self._compute_type}"

    def _load_model(self) -> object:
        """Lazy model construction — overridden in tests via monkeypatch."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - exercised via VoiceHandler path
            raise VoiceUnavailableError("faster-whisper is not installed") from exc

        return WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        hint_lang: str | None = None,
    ) -> Transcript:
        import asyncio
        import io

        if self._model is None:
            self._model = await asyncio.to_thread(self._load_model)
        lang_hint: str | None = hint_lang if hint_lang in _SUPPORTED_LANGS else None

        def _run() -> tuple[str, str, float, float]:
            assert self._model is not None
            t0 = time.monotonic()
            # faster-whisper accepts a file-like; pass BytesIO of raw ogg bytes.
            segments, info = self._model.transcribe(  # type: ignore[attr-defined]
                io.BytesIO(audio_bytes),
                language=lang_hint,
                vad_filter=True,
            )
            text = "".join(seg.text for seg in segments).strip()
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            detected = getattr(info, "language", lang_hint) or _FALLBACK_LANG
            if detected not in _SUPPORTED_LANGS:
                detected = _FALLBACK_LANG
            elapsed = time.monotonic() - t0
            rtf = elapsed / duration if duration > 0 else 0.0
            return text, detected, duration, rtf

        text, detected, duration, rtf = await asyncio.to_thread(_run)
        tr = Transcript(
            text=text,
            lang=detected,
            duration_s=duration,
            model=self.model_name,
            rtf=rtf,
        )
        _log.info(
            "voice.transcribed",
            lang=detected,
            duration_s=duration,
            rtf=rtf,
            model=self.model_name,
            chars=len(text),
        )
        return tr


class VoiceHandler:
    """Bridges raw ogg bytes → staged MediaRef + Transcript.

    The media-staging root is per-sender (``<wiki_root>/<telegram_id>/Inbox-WIKI``,
    D-022) and therefore resolved at call time. ``__init__``'s ``inbox_root`` is an
    optional default (used standalone / by integration tests); ``handle``'s
    ``inbox_root`` overrides it. At least one of the two MUST be set.
    """

    def __init__(
        self,
        transcriber: VoiceTranscriber,
        *,
        inbox_root: Path | None = None,
    ) -> None:
        self._transcriber = transcriber
        self._inbox_root = inbox_root

    async def handle(
        self,
        audio_bytes: bytes,
        *,
        run_id: str,
        hint_lang: str | None = None,
        mime: str = "audio/ogg",
        ext: str = "ogg",
        inbox_root: Path | None = None,
    ) -> tuple[MediaRef, Transcript]:
        root = inbox_root if inbox_root is not None else self._inbox_root
        if root is None:
            raise ValueError("VoiceHandler.handle: inbox_root required (no constructor default)")
        ref = stage_media(
            audio_bytes,
            ext=ext,
            run_id=run_id,
            inbox_root=root,
            mime=mime,
        )
        transcript = await self._transcriber.transcribe(audio_bytes, hint_lang=hint_lang)
        return ref, transcript
