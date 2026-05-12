# FILE: src/ai_steward_wiki/tg/pipeline.py
# VERSION: 0.5.0
# START_MODULE_CONTRACT
#   PURPOSE: Coordinator over already-built ingest blocks. Aiogram routers
#            delegate here so handler functions stay framework-thin and the
#            orchestration is unit-testable without a live Telegram bot.
#            v0.1.0 (chunk 20): wires Classifier (Stage-0) → Inbox L2 dedup →
#            WikiRunner (Stage-1a/1b) → OutputDelivery into on_text and
#            on_voice. v0.3.0 (chunk 22, M-TG-DOCUMENT-FULL): on_document
#            performs mime-based routing (DEC-L3) — pdf → pypdf text extract
#            → text pipeline; text/* → utf-8 decode → text pipeline; image/*
#            → PhotoIngestor; else → ru-only reject. L2 dedup on doc_sha256
#            (D-018) and tier-2 PII filename hashing in all log lines.
#            v0.3.3 (aisw-m2m): on_photo and the image-document branch run the
#            wiki pipeline with media_paths (PHOTO_PROMPT_RU) so Claude vision
#            actually processes the image (D-022); on_photo L2-dedups image bytes.
#   SCOPE: MessagePipeline Protocol + Classifier/WikiRunner/OutputDelivery
#          Protocols + WikiRunOutcome dataclass + DefaultPipeline
#          implementation with optional injection (None → ack fallback) +
#          document mime router with size cap, L2 dedup and PII-safe logs.
#   DEPENDS: ai_steward_wiki.classifier.schema (ClassifierResult, Intent,
#            ClassifierError),
#            ai_steward_wiki.inbox.idempotency.IdempotencyService,
#            ai_steward_wiki.inbox.router (RouterDecision, RouterError, RouterIntent),
#            ai_steward_wiki.inbox.staging.MediaRef,
#            ai_steward_wiki.ops.pii.PIIRedactor,
#            ai_steward_wiki.tg.voice.VoiceHandler (optional),
#            ai_steward_wiki.tg.photo.PhotoIngestor (optional),
#            ai_steward_wiki.tg.confirm.ConfirmationService,
#            ai_steward_wiki.tg.bot.TgSender, structlog, pypdf
#   LINKS: M-TG-PIPELINE-CLASSIFIER (chunk 20), M-TG-PIPELINE-STREAMING
#          (chunk 21), M-TG-DOCUMENT-FULL (chunk 22), M-TG-HANDLERS-WIRING
#          (chunk 19), D-016, D-017, D-018, D-022, D-034, DEC-L3, DEC-TPC-1..6
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ACK_TEXT_RU - default ack copy (fallback when classifier/runner/output missing)
#   ACK_VOICE_RU - prefix for voice-transcript reply (fallback)
#   ACK_VOICE_UNAVAILABLE_RU - reply when STT backend (faster-whisper) missing
#   ACK_PHOTO_RU - ack for staged photo
#   ACK_DOC_RU - ack for staged document (legacy fallback when pipeline incomplete)
#   ACK_DOC_UNSUPPORTED_RU - reject for unsupported document mime (DEC-L3)
#   ACK_DOC_PDF_NO_TEXT_RU - hint when PDF has no extractable text
#   ACK_DOC_TOO_LARGE_RU - reject above MAX_DOC_BYTES
#   ACK_DEDUP_RU - reply on L2 dedup hit
#   ACK_CLASSIFY_ERR_RU - safe ack on classifier failure
#   ACK_RUNNER_ERR_RU - safe ack on runner failure
#   MAX_DOC_BYTES - hard cap on incoming document size (25 MB)
#   PDF_MAX_EXTRACT_CHARS - truncate point for pypdf-extracted text
#   PHOTO_PROMPT_RU - synthetic Stage-1 prompt for a caption-less image (D-022)
#   PHOTO_CAPTION_PROMPT_RU - Stage-1 prompt template for an image WITH caption
#   SUPPORTED_IMAGE_MIMES - frozenset of mimes routed to PhotoIngestor
#   ConfirmKeyboardAction - Literal[confirm|correct|cancel]
#   Classifier - Protocol (Stage-0 wrapper, narrow API)
#   Router - Protocol (Inbox-WIKI Stage-1a router wrapper; aisw-dsg)
#   IngestOutcome - frozen dataclass returned by Librarian.ingest (aisw-zd9)
#   Librarian - Protocol (Inbox-WIKI Stage-1b librarian wrapper; aisw-zd9)
#   WikiRunOutcome - frozen dataclass returned by WikiRunner.run
#   WikiRunner - Protocol (Stage-1a/1b wrapper, narrow API)
#   OutputDelivery - Protocol (deliver_output wrapper)
#   MessagePipeline - Protocol for the 5 entry points used by handlers
#   DefaultPipeline - concrete coordinator wiring existing building blocks
#   StreamingDelivery - Protocol for slow-path race+stream wrapper (chunk 21)
#   DefaultStreamingDelivery - default race-and-stream impl over StreamEditor
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.5.0 - aisw-zd9 (Inbox-WIKI Phase-B): Librarian Protocol +
#                IngestOutcome; DefaultPipeline gains an optional `librarian`;
#                in the routable branch a ROUTE/CREATE_WIKI decision (with a
#                wired librarian + output) is executed via librarian.ingest()
#                — resolve/create the target <Domain>-WIKI, move raw, Stage-1b
#                ingest — and the reply (notes + summary, or notes + hint) is
#                delivered via OutputDelivery (ok) or send_message (rejected /
#                run_failed); CLARIFY/REJECT and the no-librarian case keep the
#                Phase-A notes-echo. Phase-A's tg.pipeline.router.delivered log
#                renamed → tg.pipeline.router.decided; new anchors
#                tg.pipeline.route.ingest_dispatched|delivered.
#   PREVIOUS:    v0.4.0 - aisw-dsg (Inbox-WIKI Phase-A): _ROUTABLE_INTENTS +
#                Router Protocol; DefaultPipeline gains an optional `router`;
#                _run_text_pipeline routes WIKI_INGEST/WIKI_QUERY/UNKNOWN through
#                router.route() (Stage-1a in Inbox-WIKI/) and replies with the
#                parsed RouterDecision.notes — legacy flat run kept for the
#                other intents and when no router is wired. New log anchors
#                tg.pipeline.router.dispatched|decided|error.
#   PREVIOUS:    v0.3.7 - aisw-3dr: on_voice accepts ext/mime (default ogg) and
#                forwards them to VoiceHandler.handle so video notes (mp4) and
#                audio files are staged with the right extension, not .ogg.
#   PREVIOUS:    v0.3.6 - aisw-b2x: on_document + on_voice accept an optional
#                caption — _with_caption() prepends "Подпись пользователя: …" to
#                the extracted text / transcript, image-document uses
#                PHOTO_CAPTION_PROMPT_RU; MessagePipeline Protocol updated.
#                v0.3.5 - aisw-t0n: DefaultPipeline gains photo_vision_timeout_s;
#                on_photo + image-document run with that per-call runner timeout
#                (D-022 ~30s vision vs ~300s text); WikiRunner.run + _run_text_pipeline
#                gain timeout_s.
#                v0.3.4 - aisw-ahv (media chunk 3): on_photo accepts an optional
#                caption — when present, PHOTO_CAPTION_PROMPT_RU carries the user
#                request alongside the image (D-022). MessagePipeline.on_photo
#                Protocol gains caption.
#                v0.3.3 - aisw-m2m (media chunk 2): on_photo + image-document
#                branch run the wiki pipeline with media_paths (PHOTO_PROMPT_RU)
#                so Claude vision processes the image instead of a bare ack;
#                on_photo gains L2 dedup on image bytes; WikiRunner.run and
#                _run_text_pipeline gain media_paths (+ skip_l2_dedup) (D-022).
#   PREVIOUS:    v0.3.2 - aisw-zny (media chunk 1): on_voice maps
#                VoiceUnavailableError → ACK_VOICE_UNAVAILABLE_RU + log
#                tg.pipeline.voice.stt_unavailable (graceful STT degradation).
#                v0.3.1 - aisw-x92: streaming slow-path deliver(tg_send=False)
#                — reply already sent via StreamEditor, no duplicate TG message;
#                OutputDelivery.deliver gains tg_send param.
#                v0.3.0 - chunk 22: on_document mime router (DEC-L3) +
#                L2 dedup on doc_sha256 + PII tier-2 filename hash in logs
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import structlog

