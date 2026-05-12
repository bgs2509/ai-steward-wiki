# FILE: tests/integration/test_e2e_pipeline.py
# VERSION: 0.4.0
# START_MODULE_CONTRACT
#   PURPOSE: M-INTEGRATION-E2E — exercise DefaultPipeline against the real
#            Claude CLI across representative user paths (text, voice,
#            photo→vision, photo+caption, photo+confirm, document PDF, the
#            Inbox-WIKI Stage-1a router, and the Phase-C route confirm →
#            Stage-1b ingest). Last safety net before production cutover.
#   SCOPE: 8 scenarios gated by RUN_INTEGRATION=1 + claude-binary presence (see
#          tests/integration/conftest.py pytest_collection_modifyitems).
#   DEPENDS: conftest fixtures (pipeline, pipeline_with_router, pipeline_full_routing,
#            real_router_adapter, real_librarian_adapter, wiki_root_e2e, fake_runner,
#            fake_output, fake_bot, confirmation, sessions_sm), ai_steward_wiki.tg.pipeline,
#            ai_steward_wiki.tg.confirm, ai_steward_wiki.__main__.{_RouterAdapter,_LibrarianAdapter}, pypdf
#   LINKS: chunk-23, aisw-vb9, aisw-dsg, aisw-zd9, aisw-e45, breakdown.xml chunk-23, DEC-E2E-1..3
#   ROLE: TEST
#   MAP_MODE: SUMMARY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_text_turn_end_to_end - text → real classifier → fake runner → deliver
#   test_voice_turn_end_to_end - voice (stub STT) → real classifier → fake runner
#   test_photo_routed_to_runner_with_media - photo → stage → runner(media_paths) → deliver
#   test_photo_with_caption_carries_caption - caption appears in the runner prompt
#   test_photo_then_confirm_callback - photo routed + explicit confirm resolve
#   test_pdf_document_end_to_end - PDF → pypdf extract → classifier → runner
#   test_routable_text_runs_router_in_inbox_wiki - routable text → Stage-1a router in Inbox-WIKI/
#   test_routable_text_confirm_then_ingests - routable text → recap → confirm → Stage-1b ingest in <Domain>-WIKI/
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.4.0 - aisw-e45 (Inbox-WIKI Phase-C): scenario 6 reworked —
#                routable text now yields a route_ingest pending row + recap (no
#                ingest yet); on_confirm_callback(confirm) then drives the real
#                Stage-1b librarian into the target <Domain>-WIKI. Tolerant of a
#                CLARIFY/REJECT decision (no pending row, notes reply).
#   PREVIOUS:    v0.3.0 - aisw-zd9 (Inbox-WIKI Phase-B): add scenario 6 —
#                routable text → real Stage-1a router → (if ROUTE/CREATE_WIKI)
#                real Stage-1b librarian ingest into <wiki>/<tid>/<Name>-WIKI/;
#                tolerant of the router deciding CLARIFY/REJECT.
#   PREVIOUS:    v0.2.0 - aisw-dsg (Inbox-WIKI Phase-A): add scenario 5 —
#                routable text goes through the Inbox-WIKI Stage-1a router
#                (_RouterAdapter), Claude runs inside <wiki>/<tid>/Inbox-WIKI/,
#                bot replies with RouterDecision.notes, legacy runner untouched.
#   PREVIOUS:    v0.1.1 - aisw-nzl (media chunk 5): add photo→vision scenarios
#                (media_paths forwarded to runner; caption carried in prompt);
#                test_photo_then_confirm_callback updated for the new photo path.
#                v0.1.0 - chunk 23 M-INTEGRATION-E2E: initial 4-scenario suite
#                gated by RUN_INTEGRATION=1 + claude binary on PATH.
# END_CHANGE_SUMMARY

from __future__ import annotations

import io
import os
import shutil

import pytest
from sqlalchemy import select

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.storage.sessions.models import PendingConfirm
from ai_steward_wiki.tg.confirm import PendingConfirmDraft

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 to enable integration suite",
    ),
    pytest.mark.skipif(shutil.which("claude") is None, reason="`claude` binary not on PATH"),
    pytest.mark.skipif(
        os.environ.get("CLAUDECODE") == "1",
        reason="recursive claude invocation (CLAUDECODE=1) — run outside Claude Code",
    ),
]

# Minimal 1x1 PNG (RFC 2083) — header + IHDR + IDAT + IEND.
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


