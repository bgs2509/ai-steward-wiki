# FILE: src/ai_steward_wiki/tg/pipeline.py
# VERSION: 0.15.0
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
#            ai_steward_wiki.inbox.hint_match (score_catalog, is_confident),
#            ai_steward_wiki.inbox.idempotency.IdempotencyService,
#            ai_steward_wiki.inbox.materialize.inbox_wiki_path,
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
#          DEC-TPC-1..6, aisw-zd9, aisw-e45, aisw-kcz, aisw-5sd
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
#   CHAT_REPLY_RU - short conversational reply for intent=chat (aisw-df4, ex-SMALLTALK, aisw-xi8 rename)
#   MAX_DOC_BYTES - hard cap on incoming document size (25 MB)
#   PDF_MAX_EXTRACT_CHARS - truncate point for pypdf-extracted text
#   PHOTO_PROMPT_RU - synthetic Stage-1 prompt for a caption-less image (D-022)
#   PHOTO_CAPTION_PROMPT_RU - Stage-1 prompt template for an image WITH caption
#   ROUTE_CONFIRM_RECAP_ROUTE_RU - recap template for a ROUTE confirm (Phase-C)
#   ROUTE_CONFIRM_RECAP_CREATE_RU - recap template for a CREATE_WIKI confirm (Phase-C)
#   ROUTE_CONFIRM_ACK_RU - short ack sent on confirm before the Stage-1b ingest
#   ROUTE_CONFIRM_CANCELLED_RU - reply on cancel/correct of a route confirm
#   ROUTE_CONFIRM_STALE_RU - reply when a route confirm was already resolved/expired
#   ROUTE_SILENT_ACK_RU - silent auto-route ack carrying a redirect picker (aisw-2ra)
#   ROUTE_SILENT_ACK_NOREDIR_RU - silent auto-route ack with no redirect option (aisw-2ra)
#   ACTIVE_WIKI_DEFAULT_ROUTE_RU - notice when a bare follow-up is default-routed to the sticky WIKI (aisw-0ym)
#   build_route_recap - build the ru recap text for a RouterDecision route confirm
#   CLASSIFIER_CONFIDENCE_THRESHOLD - Stage-0 confidence floor for the reminder fast-path (aisw-kcz); renamed aisw-xi8 (DEC-2)
#   SUBTHRESHOLD_CLARIFY_RU - ru clarify reply for a below-threshold job/admin classification (aisw-xi8, DEC-2/FR-10)
#   ACK_JOB_STUB_RU - Phase-C.1 stub reply for intent=job (aisw-xi8, replaced by Phase-C.2/C.3)
#   JOB_LIST_EMPTY_RU - reply when the owner has no active jobs (aisw-xi8, Phase-C.2, DEC-9)
#   JOB_LIST_HEADER_RU - list header template for job/list (aisw-xi8, Phase-C.2, DEC-9)
#   JOB_NOT_FOUND_RU - reply when a needle matches no owner job (aisw-xi8, Phase-C.2, DEC-9)
#   JOB_CANCEL_RECAP_RU - destructive-confirm recap for job/cancel (aisw-xi8, Phase-C.2, DEC-10)
#   JOB_CANCEL_ACK_RU - ack sent after a job is cancelled (aisw-xi8, Phase-C.2, DEC-9)
#   JOB_RESCHEDULE_RECAP_RU - destructive-confirm recap for job/reschedule (aisw-xi8, Phase-C.2, DEC-10)
#   JOB_RESCHEDULE_ACK_RU - ack sent after a job is rescheduled (aisw-xi8, Phase-C.2, DEC-9)
#   JOB_RESCHEDULE_UNPARSEABLE_RU - reply when the reschedule time/schedule is unparseable (aisw-xi8, Phase-C.2)
#   JOB_CONFIRM_CANCELLED_RU - reply on cancel of a job_cancel confirm (aisw-xi8, Phase-C.2)
#   JOB_CONFIRM_STALE_RU - reply when a job confirm was already resolved/expired (aisw-xi8, Phase-C.2)
#   JOB_PICK_PROMPT_RU - prompt shown alongside build_job_pick_keyboard candidates (aisw-xi8, Phase-C.2, DEC-10)
#   JOB_RECURRING_RECAP_RU - confirm recap for job/create kind=recurring (aisw-xi8, Phase-C.3, DEC-11)
#   JOB_RECURRING_ACK_RU - ack sent after a recurring job is created (aisw-xi8, Phase-C.3, DEC-11)
#   JOB_RECURRING_UNPARSEABLE_RU - reply when the recurring schedule_expr is unparseable (aisw-xi8, Phase-C.3)
#   JOB_CHECKIN_RECAP_RU - confirm recap for job/create kind=check_in (aisw-xi8, Phase-C.3, DEC-11)
#   JOB_CHECKIN_ACK_RU - ack sent after a check-in job is created (aisw-xi8, Phase-C.3, DEC-11)
#   JOB_CHECKIN_UNPARSEABLE_RU - reply when the check-in schedule_expr is unparseable (aisw-xi8, Phase-C.3)
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
#   DIGEST_WIKI_UNKNOWN_RU - reply when a «по <Name>» token does not match any owner WIKI (aisw-269)
#   RecurrenceParser - Protocol for the NL-recurrence parser used by the digest fast-path (aisw-oqq)
#   humanize_recurrence - short ru rendering of a Recurrence for the digest recap/ack (aisw-oqq)
#   build_digest_recap - build the ru recap text for a digest confirm (aisw-oqq; +wiki_scope aisw-269)
#   extract_wiki_names - heuristic WIKI-name extraction for the digest fast-path (aisw-269)
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
#   LAST_CHANGE: v0.15.0 - aisw-xi8 (Phase-C.2, DEC-9/DEC-10): _handle_job's
#                Task-C1.4 stub is REPLACED wholesale — list/cancel/reschedule
#                dispatch on JobSlots.action over scheduler/manage.py
#                (list_owner_jobs/match_jobs_by_needle/cancel_job/
#                reschedule_once/reschedule_recurring). A zero-match needle
#                replies JOB_NOT_FOUND_RU; a single match proposes a
#                category="job_cancel" destructive explicit confirm (cancel AND
#                single-match reschedule share this category — the draft's
#                action field disambiguates the mutation, DEC-10); a >1-match
#                needle proposes category="job_pick" via the new
#                build_job_pick_keyboard (jobpick:<pending_id>:<idx>, tap-IS-
#                confirm, resolved by the new on_jobpick_callback /
#                MessagePipeline.on_jobpick_callback, never through
#                on_confirm_callback). on_confirm_callback gains a job_cancel
#                category branch to _handle_job_confirm. New anchors
#                tg.pipeline.job.list|cancel|reschedule|pick_requested|
#                not_found|confirm_requested|confirm_cancelled|confirm_stale|
#                pick_resolved. action="create" still falls through to
#                ACK_JOB_STUB_RU pending Phase-C.3.
#   PREVIOUS:    v0.14.0 - aisw-df4: intent=smalltalk dispatch. A new SMALLTALK
#                branch (before the reminder/digest fast-paths) replies with a short
#                ru line (SMALLTALK_REPLY_RU) and returns — no time/recurrence
#                parsing, no router, no generic runner. Stops casual chitchat
#                («ты дурак?», «расскажи что-нибудь») from producing
#                tg.pipeline.digest.unparseable or freelancing a WIKI run. New log
#                anchor tg.pipeline.smalltalk.replied.
#   PREVIOUS:    v0.13.0 - aisw-50z (query-gap): WIKI_QUERY removed from
#                _ROUTABLE_INTENTS (now {WIKI_INGEST, UNKNOWN}). A wiki_query is a
#                question about already-stored content, not material to file: it now
#                skips BOTH filing branches (HINT_FASTPATH + ROUTABLE_BRANCH, both
#                gated on the set) and falls through to the generic answer runner
#                (run_and_deliver / runner.run + output.deliver), which runs Claude in
#                the user root with cross-WIKI --add-dir read access and delivers the
#                answer to chat. No new module / protocol / anchor; theme cwd-scoping
#                deferred (would need a WikiRunner-Protocol change). Fixes Cycle-1 e2e
#                "filed instead of answered" (6/7 query__* scenarios).
#   PREVIOUS:    v0.12.0 - aisw-2ra: confident hint fast-path now routes SILENTLY
#                (D-023 'auto') — the confident branch ingests via the new shared
#                _ingest_and_deliver tail (also reused by _handle_route_confirm) and
#                acks with ROUTE_SILENT_ACK_RU + a one-tap redirect picker
#                (build_route_redirect_keyboard / on_wikipick_callback); no other
#                WIKI ⇒ plain auto_ack. New log anchor hint_fastpath.silent_route
#                replaces hint_fastpath.hit. Threshold (is_confident) unchanged.
#                Loader label (FR-2) deferred to aisw-05k.
#   PREVIOUS:    v0.11.0 - aisw-5sd (Inbox-WIKI Phase-E.b): '## Inbox hint' pre-router
#                fast-path — DefaultPipeline gains an optional hint_catalog_resolver;
#                a new HINT-FASTPATH block (between the reminder fast-path and the
#                routable branch) scores the content against the sender's cached hint
#                catalog and, on a confident single match (inbox.hint_match.is_confident),
#                synthesises RouterDecision(ROUTE, target_wiki=<stem>) → the existing
#                Phase-C confirm loop, skipping the heavy Sonnet Router run; miss /
#                ambiguous / empty / disabled ⇒ falls through unchanged. Never routes
#                silently — the user still confirms via the route-confirm keyboard.
#   PREVIOUS:    v0.10.0 - aisw-12t (Inbox-WIKI Phase-E.a): media staging root is now
#                per-sender — DefaultPipeline gains an optional wiki_root; on_voice /
#                on_photo / the image-document branch resolve inbox_wiki_path(telegram_id)
#                and pass it to VoiceHandler/PhotoIngestor.handle(inbox_root=…) so bytes
#                land in <wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging (D-022).
#                wiki_root=None ⇒ the handler falls back to its constructor inbox_root.
#   PREVIOUS:    v0.9.0 - aisw-269 (Inbox-WIKI Phase-D.b.2b): named-subset WIKI in the
#                digest fast-path — new optional owner_wikis_resolver dep; _handle_digest_intent
#                calls extract_wiki_names(text, owner-stems) → on a «по <Name>» token that
#                matches no WIKI a ru clarification (tg.pipeline.digest.wiki_unknown), else the
#                category='digest' confirm draft carries wiki_scope ('all'|list[str]) and the
#                recap/ack name the WIKIs; _handle_digest_confirm passes the list shape through
#                to create_digest_job (no more str() coercion). build_digest_recap +wiki_scope arg.
#   PREVIOUS:    v0.7.0 - aisw-kcz (Inbox-WIKI Phase-D.a): a Stage-0 intent=reminder
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
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from ai_steward_wiki.inbox.hint_match import MAX_FASTPATH_CHARS as HINT_FASTPATH_MAX_CHARS
from ai_steward_wiki.inbox.hint_match import MIN_SCORE as HINT_MIN_SCORE
from ai_steward_wiki.inbox.hint_match import is_confident, score_catalog
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.inbox.materialize import inbox_wiki_path
from ai_steward_wiki.inbox.route import route_action_from_payload, route_action_to_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, RouterIntent
from ai_steward_wiki.logging_events import TG_PIPELINE_DISPATCH
from ai_steward_wiki.logging_setup import traced
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.confirm import (
    ConfirmationService,
    PendingConfirmDraft,
    build_job_pick_keyboard,
    build_route_confirm_keyboard,
    build_route_redirect_keyboard,
)
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import VoiceHandler, VoiceUnavailableError
from ai_steward_wiki.wiki.runner import WikiRunnerError

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ai_steward_wiki.classifier.schema import TimeParseResult
    from ai_steward_wiki.scheduler.manage import OwnerJob
    from ai_steward_wiki.storage.audit.chat_log import ChatTurn