from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierResult,
    Intent,
)
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, RouterIntent
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import VoiceHandler, VoiceUnavailableError
from ai_steward_wiki.wiki.runner import WikiRunnerError

__all__ = [
    "ACK_CLASSIFY_ERR_RU",
    "ACK_DEDUP_RU",
    "ACK_DOC_PDF_NO_TEXT_RU",
    "ACK_DOC_RU",
    "ACK_DOC_TOO_LARGE_RU",
    "ACK_DOC_UNSUPPORTED_RU",
    "ACK_PHOTO_RU",
    "ACK_RUNNER_ERR_RU",
    "ACK_TEXT_RU",
    "ACK_VOICE_RU",
    "ACK_VOICE_UNAVAILABLE_RU",
    "MAX_DOC_BYTES",
    "PDF_MAX_EXTRACT_CHARS",
    "PHOTO_CAPTION_PROMPT_RU",
    "PHOTO_PROMPT_RU",
    "SUPPORTED_IMAGE_MIMES",
    "Classifier",
    "ConfirmKeyboardAction",
    "DefaultPipeline",
    "DefaultStreamingDelivery",
    "IngestOutcome",
    "Librarian",
    "MessagePipeline",
    "OutputDelivery",
    "Router",
    "StreamingDelivery",
    "WikiRunOutcome",
    "WikiRunner",
]

STREAMING_PLACEHOLDER_RU = "\u23f3 Думаю\u2026"
STREAMING_TIMEOUT_S = 5.0

