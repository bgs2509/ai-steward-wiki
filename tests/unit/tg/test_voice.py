"""Unit tests for ai_steward_wiki.tg.voice — D-022 / §9 tech-spec.

Uses a fake whisper model (no faster-whisper download in unit tier).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_steward_wiki.tg.voice import (
    FasterWhisperTranscriber,
    Transcript,
    VoiceHandler,
)


@dataclass
class _Seg:
    text: str


class _FakeInfo:
    def __init__(self, language: str, duration: float) -> None:
        self.language = language
        self.duration = duration


class _FakeModel:
    def __init__(self, *, segments: list[_Seg], language: str, duration: float) -> None:
        self._segments = segments
        self._language = language
        self._duration = duration
        self.calls: list[dict[str, object]] = []

    def transcribe(self, audio, *, language=None, vad_filter=False):
        self.calls.append({"language": language, "vad_filter": vad_filter})
        return iter(self._segments), _FakeInfo(self._language, self._duration)


@pytest.fixture
def fake_model() -> _FakeModel:
    return _FakeModel(
        segments=[_Seg(text="привет "), _Seg(text="мир")],
        language="ru",
        duration=3.0,
    )


@pytest.mark.asyncio
async def test_transcribe_returns_concatenated_text_and_lang(
    fake_model: _FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake_model)

    result = await t.transcribe(b"\x00" * 16, hint_lang="ru")

    assert isinstance(result, Transcript)
    assert result.text == "привет мир"
    assert result.lang == "ru"
    assert result.duration_s == 3.0
    assert result.rtf >= 0.0
    assert result.model.startswith("faster-whisper/")
    assert fake_model.calls[0]["language"] == "ru"


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_ru_on_unsupported_lang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel(segments=[_Seg(text="hi")], language="zh", duration=1.0)
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake)

    result = await t.transcribe(b"x")
    assert result.lang == "ru"  # fallback per D-022


@pytest.mark.asyncio
async def test_transcribe_passes_no_lang_hint_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel(segments=[_Seg(text="ok")], language="en", duration=0.5)
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake)

    await t.transcribe(b"x", hint_lang="fr")
    # Unsupported hint becomes None at the model boundary.
    assert fake.calls[0]["language"] is None


@pytest.mark.asyncio
async def test_voice_handler_stages_and_transcribes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_model: _FakeModel
) -> None:
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake_model)

    handler = VoiceHandler(t, inbox_root=tmp_path / "Inbox-WIKI")
    payload = b"OggS" + b"\x00" * 32
    ref, transcript = await handler.handle(payload, run_id="r1", hint_lang="ru")

    assert ref.staging_path.exists()
    assert ref.staging_path.read_bytes() == payload
    assert ref.ext == "ogg"
    assert transcript.text == "привет мир"


@pytest.mark.asyncio
async def test_voice_handler_per_call_inbox_root_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_model: _FakeModel
) -> None:
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake_model)

    handler = VoiceHandler(t)  # no constructor root
    inbox = tmp_path / "111" / "Inbox-WIKI"
    ref, _ = await handler.handle(b"OggS" + b"\x00" * 8, run_id="r1", inbox_root=inbox)
    assert ref.staging_path.parent == inbox / "raw" / "media" / "_staging"


@pytest.mark.asyncio
async def test_voice_handler_no_root_anywhere_raises(
    monkeypatch: pytest.MonkeyPatch, fake_model: _FakeModel
) -> None:
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake_model)
    with pytest.raises(ValueError, match="inbox_root required"):
        await VoiceHandler(t).handle(b"x", run_id="r1")


@pytest.mark.asyncio
async def test_transcribe_zero_duration_rtf_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel(segments=[], language="ru", duration=0.0)
    t = FasterWhisperTranscriber()
    monkeypatch.setattr(t, "_load_model", lambda: fake)
    result = await t.transcribe(b"x")
    assert result.rtf == 0.0
    assert result.text == ""
