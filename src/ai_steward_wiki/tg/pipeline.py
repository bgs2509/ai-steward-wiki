# FILE: src/ai_steward_wiki/tg/pipeline.py
# VERSION: 0.8.0
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
#            ai_steward_wiki.inbox.route (route_action_to_payload, route_action_from_payload),
#            ai_steward_wiki.inbox.staging.MediaRef,
#            ai_steward_wiki.ops.pii.PIIRedactor,
#            ai_steward_wiki.tg.voice.VoiceHandler (optional),
#            ai_steward_wiki.tg.photo.PhotoIngestor (optional),
#            ai_steward_wiki.tg.confirm (ConfirmationService, PendingConfirmDraft,
#            build_route_confirm_keyboard),
#            ai_steward_wiki.tg.bot.TgSender, ai_steward_wiki.classifier.schema.TimeParseResult,
#            ai_steward_wiki.scheduler.firing.create_reminder_job (lazy import in the
#            reminder confirm callback), apscheduler (AsyncIOScheduler, typing only),
#            sqlalchemy.ext.asyncio (async_sessionmaker, typing only), structlog, pypdf
#   LINKS: M-TG-PIPELINE-CLASSIFIER (chunk 20), M-TG-PIPELINE-STREAMING
#          (chunk 21), M-TG-DOCUMENT-FULL (chunk 22), M-TG-HANDLERS-WIRING
#          (chunk 19), M-INBOX-ROUTE, M-SCHEDULER-FIRING, M-TG-TEXT (D-023 confirm
#          loop), D-010, D-016, D-017, D-018, D-022, D-023, D-034, DEC-L3,
#          DEC-TPC-1..6, aisw-zd9, aisw-e45, aisw-kcz
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
#   ROUTE_CONFIRM_RECAP_ROUTE_RU - recap template for a ROUTE confirm (Phase-C)
#   ROUTE_CONFIRM_RECAP_CREATE_RU - recap template for a CREATE_WIKI confirm (Phase-C)
#   ROUTE_CONFIRM_ACK_RU - short ack sent on confirm before the Stage-1b ingest
#   ROUTE_CONFIRM_CANCELLED_RU - reply on cancel/correct of a route confirm
#   ROUTE_CONFIRM_STALE_RU - reply when a route confirm was already resolved/expired
#   build_route_recap - build the ru recap text for a RouterDecision route confirm
#   REMINDER_CONFIDENCE_THRESHOLD - Stage-0 confidence floor for the reminder fast-path (aisw-kcz)
#   REMINDER_RECAP_RU - recap template for a reminder confirm (aisw-kcz)
#   REMINDER_ACK_RU - ack sent after a reminder is scheduled (aisw-kcz)
#   REMINDER_UNPARSEABLE_RU - reply when the reminder time is ambiguous/unparseable (aisw-kcz)
#   REMINDER_PAST_RU - reply when the reminder names an explicitly-past absolute date (aisw-kcz)
#   REMINDER_RECURRING_RU - reply when recurring-digest phrasing is detected (aisw-kcz)
#   REMINDER_CONFIRM_CANCELLED_RU - reply on cancel of a reminder confirm (aisw-kcz)
#   REMINDER_CONFIRM_STALE_RU - reply when a reminder confirm was already resolved/expired (aisw-kcz)
#   TimeParser - Protocol for the NL-time parser used by the reminder fast-path (aisw-kcz)
#   build_reminder_recap - build the ru recap text for a reminder confirm (aisw-kcz)
#   DIGEST_RECAP_RU - recap template for a digest confirm (aisw-oqq)
#   DIGEST_ACK_RU - ack sent after a digest job is scheduled (aisw-oqq)
#   DIGEST_UNPARSEABLE_RU - reply when the recurrence phrasing is ambiguous/unparseable (aisw-oqq)
#   DIGEST_CONFIRM_CANCELLED_RU - reply on cancel of a digest confirm (aisw-oqq)
#   DIGEST_CONFIRM_STALE_RU - reply when a digest confirm was already resolved/expired (aisw-oqq)
#   RecurrenceParser - Protocol for the NL-recurrence parser used by the digest fast-path (aisw-oqq)
#   humanize_recurrence - short ru rendering of a Recurrence for the digest recap/ack (aisw-oqq)
#   build_digest_recap - build the ru recap text for a digest confirm (aisw-oqq)
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
#   LAST_CHANGE: v0.7.0 - aisw-kcz (Inbox-WIKI Phase-D.a): a Stage-0 intent=reminder
#                fast-path runs BEFORE the routable branch — recurring-digest phrasing
#                gets a ru "not yet" line; otherwise parse_time(prefer_future=True) →
#                escalate→ru clarification / explicitly-past absolute date→ru rejection /
#                else a category='reminder' confirm draft (when_utc, message, lead_time_min,
#                user_tz, correlation_id) + recap via ConfirmationService.request_explicit
#                with the 2-button keyboard; on_confirm_callback dispatches category=='reminder'
#                rows to _handle_reminder_confirm → on 'confirmed' scheduler.firing.create_reminder_job
#                + ack, else ru cancelled/stale notice. DefaultPipeline gains optional
#                time_parser/jobs_session_maker/scheduler/user_tz_lookup/default_user_tz/clock.
#                New anchors tg.pipeline.reminder.* + tg.pipeline.confirm.reminder_dispatched.
#   PREVIOUS:    v0.6.0 - aisw-e45 (Inbox-WIKI Phase-C): a routable ROUTE/CREATE_WIKI
#                decision no longer ingests immediately — _run_text_pipeline builds a
#                route_ingest confirm draft (RouterDecision + user_text + source +
#                media_paths + correlation_id, via inbox.route.route_action_to_payload)
#                and proposes it through ConfirmationService.request_explicit with a
#                2-button keyboard (tg.confirm.build_route_confirm_keyboard);
#                on_confirm_callback dispatches route_ingest rows to _handle_route_confirm
#                which resolves the row and, on 'confirmed', replays the decision through
#                Librarian.ingest (the Phase-B path) + delivers the reply, else sends a ru
#                cancelled/stale notice (the staged raw stays in Inbox per D-022). New
#                anchors tg.pipeline.route.confirm_requested|confirm_executed|
#                confirm_cancelled|confirm_stale and tg.pipeline.confirm.route_dispatched;
#                Phase-B's tg.pipeline.route.ingest_dispatched|delivered are removed.
#   PREVIOUS:    v0.5.0 - aisw-zd9 (Inbox-WIKI Phase-B): Librarian Protocol +
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
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog

from ai_steward_wiki.classifier.recurrence import Recurrence, RecurrenceParseResult
from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierResult,
    Intent,
)
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.inbox.route import route_action_from_payload, route_action_to_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, RouterIntent
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.confirm import (
    ConfirmationService,
    PendingConfirmDraft,
    build_route_confirm_keyboard,
)
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import VoiceHandler, VoiceUnavailableError
from ai_steward_wiki.wiki.runner import WikiRunnerError

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ai_steward_wiki.classifier.schema import TimeParseResult

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
    "DIGEST_ACK_RU",
    "DIGEST_CONFIRM_CANCELLED_RU",
    "DIGEST_CONFIRM_STALE_RU",
    "DIGEST_RECAP_RU",
    "DIGEST_UNPARSEABLE_RU",
    "MAX_DOC_BYTES",
    "PDF_MAX_EXTRACT_CHARS",
    "PHOTO_CAPTION_PROMPT_RU",
    "PHOTO_PROMPT_RU",
    "REMINDER_ACK_RU",
    "REMINDER_CONFIDENCE_THRESHOLD",
    "REMINDER_CONFIRM_CANCELLED_RU",
    "REMINDER_CONFIRM_STALE_RU",
    "REMINDER_PAST_RU",
    "REMINDER_RECAP_RU",
    "REMINDER_RECURRING_RU",
    "REMINDER_UNPARSEABLE_RU",
    "ROUTE_CONFIRM_ACK_RU",
    "ROUTE_CONFIRM_CANCELLED_RU",
    "ROUTE_CONFIRM_RECAP_CREATE_RU",
    "ROUTE_CONFIRM_RECAP_ROUTE_RU",
    "ROUTE_CONFIRM_STALE_RU",
    "SUPPORTED_IMAGE_MIMES",
    "Classifier",
    "ConfirmKeyboardAction",
    "DefaultPipeline",
    "DefaultStreamingDelivery",
    "IngestOutcome",
    "Librarian",
    "MessagePipeline",
    "OutputDelivery",
    "RecurrenceParser",
    "Router",
    "StreamingDelivery",
    "TimeParser",
    "WikiRunOutcome",
    "WikiRunner",
    "build_digest_recap",
    "build_reminder_recap",
    "build_route_recap",
    "humanize_recurrence",
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