_log = structlog.get_logger("tg.pipeline")

ACK_TEXT_RU = "Принято."
ACK_VOICE_RU = "Распознано:"
ACK_VOICE_UNAVAILABLE_RU = "Голосовые сообщения сейчас недоступны — напишите текстом."
ACK_PHOTO_RU = "Фото получено."
ACK_DOC_RU = "Файл получен."
ACK_DEDUP_RU = "Уже видел такое сообщение — повторно не запускаю."
ACK_CLASSIFY_ERR_RU = "Не удалось распознать запрос, попробуйте ещё раз."  # noqa: RUF001
ACK_RUNNER_ERR_RU = "Задача заняла слишком много времени, попробуйте позже."
# DEC-L3 reject + edge-case strings (chunk 22 M-TG-DOCUMENT-FULL).
ACK_DOC_UNSUPPORTED_RU = "Этот тип файла пока не поддерживается."
ACK_DOC_PDF_NO_TEXT_RU = "Не вижу текста в PDF. Попробуйте отправить страницу как фото."  # noqa: RUF001
ACK_DOC_TOO_LARGE_RU = "Файл слишком большой (лимит 25 МБ)."

# Document handler limits (chunk 22).
MAX_DOC_BYTES = 25 * 1024 * 1024
PDF_MAX_EXTRACT_CHARS = 50_000
SUPPORTED_IMAGE_MIMES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})

# Synthetic Stage-1 prompt for an image with no caption (D-022 — photo via Claude
# vision). The staged file's directory is granted to the CLI Read tool by the runner.
PHOTO_PROMPT_RU = (
    "Пользователь прислал изображение. Файл: {path}\n"
    "Открой его инструментом Read, опиши содержимое и, если уместно, занеси "  # noqa: RUF001
    "информацию в подходящую WIKI. Кратко ответь, что распознал и что записал."
)
# Same, but the message carried a user caption (D-022 — caption + image content).
PHOTO_CAPTION_PROMPT_RU = (
    "Пользователь прислал изображение. Файл: {path}\n"
    "Подпись пользователя: {caption}\n"
    "Открой изображение инструментом Read, выполни просьбу из подписи и, если "
    "уместно, занеси информацию в подходящую WIKI. Кратко ответь, что сделал."
)

ConfirmKeyboardAction = Literal["confirm", "correct", "cancel"]


def _with_caption(text: str, caption: str | None) -> str:
    """Prepend a user caption (if any) as context for the Stage-1 prompt."""
    if not caption:
        return text
    return f"Подпись пользователя: {caption}\n\n{text}".strip()


# Stage-0 intents that mean "route this somewhere" → handled by the Inbox-WIKI
# Router (Stage-1a) when one is wired (aisw-dsg, Inbox-WIKI Phase-A). The other
# intents (REMINDER, DIGEST, WIKI_LINT, ADMIN) keep their legacy handling.
_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.WIKI_QUERY, Intent.UNKNOWN})


class Classifier(Protocol):
    """Stage-0 Haiku classifier wrapper (D-016 + DEC-TPC-1)."""

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult: ...