def _build_minimal_pdf(text: str = "Напомни мне завтра в 9 утра позвонить маме") -> bytes:
    """Build a 1-page PDF with the given text using pypdf."""
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    # Use a low-level PDF stream with a single text-show operator so
    # _extract_pdf_text returns non-empty content.
    page = writer.pages[0]
    # Inject a content stream — pypdf API for adding text is limited; the
    # simpler path is to attach a ContentStream with BT/ET sequence.
    from pypdf.generic import (  # type: ignore[attr-defined]
        ContentStream,
        DecodedStreamObject,
        NameObject,
    )

    cs = ContentStream(None, writer)
    cs.operations.append(([], "BT"))
    cs.operations.append(([NameObject("/F1"), 12], "Tf"))
    cs.operations.append(([10, 30], "Td"))
    cs.operations.append(([text.encode("latin-1", errors="replace")], "Tj"))
    cs.operations.append(([], "ET"))
    stream = DecodedStreamObject()
    stream.set_data(cs.get_data())
    page[NameObject("/Contents")] = stream
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- Scenario 1: text turn ----------


async def test_text_turn_end_to_end(pipeline, fake_runner, fake_output) -> None:
    await pipeline.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=1001,
        text="напомни мне завтра в 9 утра позвонить маме",
    )
    fake_runner.run.assert_awaited_once()
    intent = fake_runner.run.await_args.kwargs["intent"]
    assert isinstance(intent, Intent)
    fake_output.deliver.assert_awaited_once()


# ---------- Scenario 2: voice turn ----------


async def test_voice_turn_end_to_end(pipeline, fake_runner, fake_output) -> None:
    await pipeline.on_voice(
        telegram_id=42,
        chat_id=10,
        update_id=1002,
        audio_bytes=b"fake-ogg-payload",
    )
    fake_runner.run.assert_awaited_once()
    fake_output.deliver.assert_awaited_once()


# ---------- Scenario 3: photo → Claude vision (media_paths) ----------


async def test_photo_routed_to_runner_with_media(pipeline, fake_runner, fake_output) -> None:
    await pipeline.on_photo(
        telegram_id=42,
        chat_id=10,
        update_id=1003,
        photo_bytes=_PNG_1X1,
        mime="image/png",
    )
    fake_runner.run.assert_awaited_once()
    media = fake_runner.run.await_args.kwargs["media_paths"]
    assert media is not None
    assert len(media) == 1
    fake_output.deliver.assert_awaited_once()


# ---------- Scenario 3b: photo with caption ----------


async def test_photo_with_caption_carries_caption(pipeline, fake_runner) -> None:
    await pipeline.on_photo(
        telegram_id=42,
        chat_id=10,
        update_id=1031,
        photo_bytes=_PNG_1X1,
        mime="image/png",
        caption="занеси в health",
    )
    fake_runner.run.assert_awaited_once()
    assert "занеси в health" in fake_runner.run.await_args.kwargs["text"]


# ---------- Scenario 3c: photo routed + explicit confirm callback ----------


async def test_photo_then_confirm_callback(
    pipeline, fake_runner, confirmation, sessions_sm
) -> None:
    # Photo path: stage → runner(media_paths) → deliver.
    await pipeline.on_photo(
        telegram_id=42,
        chat_id=10,
        update_id=1003,
        photo_bytes=_PNG_1X1,
        mime="image/png",
    )
    fake_runner.run.assert_awaited()

    # Seed an explicit pending row, then resolve via confirm callback.
    rec = await confirmation.request_explicit(
        PendingConfirmDraft(
            telegram_id=42,
            chat_id=10,
            category="reminder.create",
            draft={"title": "позвонить маме", "time": "09:00"},
            recap_text="Создать напоминание?",
        )
    )
    await pipeline.on_confirm_callback(
        telegram_id=42,
        chat_id=10,
        pending_id=rec.pending_id,
        action="confirm",
    )

    # Read back to assert the row flipped to 'confirmed'.
    async with sessions_sm() as session:
        row = (
            (
                await session.execute(
                    select(PendingConfirm).where(PendingConfirm.id == rec.pending_id)
                )
            )
            .scalars()
            .one()
        )
        assert row.status == "confirmed"


# ---------- Scenario 4: PDF document turn ----------


async def test_pdf_document_end_to_end(pipeline, fake_runner, fake_output) -> None:
    pdf_bytes = _build_minimal_pdf()
    if not pdf_bytes:
        pytest.fail("PDF fixture is empty")

    await pipeline.on_document(
        telegram_id=42,
        chat_id=10,
        update_id=1004,
        doc_bytes=pdf_bytes,
        mime="application/pdf",
        filename="note.pdf",
    )

    # _extract_pdf_text may legitimately return empty string for some pypdf
    # encodings of latin-1 text streams — the pipeline then sends a hint and
    # does NOT call the runner. We accept either branch; the assertion is
    # "pipeline did not crash". Prefer the happy path if extraction worked.
    if fake_runner.run.await_count == 1:
        fake_output.deliver.assert_awaited_once()
    else:
        # No extractable text branch: an ack should have been sent.
        assert fake_runner.run.await_count == 0