# Inbox-WIKI route confirm loop (aisw-e45, Phase-C). A ROUTE/CREATE_WIKI decision
# is proposed via inline buttons; the move+ingest runs in on_confirm_callback.
ROUTE_CONFIRM_RECAP_ROUTE_RU = "Положу это в вики «{target}».\n\n{notes}\n\nПодтверждаешь?"  # noqa: RUF001
ROUTE_CONFIRM_RECAP_CREATE_RU = (
    "Заведу новую вики «{target}» и положу это туда.\n\n{notes}\n\nПодтверждаешь?"  # noqa: RUF001
)
ROUTE_CONFIRM_ACK_RU = "\U0001f4dd Записываю в вики…"
ROUTE_CONFIRM_CANCELLED_RU = "Отменено. Файл остался в Inbox — пришли заново с уточнением."  # noqa: RUF001
ROUTE_CONFIRM_STALE_RU = "Время на подтверждение истекло — пришли заново."

# Reminder fast-path (aisw-kcz, Inbox-WIKI Phase-D.a). A Stage-0 intent=reminder
# above this confidence floor with a parseable future time → an explicit confirm.
REMINDER_CONFIDENCE_THRESHOLD = 0.85
# Heuristic ru keyword set for recurring digests — punt to the digest phase (aisw-19o).
_RECURRING_KEYWORDS = frozenset({"кажд", "ежедневн", "еженедельн", "сводк", "дайджест"})

REMINDER_RECAP_RU = "Поставлю напоминание на {when_local} ({tz}): «{message}». Подтверждаешь?"
REMINDER_ACK_RU = "Готово — напомню {when_local}."
REMINDER_UNPARSEABLE_RU = "Не понял, на когда поставить напоминание — уточни время."  # noqa: RUF001
REMINDER_PAST_RU = "Эта дата уже прошла — назови будущую."
REMINDER_RECURRING_RU = "Регулярные сводки — скоро будет, пока могу только разовые напоминания."
REMINDER_CONFIRM_CANCELLED_RU = "Отменено — напоминание не поставил."
REMINDER_CONFIRM_STALE_RU = "Время на подтверждение истекло — пришли заново."

# Recurring-digest fast-path (aisw-oqq, Inbox-WIKI Phase-D.b.1). Reuses the
# reminder fast-path's recurring-keyword detection: when a recurrence parser is
# wired the phrasing is parsed and a category='digest' explicit confirm proposed.
DIGEST_RECAP_RU = "Буду присылать сводку: {schedule_human}. Подтверждаешь?"
DIGEST_ACK_RU = "Готово — буду присылать сводку: {schedule_human}."
DIGEST_UNPARSEABLE_RU = (
    "Не понял расписание сводки. Скажи, например: «каждый день в 9» или «по будням в 19:00»."  # noqa: RUF001
)
DIGEST_CONFIRM_CANCELLED_RU = "Хорошо, сводку настраивать не буду."
DIGEST_CONFIRM_STALE_RU = "Время на подтверждение истекло — пришли заново."
_WEEKDAY_RU_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")  # noqa: RUF001


def _with_caption(text: str, caption: str | None) -> str:
    """Prepend a user caption (if any) as context for the Stage-1 prompt."""
    if not caption:
        return text
    return f"Подпись пользователя: {caption}\n\n{text}".strip()


def build_route_recap(decision: RouterDecision) -> str:
    """Russian recap text for an Inbox-WIKI route confirm (Phase-C, aisw-e45)."""
    target = decision.target_wiki or "?"
    tmpl = (
        ROUTE_CONFIRM_RECAP_CREATE_RU
        if decision.intent is RouterIntent.CREATE_WIKI
        else ROUTE_CONFIRM_RECAP_ROUTE_RU
    )
    return tmpl.format(target=target, notes=decision.notes)


def build_reminder_recap(*, when_utc: datetime, user_tz: ZoneInfo, message: str) -> str:
    """Russian recap text for a reminder confirm (aisw-kcz). Time shown in user TZ."""
    when_local = when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
    return REMINDER_RECAP_RU.format(when_local=when_local, tz=str(user_tz), message=message)