class Router(Protocol):
    """Inbox-WIKI Stage-1a router wrapper (aisw-dsg). Runs Claude inside the
    user's Inbox-WIKI/ with prompts/inbox.md and returns a parsed decision."""

    async def route(
        self,
        *,
        text: str,
        telegram_id: int,
        correlation_id: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None = None,
        timeout_s: float | None = None,
    ) -> RouterDecision: ...


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of a Stage-1b librarian ingest into a target WIKI (aisw-zd9)."""

    status: Literal["ok", "rejected", "run_failed"]
    reply: str  # already composed: notes + summary | notes + hint
    run_id: str | None  # set when a Stage-1b run happened (ok | run_failed)
    target_wiki: str | None  # primary name when resolved
    created: bool  # True iff the target WIKI was newly created this turn


class Librarian(Protocol):
    """Inbox-WIKI Stage-1b librarian wrapper (aisw-zd9). Resolves/creates the
    target <Domain>-WIKI from a RouterDecision, moves the raw payload into it,
    and runs Claude there (prompts/wiki.md + a domain overlay) to ingest."""

    async def ingest(
        self,
        decision: RouterDecision,
        *,
        telegram_id: int,
        user_text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None = None,
        correlation_id: str,
    ) -> IngestOutcome: ...


@dataclass(frozen=True, slots=True)
class WikiRunOutcome:
    """Aggregated Stage-1a/1b runner result returned to the pipeline (DEC-TPC-2)."""

    run_id: str
    text: str
    latency_ms: int


class WikiRunner(Protocol):
    """Stage-1a/1b Sonnet runner wrapper (D-017 + DEC-TPC-2 + DEC-TPS-2)."""

    async def run(
        self,
        *,
        text: str,
        owner_telegram_id: int,
        correlation_id: str,
        intent: Intent,
        on_event: Callable[[object], Awaitable[None]] | None = None,
        media_paths: list[Path] | None = None,
        timeout_s: float | None = None,
    ) -> WikiRunOutcome: ...


class StreamingDelivery(Protocol):
    """Slow-path streaming wrapper (D-026 + DEC-TPS-3..5).

    Implementations race the runner against a 5s timer; if the runner
    completes first the fast path is taken and a single deliver is issued.
    Otherwise a placeholder is sent and assistant text streamed in-place.
    """

    async def run_and_deliver(
        self,
        *,
        runner: WikiRunner,
        output: OutputDelivery,
        chat_id: int,
        telegram_id: int,
        text: str,
        intent: Intent,
        correlation_id: str,
    ) -> WikiRunOutcome: ...


class OutputDelivery(Protocol):
    """Hybrid-policy reply delivery wrapper (D-025 + DEC-TPC-1)."""

    async def deliver(
        self,
        *,
        chat_id: int,
        telegram_id: int,
        run_id: str,
        text: str,
        tg_send: bool = True,
    ) -> None: ...


class MessagePipeline(Protocol):
    """Entry points called by aiogram handler functions in handlers.py."""

    async def on_text(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
    ) -> None: ...

    async def on_voice(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        audio_bytes: bytes,
        caption: str | None = None,
        ext: str = "ogg",
        mime: str = "audio/ogg",
    ) -> None: ...

    async def on_photo(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        photo_bytes: bytes,
        mime: str,
        caption: str | None = None,
    ) -> None: ...

    async def on_document(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        mime: str,
        filename: str,
        caption: str | None = None,
    ) -> None: ...

    async def on_confirm_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
    ) -> None: ...


# START_CONTRACT: _extract_pdf_text
#   PURPOSE: Pure-python text extraction from PDF bytes via pypdf.
#   INPUTS: { data: bytes - raw PDF bytes,
#             max_chars: int - cap on returned string length }
#   OUTPUTS: { str - concatenated page text (truncated with suffix if too long),
#              or "" if no text extractable / parse error }
#   SIDE_EFFECTS: none (memory-only parse).
#   LINKS: DEC-L3 (PDF branch), D-022, R-3, R-4 (Discovery)
# END_CONTRACT: _extract_pdf_text
def _extract_pdf_text(data: bytes, *, max_chars: int = PDF_MAX_EXTRACT_CHARS) -> str:
    # START_BLOCK_PDF_EXTRACT
    import io

    import pypdf
    from pypdf.errors import PdfReadError

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, ValueError, OSError, KeyError, IndexError):
        return ""
    except Exception:
        return ""
    text = "\n\n".join(s.strip() for s in pages if s.strip())
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text
    # END_BLOCK_PDF_EXTRACT


class DefaultPipeline:
    """Default coordinator. Composes building blocks; safe-by-default acks."""

    def __init__(
        self,
        *,
        sender: TgSender,
        idempotency: IdempotencyService,
        confirmation: ConfirmationService,
        voice: VoiceHandler | None = None,
        photo: PhotoIngestor | None = None,
        classifier: Classifier | None = None,
        runner: WikiRunner | None = None,
        output: OutputDelivery | None = None,
        streaming: StreamingDelivery | None = None,
        router: Router | None = None,
        librarian: Librarian | None = None,
        pii: PIIRedactor | None = None,
        photo_vision_timeout_s: float | None = None,
    ) -> None:
        self._sender = sender
        self._idem = idempotency
        self._confirm = confirmation
        self._voice = voice
        self._photo = photo
        self._classifier = classifier
        self._runner = runner
        self._output = output
        self._streaming = streaming
        # Inbox-WIKI Stage-1a router (aisw-dsg). When wired, it intercepts
        # routable intents; when None, those fall through to the legacy run.
        self._router = router
        # Inbox-WIKI Stage-1b librarian (aisw-zd9). When wired (with output),
        # ROUTE/CREATE_WIKI decisions are executed (resolve target + move raw +
        # ingest); when None, those fall through to the Phase-A notes-echo.
        self._librarian = librarian
        self._pii = pii or PIIRedactor()
        # D-022: shorter cap for photo→vision runs (vs the default text-turn cap).
        self._photo_vision_timeout_s = photo_vision_timeout_s

    async def _l1_check(self, *, update_id: int, telegram_id: int, kind: str) -> bool:
        """Return True iff the update is new (proceed). Logs on duplicate."""
        is_new = await self._idem.check_update_id(update_id)
        if not is_new:
            _log.info(
                "tg.pipeline.skip.l1_dup",
                update_id=update_id,
                telegram_id=telegram_id,
                kind=kind,
            )
        return is_new

    def _full_pipeline_available(self) -> bool:
        """True iff Classifier+WikiRunner+OutputDelivery are all wired."""
        return (
            self._classifier is not None and self._runner is not None and self._output is not None
        )

    # START_BLOCK_TEXT_PIPELINE
    async def _run_text_pipeline(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None = None,
        skip_l2_dedup: bool = False,
        timeout_s: float | None = None,
    ) -> None:
        """Shared body: L2 dedup → classify → run → deliver. Errors → safe acks.

        `media_paths` are forwarded to the runner (D-022 photo vision).
        `skip_l2_dedup` is set by callers that already deduped on the raw bytes
        (e.g. on_photo) and pass a synthetic, constant `text` here.
        `timeout_s` is a per-call runner timeout override (D-022: shorter for
        photo vision). None → the runner uses its configured default.
        """
        assert self._classifier is not None
        assert self._runner is not None
        assert self._output is not None

        correlation_id = f"tg-{update_id}-{telegram_id}"

        if not skip_l2_dedup:
            sha256, match = await self._idem.check_content(telegram_id, "text", text)
            if match is not None:
                _log.info(
                    "tg.pipeline.inbox.l2_dedup_hit",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    kind="text",
                    sha8=sha256[:8],
                    source=source,
                )
                await self._idem.record_dedup_choice(sha256, telegram_id, "auto_skip")
                await self._sender.send_message(chat_id, ACK_DEDUP_RU)
                return

        _log.info(
            "tg.pipeline.classify.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            chars=len(text),
            source=source,
        )
        try:
            result = await self._classifier.classify(text, correlation_id=correlation_id)
        except ClassifierError:
            _log.exception(
                "tg.pipeline.classify.error",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                error_class="ClassifierError",
            )
            await self._sender.send_message(chat_id, ACK_CLASSIFY_ERR_RU)
            return

        _log.info(
            "tg.pipeline.classify.done",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            intent=result.intent.value,
            confidence=result.confidence,
            latency_ms=result.latency_ms,
        )

        # START_BLOCK_ROUTABLE_BRANCH (aisw-dsg, Inbox-WIKI Phase-A)
        if result.intent in _ROUTABLE_INTENTS and self._router is not None:
            _log.info(
                "tg.pipeline.router.dispatched",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                intent=result.intent.value,
                source=source,
            )
            try:
                decision = await self._router.route(
                    text=text,
                    telegram_id=telegram_id,
                    correlation_id=correlation_id,
                    source=source,
                    media_paths=media_paths,
                    timeout_s=timeout_s,
                )
            except RouterError:
                _log.exception(
                    "tg.pipeline.router.error",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    error_class="RouterError",
                )
                await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
                return
            _log.info(
                "tg.pipeline.router.decided",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                intent=decision.intent.value,
                target_wiki=decision.target_wiki,
                parsed_ok=decision.parsed_ok,
            )
            # Phase-B (aisw-zd9): ROUTE/CREATE_WIKI → resolve target WIKI + move
            # raw + Stage-1b ingest via the Librarian; CLARIFY/REJECT (and the
            # no-librarian case) keep Phase-A's notes-echo behaviour.
            if (
                decision.intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI)
                and self._librarian is not None
                and self._output is not None
            ):
                _log.info(
                    "tg.pipeline.route.ingest_dispatched",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    intent=decision.intent.value,
                    source=source,
                )
                ingest_outcome = await self._librarian.ingest(
                    decision,
                    telegram_id=telegram_id,
                    user_text=text,
                    source=source,
                    media_paths=media_paths,
                    correlation_id=correlation_id,
                )
                _log.info(
                    "tg.pipeline.route.delivered",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    status=ingest_outcome.status,
                    target_wiki=ingest_outcome.target_wiki,
                    created=ingest_outcome.created,
                    run_id=ingest_outcome.run_id,
                )
                if ingest_outcome.status == "ok":
                    await self._output.deliver(
                        chat_id=chat_id,
                        telegram_id=telegram_id,
                        run_id=ingest_outcome.run_id or "",
                        text=ingest_outcome.reply,
                    )
                else:
                    await self._sender.send_message(chat_id, ingest_outcome.reply)
                return
            await self._sender.send_message(chat_id, decision.notes)
            return
        # END_BLOCK_ROUTABLE_BRANCH

        _log.info(
            "tg.pipeline.runner.dispatched",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            intent=result.intent.value,
        )
        try:
            if self._streaming is not None and source == "text":
                outcome = await self._streaming.run_and_deliver(
                    runner=self._runner,
                    output=self._output,
                    chat_id=chat_id,
                    telegram_id=telegram_id,
                    text=text,
                    intent=result.intent,
                    correlation_id=correlation_id,
                )
                _log.info(
                    "tg.pipeline.runner.completed",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    run_id=outcome.run_id,
                    chars=len(outcome.text),
                    latency_ms=outcome.latency_ms,
                )
                _log.info(
                    "tg.pipeline.deliver.sent",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    run_id=outcome.run_id,
                    chars=len(outcome.text or ACK_TEXT_RU),
                    streamed=True,
                )
                return
            outcome = await self._runner.run(
                text=text,
                owner_telegram_id=telegram_id,
                correlation_id=correlation_id,
                intent=result.intent,
                media_paths=media_paths,
                timeout_s=timeout_s,
            )
        except WikiRunnerError:
            _log.exception(
                "tg.pipeline.runner.error",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                error_class="WikiRunnerError",
            )
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return

        _log.info(
            "tg.pipeline.runner.completed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(outcome.text),
            latency_ms=outcome.latency_ms,
        )

        # Empty assistant output → safe fallback (avoid silent zero-byte reply).
        reply_text = outcome.text if outcome.text else ACK_TEXT_RU

        await self._output.deliver(
            chat_id=chat_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            text=reply_text,
        )
        _log.info(
            "tg.pipeline.deliver.sent",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(reply_text),
        )

    # END_BLOCK_TEXT_PIPELINE

    async def on_text(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="text"):
            return
        _log.info(
            "tg.pipeline.text",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            chars=len(text),
        )
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=text,
            source="text",
        )

    async def on_voice(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        audio_bytes: bytes,
        caption: str | None = None,
        ext: str = "ogg",
        mime: str = "audio/ogg",
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="voice"):
            return
        if self._voice is None:
            _log.warning("tg.pipeline.voice.no_handler", telegram_id=telegram_id)
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        run_id = f"voice-{uuid4().hex[:12]}"
        try:
            ref, transcript = await self._voice.handle(
                audio_bytes, run_id=run_id, ext=ext, mime=mime
            )
        except VoiceUnavailableError:
            _log.warning("tg.pipeline.voice.stt_unavailable", telegram_id=telegram_id)
            await self._sender.send_message(chat_id, ACK_VOICE_UNAVAILABLE_RU)
            return
        _log.info(
            "tg.pipeline.voice",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            run_id=run_id,
            sha256=ref.sha256,
            ext=ref.ext,
            lang=transcript.lang,
            chars=len(transcript.text),
            has_caption=bool(caption),
        )
        user_text = _with_caption(transcript.text, caption)
        if not user_text:
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        if not self._full_pipeline_available():
            body = f"{ACK_VOICE_RU}\n{transcript.text}" if transcript.text else ACK_TEXT_RU
            await self._sender.send_message(chat_id, body)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=user_text,
            source="voice",
        )

    async def on_photo(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        photo_bytes: bytes,
        mime: str,
        caption: str | None = None,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="photo"):
            return
        if self._photo is None:
            _log.warning("tg.pipeline.photo.no_handler", telegram_id=telegram_id)
            await self._sender.send_message(chat_id, ACK_PHOTO_RU)
            return
        run_id = f"photo-{uuid4().hex[:12]}"
        ref = self._photo.handle(photo_bytes, run_id=run_id, mime=mime)
        _log.info(
            "tg.pipeline.photo",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            run_id=run_id,
            sha256=ref.sha256,
            ext=ref.ext,
            has_caption=bool(caption),
        )
        # L2 dedup on raw image bytes (D-018) — second copy of the same photo
        # is a re-send, not new content.
        sha256, match = await self._idem.check_content(telegram_id, "file", photo_bytes)
        if match is not None:
            _log.info(
                "tg.pipeline.photo.dedup_hit",
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                sha256_short=sha256[:8],
            )
            await self._idem.record_dedup_choice(sha256, telegram_id, "duplicate_photo")
            await self._sender.send_message(chat_id, ACK_DEDUP_RU)
            return
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_PHOTO_RU)
            return
        prompt = (
            PHOTO_CAPTION_PROMPT_RU.format(path=ref.staging_path, caption=caption)
            if caption
            else PHOTO_PROMPT_RU.format(path=ref.staging_path)
        )
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=prompt,
            source="photo",
            media_paths=[ref.staging_path],
            skip_l2_dedup=True,
            timeout_s=self._photo_vision_timeout_s,
        )

    def _safe_filename_log(self, filename: str) -> str:
        """Return tier-2 PII-hashed filename token for use in log lines."""
        normalized = (filename or "unnamed").lower().strip() or "unnamed"
        return self._pii.hash_token(normalized)

    # START_BLOCK_ON_DOCUMENT
    async def on_document(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        mime: str,
        filename: str,
        caption: str | None = None,
    ) -> None:
        """Mime-routed document ingest (DEC-L3, chunk 22 M-TG-DOCUMENT-FULL).

        Branches:
          - application/pdf      → pypdf text extract → _run_text_pipeline
          - text/*               → utf-8 decode (BOM-tolerant) → _run_text_pipeline
          - image/* (supported)  → PhotoIngestor.handle, ack
          - else                 → ru-only reject, no audit error
        L2 dedup on raw doc bytes runs before branching; filenames are PII-hashed.
        """
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="document"):
            return

        hashed_filename = self._safe_filename_log(filename)
        size = len(doc_bytes)
        mime_lc = mime.lower()

        # Size cap (R-3).
        if size > MAX_DOC_BYTES:
            _log.info(
                "tg.pipeline.document.rejected",
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                hashed_filename=hashed_filename,
                mime=mime_lc,
                size=size,
                reason="too_large",
            )
            await self._sender.send_message(chat_id, ACK_DOC_TOO_LARGE_RU)
            return

        # L2 dedup on raw doc bytes (D-018).
        sha256, match = await self._idem.check_content(telegram_id, "file", doc_bytes)
        if match is not None:
            _log.info(
                "tg.pipeline.document.dedup_hit",
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                hashed_filename=hashed_filename,
                sha256_short=sha256[:8],
                mime=mime_lc,
            )
            await self._idem.record_dedup_choice(sha256, telegram_id, "duplicate_doc")
            await self._sender.send_message(chat_id, ACK_DEDUP_RU)
            return

        _log.info(
            "tg.pipeline.document.received",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            hashed_filename=hashed_filename,
            mime=mime_lc,
            size=size,
            sha256_short=sha256[:8],
            has_caption=bool(caption),
        )

        # Mime dispatch (DEC-L3).
        if mime_lc == "application/pdf":
            await self._handle_pdf_branch(
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                doc_bytes=doc_bytes,
                hashed_filename=hashed_filename,
                caption=caption,
            )
            return
        if mime_lc.startswith("text/"):
            await self._handle_text_branch(
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                doc_bytes=doc_bytes,
                hashed_filename=hashed_filename,
                mime=mime_lc,
                caption=caption,
            )
            return
        if mime_lc in SUPPORTED_IMAGE_MIMES:
            await self._handle_image_branch(
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                doc_bytes=doc_bytes,
                mime=mime_lc,
                hashed_filename=hashed_filename,
                caption=caption,
            )
            return

        _log.info(
            "tg.pipeline.document.rejected",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            hashed_filename=hashed_filename,
            mime=mime_lc,
            reason="unsupported_mime",
        )
        await self._sender.send_message(chat_id, ACK_DOC_UNSUPPORTED_RU)

    async def _handle_pdf_branch(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        hashed_filename: str,
        caption: str | None = None,
    ) -> None:
        extracted = _extract_pdf_text(doc_bytes)
        if not extracted:
            _log.info(
                "tg.pipeline.document.rejected",
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                hashed_filename=hashed_filename,
                mime="application/pdf",
                reason="pdf_no_text",
            )
            await self._sender.send_message(chat_id, ACK_DOC_PDF_NO_TEXT_RU)
            return
        text = _with_caption(extracted, caption)
        _log.info(
            "tg.pipeline.document.routed_text",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            hashed_filename=hashed_filename,
            source="pdf",
            chars=len(text),
            has_caption=bool(caption),
        )
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_DOC_RU)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=text,
            source="document",
        )

    async def _handle_text_branch(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        hashed_filename: str,
        mime: str,
        caption: str | None = None,
    ) -> None:
        try:
            decoded = doc_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            _log.info(
                "tg.pipeline.document.rejected",
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
                hashed_filename=hashed_filename,
                mime=mime,
                reason="text_not_utf8",
            )
            await self._sender.send_message(chat_id, ACK_DOC_UNSUPPORTED_RU)
            return
        text = _with_caption(decoded, caption)
        _log.info(
            "tg.pipeline.document.routed_text",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            hashed_filename=hashed_filename,
            source="text",
            chars=len(text),
            has_caption=bool(caption),
        )
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_DOC_RU)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=text,
            source="document",
        )

    async def _handle_image_branch(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        mime: str,
        hashed_filename: str,
        caption: str | None = None,
    ) -> None:
        if self._photo is None:
            _log.warning(
                "tg.pipeline.document.image_no_handler",
                telegram_id=telegram_id,
                hashed_filename=hashed_filename,
                mime=mime,
            )
            await self._sender.send_message(chat_id, ACK_PHOTO_RU)
            return
        run_id = f"doc-img-{uuid4().hex[:12]}"
        ref = self._photo.handle(doc_bytes, run_id=run_id, mime=mime)
        _log.info(
            "tg.pipeline.document.routed_image",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            hashed_filename=hashed_filename,
            run_id=run_id,
            sha256_short=ref.sha256[:8],
            ext=ref.ext,
            has_caption=bool(caption),
        )
        # doc_bytes were L2-deduped at on_document entry → skip dedup here.
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_PHOTO_RU)
            return
        prompt = (
            PHOTO_CAPTION_PROMPT_RU.format(path=ref.staging_path, caption=caption)
            if caption
            else PHOTO_PROMPT_RU.format(path=ref.staging_path)
        )
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=prompt,
            source="photo",
            media_paths=[ref.staging_path],
            skip_l2_dedup=True,
            timeout_s=self._photo_vision_timeout_s,
        )

    # END_BLOCK_ON_DOCUMENT

    async def on_confirm_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
    ) -> None:
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )


class DefaultStreamingDelivery:
    """Race-and-stream wrapper (DEC-TPS-1..5).

    Behaviour:
      - Launches ``runner.run`` with an on_event callback that captures
        StreamEvent objects into an internal list.
      - Waits up to ``timeout_s`` for the runner to finish. If it does,
        fast path: single ``output.deliver``.
      - Otherwise sends ``placeholder_text``, constructs StreamEditor over the
        placeholder, replays buffered chunks, then live-feeds subsequent ones.
      - On runner completion finalizes the editor and calls ``output.deliver``
        with the final aggregated text.
      - On runner exception finalizes editor (best-effort) and re-raises so
        the parent pipeline maps to ACK_RUNNER_ERR_RU.
    """

    def __init__(
        self,
        *,
        sender: TgSender,
        timeout_s: float = STREAMING_TIMEOUT_S,
        placeholder_text: str = STREAMING_PLACEHOLDER_RU,
        stream_editor_factory: Callable[..., object] | None = None,
    ) -> None:
        self._sender = sender
        self._timeout_s = timeout_s
        self._placeholder_text = placeholder_text
        self._stream_editor_factory = stream_editor_factory

    def _make_editor(self, *, chat_id: int, message_id: int) -> object:
        if self._stream_editor_factory is not None:
            return self._stream_editor_factory(
                sender=self._sender, chat_id=chat_id, first_message_id=message_id
            )
        # Local import to keep stream_edit dependency lazy / test-friendly.
        from ai_steward_wiki.tg.stream_edit import StreamEditor

        return StreamEditor(sender=self._sender, chat_id=chat_id, first_message_id=message_id)

    async def run_and_deliver(
        self,
        *,
        runner: WikiRunner,
        output: OutputDelivery,
        chat_id: int,
        telegram_id: int,
        text: str,
        intent: Intent,
        correlation_id: str,
    ) -> WikiRunOutcome:
        from ai_steward_wiki.wiki.runner import aggregate_text  # lazy import

        buffered: list[object] = []
        live_editor: object | None = None

        async def on_event(ev: object) -> None:
            if live_editor is None:
                buffered.append(ev)
                return
            text_piece = _event_text(ev)
            if text_piece:
                await live_editor.feed(text_piece)  # type: ignore[attr-defined]

        runner_task = asyncio.create_task(
            runner.run(
                text=text,
                owner_telegram_id=telegram_id,
                correlation_id=correlation_id,
                intent=intent,
                on_event=on_event,
            )
        )

        try:
            outcome = await asyncio.wait_for(asyncio.shield(runner_task), timeout=self._timeout_s)
            # Fast path.
            reply_text = outcome.text if outcome.text else ACK_TEXT_RU
            await output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id,
                text=reply_text,
            )
            return outcome
        except TimeoutError:
            pass

        # Slow path: send placeholder + start streaming.
        placeholder = await self._sender.send_message(chat_id, self._placeholder_text)
        live_editor = self._make_editor(chat_id=chat_id, message_id=placeholder.message_id)
        _log.info(
            "tg.pipeline.stream.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            chat_id=chat_id,
            message_id=placeholder.message_id,
        )

        # Replay any chunks that arrived during the race.
        for ev in buffered:
            text_piece = _event_text(ev)
            if text_piece:
                try:
                    await live_editor.feed(text_piece)  # type: ignore[attr-defined]
                    _log.info(
                        "tg.pipeline.stream.chunk",
                        correlation_id=correlation_id,
                        chars=len(text_piece),
                        replayed=True,
                    )
                except Exception as exc:
                    _log.warning(
                        "tg.pipeline.stream.error",
                        correlation_id=correlation_id,
                        error=type(exc).__name__,
                    )

        try:
            outcome = await runner_task
        except Exception:
            # Finalize editor best-effort, then re-raise.
            try:
                await live_editor.finalize()  # type: ignore[attr-defined]
            except Exception as exc:
                _log.warning(
                    "tg.pipeline.stream.error",
                    correlation_id=correlation_id,
                    error=type(exc).__name__,
                    phase="finalize_on_runner_exception",
                )
            raise

        # Build final text from the streamed events (DEC-TPS-4) — fall back
        # to outcome.text or ACK_TEXT_RU if events lacked assistant content.
        from ai_steward_wiki.wiki.streaming import StreamEvent

        final_text = aggregate_text([e for e in buffered if isinstance(e, StreamEvent)])
        if not final_text:
            final_text = outcome.text or ACK_TEXT_RU

        try:
            await live_editor.finalize()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning(
                "tg.pipeline.stream.error",
                correlation_id=correlation_id,
                error=type(exc).__name__,
                phase="finalize",
            )

        # Reply already delivered to TG via the StreamEditor placeholder edits;
        # deliver() here only persists the full text + records the audit row.
        await output.deliver(
            chat_id=chat_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            text=final_text,
            tg_send=False,
        )
        _log.info(
            "tg.pipeline.stream.final",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(final_text),
        )
        return WikiRunOutcome(run_id=outcome.run_id, text=final_text, latency_ms=outcome.latency_ms)


def _event_text(ev: object) -> str:
    """Extract text fragment from a StreamEvent (best effort)."""
    payload = getattr(ev, "payload", None)
    if not isinstance(payload, dict):
        return ""
    if getattr(ev, "type", None) != "assistant_chunk":
        return ""
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "".join(parts)
    delta = payload.get("delta")
    if isinstance(delta, dict):
        t = delta.get("text")
        if isinstance(t, str):
            return t
    t = payload.get("text")
    if isinstance(t, str):
        return t
    return ""