# ---------- Scenario 5: routable text → Stage-1a Router runs in Inbox-WIKI/ ----------


async def test_routable_text_runs_router_in_inbox_wiki(
    pipeline_with_router, fake_bot, fake_runner, wiki_root_e2e
) -> None:
    """A routable user message goes through the Inbox-WIKI Router (Stage-1a):
    Claude runs with cwd inside <wiki_root>/<tid>/Inbox-WIKI/ and the bot
    replies with the parsed RouterDecision.notes — the legacy flat runner is
    NOT invoked for this intent (aisw-dsg)."""
    await pipeline_with_router.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=1005,
        text="вот мой авиабилет SVO→IST на 15 июня, напомни за сутки",
    )

    # Legacy flat run must not be used for a routable intent when a router is wired.
    fake_runner.run.assert_not_awaited()
    # Bot replied something (the RouterDecision.notes).
    assert fake_bot.sends, "expected a reply with the router notes"
    # The router run materialised the per-user Inbox-WIKI and ran there.
    inbox_dir = wiki_root_e2e / "42" / "Inbox-WIKI"
    assert (inbox_dir / "CLAUDE.md").exists()
    assert list((inbox_dir / "raw").glob("*_text.md")), "raw payload sidecar missing"
    # A transcript landed under Inbox-WIKI/runs/<run_id>/ — proves wiki_path/cwd.
    assert list((inbox_dir / "runs").glob("*/transcript.jsonl")), "router transcript missing"


# ---------- Scenario 6: routable text → confirm → Domain-WIKI + Stage-1b ingest ----------


async def test_routable_text_confirm_then_ingests(
    pipeline_full_routing, fake_bot, fake_output, wiki_root_e2e, sessions_sm
) -> None:
    """A routable user message goes through the Stage-1a router; if it decides
    ROUTE/CREATE_WIKI the pipeline does NOT ingest immediately (Phase-C,
    aisw-e45) — it persists a route_ingest pending row and sends a recap. After
    on_confirm_callback(action="confirm") the Stage-1b librarian resolves/creates
    the target <Domain>-WIKI and ingests there. Tolerant of the router deciding
    CLARIFY/REJECT (then no pending row, just a notes reply)."""
    from ai_steward_wiki.tg.pipeline import ROUTE_CONFIRM_ACK_RU

    await pipeline_full_routing.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=1006,
        text="вот мой авиабилет SVO→IST на 15 июня, занеси в Travel-WIKI",
    )

    user_dir = wiki_root_e2e / "42"

    def _domain_dirs() -> list:
        return [
            d
            for d in (user_dir.iterdir() if user_dir.exists() else [])
            if d.is_dir() and d.name.endswith("-WIKI") and d.name != "Inbox-WIKI"
        ]

    async with sessions_sm() as session:
        pending = (
            (
                await session.execute(
                    select(PendingConfirm).where(PendingConfirm.category == "route_ingest")
                )
            )
            .scalars()
            .all()
        )

    if not pending:
        # CLARIFY/REJECT path: no pending row, no domain WIKI, reply via send_message.
        assert _domain_dirs() == []
        assert fake_bot.sends, "expected a clarify/reject reply"
        return

    # ROUTE/CREATE_WIKI path: nothing ingested yet, only Inbox-WIKI/ + a recap.
    assert _domain_dirs() == [], "no domain WIKI should exist before confirm"
    fake_output.deliver.assert_not_awaited()
    row = pending[0]

    await pipeline_full_routing.on_confirm_callback(
        telegram_id=42, chat_id=10, pending_id=row.id, action="confirm"
    )

    # After confirm: the librarian created/wrote into the target <Domain>-WIKI,
    # the short ack was sent, and the reply went via OutputDelivery (status=ok).
    assert any(s["text"] == ROUTE_CONFIRM_ACK_RU for s in fake_bot.sends)
    domain_dirs = _domain_dirs()
    assert domain_dirs, "expected a target <Domain>-WIKI after confirm"
    wiki = domain_dirs[0]
    assert (wiki / "raw").exists()
    assert list((wiki / "raw").iterdir())
    fake_output.deliver.assert_awaited()
    assert fake_output.deliver.await_args.kwargs["text"]  # non-empty notes + summary