def humanize_recurrence(rec: Recurrence) -> str:
    """Short Russian rendering of a Recurrence for the digest recap/ack (aisw-oqq)."""
    if rec.kind == "daily":
        return f"каждый день в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (0, 1, 2, 3, 4):
        return f"по будням в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (5, 6):
        return f"по выходным в {rec.time_hhmm}"
    days = ", ".join(_WEEKDAY_RU_SHORT[d] for d in sorted(set(rec.weekdays)))
    return f"по дням ({days}) в {rec.time_hhmm}"


def build_digest_recap(rec: Recurrence) -> str:
    """Russian recap text for a digest confirm (aisw-oqq)."""
    return DIGEST_RECAP_RU.format(schedule_human=humanize_recurrence(rec))


# Stage-0 intents that mean "route this somewhere" → handled by the Inbox-WIKI
# Router (Stage-1a) when one is wired (aisw-dsg, Inbox-WIKI Phase-A). The other
# intents (REMINDER, DIGEST, WIKI_LINT, ADMIN) keep their legacy handling.
_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.WIKI_QUERY, Intent.UNKNOWN})


class Classifier(Protocol):
    """Stage-0 Haiku classifier wrapper (D-016 + DEC-TPC-1)."""

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult: ...


class TimeParser(Protocol):
    """NL-time parser wrapper used by the reminder fast-path (aisw-kcz, D-010)."""

    async def parse_time(
        self,
        text: str,
        *,
        user_tz: ZoneInfo,
        now_utc: datetime,
        prefer_future: bool = False,
        correlation_id: str = "",
    ) -> TimeParseResult: ...