__all__ = [
    "ACK_CLASSIFY_ERR_RU",
    "ACK_DEDUP_RU",
    "ACK_DOC_PDF_NO_TEXT_RU",
    "ACK_DOC_RU",
    "ACK_DOC_TOO_LARGE_RU",
    "ACK_DOC_UNSUPPORTED_RU",
    "ACK_JOB_STUB_RU",
    "ACK_PHOTO_RU",
    "ACK_RUNNER_ERR_RU",
    "ACK_TEXT_RU",
    "ACK_VOICE_RU",
    "ACK_VOICE_UNAVAILABLE_RU",
    "ACTIVE_WIKI_DEFAULT_ROUTE_RU",
    "CHAT_REPLY_RU",
    "CLASSIFIER_CONFIDENCE_THRESHOLD",
    "DIGEST_ACK_RU",
    "DIGEST_CONFIRM_CANCELLED_RU",
    "DIGEST_CONFIRM_STALE_RU",
    "DIGEST_RECAP_RU",
    "DIGEST_UNPARSEABLE_RU",
    "DIGEST_WIKI_UNKNOWN_RU",
    "JOB_CANCEL_ACK_RU",
    "JOB_CANCEL_RECAP_RU",
    "JOB_CHECKIN_ACK_RU",
    "JOB_CHECKIN_RECAP_RU",
    "JOB_CHECKIN_UNPARSEABLE_RU",
    "JOB_CONFIRM_CANCELLED_RU",
    "JOB_CONFIRM_STALE_RU",
    "JOB_LIST_EMPTY_RU",
    "JOB_LIST_HEADER_RU",
    "JOB_NOT_FOUND_RU",
    "JOB_PICK_PROMPT_RU",
    "JOB_RECURRING_ACK_RU",
    "JOB_RECURRING_RECAP_RU",
    "JOB_RECURRING_UNPARSEABLE_RU",
    "JOB_RESCHEDULE_ACK_RU",
    "JOB_RESCHEDULE_RECAP_RU",
    "JOB_RESCHEDULE_UNPARSEABLE_RU",
    "MAX_DOC_BYTES",
    "PDF_MAX_EXTRACT_CHARS",
    "PHOTO_CAPTION_PROMPT_RU",
    "PHOTO_PROMPT_RU",
    "REMINDER_ACK_RU",
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
    "ROUTE_SILENT_ACK_NOREDIR_RU",
    "ROUTE_SILENT_ACK_RU",
    "SUBTHRESHOLD_CLARIFY_RU",
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
    "extract_wiki_names",
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
# aisw-df4: conversational chitchat (greetings, banter, "расскажи что-нибудь",
# "ты дурак?") gets a short friendly reply — never filed, scheduled, or run
# through a WIKI. A single canned ru line keeps the path deterministic (no LLM
# round-trip) and nudges the user back toward the WIKI capabilities.
CHAT_REPLY_RU = (
    "Я на связи. 🙂 Я веду твои вики-заметки: сохраняю материалы, отвечаю по ним, "
    "ставлю напоминания и присылаю сводки. Что занесём или о чём напомнить?"  # noqa: RUF001
)
# DEC-L3 reject + edge-case strings (chunk 22 M-TG-DOCUMENT-FULL).
ACK_DOC_UNSUPPORTED_RU = "Этот тип файла пока не поддерживается."
# aisw-aca: intent=admin has no real handler — it used to fall into the generic
# root runner (Write access to the whole user workspace), which freelance-created
# malformed WIKIs from misclassified "создай X". Reply safely instead of running.
ACK_ADMIN_RU = "Админ-действия пока не поддерживаю. Если хочешь что-то сохранить — пришли материал (текст, файл или фото), и я предложу, куда его занести."  # noqa: RUF001
# aisw-xi8 (DEC-2, FR-10): a below-threshold job/admin classification MUST NOT
# reach the write-capable generic root runner — structural guarantee, not just
# a UX nicety (kills defect class #78/#96 by construction).
SUBTHRESHOLD_CLARIFY_RU = "Не уверен, что правильно понял — уточни, пожалуйста, что нужно сделать."  # noqa: RUF001
# aisw-xi8 (Phase-C.1 stub — see the JOB_DISPATCH block's note below; REPLACED
# by Phase-C.2/C.3's real job handlers).
ACK_JOB_STUB_RU = "Понял, но эта функция ещё дорабатывается."
# aisw-xi8 (Phase-C.2, DEC-9/DEC-10): job list/cancel/reschedule + needle
# disambiguation + destructive confirm reply strings.
JOB_LIST_EMPTY_RU = "У тебя нет активных задач."  # noqa: RUF001
JOB_LIST_HEADER_RU = "Твои задачи:\n{items}"
JOB_NOT_FOUND_RU = "Не нашёл подходящую задачу.\n{list}"  # noqa: RUF001
JOB_CANCEL_RECAP_RU = "Отменить «{rendered}»?"
JOB_CANCEL_ACK_RU = "Отменил."
JOB_RESCHEDULE_RECAP_RU = "Перенести «{rendered}» на {when}?"
JOB_RESCHEDULE_ACK_RU = "Перенёс на {when}."
JOB_RESCHEDULE_UNPARSEABLE_RU = "Не понял, на какое время перенести — уточни."  # noqa: RUF001
JOB_CONFIRM_CANCELLED_RU = "Хорошо, не буду."
JOB_CONFIRM_STALE_RU = "Время на подтверждение истекло — повтори запрос."
JOB_PICK_PROMPT_RU = "Нашёл несколько похожих задач — какую?"
JOB_RECURRING_RECAP_RU = "Буду напоминать {schedule}: «{message}». Подтверждаешь?"
JOB_RECURRING_ACK_RU = "Готово — буду напоминать {schedule}."
JOB_RECURRING_UNPARSEABLE_RU = "Не понял расписание. Скажи, например: «каждый день в 8»."  # noqa: RUF001
JOB_CHECKIN_RECAP_RU = "Буду спрашивать {schedule}: «{topic}». Подтверждаешь?"
JOB_CHECKIN_ACK_RU = "Готово — буду спрашивать {schedule}."
JOB_CHECKIN_UNPARSEABLE_RU = "Не понял расписание. Скажи, например: «каждый вечер в 21»."  # noqa: RUF001
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

# aisw-2ra: silent auto-route ack (D-023 'auto' level). Shown AFTER a confident
# hint match has been ingested without a confirm step. The redirect variant
# carries a picker of the owner's OTHER WIKIs (build_route_redirect_keyboard); the
# no-redirect variant is used when there is no other WIKI to redirect into.
ROUTE_SILENT_ACK_RU = "✅ Записал в {wiki}. Не туда? Перенесу:"  # noqa: RUF001
ROUTE_SILENT_ACK_NOREDIR_RU = "✅ Записал в {wiki}."

# aisw-0ym: shown when a bare follow-up is default-routed into the user's sticky
# last-active WIKI (instead of cold-rejecting it).
ACTIVE_WIKI_DEFAULT_ROUTE_RU = "Похоже, это продолжение разговора — отправлю в {wiki}."

# Reminder fast-path (aisw-kcz, Inbox-WIKI Phase-D.a). A Stage-0 intent=reminder
# above this confidence floor with a parseable future time → an explicit confirm.
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.85

REMINDER_RECAP_RU = "Поставлю напоминание на {when_local} ({tz}): «{message}». Подтверждаешь?"
REMINDER_ACK_RU = "Готово — напомню {when_local}."
# #3 aisw-5wr: a pre-reminder ("an hour before") sends a second, earlier ping.
REMINDER_ACK_LEAD_RU = "Готово — напомню {when_local}, и ещё раз заранее."
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
# aisw-269: a name-shaped token after «по …» that does not match any of the
# owner's *-WIKI/ dir-stems → ask which WIKIs to use (don't silently widen to 'all').
DIGEST_WIKI_UNKNOWN_RU = (
    "Не нашёл такие WIKI. У тебя есть: {known}. Уточни, по каким делать сводку — "  # noqa: RUF001
    "или скажи «по всем»."
)
_WEEKDAY_RU_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")  # noqa: RUF001

# HH:MM («7:30», «07.30») or a bare hour after в/на («на 7», «в 9 утра»). Kept as
# a parameter validator (FR-3) — promoted from the deleted digest-control
# fast-path (#2/aisw-578) to Phase-C.2's reschedule-time-only merge path.
_DIGEST_HHMM_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")
_DIGEST_BARE_HOUR_RE = re.compile(
    r"(?:\bв\b|\bна\b)\s*(\d{1,2})(?!\s*[:.]?\d)",  # noqa: RUF001
    re.IGNORECASE,
)


def _extract_hhmm(text: str) -> str | None:
    """Extract a zero-padded HH:MM from free text, or None.

    Accepts «7:30» / «07.30» and a bare hour after в/на («на 7» → «07:00»).
    """
    m = _DIGEST_HHMM_RE.search(text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m = _DIGEST_BARE_HOUR_RE.search(text)
        if not m:
            return None
        hh, mm = int(m.group(1)), 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


# #3 aisw-5wr: a pre-reminder phrase ("an hour before") -> lead offset in minutes.
_LEAD_RE = re.compile(
    r"за\s+(?:(\d+)\s+)?(полчаса|минут\w*|мин|час\w*|сут\w*|дн\w*|день|дня)\s+до",
    re.IGNORECASE,
)


def _extract_lead_minutes(text: str) -> int:
    """Minutes for an «за N <unit> до» pre-reminder, or 0 if none.

    «за час до» → 60, «за 30 минут до» → 30, «за полчаса до» → 30,
    «за 2 дня до» → 2880. A bare unit with no number means 1.
    """
    m = _LEAD_RE.search(text)
    if not m:
        return 0
    unit = m.group(2).lower()
    if unit.startswith("полчаса"):
        return 30
    n = int(m.group(1)) if m.group(1) else 1
    if unit.startswith(("мин",)):
        return n
    if unit.startswith("час"):
        return n * 60
    if unit.startswith(("сут", "дн")) or unit in ("день", "дня"):
        return n * 1440
    return 0


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
    if rec.kind == "monthly":
        return f"{rec.day_of_month} числа каждого месяца в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (0, 1, 2, 3, 4):
        return f"по будням в {rec.time_hhmm}"
    if tuple(sorted(rec.weekdays)) == (5, 6):
        return f"по выходным в {rec.time_hhmm}"
    days = ", ".join(_WEEKDAY_RU_SHORT[d] for d in sorted(set(rec.weekdays)))
    return f"по дням ({days}) в {rec.time_hhmm}"


def build_digest_recap(rec: Recurrence, wiki_scope: str | list[str] = "all") -> str:
    """Russian recap text for a digest confirm (aisw-oqq; named subset aisw-269)."""
    schedule = humanize_recurrence(rec)
    if isinstance(wiki_scope, list) and wiki_scope:
        schedule = f"{schedule} по WIKI: {', '.join(wiki_scope)}"
    return DIGEST_RECAP_RU.format(schedule_human=schedule)


# aisw-269 — name-shaped tokens for the «по <X>» fallthrough check.
_PO_NAME_RE = re.compile(r"\bпо\s+([^\W\d_][\w-]*)", re.UNICODE)  # noqa: RUF001
_WORD_TOKEN_RE = re.compile(r"[^\W\d_][\w-]*", re.UNICODE)


def extract_wiki_names(
    text: str, owner_wiki_stems: Sequence[str]
) -> list[str] | Literal["all"] | None:
    """Heuristic WIKI-name extraction for the digest fast-path (aisw-269).

    Whole-token, case-insensitive intersection of ``text`` with the owner's
    ``*-WIKI/`` dir-stems. Returns the matched stems (original casing,
    first-seen order) when any resolved; ``"all"`` when no WIKI name is
    mentioned; ``None`` when a capitalised token follows «по …» but does not
    match any stem (the caller should ask for clarification rather than
    silently widen to ``"all"``).
    """
    stems = {s.lower(): s for s in owner_wiki_stems}
    seen: dict[str, None] = {}
    for tok in _WORD_TOKEN_RE.findall(text):
        original = stems.get(tok.lower())
        if original is not None:
            seen.setdefault(original, None)
    if seen:
        return list(seen)
    m = _PO_NAME_RE.search(text)
    if m is not None:
        cand = m.group(1)
        if cand.lower() not in stems and cand[:1].isupper():
            return None
    return "all"


def _is_routable(intent: Intent, action: str | None) -> bool:
    """DEC-3: routable ⇔ UNKNOWN, or WIKI with action∈{ingest,catalog} or missing.

    wiki/query and wiki/lint answer in chat (generic runner) — never filed.
    Replaces the old static `_ROUTABLE_INTENTS` frozenset (was {WIKI_INGEST,
    UNKNOWN}) with a predicate over (intent, action), since a single WIKI intent
    now covers 4 different downstream behaviours.
    """
    if intent is Intent.UNKNOWN:
        return True
    if intent is Intent.WIKI:
        return action in ("ingest", "catalog", None)
    return False


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
        recent_window: Sequence[ChatTurn] | None = None,
    ) -> RouterDecision: ...


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of a Stage-1b librarian ingest into a target WIKI (aisw-zd9)."""

    status: Literal["ok", "rejected", "run_failed", "partial"]
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
        action: str | None = None,
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
        action: str | None = None,
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


class ChatLogPort(Protocol):
    """D-033 conversation buffer port (aisw-kml). Writes in/out turns and reads
    back the recent last-20/24h window. The dispatcher stays stateless — it is
    FED the buffer, it does not own state."""

    async def write_in(
        self, *, telegram_id: int, chat_id: int, text: str, kind: str = "text"
    ) -> None: ...

    async def write_out(
        self, *, telegram_id: int, chat_id: int, text: str, kind: str = "text"
    ) -> None: ...

    async def read_recent_window(self, telegram_id: int) -> list[ChatTurn]: ...


class ActiveWikiPort(Protocol):
    """Sticky last-active-<Domain>-WIKI pointer port (aisw-0ym). Set on a
    successful route/ingest; read each turn (TTL-guarded) to default-route a
    bare follow-up instead of cold-rejecting it. Read from sessions.db each
    turn — the dispatcher stays stateless."""

    async def set_active(self, telegram_id: int, wiki_name: str) -> None: ...

    async def get_active(self, telegram_id: int) -> str | None: ...


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

    async def on_wikipick_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        wiki_index: int,
    ) -> None: ...

    async def on_jobpick_callback(
        self, *, telegram_id: int, chat_id: int, pending_id: int, job_index: int
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
        owner_wikis_resolver: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]] | None = None,
        hint_catalog_resolver: Callable[[int], Awaitable[Mapping[str, str]]] | None = None,
        jobs_session_maker: async_sessionmaker[AsyncSession] | None = None,
        scheduler: AsyncIOScheduler | None = None,
        user_tz_lookup: Callable[[int], str | None] | None = None,
        default_user_tz: str = "Europe/Moscow",
        clock: Callable[[], datetime] | None = None,
        wiki_root: Path | None = None,
        chat_log: ChatLogPort | None = None,
        active_wiki: ActiveWikiPort | None = None,
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
        # aisw-269: owner→[(wiki_stem, path)] resolver (same shape firing uses);
        # None ⇒ named-subset WIKI selection degrades to wiki_scope='all'.
        self._owner_wikis_resolver = owner_wikis_resolver
        # aisw-5sd (Phase-E.b): telegram_id → {wiki_stem: '## Inbox hint' text}.
        # None ⇒ the pre-router hint fast-path is disabled (all routable intents
        # go straight to the heavy Sonnet Router).
        self._hint_catalog_resolver = hint_catalog_resolver
        self._jobs_session_maker = jobs_session_maker
        self._scheduler = scheduler
        self._user_tz_lookup = user_tz_lookup
        self._default_user_tz = default_user_tz
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        # aisw-12t (Phase-E.a): per-sender media staging root. None ⇒ VoiceHandler /
        # PhotoIngestor fall back to the inbox_root they were constructed with.
        self._wiki_root = wiki_root
        # aisw-kml (D-033): conversation buffer. None ⇒ no chat_log persistence /
        # no recent-history injection (back-compat; all legacy tests pass it None).
        self._chat_log = chat_log
        # aisw-0ym: sticky last-active-WIKI pointer. None ⇒ no sticky default-route
        # (back-compat; a bare follow-up still cold-rejects as before).
        self._active_wiki = active_wiki

    def _inbox_root_for(self, telegram_id: int) -> Path | None:
        """Per-sender Inbox-WIKI dir (media-staging root, D-022), or None if no wiki_root."""
        if self._wiki_root is None:
            return None
        return inbox_wiki_path(telegram_id, wiki_root=self._wiki_root)

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

    # START_BLOCK_CHAT_LOG_HELPERS (aisw-kml, D-033)
    async def _chatlog_in(self, *, telegram_id: int, chat_id: int, text: str, kind: str) -> None:
        """Persist one inbound user turn (best-effort; never blocks the turn)."""
        if self._chat_log is None:
            return
        try:
            await self._chat_log.write_in(
                telegram_id=telegram_id, chat_id=chat_id, text=text, kind=kind
            )
        except Exception as exc:  # chat_log is auxiliary — a failure must not kill the reply
            _log.warning(
                "tg.pipeline.chat_log.write_in_failed",
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
            )

    async def _chatlog_out(self, *, telegram_id: int, chat_id: int, text: str) -> None:
        """Persist one final outbound bot reply (best-effort; never blocks the turn)."""
        if self._chat_log is None or not text:
            return
        try:
            await self._chat_log.write_out(telegram_id=telegram_id, chat_id=chat_id, text=text)
        except Exception as exc:
            _log.warning(
                "tg.pipeline.chat_log.write_out_failed",
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
            )

    async def _chatlog_window(self, telegram_id: int) -> list[ChatTurn] | None:
        """Read the D-033 recent window (best-effort); None when no chat_log wired."""
        if self._chat_log is None:
            return None
        try:
            return await self._chat_log.read_recent_window(telegram_id)
        except Exception as exc:
            _log.warning(
                "tg.pipeline.chat_log.read_failed",
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
            )
            return None

    # END_BLOCK_CHAT_LOG_HELPERS

    # START_BLOCK_ACTIVE_WIKI_HELPERS (aisw-0ym)
    async def _active_wiki_get(self, telegram_id: int) -> str | None:
        """Read the fresh sticky pointer (best-effort); None when unset/stale/unwired."""
        if self._active_wiki is None:
            return None
        try:
            return await self._active_wiki.get_active(telegram_id)
        except Exception as exc:
            _log.warning(
                "tg.pipeline.active_wiki.read_failed",
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
            )
            return None

    async def _active_wiki_set(self, telegram_id: int, wiki_name: str | None) -> None:
        """Upsert the sticky pointer on a successful route/ingest (best-effort)."""
        if self._active_wiki is None or not wiki_name:
            return
        try:
            await self._active_wiki.set_active(telegram_id, wiki_name)
        except Exception as exc:
            _log.warning(
                "tg.pipeline.active_wiki.write_failed",
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
            )

    # END_BLOCK_ACTIVE_WIKI_HELPERS

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

        # D-033 (aisw-kml): persist the inbound turn AFTER STT/OCR + dedup gate
        # (the resolved `text` is already post-transcription/post-extraction).
        # `source` is one of text|voice|document|photo → chat_log.kind.
        await self._chatlog_in(telegram_id=telegram_id, chat_id=chat_id, text=text, kind=source)
        recent_window = await self._chatlog_window(telegram_id)

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

        # START_BLOCK_SUBTHRESHOLD_GATE (aisw-xi8, DEC-2, FR-10)
        # Structural double-guarantee: intent∈{JOB, ADMIN} below the confidence
        # floor gets a deterministic ru clarification and RETURNS — it can never
        # fall through to any handler, confirm draft, or the generic runner. All
        # other intents (non-destructive) proceed normally even at low confidence.
        if result.confidence < CLASSIFIER_CONFIDENCE_THRESHOLD and result.intent in (
            Intent.JOB,
            Intent.ADMIN,
        ):
            _log.info(
                "tg.pipeline.subthreshold.clarify",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                intent=result.intent.value,
                confidence=result.confidence,
            )
            await self._sender.send_message(chat_id, SUBTHRESHOLD_CLARIFY_RU)
            await self._chatlog_out(
                telegram_id=telegram_id, chat_id=chat_id, text=SUBTHRESHOLD_CLARIFY_RU
            )
            return
        # END_BLOCK_SUBTHRESHOLD_GATE

        # START_BLOCK_INTENT_DISPATCH (aisw-xi8, DEC-1 — flat 6-intent switch)
        if result.intent is Intent.CHAT:
            await self._handle_chat(
                telegram_id=telegram_id, chat_id=chat_id, correlation_id=correlation_id
            )
            return
        if result.intent is Intent.JOB:
            await self._handle_job(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                distilled_payload=result.distilled_payload,
                correlation_id=correlation_id,
            )
            return
        if result.intent is Intent.ADMIN:
            await self._handle_admin(
                telegram_id=telegram_id, chat_id=chat_id, correlation_id=correlation_id
            )
            return
        if result.intent is Intent.WIKI:
            await self._handle_wiki(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                source=source,
                media_paths=media_paths,
                distilled_payload=result.distilled_payload,
                correlation_id=correlation_id,
                recent_window=recent_window,
                timeout_s=timeout_s,
            )
            return
        if result.intent is Intent.WEB:
            await self._handle_web(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                source=source,
                media_paths=media_paths,
                correlation_id=correlation_id,
                timeout_s=timeout_s,
            )
            return
        # Intent.UNKNOWN — the only remaining member.
        await self._handle_unknown(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            source=source,
            media_paths=media_paths,
            correlation_id=correlation_id,
            recent_window=recent_window,
            timeout_s=timeout_s,
        )
        # END_BLOCK_INTENT_DISPATCH

    # END_BLOCK_TEXT_PIPELINE

    # START_BLOCK_JOB_DISPATCH (aisw-xi8; Phase-C.2 REPLACES the Task-C1.4 stub)
    async def _handle_job(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        distilled_payload: dict[str, object],
        correlation_id: str,
    ) -> None:
        from ai_steward_wiki.classifier.schema import JobSlots, parse_slots

        slots = parse_slots(JobSlots, distilled_payload)
        if slots.action == "list":
            await self._handle_job_list(
                telegram_id=telegram_id, chat_id=chat_id, correlation_id=correlation_id
            )
            return
        if slots.action == "cancel":
            await self._handle_job_cancel(
                telegram_id=telegram_id,
                chat_id=chat_id,
                needle=slots.needle,
                correlation_id=correlation_id,
            )
            return
        if slots.action == "reschedule":
            await self._handle_job_reschedule(
                telegram_id=telegram_id,
                chat_id=chat_id,
                needle=slots.needle,
                time_expr=slots.time_expr,
                schedule_expr=slots.schedule_expr,
                correlation_id=correlation_id,
            )
            return
        await self._handle_job_create(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            slots=slots,
            correlation_id=correlation_id,
        )

    async def _handle_job_list(
        self, *, telegram_id: int, chat_id: int, correlation_id: str
    ) -> None:
        if self._jobs_session_maker is None or self._scheduler is None:
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return
        from ai_steward_wiki.scheduler.manage import list_owner_jobs

        user_tz = str(self._resolve_user_tz(telegram_id))
        async with self._jobs_session_maker() as session:
            jobs = await list_owner_jobs(session, telegram_id, user_tz=user_tz)
        if not jobs:
            await self._sender.send_message(chat_id, JOB_LIST_EMPTY_RU)
        else:
            items = "\n".join(f"- {j.rendered}" for j in jobs)
            await self._sender.send_message(chat_id, JOB_LIST_HEADER_RU.format(items=items))
        _log.info(
            "tg.pipeline.job.list",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            count=len(jobs),
        )

    async def _handle_job_cancel(
        self, *, telegram_id: int, chat_id: int, needle: str, correlation_id: str
    ) -> None:
        matches = await self._match_owner_jobs(telegram_id, needle)
        if matches is None:  # misconfigured
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return
        if not matches:
            await self._send_not_found(
                telegram_id=telegram_id,
                chat_id=chat_id,
                needle=needle,
                correlation_id=correlation_id,
            )
            return
        if len(matches) > 1:
            await self._request_job_pick(
                telegram_id=telegram_id,
                chat_id=chat_id,
                action="cancel",
                candidates=matches,
                correlation_id=correlation_id,
            )
            return
        job = matches[0]
        draft = {
            "job_id": job.id,
            "job_kind": job.kind,
            "action": "cancel",
            "correlation_id": correlation_id,
        }
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="job_cancel",
            draft=draft,
            recap_text=JOB_CANCEL_RECAP_RU.format(rendered=job.rendered),
        )
        rec = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.job.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            category="job_cancel",
            action="cancel",
        )

    async def _handle_job_reschedule(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        needle: str,
        time_expr: str,
        schedule_expr: str,
        correlation_id: str,
    ) -> None:
        matches = await self._match_owner_jobs(telegram_id, needle)
        if matches is None:
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return
        if not matches:
            await self._send_not_found(
                telegram_id=telegram_id,
                chat_id=chat_id,
                needle=needle,
                correlation_id=correlation_id,
            )
            return
        if len(matches) > 1:
            await self._request_job_pick(
                telegram_id=telegram_id,
                chat_id=chat_id,
                action="reschedule",
                candidates=matches,
                correlation_id=correlation_id,
                time_expr=time_expr,
                schedule_expr=schedule_expr,
            )
            return
        await self._build_reschedule_confirm(
            telegram_id=telegram_id,
            chat_id=chat_id,
            job=matches[0],
            time_expr=time_expr,
            schedule_expr=schedule_expr,
            correlation_id=correlation_id,
        )

    async def _match_owner_jobs(self, telegram_id: int, needle: str) -> list[OwnerJob] | None:
        """Returns None if scheduler/jobs_session_maker are unwired."""
        if self._jobs_session_maker is None or self._scheduler is None:
            return None
        from ai_steward_wiki.scheduler.manage import list_owner_jobs, match_jobs_by_needle

        user_tz = str(self._resolve_user_tz(telegram_id))
        async with self._jobs_session_maker() as session:
            jobs = await list_owner_jobs(session, telegram_id, user_tz=user_tz)
        return match_jobs_by_needle(jobs, needle)

    async def _send_not_found(
        self, *, telegram_id: int, chat_id: int, needle: str, correlation_id: str
    ) -> None:
        from ai_steward_wiki.scheduler.manage import list_owner_jobs

        user_tz = str(self._resolve_user_tz(telegram_id))
        assert self._jobs_session_maker is not None
        async with self._jobs_session_maker() as session:
            jobs = await list_owner_jobs(session, telegram_id, user_tz=user_tz)
        rendered = "\n".join(f"- {j.rendered}" for j in jobs) if jobs else JOB_LIST_EMPTY_RU
        await self._sender.send_message(chat_id, JOB_NOT_FOUND_RU.format(list=rendered))
        _log.info(
            "tg.pipeline.job.not_found",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            needle=needle,
        )

    async def _build_reschedule_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        job: object,
        time_expr: str,
        schedule_expr: str,
        correlation_id: str,
    ) -> None:
        if job.kind == "reminder_job":  # type: ignore[attr-defined]
            if self._time_parser is None or not time_expr:
                await self._sender.send_message(chat_id, JOB_RESCHEDULE_UNPARSEABLE_RU)
                return
            user_tz = self._resolve_user_tz(telegram_id)
            now_utc = self._clock()
            tp = await self._time_parser.parse_time(
                time_expr,
                user_tz=user_tz,
                now_utc=now_utc,
                prefer_future=True,
                correlation_id=correlation_id,
            )
            if tp.escalate or tp.when_utc is None or tp.when_utc <= now_utc:
                await self._sender.send_message(chat_id, JOB_RESCHEDULE_UNPARSEABLE_RU)
                return
            when_local = tp.when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
            draft = {
                "job_id": job.id,  # type: ignore[attr-defined]
                "job_kind": job.kind,  # type: ignore[attr-defined]
                "action": "reschedule",
                "new_when_utc": tp.when_utc.astimezone(UTC).isoformat(),
                "correlation_id": correlation_id,
            }
            recap = JOB_RESCHEDULE_RECAP_RU.format(rendered=job.rendered, when=when_local)  # type: ignore[attr-defined]
        else:
            new_rec = None
            if self._recurrence_parser is not None and schedule_expr:
                res = self._recurrence_parser(
                    schedule_expr,
                    user_tz=str(self._resolve_user_tz(telegram_id)),
                    correlation_id=correlation_id,
                )
                new_rec = res.recurrence
            if new_rec is None:
                hhmm = _extract_hhmm(schedule_expr or time_expr)
                existing_rec = getattr(job.payload, "recurrence", None)  # type: ignore[attr-defined]
                if hhmm is not None and existing_rec is not None:
                    new_rec = existing_rec.model_copy(update={"time_hhmm": hhmm})
            if new_rec is None:
                await self._sender.send_message(chat_id, JOB_RESCHEDULE_UNPARSEABLE_RU)
                return
            draft = {
                "job_id": job.id,  # type: ignore[attr-defined]
                "job_kind": job.kind,  # type: ignore[attr-defined]
                "action": "reschedule",
                "new_recurrence": new_rec.model_dump(mode="json"),
                "correlation_id": correlation_id,
            }
            recap = JOB_RESCHEDULE_RECAP_RU.format(
                rendered=job.rendered,  # type: ignore[attr-defined]
                when=humanize_recurrence(new_rec),
            )
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="job_cancel",
            draft=draft,
            recap_text=recap,
        )
        rec = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.job.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            category="job_cancel",
            action="reschedule",
        )

    async def _request_job_pick(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        action: str,
        candidates: list[OwnerJob],
        correlation_id: str,
        time_expr: str = "",
        schedule_expr: str = "",
    ) -> None:
        payload_candidates = []
        for job in candidates:
            entry: dict[str, object] = {"job_id": job.id, "job_kind": job.kind, "action": action}
            if action == "reschedule":
                entry["time_expr"] = time_expr
                entry["schedule_expr"] = schedule_expr
            payload_candidates.append(entry)
        draft = {"candidates": payload_candidates, "correlation_id": correlation_id}
        rendered_list = "\n".join(f"{i + 1}. {j.rendered}" for i, j in enumerate(candidates))
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="job_pick",
            draft=draft,
            recap_text=f"{JOB_PICK_PROMPT_RU}\n{rendered_list}",
        )
        rec = await self._confirm.request_explicit(
            confirm_draft,
            keyboard_factory=lambda pid: build_job_pick_keyboard(pid, len(candidates)),
        )
        _log.info(
            "tg.pipeline.job.pick_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            n_candidates=len(candidates),
        )

    async def _execute_job_mutation(
        self, *, telegram_id: int, chat_id: int, draft: dict[str, object]
    ) -> None:
        from ai_steward_wiki.classifier.recurrence import Recurrence
        from ai_steward_wiki.scheduler.manage import (
            OwnerJob,
            cancel_job,
            reschedule_once,
            reschedule_recurring,
        )
        from ai_steward_wiki.storage.jobs.models import Job
        from ai_steward_wiki.storage.jobs.payloads import parse_job_payload

        job_id = int(draft["job_id"])  # type: ignore[call-overload]
        action = str(draft.get("action") or "cancel")
        correlation_id = str(draft.get("correlation_id") or f"job-mutate-{job_id}")
        assert self._jobs_session_maker is not None
        assert self._scheduler is not None
        async with self._jobs_session_maker() as session:
            row = await session.get(Job, job_id)
            if row is None:
                await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
                return
            try:
                payload = parse_job_payload(row.payload)
            except Exception:
                await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
                return
            job = OwnerJob(
                id=row.id,
                kind=row.kind,
                payload=payload,
                scheduled_at_utc=row.scheduled_at_utc,
                rendered="",
            )
            if action == "cancel":
                await cancel_job(self._scheduler, session, job)
                await self._sender.send_message(chat_id, JOB_CANCEL_ACK_RU)
                _log.info(
                    "tg.pipeline.job.cancel",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    job_id=job_id,
                )
                return
            if "new_when_utc" in draft:
                new_when = datetime.fromisoformat(str(draft["new_when_utc"]))
                await reschedule_once(self._scheduler, session, job, new_when)
                when_local = new_when.astimezone(self._resolve_user_tz(telegram_id)).strftime(
                    "%d.%m %H:%M"
                )
                await self._sender.send_message(
                    chat_id, JOB_RESCHEDULE_ACK_RU.format(when=when_local)
                )
            elif "new_recurrence" in draft:
                new_rec = Recurrence(**draft["new_recurrence"])  # type: ignore[arg-type]
                await reschedule_recurring(self._scheduler, session, job, new_rec)
                await self._sender.send_message(
                    chat_id, JOB_RESCHEDULE_ACK_RU.format(when=humanize_recurrence(new_rec))
                )
            else:
                # job_pick reschedule path — raw time_expr/schedule_expr deferred
                # to this point (see the plan's design note in Task C2.2).
                await self._build_reschedule_confirm(
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                    job=job,
                    time_expr=str(draft.get("time_expr") or ""),
                    schedule_expr=str(draft.get("schedule_expr") or ""),
                    correlation_id=correlation_id,
                )
                return
            _log.info(
                "tg.pipeline.job.reschedule",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                job_id=job_id,
            )

    async def _handle_job_create(
        self, *, telegram_id: int, chat_id: int, text: str, slots: object, correlation_id: str
    ) -> None:
        if slots.kind == "once":  # type: ignore[attr-defined]
            # DEC-2: job never falls through to the generic runner, unlike v1's
            # REMINDER fast-path.
            if self._time_parser is None:
                await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
                return
            await self._handle_reminder_intent(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                distilled_payload={
                    "time_expr": slots.time_expr,  # type: ignore[attr-defined]
                    "reminder_text": slots.text,  # type: ignore[attr-defined]
                },
                correlation_id=correlation_id,
            )
            return
        if slots.kind == "digest":  # type: ignore[attr-defined]
            # FR-4/FR-7: byte-identical to v1's digest fast-path — only the ENTRY
            # point (classifier slots vs regex) changed.
            await self._handle_digest_intent(
                telegram_id=telegram_id, chat_id=chat_id, text=text, correlation_id=correlation_id
            )
            return
        if slots.kind == "recurring":  # type: ignore[attr-defined]
            await self._handle_job_create_recurring(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                slots=slots,
                correlation_id=correlation_id,
            )
            return
        # kind == "check_in"
        await self._handle_job_create_check_in(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            slots=slots,
            correlation_id=correlation_id,
        )

    async def _handle_job_create_recurring(
        self, *, telegram_id: int, chat_id: int, text: str, slots: object, correlation_id: str
    ) -> None:
        if self._recurrence_parser is None:
            await self._sender.send_message(chat_id, JOB_RECURRING_UNPARSEABLE_RU)
            return
        user_tz = self._resolve_user_tz(telegram_id)
        schedule_expr = slots.schedule_expr or text  # type: ignore[attr-defined]
        res = self._recurrence_parser(
            schedule_expr, user_tz=str(user_tz), correlation_id=correlation_id
        )
        if res.recurrence is None:
            await self._sender.send_message(chat_id, JOB_RECURRING_UNPARSEABLE_RU)
            _log.info(
                "tg.pipeline.job.recurring_unparseable",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                reason=res.reason,
            )
            return
        message = slots.text or text  # type: ignore[attr-defined]
        draft = {
            "message": message,
            "recurrence": res.recurrence.model_dump(mode="json"),
            "correlation_id": correlation_id,
        }
        recap = JOB_RECURRING_RECAP_RU.format(
            schedule=humanize_recurrence(res.recurrence), message=message
        )
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="job_recurring",
            draft=draft,
            recap_text=recap,
        )
        rec = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.job.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            category="job_recurring",
        )

    async def _handle_job_create_check_in(
        self, *, telegram_id: int, chat_id: int, text: str, slots: object, correlation_id: str
    ) -> None:
        if self._recurrence_parser is None:
            await self._sender.send_message(chat_id, JOB_CHECKIN_UNPARSEABLE_RU)
            return
        user_tz = self._resolve_user_tz(telegram_id)
        schedule_expr = slots.schedule_expr or text  # type: ignore[attr-defined]
        res = self._recurrence_parser(
            schedule_expr, user_tz=str(user_tz), correlation_id=correlation_id
        )
        if res.recurrence is None:
            await self._sender.send_message(chat_id, JOB_CHECKIN_UNPARSEABLE_RU)
            _log.info(
                "tg.pipeline.job.checkin_unparseable",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                reason=res.reason,
            )
            return
        topic = slots.text or text  # type: ignore[attr-defined]
        draft = {
            "question_topic": topic,
            "recurrence": res.recurrence.model_dump(mode="json"),
            "correlation_id": correlation_id,
        }
        recap = JOB_CHECKIN_RECAP_RU.format(
            schedule=humanize_recurrence(res.recurrence), topic=topic
        )
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="job_checkin",
            draft=draft,
            recap_text=recap,
        )
        rec = await self._confirm.request_explicit(
            confirm_draft, keyboard_factory=build_route_confirm_keyboard
        )
        _log.info(
            "tg.pipeline.job.confirm_requested",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=rec.pending_id,
            category="job_checkin",
        )

    async def _execute_job_create_recurring(
        self, *, telegram_id: int, chat_id: int, draft: dict[str, object]
    ) -> None:
        from ai_steward_wiki.classifier.recurrence import Recurrence
        from ai_steward_wiki.scheduler.firing import create_recurring_job

        correlation_id = str(draft.get("correlation_id") or f"job-recurring-{telegram_id}")
        if self._jobs_session_maker is None or self._scheduler is None:
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return
        rec = Recurrence(**draft["recurrence"])  # type: ignore[arg-type]
        message = str(draft["message"])
        async with self._jobs_session_maker() as session:
            job_id = await create_recurring_job(
                session,
                self._scheduler,
                owner_telegram_id=telegram_id,
                chat_id=chat_id,
                message=message,
                recurrence=rec,
                correlation_id=correlation_id,
            )
        await self._sender.send_message(
            chat_id, JOB_RECURRING_ACK_RU.format(schedule=humanize_recurrence(rec))
        )
        _log.info(
            "tg.pipeline.job.confirm_created",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            job_id=job_id,
            category="job_recurring",
        )

    async def _execute_job_create_check_in(
        self, *, telegram_id: int, chat_id: int, draft: dict[str, object]
    ) -> None:
        from ai_steward_wiki.classifier.recurrence import Recurrence
        from ai_steward_wiki.scheduler.cron_user import create_check_in_job

        correlation_id = str(draft.get("correlation_id") or f"job-checkin-{telegram_id}")
        rec = Recurrence(**draft["recurrence"])  # type: ignore[arg-type]
        topic = str(draft["question_topic"])
        job_id = await create_check_in_job(
            owner_telegram_id=telegram_id,
            chat_id=chat_id,
            recurrence=rec,
            question_topic=topic,
            user_tz=str(rec.tz),
            wiki_id=None,
        )
        await self._sender.send_message(
            chat_id, JOB_CHECKIN_ACK_RU.format(schedule=humanize_recurrence(rec))
        )
        _log.info(
            "tg.pipeline.job.confirm_created",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            job_id=job_id,
            category="job_checkin",
        )

    async def _handle_job_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
        draft_json: str | None,
        category: str,
    ) -> None:
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        if status is None:
            await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
            _log.info(
                "tg.pipeline.job.confirm_stale",
                telegram_id=telegram_id,
                pending_id=pending_id,
                category=category,
            )
            return
        if status != "confirmed":
            await self._sender.send_message(chat_id, JOB_CONFIRM_CANCELLED_RU)
            _log.info(
                "tg.pipeline.job.confirm_cancelled",
                telegram_id=telegram_id,
                pending_id=pending_id,
                status=status,
                category=category,
            )
            return
        draft = json.loads(draft_json or "{}")
        if category == "job_recurring":
            await self._execute_job_create_recurring(
                telegram_id=telegram_id, chat_id=chat_id, draft=draft
            )
            return
        if category == "job_checkin":
            await self._execute_job_create_check_in(
                telegram_id=telegram_id, chat_id=chat_id, draft=draft
            )
            return
        # category == "job_cancel" (covers both cancel AND reschedule mutations —
        # see Task C2.2's category-naming design decision)
        await self._execute_job_mutation(telegram_id=telegram_id, chat_id=chat_id, draft=draft)

    async def on_jobpick_callback(
        self, *, telegram_id: int, chat_id: int, pending_id: int, job_index: int
    ) -> None:
        pending = await self._confirm.get_pending(pending_id)
        if pending is None or getattr(pending, "category", None) != "job_pick":
            await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
            return
        draft = json.loads(pending.draft_json or "{}")
        candidates = draft.get("candidates", [])
        if job_index < 0 or job_index >= len(candidates):
            await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
            return
        status = await self._confirm.resolve(telegram_id, pending_id, "correct")
        if status is None:
            await self._sender.send_message(chat_id, JOB_CONFIRM_STALE_RU)
            return
        if status != "corrected":
            await self._sender.send_message(chat_id, JOB_CONFIRM_CANCELLED_RU)
            return
        chosen = candidates[job_index]
        correlation_id = str(draft.get("correlation_id") or f"jobpick-{pending_id}-{telegram_id}")
        await self._execute_job_mutation(
            telegram_id=telegram_id,
            chat_id=chat_id,
            draft={**chosen, "correlation_id": correlation_id},
        )
        _log.info(
            "tg.pipeline.job.pick_resolved",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            job_index=job_index,
        )

    # END_BLOCK_JOB_DISPATCH

    # START_BLOCK_WIKI_DISPATCH (aisw-xi8, DEC-1/DEC-3)
    async def _handle_wiki(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        distilled_payload: dict[str, object],
        correlation_id: str,
        recent_window: list[ChatTurn] | None,
        timeout_s: float | None,
    ) -> None:
        from ai_steward_wiki.classifier.schema import WikiSlots, parse_slots

        slots = parse_slots(WikiSlots, distilled_payload)
        if _is_routable(Intent.WIKI, slots.action):
            await self._handle_routable(
                telegram_id=telegram_id,
                chat_id=chat_id,
                text=text,
                source=source,
                media_paths=media_paths,
                correlation_id=correlation_id,
                recent_window=recent_window,
                timeout_s=timeout_s,
                # DEC-3: catalog/None goes straight to the router — a catalog
                # request has no content to keyword-match (conservative).
                hint_fastpath_eligible=(slots.action == "ingest"),
            )
            return
        # query / lint -> generic answer runner (streaming tail); action threaded
        # through the DEC-5 Protocol widening.
        await self._handle_generic_runner(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            source=source,
            media_paths=media_paths,
            intent=Intent.WIKI,
            action=slots.action,
            correlation_id=correlation_id,
            timeout_s=timeout_s,
        )

    # END_BLOCK_WIKI_DISPATCH

    # START_BLOCK_CHAT_DISPATCH (aisw-xi8 — ex-SMALLTALK, renamed; FR-17 unchanged behaviour)
    async def _handle_chat(self, *, telegram_id: int, chat_id: int, correlation_id: str) -> None:
        _log.info(
            "tg.pipeline.chat.replied", correlation_id=correlation_id, telegram_id=telegram_id
        )
        await self._sender.send_message(chat_id, CHAT_REPLY_RU)
        await self._chatlog_out(telegram_id=telegram_id, chat_id=chat_id, text=CHAT_REPLY_RU)

    # END_BLOCK_CHAT_DISPATCH

    # START_BLOCK_ADMIN_DISPATCH (aisw-xi8 — FR-17 unchanged behaviour)
    async def _handle_admin(self, *, telegram_id: int, chat_id: int, correlation_id: str) -> None:
        _log.info(
            "tg.pipeline.admin.declined", correlation_id=correlation_id, telegram_id=telegram_id
        )
        await self._sender.send_message(chat_id, ACK_ADMIN_RU)

    # END_BLOCK_ADMIN_DISPATCH

    # START_BLOCK_WEB_DISPATCH (aisw-xi8, DEC-1)
    async def _handle_web(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        correlation_id: str,
        timeout_s: float | None,
    ) -> None:
        await self._handle_generic_runner(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            source=source,
            media_paths=media_paths,
            intent=Intent.WEB,
            action=None,
            correlation_id=correlation_id,
            timeout_s=timeout_s,
        )

    # END_BLOCK_WEB_DISPATCH

    # START_BLOCK_UNKNOWN_DISPATCH (aisw-xi8, DEC-1)
    async def _handle_unknown(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        correlation_id: str,
        recent_window: list[ChatTurn] | None,
        timeout_s: float | None,
    ) -> None:
        await self._handle_routable(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            source=source,
            media_paths=media_paths,
            correlation_id=correlation_id,
            recent_window=recent_window,
            timeout_s=timeout_s,
            hint_fastpath_eligible=True,  # unchanged from v1 (UNKNOWN had no action slot)
        )

    # END_BLOCK_UNKNOWN_DISPATCH

    # START_BLOCK_ROUTABLE_SHARED (aisw-xi8 — the pre-existing hint-fastpath +
    # Stage-1a router mechanics, UNCHANGED, now shared by _handle_wiki(ingest/
    # catalog/None) and _handle_unknown instead of being gated by a static
    # frozenset membership test)
    async def _handle_routable(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        correlation_id: str,
        recent_window: list[ChatTurn] | None,
        timeout_s: float | None,
        hint_fastpath_eligible: bool,
    ) -> None:
        # START_BLOCK_HINT_FASTPATH (aisw-5sd, Inbox-WIKI Phase-E.b; aisw-2ra silent
        # route; aisw-xi8 DEC-3 additionally gates on hint_fastpath_eligible)
        if (
            hint_fastpath_eligible
            and self._router is not None
            and self._hint_catalog_resolver is not None
            and self._librarian is not None
            and self._output is not None
        ):
            catalog = await self._hint_catalog_resolver(telegram_id)
            if not catalog:
                _log.info(
                    "tg.pipeline.hint_fastpath.miss",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    reason="empty_catalog",
                    top_stem=None,
                    top_score=0.0,
                    margin=0.0,
                )
                _log.info(
                    "tg.pipeline.hint_fastpath.fallthrough",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    reason="empty_catalog",
                )
            else:
                hint_match = score_catalog(text, catalog)
                _log.info(
                    "tg.pipeline.hint_fastpath.catalog",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    n_domains=len(catalog),
                )
                if len(text) <= HINT_FASTPATH_MAX_CHARS and is_confident(hint_match):
                    target = hint_match.top_stem
                    decision = RouterDecision(
                        intent=RouterIntent.ROUTE,
                        target_wiki=target,
                        notes="Похоже по ключевым словам из подсказки этой вики.",
                        raw="",
                        parsed_ok=True,
                    )
                    ingest_outcome = await self._ingest_and_deliver(
                        decision,
                        telegram_id=telegram_id,
                        chat_id=chat_id,
                        user_text=text,
                        source=source,
                        media_paths=media_paths,
                        correlation_id=correlation_id,
                    )
                    _log.info(
                        "tg.pipeline.hint_fastpath.silent_route",
                        correlation_id=correlation_id,
                        telegram_id=telegram_id,
                        target_wiki=target,
                        score=hint_match.top_score,
                        margin=hint_match.margin,
                        status=ingest_outcome.status,
                        run_id=ingest_outcome.run_id,
                        source=source,
                    )
                    if ingest_outcome.status == "ok":
                        others = [
                            w for w in await self._list_owner_wiki_names(telegram_id) if w != target
                        ]
                        if others:
                            payload = route_action_to_payload(
                                decision,
                                user_text=text,
                                source=source,
                                media_paths=media_paths,
                                correlation_id=correlation_id,
                            )
                            redirect_draft = PendingConfirmDraft(
                                telegram_id=telegram_id,
                                chat_id=chat_id,
                                category="route_ingest",
                                draft=payload,
                                recap_text=ROUTE_SILENT_ACK_RU.format(wiki=target),
                            )
                            await self._confirm.request_explicit(
                                redirect_draft,
                                keyboard_factory=lambda pid: build_route_redirect_keyboard(
                                    pid, others
                                ),
                            )
                        else:
                            await self._confirm.auto_ack(
                                chat_id, ROUTE_SILENT_ACK_NOREDIR_RU.format(wiki=target)
                            )
                    return
                _log.info(
                    "tg.pipeline.hint_fastpath.miss",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    reason="ambiguous" if hint_match.top_score >= HINT_MIN_SCORE else "no_match",
                    top_stem=hint_match.top_stem,
                    top_score=hint_match.top_score,
                    margin=hint_match.margin,
                )
                _log.info(
                    "tg.pipeline.hint_fastpath.fallthrough",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    reason="not_confident",
                )
        # END_BLOCK_HINT_FASTPATH

        # START_BLOCK_ROUTABLE_BRANCH (aisw-dsg, unchanged mechanics)
        if self._router is not None:
            _log.info(
                "tg.pipeline.router.dispatched",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
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
                    recent_window=recent_window,
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
            if (
                decision.intent in (RouterIntent.CLARIFY, RouterIntent.REJECT)
                and self._librarian is not None
                and self._output is not None
            ):
                sticky = await self._active_wiki_get(telegram_id)
                if sticky:
                    _log.info(
                        "tg.pipeline.active_wiki.default_route",
                        correlation_id=correlation_id,
                        telegram_id=telegram_id,
                        target_wiki=sticky,
                        from_intent=decision.intent.value,
                    )
                    decision = decision.model_copy(
                        update={
                            "intent": RouterIntent.ROUTE,
                            "target_wiki": sticky,
                            "notes": ACTIVE_WIKI_DEFAULT_ROUTE_RU.format(wiki=sticky),
                        }
                    )
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
                wiki_names = [
                    w
                    for w in await self._list_owner_wiki_names(telegram_id)
                    if w != decision.target_wiki
                ]
                rec = await self._confirm.request_explicit(
                    confirm_draft,
                    keyboard_factory=lambda pid: build_route_confirm_keyboard(pid, wiki_names),
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
            await self._chatlog_out(telegram_id=telegram_id, chat_id=chat_id, text=decision.notes)
            return
        # END_BLOCK_ROUTABLE_BRANCH

        # No router wired -> legacy fallthrough to the generic runner (back-compat,
        # matches v1's test_routable_intent_without_router_falls_through_to_legacy).
        await self._handle_generic_runner(
            telegram_id=telegram_id,
            chat_id=chat_id,
            text=text,
            source=source,
            media_paths=media_paths,
            intent=Intent.WIKI,
            action=None,
            correlation_id=correlation_id,
            timeout_s=timeout_s,
        )

    # END_BLOCK_ROUTABLE_SHARED

    # START_BLOCK_GENERIC_RUNNER (aisw-xi8 — extracted from the old
    # _run_text_pipeline tail, unchanged mechanics, +action threading DEC-5)
    async def _handle_generic_runner(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        intent: Intent,
        action: str | None,
        correlation_id: str,
        timeout_s: float | None,
    ) -> None:
        assert self._runner is not None
        assert self._output is not None
        _log.info(
            "tg.pipeline.runner.dispatched",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            intent=intent.value,
        )
        try:
            if self._streaming is not None and source == "text":
                outcome = await self._streaming.run_and_deliver(
                    runner=self._runner,
                    output=self._output,
                    chat_id=chat_id,
                    telegram_id=telegram_id,
                    text=text,
                    intent=intent,
                    correlation_id=correlation_id,
                    action=action,
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
                await self._chatlog_out(
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                    text=outcome.text or ACK_TEXT_RU,
                )
                return
            outcome = await self._runner.run(
                text=text,
                owner_telegram_id=telegram_id,
                correlation_id=correlation_id,
                intent=intent,
                media_paths=media_paths,
                timeout_s=timeout_s,
                action=action,
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
        await self._chatlog_out(telegram_id=telegram_id, chat_id=chat_id, text=reply_text)

    # END_BLOCK_GENERIC_RUNNER

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
        assert self._time_parser is not None  # guarded by the caller
        user_tz = self._resolve_user_tz(telegram_id)
        now_utc = self._clock()
        # aisw-2mg (RC-2): Stage-0 classifier is asked to distil the time
        # expression into distilled_payload["time_expr"] (e.g. "через 5 минут")
        # because dateparser.parse on a full sentence ("напомни мне пойти
        # гулять через 5 минут") returns None for any non-trivial phrasing.
        # Backward-compat (NFR-2): a missing/blank time_expr falls back to the
        # raw user text, matching the legacy behaviour.
        time_expr_raw = distilled_payload.get("time_expr")
        time_expr = (
            time_expr_raw.strip()
            if isinstance(time_expr_raw, str) and time_expr_raw.strip()
            else text
        )
        _log.info(
            "tg.pipeline.reminder.distill_used",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            time_expr_present=time_expr is not text,
        )
        # aisw-4dr (RC-3): parse_time is best-effort — a broken Haiku-fallback,
        # CLI timeout, or schema violation MUST NOT kill the handler silently
        # (NFR-3, FR-1). On any failure we emit the user-facing ru fallback and
        # a structured log anchor instead of bubbling the exception to aiogram.
        try:
            tp = await self._time_parser.parse_time(
                time_expr,
                user_tz=user_tz,
                now_utc=now_utc,
                prefer_future=True,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            _log.warning(
                "tg.pipeline.reminder.parser_failed",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:200],
            )
            await self._sender.send_message(chat_id, REMINDER_UNPARSEABLE_RU)
            return
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
            "lead_time_min": _extract_lead_minutes(text),
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

        «выключи/переноси сводку» control (#2, aisw-578) now lives generically
        in Phase-C.2's job/cancel+job/reschedule surface for ALL job kinds, not
        only digest — this handler is a pure create-flow builder again.
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
        # aisw-269: named-subset WIKI selection at digest-creation time.
        wiki_scope: str | list[str] = "all"
        if self._owner_wikis_resolver is not None:
            stems = [stem for stem, _ in await self._owner_wikis_resolver(telegram_id)]
            extracted = extract_wiki_names(text, stems)
            if extracted is None:
                await self._sender.send_message(
                    chat_id, DIGEST_WIKI_UNKNOWN_RU.format(known=", ".join(stems) or "—")
                )
                _log.info(
                    "tg.pipeline.digest.wiki_unknown",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                )
                return
            wiki_scope = extracted
        _log.info(
            "tg.pipeline.digest.detected",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            recurrence=rec.model_dump(mode="json"),
            wiki_scope=wiki_scope,
        )
        draft = {
            "recurrence": rec.model_dump(mode="json"),
            "wiki_scope": wiki_scope,
            "window_hours": 24,
            "user_tz": str(user_tz),
            "correlation_id": correlation_id,
        }
        confirm_draft = PendingConfirmDraft(
            telegram_id=telegram_id,
            chat_id=chat_id,
            category="digest",
            draft=draft,
            recap_text=build_digest_recap(rec, wiki_scope),
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

    @traced(event_prefix=TG_PIPELINE_DISPATCH)
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
                audio_bytes,
                run_id=run_id,
                ext=ext,
                mime=mime,
                inbox_root=self._inbox_root_for(telegram_id),
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
        ref = self._photo.handle(
            photo_bytes, run_id=run_id, mime=mime, inbox_root=self._inbox_root_for(telegram_id)
        )
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
        ref = self._photo.handle(
            doc_bytes, run_id=run_id, mime=mime, inbox_root=self._inbox_root_for(telegram_id)
        )
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
        if pending is not None and getattr(pending, "category", None) == "job_cancel":
            await self._handle_job_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
                category="job_cancel",
            )
            return
        if pending is not None and getattr(pending, "category", None) == "job_recurring":
            await self._handle_job_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
                category="job_recurring",
            )
            return
        if pending is not None and getattr(pending, "category", None) == "job_checkin":
            await self._handle_job_confirm(
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                draft_json=pending.draft_json,
                category="job_checkin",
            )
            return
        # (note: category == "job_pick" rows are resolved exclusively through
        # on_jobpick_callback, never through on_confirm_callback — a job_pick
        # row tapped via the generic confirm: prefix should never occur since
        # its keyboard only emits jobpick: callback data; no dispatch branch is
        # added for it here — see the plan's design note in Task C2.2.)
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
        media = [Path(p) for p in action_obj.media_paths]
        outcome = await self._ingest_and_deliver(
            action_obj.decision,
            telegram_id=telegram_id,
            chat_id=chat_id,
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

    # END_BLOCK_ROUTE_CONFIRM

    # START_BLOCK_INGEST_AND_DELIVER (aisw-2ra — shared Stage-1b ingest + deliver tail)
    async def _ingest_and_deliver(
        self,
        decision: RouterDecision,
        *,
        telegram_id: int,
        chat_id: int,
        user_text: str,
        source: Literal["text", "voice", "document", "photo"],
        media_paths: list[Path] | None,
        correlation_id: str,
    ) -> IngestOutcome:
        """Run the Stage-1b librarian ingest and deliver the result.

        Single ingest path shared by the confirmed-route gate (_handle_route_confirm)
        and the confident silent auto-route branch (aisw-2ra). On status=='ok' the
        sticky active-WIKI pointer is updated (aisw-0ym) and the reply is delivered
        via OutputDelivery; otherwise the (already-composed) reply is sent as-is.
        Callers log their own context-specific anchor around this call.
        """
        # Only invoked on paths that require a wired librarian + output.
        assert self._librarian is not None
        assert self._output is not None
        outcome = await self._librarian.ingest(
            decision,
            telegram_id=telegram_id,
            user_text=user_text,
            source=source,
            media_paths=media_paths or None,
            correlation_id=correlation_id,
        )
        if outcome.status == "ok":
            await self._active_wiki_set(telegram_id, outcome.target_wiki)
            await self._output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id or "",
                text=outcome.reply,
            )
        else:
            await self._sender.send_message(chat_id, outcome.reply)
        return outcome

    # END_BLOCK_INGEST_AND_DELIVER

    # START_BLOCK_WIKIPICK (aisw-13h — redirect a route_ingest into an existing WIKI)
    async def _list_owner_wiki_names(self, telegram_id: int) -> list[str]:
        """Owner's existing <Domain>-WIKI names (sorted, minus Inbox-WIKI) or []."""
        if self._owner_wikis_resolver is None:
            return []
        pairs = await self._owner_wikis_resolver(telegram_id)
        return [name for name, _path in pairs]

    async def on_wikipick_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        wiki_index: int,
    ) -> None:
        """Route a pending route_ingest into the user-picked existing WIKI (aisw-13h).

        The picked WIKI overrides the proposed target (intent → ROUTE), then the
        staged item is ingested via the same Stage-1b path as a plain confirm.
        ``wiki_index`` references the picker list — the owner's WIKIs minus the
        proposed target — rebuilt here identically (load draft → exclude target)
        and bounds-checked, so it stays consistent with the rendered keyboard.
        """
        pending = await self._confirm.get_pending(pending_id)
        if pending is None or getattr(pending, "category", None) != "route_ingest":
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_STALE_RU)
            return
        action_obj = route_action_from_payload(json.loads(pending.draft_json or "{}"))
        proposed = action_obj.decision.target_wiki
        candidates = [w for w in await self._list_owner_wiki_names(telegram_id) if w != proposed]
        if wiki_index < 0 or wiki_index >= len(candidates):
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_STALE_RU)
            return
        chosen = candidates[wiki_index]
        status = await self._confirm.resolve(telegram_id, pending_id, "correct")
        if status is None:
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_STALE_RU)
            return
        if status != "corrected":  # another tap already confirmed/cancelled it
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_CANCELLED_RU)
            return
        correlation_id = action_obj.correlation_id or f"wikipick-{pending_id}-{telegram_id}"
        decision = action_obj.decision.model_copy(
            update={"intent": RouterIntent.ROUTE, "target_wiki": chosen}
        )
        await self._sender.send_message(chat_id, ROUTE_CONFIRM_ACK_RU)
        assert self._librarian is not None
        assert self._output is not None
        media = [Path(p) for p in action_obj.media_paths]
        outcome = await self._librarian.ingest(
            decision,
            telegram_id=telegram_id,
            user_text=action_obj.user_text,
            source=action_obj.source,
            media_paths=media or None,
            correlation_id=correlation_id,
        )
        _log.info(
            "tg.pipeline.route.wikipick_executed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            chosen_wiki=chosen,
            status=outcome.status,
            run_id=outcome.run_id,
        )
        if outcome.status == "ok":
            # aisw-0ym: a wiki-picked ingest also updates the sticky pointer (chosen WIKI).
            await self._active_wiki_set(telegram_id, outcome.target_wiki or chosen)
            await self._output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id or "",
                text=outcome.reply,
            )
        else:
            await self._sender.send_message(chat_id, outcome.reply)

    # END_BLOCK_WIKIPICK

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

        # #3 aisw-5wr: a pre-reminder phrase schedules a SECOND, earlier reminder at
        # (T - lead) in addition to the main one at T. The dormant ReminderPayload
        # lead_time_min field is not a fire-time mechanism, so two real jobs are
        # created (lead_time_min=0 on both). The early job is skipped if (T - lead)
        # is already in the past.
        async with self._jobs_session_maker() as session:
            job_id = await create_reminder_job(
                session,
                self._scheduler,
                owner_telegram_id=telegram_id,
                chat_id=chat_id,
                when_utc=when_utc,
                message=message,
                lead_time_min=0,
                correlation_id=correlation_id,
            )
            lead_job_id: int | None = None
            early_utc = when_utc - timedelta(minutes=lead)
            if lead > 0 and early_utc > self._clock():
                lead_job_id = await create_reminder_job(
                    session,
                    self._scheduler,
                    owner_telegram_id=telegram_id,
                    chat_id=chat_id,
                    when_utc=early_utc,
                    message=message,
                    lead_time_min=0,
                    correlation_id=f"{correlation_id}-lead",
                )
        when_local = when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
        ack = REMINDER_ACK_LEAD_RU if lead_job_id is not None else REMINDER_ACK_RU
        await self._sender.send_message(chat_id, ack.format(when_local=when_local))
        _log.info(
            "tg.pipeline.reminder.confirm_created",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            job_id=job_id,
            lead_job_id=lead_job_id,
            lead_time_min=lead,
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
        raw_scope = draft.get("wiki_scope") or "all"
        # aisw-269: 'all' or an explicit list[str] of WIKI dir-stems; anything
        # unexpected falls back to 'all' (defensive — the draft is our own JSON).
        wiki_scope: str | list[str] = (
            list(raw_scope) if isinstance(raw_scope, list) and raw_scope else "all"
        )
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
        schedule_human = humanize_recurrence(rec)
        if isinstance(wiki_scope, list) and wiki_scope:
            schedule_human = f"{schedule_human} по WIKI: {', '.join(wiki_scope)}"
        await self._sender.send_message(
            chat_id, DIGEST_ACK_RU.format(schedule_human=schedule_human)
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
        action: str | None = None,
    ) -> WikiRunOutcome:
        from ai_steward_wiki.wiki.runner import final_turn_text  # lazy import

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
                action=action,
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

        # aisw-2n2: deliver only the trailing answer turn, not the concatenated
        # inter-tool narration that was streamed live as loader progress.
        final_text = final_turn_text([e for e in buffered if isinstance(e, StreamEvent)])
        if not final_text:
            final_text = outcome.text or ACK_TEXT_RU

        try:
            # aisw-2n2: replace the streamed narration (shown live as loader progress)
            # with the clean trailing answer.
            await live_editor.finalize(final_text)  # type: ignore[attr-defined]
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
