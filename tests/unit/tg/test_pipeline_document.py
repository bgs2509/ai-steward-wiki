"""Unit tests for M-TG-DOCUMENT-FULL (chunk 22, DEC-L3).

Covers DefaultPipeline.on_document mime routing:
  - application/pdf (text-bearing, no-text, parse-error)
  - text/* (UTF-8 + BOM, non-UTF-8)
  - image/* (supported, unsupported)
  - unsupported mime + octet-stream
  - L2 dedup short-circuit
  - oversize rejection
  - PII filename tier-2 hashing (raw filename never logged)
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pypdf
import pytest

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.inbox.idempotency import SeenFileMatch
from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.tg.pipeline import (
    ACK_DEDUP_RU,
    ACK_DOC_PDF_NO_TEXT_RU,
    ACK_DOC_RU,
    ACK_DOC_TOO_LARGE_RU,
    ACK_DOC_UNSUPPORTED_RU,
    ACK_PHOTO_RU,
    MAX_DOC_BYTES,
    DefaultPipeline,
    WikiRunOutcome,
)
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender

# ----------------------- fixtures -----------------------


def _build_pdf_with_text(text: str = "Hello, мир!") -> bytes:
    """Create a minimal one-page PDF with the given text via pypdf.

    The page declares a real ``/F1`` Helvetica font resource so pypdf's text
    extractor can resolve the ``Tj`` operator. Without the registered font the
    pypdf 6.x extractor returns empty text (it no longer guesses a default
    font), which would mis-route the document as ``pdf_no_text``.
    """
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources

    data = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1", errors="replace")
    contents = DecodedStreamObject()
    contents.set_data(data)
    page[NameObject("/Contents")] = writer._add_object(contents)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_empty_pdf() -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_idem(
    *,
    new_update: bool = True,
    l2_match: SeenFileMatch | None = None,
    content_sha: str = "f" * 64,
) -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=new_update)
    idem.check_content = AsyncMock(return_value=(content_sha, l2_match))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_confirm() -> MagicMock:
    svc = MagicMock()
    svc.resolve = AsyncMock(return_value="confirmed")
    return svc


def _make_classifier() -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=make_classifier_result(Intent.WIKI, action="query", confidence=0.9)
    )
    return cls


def _make_runner() -> MagicMock:
    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-doc", text="ответ", latency_ms=10))
    return r


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


@dataclass
class _StagedRef:
    sha256: str = "deadbeef" * 8
    ext: str = "jpg"
    staging_path: Path = Path("/tmp/_staging/run-img_deadbeef.jpg")


def _make_photo() -> MagicMock:
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_StagedRef())
    return photo


def _make_pipeline(
    *,
    sender: FakeSender,
    idem: MagicMock | None = None,
    classifier: MagicMock | None = None,
    runner: MagicMock | None = None,
    output: MagicMock | None = None,
    photo: MagicMock | None = None,
    pii: PIIRedactor | None = None,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=idem or _make_idem(),
        confirmation=_make_confirm(),
        classifier=classifier or _make_classifier(),
        runner=runner or _make_runner(),
        output=output or _make_output(),
        photo=photo,
        pii=pii,
    )


# ----------------------- tests -----------------------


@pytest.mark.asyncio
async def test_pdf_with_text_routes_through_text_pipeline() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    pdf = _build_pdf_with_text("важный документ")
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=pdf,
        mime="application/pdf",
        filename="invoice.pdf",
    )
    runner.run.assert_awaited_once()
    # Output went via the OutputDelivery mock — no sender messages.
    assert sender.sends == []


@pytest.mark.asyncio
async def test_pdf_with_no_extractable_text_rejects_with_hint() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=_build_empty_pdf(),
        mime="application/pdf",
        filename="empty.pdf",
    )
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_PDF_NO_TEXT_RU


@pytest.mark.asyncio
async def test_pdf_parse_error_rejects_gracefully() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"this is not a pdf file at all",
        mime="application/pdf",
        filename="broken.pdf",
    )
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_PDF_NO_TEXT_RU


@pytest.mark.asyncio
async def test_text_plain_utf8_bom_decodes_and_routes() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    body = "привет\nмир".encode("utf-8-sig")  # leading BOM
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=body,
        mime="text/plain",
        filename="note.txt",
    )
    runner.run.assert_awaited_once()
    text_arg = runner.run.await_args.kwargs["text"]
    assert text_arg.startswith("привет")
    assert not text_arg.startswith("\ufeff")


@pytest.mark.asyncio
async def test_text_plain_with_caption_prepends_caption() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"line one\nline two",
        mime="text/plain",
        filename="note.txt",
        caption="разбери и занеси в study",
    )
    runner.run.assert_awaited_once()
    text_arg = runner.run.await_args.kwargs["text"]
    assert "разбери и занеси в study" in text_arg
    assert "line one" in text_arg


@pytest.mark.asyncio
async def test_pdf_no_text_with_caption_still_rejected() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=_build_empty_pdf(),
        mime="application/pdf",
        filename="empty.pdf",
        caption="это важно",
    )
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_PDF_NO_TEXT_RU


@pytest.mark.asyncio
async def test_text_plain_non_utf8_rejected_as_unsupported() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    body = "тест".encode("cp1251")
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=body,
        mime="text/plain",
        filename="cp.txt",
    )
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_UNSUPPORTED_RU


@pytest.mark.asyncio
async def test_image_jpeg_routed_to_runner_with_media() -> None:
    sender = FakeSender()
    photo = _make_photo()
    runner = _make_runner()
    output = _make_output()
    pipe = _make_pipeline(sender=sender, photo=photo, runner=runner, output=output)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\xff\xd8\xff\xe0fake-jpeg",
        mime="image/jpeg",
        filename="photo.jpg",
    )
    photo.handle.assert_called_once()
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["media_paths"] is not None
    output.deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_jpeg_with_caption_carries_caption() -> None:
    sender = FakeSender()
    photo = _make_photo()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, photo=photo, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\xff\xd8\xff\xe0fake-jpeg",
        mime="image/jpeg",
        filename="photo.jpg",
        caption="что на скрине",
    )
    runner.run.assert_awaited_once()
    assert "что на скрине" in runner.run.await_args.kwargs["text"]
    assert runner.run.await_args.kwargs["media_paths"] is not None


@pytest.mark.asyncio
async def test_image_jpeg_without_full_pipeline_acks() -> None:
    sender = FakeSender()
    photo = _make_photo()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
    )
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\xff\xd8\xff\xe0fake-jpeg",
        mime="image/jpeg",
        filename="photo.jpg",
    )
    photo.handle.assert_called_once()
    assert sender.sends[-1]["text"] == ACK_PHOTO_RU


@pytest.mark.asyncio
async def test_image_heic_rejected_as_unsupported() -> None:
    sender = FakeSender()
    photo = _make_photo()
    pipe = _make_pipeline(sender=sender, photo=photo)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\x00\x00\x00 ftypheic",
        mime="image/heic",
        filename="photo.heic",
    )
    photo.handle.assert_not_called()
    assert sender.sends[-1]["text"] == ACK_DOC_UNSUPPORTED_RU


@pytest.mark.asyncio
async def test_octet_stream_rejected_as_unsupported() -> None:
    sender = FakeSender()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\x00\x01\x02",
        mime="application/octet-stream",
        filename="blob.bin",
    )
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_UNSUPPORTED_RU


@pytest.mark.asyncio
async def test_l2_dedup_hit_short_circuits_before_routing() -> None:
    sender = FakeSender()
    match = SeenFileMatch(
        content_sha256="f" * 64,
        owner_telegram_id=1,
        kind="file",
        first_seen_at_utc=__import__("datetime").datetime(2026, 5, 11),
        within_ttl=True,
    )
    idem = _make_idem(l2_match=match)
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, idem=idem, runner=runner)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"hi",
        mime="text/plain",
        filename="dup.txt",
    )
    idem.record_dedup_choice.assert_awaited_once_with("f" * 64, 1, "duplicate_doc")
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DEDUP_RU


@pytest.mark.asyncio
async def test_oversized_doc_rejected_before_dedup() -> None:
    sender = FakeSender()
    idem = _make_idem()
    runner = _make_runner()
    pipe = _make_pipeline(sender=sender, idem=idem, runner=runner)

    # Avoid actually allocating 25MB+; use a bytes object whose len() exceeds
    # the cap via bytearray slicing trickery? No — keep it real but small.
    # We patch the constant via monkeypatch alternative: directly construct
    # a buffer just above MAX_DOC_BYTES isn't memory-friendly. Use a
    # bytes(MAX_DOC_BYTES + 1) — ~25 MB; acceptable for unit run.
    big = bytes(MAX_DOC_BYTES + 1)
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=big,
        mime="application/pdf",
        filename="big.pdf",
    )
    idem.check_content.assert_not_awaited()
    runner.run.assert_not_awaited()
    assert sender.sends[-1]["text"] == ACK_DOC_TOO_LARGE_RU


@pytest.mark.asyncio
async def test_log_uses_hashed_filename_never_raw(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeSender()
    pipe = _make_pipeline(sender=sender)

    raw_name = "very-secret-personal-name-12345.txt"
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"hello",
        mime="text/plain",
        filename=raw_name,
    )
    # Structlog routes through stdlib logging by default; inspect captured
    # records' message bodies AND any kwargs that landed in record args.
    leaked: list[Any] = []
    for rec in caplog.records:
        if raw_name in rec.getMessage():
            leaked.append(rec.getMessage())
        for k, v in vars(rec).items():
            if isinstance(v, str) and raw_name in v:
                leaked.append((k, v))
    assert not leaked, f"raw filename leaked into logs: {leaked!r}"


@pytest.mark.asyncio
async def test_image_without_handler_falls_back_to_photo_ack() -> None:
    sender = FakeSender()
    pipe = _make_pipeline(sender=sender, photo=None)

    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"\xff\xd8\xff",
        mime="image/png",
        filename="x.png",
    )
    assert sender.sends[-1]["text"] == ACK_PHOTO_RU


@pytest.mark.asyncio
async def test_text_branch_no_full_pipeline_falls_back_to_doc_ack() -> None:
    sender = FakeSender()
    idem = _make_idem()
    # Construct a pipeline WITHOUT classifier/runner/output → fallback ack.
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=idem,
        confirmation=_make_confirm(),
    )
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"hello",
        mime="text/plain",
        filename="x.txt",
    )
    assert sender.sends[-1]["text"] == ACK_DOC_RU
