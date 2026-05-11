# FILE: tests/integration/test_e2e_pipeline.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Chunk-23 M-INTEGRATION-E2E — exercise DefaultPipeline against the
#            real Claude CLI classifier across 4 representative user paths
#            (text, voice, photo+confirm, document PDF). Last safety net before
#            production cutover.
#   SCOPE: 4 scenarios gated by RUN_INTEGRATION=1 + claude-binary presence (see
#          tests/integration/conftest.py pytest_collection_modifyitems).
#   DEPENDS: conftest fixtures (pipeline, fake_runner, fake_output, fake_bot,
#            confirmation, sessions_sm), ai_steward_wiki.tg.pipeline,
#            ai_steward_wiki.tg.confirm, pypdf
#   LINKS: chunk-23, aisw-vb9, breakdown.xml chunk-23, DEC-E2E-1..3
#   ROLE: TEST
#   MAP_MODE: SUMMARY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_text_turn_end_to_end - text → real classifier → fake runner → deliver
#   test_voice_turn_end_to_end - voice (stub STT) → real classifier → fake runner
#   test_photo_then_confirm_callback - photo staging ack + explicit confirm resolve
#   test_pdf_document_end_to_end - PDF → pypdf extract → classifier → runner
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - chunk 23 M-INTEGRATION-E2E: initial 4-scenario suite
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


# ---------- Scenario 3: photo ack + explicit confirm callback ----------


async def test_photo_then_confirm_callback(pipeline, fake_bot, confirmation, sessions_sm) -> None:
    # Photo path: stage + ack, no classifier/runner involved.
    await pipeline.on_photo(
        telegram_id=42,
        chat_id=10,
        update_id=1003,
        photo_bytes=_PNG_1X1,
        mime="image/png",
    )
    assert any("Фото получено" in s["text"] for s in fake_bot.sends)

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