class RecurrenceParser(Protocol):
    """NL-recurrence parser wrapper used by the digest fast-path (aisw-oqq)."""

    def __call__(
        self, text: str, *, user_tz: str, correlation_id: str = ""
    ) -> RecurrenceParseResult: ...


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
        time_parser: TimeParser | None = None,
        recurrence_parser: RecurrenceParser | None = None,
        jobs_session_maker: async_sessionmaker[AsyncSession] | None = None,
        scheduler: AsyncIOScheduler | None = None,
        user_tz_lookup: Callable[[int], str | None] | None = None,
        default_user_tz: str = "Europe/Moscow",
        clock: Callable[[], datetime] | None = None,
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
        # Reminder fast-path (aisw-kcz). All optional: time_parser=None disables
        # the fast-path (REMINDER falls through to the legacy runner); the job is
        # only created on confirm when jobs_session_maker AND scheduler are wired.
        self._time_parser = time_parser
        # Digest fast-path (aisw-oqq): recurrence_parser=None falls back to the
        # legacy "not yet" line; the job is created on confirm only when
        # jobs_session_maker AND scheduler are wired.
        self._recurrence_parser = recurrence_parser
        self._jobs_session_maker = jobs_session_maker
        self._scheduler = scheduler
        self._user_tz_lookup = user_tz_lookup
        self._default_user_tz = default_user_tz
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    def _resolve_user_tz(self, telegram_id: int) -> ZoneInfo:
        """User IANA TZ from the lookup, else the default; never raises."""
        name = (self._user_tz_lookup(telegram_id) if self._user_tz_lookup else None) or (
            self._default_user_tz
        )
        try:
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("Europe/Moscow")

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

        # START_BLOCK_REMINDER_FASTPATH (aisw-kcz, Inbox-WIKI Phase-D.a)
        # A confident Stage-0 intent=reminder is handled BEFORE the Stage-1a
        # router (tech-spec §6 fast-path). When time_parser is not wired, fall
        # through — REMINDER is not a routable intent, so it reaches the legacy
        # runner branch (acceptable degraded behaviour).
        if (
            result.intent is Intent.REMINDER
            and result.confidence >= REMINDER_CONFIDENCE_THRESHOLD
            and self._time_parser is not None
        ):
            await self._handle_reminder_intent(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                distilled_payload=result.distilled_payload,
                correlation_id=correlation_id,
            )
            return
        # END_BLOCK_REMINDER_FASTPATH

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
            # Phase-C (aisw-e45): ROUTE/CREATE_WIKI → propose the move+ingest via
            # an explicit inline-button confirm (persisted as a route_ingest
            # pending row); the actual Stage-1b ingest runs in on_confirm_callback.
            # CLARIFY/REJECT (and the no-librarian / no-output case) keep Phase-A's
            # notes-echo behaviour.
            if (
                decision.intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI)
                and self._librarian is not None
                and self._output is not None
            ):
                payload = route_action_to_payload(
                    decision,
                    user_text=text,
                    source=source,
                    media_paths=media_paths,
                    correlation_id=correlation_id,
                )
                confirm_draft = PendingConfirmDraft(
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                    category="route_ingest",
                    draft=payload,
                    recap_text=build_route_recap(decision),
                )
                rec = await self._confirm.request_explicit(
                    confirm_draft, keyboard_factory=build_route_confirm_keyboard
                )
                _log.info(
                    "tg.pipeline.route.confirm_requested",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    pending_id=rec.pending_id,
                    intent=decision.intent.value,
                    target_wiki=decision.target_wiki,
                    source=source,
                )
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

    # START_BLOCK_REMINDER_INTENT (aisw-kcz, Inbox-WIKI Phase-D.a)
    async def _handle_reminder_intent(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        distilled_payload: dict[str, object],
        correlation_id: str,
    ) -> None:
        """Stage-0 intent=reminder fast-path: parse time → propose an explicit confirm.

        Recurring-digest phrasing gets a ru "not yet" line (→ aisw-19o). Otherwise
        parse_time(prefer_future=True): escalate → ru clarification; an
        explicitly-past absolute date → ru rejection; else a category='reminder'
        confirm draft + recap proposed via ConfirmationService.request_explicit
        with the 2-button keyboard. The jobs.Job row is created only on confirm
        (in _handle_reminder_confirm).
        """
        low = text.lower()
        if any(kw in low for kw in _RECURRING_KEYWORDS):
            await self._handle_digest_intent(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                correlation_id=correlation_id,
            )
            return

        assert self._time_parser is not None  # guarded by the caller
        user_tz = self._resolve_user_tz(telegram_id)
        now_utc = self._clock()
        tp = await self._time_parser.parse_time(
            text,
            user_tz=user_tz,
            now_utc=now_utc,
            prefer_future=True,
            correlation_id=correlation_id,
        )
        _log.info(
            "tg.pipeline.reminder.detected",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            time_source=tp.source,
            escalate=tp.escalate,
        )
        if tp.escalate or tp.when_utc is None:
            await self._sender.send_message(chat_id, REMINDER_UNPARSEABLE_RU)
            _log.info(
                "tg.pipeline.reminder.unparseable",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
            )
            return
        if tp.when_utc <= now_utc:
            # With prefer_future=True a bare past wall-clock time would have rolled
            # forward; still being in the past means an explicit past absolute date.
            await self._sender.send_message(chat_id, REMINDER_PAST_RU)
            _log.info(
                "tg.pipeline.reminder.rejected_past",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
            )
            return

        raw_reminder_text = distilled_payload.get("reminder_text")
        message = (
            raw_reminder_text
            if isinstance(raw_reminder_text, str) and raw_reminder_text.strip()
            else text
        )
        when_iso = tp.when_utc.astimezone(UTC).isoformat()
        draft = {
            "when_utc": when_iso,
            "message": message,
            "lead_time_min": 0,
            "user_tz": str(user_tz),
            "correlation_id": correlation_id,
        }
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="reminder",
            draft=draft,
            recap_text=build_reminder_recap(when_utc=tp.when_utc, user_tz=user_tz, message=message),
        )
        rec = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.reminder.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            when_utc=when_iso,
        )

    # END_BLOCK_REMINDER_INTENT

    # START_BLOCK_DIGEST_INTENT (aisw-oqq, Inbox-WIKI Phase-D.b.1)
    async def _handle_digest_intent(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        correlation_id: str,
    ) -> None:
        """Recurring-digest fast-path: parse recurrence → propose an explicit confirm.

        When no recurrence parser is wired (e.g. a unit pipeline without the
        digest deps) falls back to the legacy "not yet" line. On a parse failure
        a ru clarification; otherwise a category='digest' confirm draft + recap
        proposed via ConfirmationService.request_explicit with the 2-button
        keyboard. The jobs.Job row is created only on confirm.
        """
        if self._recurrence_parser is None:
            await self._sender.send_message(chat_id, REMINDER_RECURRING_RU)
            _log.info(
                "tg.pipeline.reminder.recurring_not_yet",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
            )
            return
        user_tz = self._resolve_user_tz(telegram_id)
        res = self._recurrence_parser(text, user_tz=str(user_tz), correlation_id=correlation_id)
        if res.recurrence is None:
            await self._sender.send_message(chat_id, DIGEST_UNPARSEABLE_RU)
            _log.info(
                "tg.pipeline.digest.unparseable",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                reason=res.reason,
            )
            return
        rec = res.recurrence
        _log.info(
            "tg.pipeline.digest.detected",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            recurrence=rec.model_dump(mode="json"),
        )
        draft = {
            "recurrence": rec.model_dump(mode="json"),
            "wiki_scope": "all",
            "window_hours": 24,
            "user_tz": str(user_tz),
            "correlation_id": correlation_id,
        }
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="digest",
            draft=draft,
            recap_text=build_digest_recap(rec),
        )
        record = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.digest.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=record.pending_id,
        )

    # END_BLOCK_DIGEST_INTENT

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
        pending = await self._confirm.get_pending(pending_id)
        if pending is not None and getattr(pending, "category", None) == "route_ingest":
            await self._handle_route_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
            )
            return
        if pending is not None and getattr(pending, "category", None) == "reminder":
            await self._handle_reminder_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
            )
            return
        if pending is not None and getattr(pending, "category", None) == "digest":
            await self._handle_digest_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
            )
            return
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )

    # START_BLOCK_ROUTE_CONFIRM (aisw-e45, Inbox-WIKI Phase-C)
    async def _handle_route_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
        draft_json: str | None,
    ) -> None:
        """Resolve a route_ingest pending row and execute / cancel / report-stale.

        On 'confirmed' the staged RouterDecision is replayed through
        Librarian.ingest (the same path Phase-B ran inline) and the reply is
        delivered like Phase-B; on 'cancelled'/'corrected' the staged raw is left
        in Inbox (D-022) and a ru notice is sent; on a race-lost resolve (None) a
        ru 'stale' notice is sent. No ingest happens unless status == 'confirmed'.
        """
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm.route_dispatched",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )
        if status is None:
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_STALE_RU)
            _log.info(
                "tg.pipeline.route.confirm_stale",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        if status != "confirmed":  # cancelled / corrected
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_CANCELLED_RU)
            _log.info(
                "tg.pipeline.route.confirm_cancelled",
                telegram_id=telegram_id,
                pending_id=pending_id,
                status=status,
            )
            return

        action_obj = route_action_from_payload(json.loads(draft_json or "{}"))
        correlation_id = action_obj.correlation_id or f"confirm-{pending_id}-{telegram_id}"
        await self._sender.send_message(chat_id, ROUTE_CONFIRM_ACK_RU)
        # route_ingest rows are only created when a librarian + output are wired.
        assert self._librarian is not None
        assert self._output is not None
        media = [Path(p) for p in action_obj.media_paths]
        outcome = await self._librarian.ingest(
            action_obj.decision,
            telegram_id=telegram_id,
            user_text=action_obj.user_text,
            source=action_obj.source,
            media_paths=media or None,
            correlation_id=correlation_id,
        )
        _log.info(
            "tg.pipeline.route.confirm_executed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            status=outcome.status,
            target_wiki=outcome.target_wiki,
            created=outcome.created,
            run_id=outcome.run_id,
        )
        if outcome.status == "ok":
            await self._output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id or "",
                text=outcome.reply,
            )
        else:
            await self._sender.send_message(chat_id, outcome.reply)

    # END_BLOCK_ROUTE_CONFIRM

    # START_BLOCK_REMINDER_CONFIRM (aisw-kcz, Inbox-WIKI Phase-D.a)
    async def _handle_reminder_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
        draft_json: str | None,
    ) -> None:
        """Resolve a reminder pending row; on 'confirmed' create the reminder job.

        Mirrors _handle_route_confirm: race-safe resolve; on a lost race (None) a
        ru 'stale' notice; on cancel a ru 'cancelled' notice; on 'confirmed'
        scheduler.firing.create_reminder_job (jobs.Job row + DateTrigger) + a ru ack.
        """
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm.reminder_dispatched",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )
        if status is None:
            await self._sender.send_message(chat_id, REMINDER_CONFIRM_STALE_RU)
            _log.info(
                "tg.pipeline.reminder.confirm_stale",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        if status != "confirmed":  # cancelled / corrected
            await self._sender.send_message(chat_id, REMINDER_CONFIRM_CANCELLED_RU)
            _log.info(
                "tg.pipeline.reminder.confirm_cancelled",
                telegram_id=telegram_id,
                pending_id=pending_id,
                status=status,
            )
            return

        draft = json.loads(draft_json or "{}")
        when_utc = datetime.fromisoformat(str(draft["when_utc"]))
        message = str(draft.get("message") or "")
        lead = int(draft.get("lead_time_min") or 0)
        try:
            user_tz = ZoneInfo(str(draft.get("user_tz") or self._default_user_tz))
        except Exception:
            user_tz = ZoneInfo("Europe/Moscow")
        correlation_id = str(
            draft.get("correlation_id") or f"reminder-confirm-{pending_id}-{telegram_id}"
        )
        # reminder rows are only created when time_parser is wired; the scheduler +
        # jobs sessionmaker are wired together in __main__ — guard defensively.
        if self._jobs_session_maker is None or self._scheduler is None:
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            _log.error(
                "tg.pipeline.reminder.confirm_misconfigured",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        # Local import: keep the scheduler/firing dependency lazy and test-friendly.
        from ai_steward_wiki.scheduler.firing import create_reminder_job

        async with self._jobs_session_maker() as session:
            job_id = await create_reminder_job(
                session,
                self._scheduler,
                owner_telegram_id=telegram_id,
                chat_id=chat_id,
                when_utc=when_utc,
                message=message,
                lead_time_min=lead,
                correlation_id=correlation_id,
            )
        when_local = when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
        await self._sender.send_message(chat_id, REMINDER_ACK_RU.format(when_local=when_local))
        _log.info(
            "tg.pipeline.reminder.confirm_created",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            job_id=job_id,
            when_utc=str(draft["when_utc"]),
        )

    # END_BLOCK_REMINDER_CONFIRM

    # START_BLOCK_DIGEST_CONFIRM (aisw-oqq, Inbox-WIKI Phase-D.b.1)
    async def _handle_digest_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
        draft_json: str | None,
    ) -> None:
        """Resolve a digest pending row; on 'confirmed' create the digest job.

        Mirrors _handle_reminder_confirm: race-safe resolve; on a lost race a ru
        'stale' notice; on cancel a ru 'cancelled' notice; on 'confirmed'
        scheduler.firing.create_digest_job (jobs.Job row + CronTrigger) + a ru ack.
        """
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm.digest_dispatched",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )
        if status is None:
            await self._sender.send_message(chat_id, DIGEST_CONFIRM_STALE_RU)
            _log.info(
                "tg.pipeline.digest.confirm_stale",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        if status != "confirmed":  # cancelled / corrected
            await self._sender.send_message(chat_id, DIGEST_CONFIRM_CANCELLED_RU)
            _log.info(
                "tg.pipeline.digest.confirm_cancelled",
                telegram_id=telegram_id,
                pending_id=pending_id,
                status=status,
            )
            return

        draft = json.loads(draft_json or "{}")
        rec = Recurrence(**draft["recurrence"])
        window_hours = int(draft.get("window_hours") or 24)
        wiki_scope = str(draft.get("wiki_scope") or "all")
        correlation_id = str(
            draft.get("correlation_id") or f"digest-confirm-{pending_id}-{telegram_id}"
        )
        if self._jobs_session_maker is None or self._scheduler is None:
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            _log.error(
                "tg.pipeline.digest.confirm_misconfigured",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        # Local import: keep the scheduler/firing dependency lazy and test-friendly.
        from ai_steward_wiki.scheduler.firing import create_digest_job

        async with self._jobs_session_maker() as session:
            job_id = await create_digest_job(
                session,
                self._scheduler,
                owner_telegram_id=telegram_id,
                chat_id=chat_id,
                recurrence=rec,
                wiki_scope=wiki_scope,
                window_hours=window_hours,
                correlation_id=correlation_id,
            )
        await self._sender.send_message(
            chat_id, DIGEST_ACK_RU.format(schedule_human=humanize_recurrence(rec))
        )
        _log.info(
            "tg.pipeline.digest.confirm_created",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            job_id=job_id,
        )

    # END_BLOCK_DIGEST_CONFIRM


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
