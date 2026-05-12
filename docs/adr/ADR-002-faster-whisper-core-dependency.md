# ADR-002: faster-whisper as a core dependency (not an optional extra)

**Status:** accepted
**Date:** 2026-05-12
**Context:** [D-022](../Spec-WIKI/decisions/D-022-voice-photo-input.md), bd `aisw-zny` (media chunk 1), epic `aisw-hcl`

## Context

`faster-whisper==1.1.0` was declared under `[project.optional-dependencies].stt`, so a plain
`uv sync` did not install it. The runtime (`__main__.py`) also never wired `VoiceHandler` into
`DefaultPipeline`. Net effect: a voice message produced only the generic `ACK_TEXT_RU` ("Принято.")
with a `tg.pipeline.voice.no_handler` warning — the main UX hole D-022 was meant to close was still
open.

When wiring voice in, we must decide how `faster-whisper` is delivered to the runtime environment.

## Options

1. **A — keep `stt` extra, add `uv sync --extra stt` to the deploy (systemd unit / Dockerfile).**
   Pro: dev installs stay slim. Con: one more thing to remember in every deploy path; voice silently
   degrades if forgotten; tests of the voice path don't need the dep (they monkeypatch `_load_model`),
   so the gap is invisible in CI.
2. **B — move `faster-whisper` into core `[project.dependencies]`.** Pro: `uv sync` "just works";
   no deploy-time flag; `mypy`/import resolution consistent everywhere. Con: +~200 MB of transitive
   deps (ctranslate2, onnxruntime, tokenizers, av) even where voice is disabled via
   `AISW_VOICE_ENABLED=false`.
3. **C — A or B + graceful degradation:** on `ImportError(faster_whisper)` raise a domain
   `VoiceUnavailableError`, mapped by the pipeline to a ru message instead of a silent ack.

## Decision

**B + the defensive part of C.**

- `faster-whisper==1.1.0` moves into core `[project.dependencies]`; the `stt` extra is removed.
- `FasterWhisperTranscriber._load_model` still wraps the import and raises `VoiceUnavailableError`
  on `ImportError`; `DefaultPipeline.on_voice` catches it → `ACK_VOICE_UNAVAILABLE_RU` +
  `tg.pipeline.voice.stt_unavailable` log. This is defence-in-depth against a broken/partial install
  or an architecture mismatch — not the primary delivery mechanism.

### Rationale

Voice is a **primary MVP feature** (D-022: "закрыта главная UX-дыра inbox'а — voice работает с MVP"),
not an opt-in add-on. Docker image size is a deploy-time concern, not a correctness concern, and the
service is single-purpose (a Telegram WIKI assistant) — there is no "voice-less" deployment profile
worth optimising for. "`uv sync` works" beats "remember the `--extra` flag in three deploy files".
The `AISW_VOICE_ENABLED` flag still lets an operator disable the feature without uninstalling the dep.

## Consequences

1. `uv sync` (dev and deploy) installs the STT stack; first transcription lazily downloads the
   `small` model (~480 MB) and loads it (~1–2 GB RAM at int8). Acceptable for a voice-first service.
2. `mypy` sees `faster_whisper` as installed-but-untyped → added to `[[tool.mypy.overrides]]`
   `ignore_missing_imports` (module `faster_whisper.*`).
3. The `stt` optional-dependency group no longer exists; any tooling that referenced
   `pip install '.[stt]'` must drop the extra.
4. D-022's RTF ≤ 0.5 bench criterion (on `small` on the target VPS) is unaffected; if it fails,
   `AISW_VOICE_WHISPER_MODEL_SIZE=medium` or a future Whisper-API fallback (separate decision).
