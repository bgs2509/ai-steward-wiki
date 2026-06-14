"""Unit tests for _RouterAdapter / _render_raw_sidecar in __main__ (aisw-dsg)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_steward_wiki import __main__ as runtime
from ai_steward_wiki.inbox.materialize import INBOX_WIKI_DIRNAME
from ai_steward_wiki.inbox.router import RouterIntent
from ai_steward_wiki.wiki.runner import WikiRunnerTimeoutError
from ai_steward_wiki.wiki.streaming import StreamEvent


def _write_inbox_template(tmp_path: Path) -> Path:
    tpl = tmp_path / "templates" / "inbox-wiki" / "CLAUDE.md"
    tpl.parent.mkdir(parents=True)
    tpl.write_text("# Inbox router\n", encoding="utf-8")
    return tpl


def _make_adapter(tmp_path: Path) -> tuple[runtime._RouterAdapter, Path]:
    wiki_root = tmp_path / "wikis"
    wiki_root.mkdir()
    adapter = runtime._RouterAdapter(
        wiki_root=wiki_root,
        inbox_template_path=_write_inbox_template(tmp_path),
        base_prompt_path=tmp_path / "prompts" / "wiki.md",
        inbox_overlay_path=tmp_path / "prompts" / "inbox.md",
        runtime_dir=tmp_path / "runtime",
        acquirer=MagicMock(),
        spawner=MagicMock(),
        run_config=MagicMock(),
    )
    return adapter, wiki_root


def _fake_run_result(text: str) -> MagicMock:
    r = MagicMock()
    r.events = [StreamEvent(type="assistant_chunk", payload={"text": text})]
    r.latency_ms = 123
    return r


# ---------- _render_raw_sidecar ----------


def test_sidecar_text_is_plain_body() -> None:
    fn, content = runtime._render_raw_sidecar(source="text", text="привет", media_paths=None)
    assert fn.endswith("_text.md")
    assert content == "привет\n"


def test_sidecar_voice_has_frontmatter_and_transcript(tmp_path: Path) -> None:
    media = tmp_path / "staged" / "x.ogg"
    fn, content = runtime._render_raw_sidecar(
        source="voice", text="запиши встречу", media_paths=[media]
    )
    assert fn.endswith("_voice.md")
    assert content.startswith("---\nsource: voice\n")
    assert f"staged_path: {media}" in content
    assert "## Содержимое" in content
    assert "запиши встречу" in content


def test_sidecar_document_multiple_media(tmp_path: Path) -> None:
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    fn, content = runtime._render_raw_sidecar(source="document", text="текст", media_paths=[a, b])
    assert fn.endswith("_document.md")
    assert "staged_paths:" in content
    assert f"  - {a}" in content
    assert f"  - {b}" in content


# ---------- _RouterAdapter.route ----------


@pytest.mark.asyncio
async def test_route_materialises_inbox_stages_raw_and_runs_in_inbox(tmp_path: Path) -> None:
    adapter, wiki_root = _make_adapter(tmp_path)
    block = "```router\ntarget_wiki: Travel-WIKI\nintent: route\nnotes: В Travel-WIKI.\n```"
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result(block))
    ) as run_mock:
        decision = await adapter.route(
            text="вот авиабилет",
            telegram_id=42,
            correlation_id="tg-1-42",
            source="text",
            media_paths=None,
            timeout_s=None,
        )

    inbox_dir = wiki_root / "42" / INBOX_WIKI_DIRNAME
    assert (inbox_dir / "CLAUDE.md").exists()  # materialised
    raw_files = list((inbox_dir / "raw").glob("*_text.md"))
    assert len(raw_files) == 1
    assert raw_files[0].read_text(encoding="utf-8") == "вот авиабилет\n"

    kw = run_mock.await_args.kwargs
    assert kw["wiki_id"] == "42/Inbox-WIKI"
    assert kw["wiki_path"] == inbox_dir
    assert kw["overlay_prompt_path"] == tmp_path / "prompts" / "inbox.md"
    # user_input now carries the existing-WIKI context header (aisw-2co); none exist here
    assert kw["user_input"].endswith("вот авиабилет")
    assert "нет ни одной" in kw["user_input"]

    assert decision.intent is RouterIntent.ROUTE
    assert decision.target_wiki == "Travel-WIKI"
    assert decision.parsed_ok is True


@pytest.mark.asyncio
async def test_route_passes_existing_wikis_to_router(tmp_path: Path) -> None:
    adapter, wiki_root = _make_adapter(tmp_path)
    owner = wiki_root / "42"
    for name in ("Medical-WIKI", "Budget-WIKI", "Inbox-WIKI"):
        (owner / name).mkdir(parents=True)
    block = "```router\ntarget_wiki: Medical-WIKI\nintent: route\nnotes: В Medical-WIKI.\n```"
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result(block))
    ) as run_mock:
        decision = await adapter.route(
            text="давление 137 96 пульс 78",
            telegram_id=42,
            correlation_id="tg-1-42",
            source="text",
            media_paths=None,
            timeout_s=None,
        )

    ui = run_mock.await_args.kwargs["user_input"]
    assert "Medical-WIKI" in ui
    assert "Budget-WIKI" in ui
    assert "Inbox-WIKI" not in ui  # Inbox-WIKI is excluded from the router's existing list
    assert "давление 137 96 пульс 78" in ui
    assert decision.intent is RouterIntent.ROUTE
    assert decision.target_wiki == "Medical-WIKI"


@pytest.mark.asyncio
async def test_route_wraps_runner_error_in_router_error(tmp_path: Path) -> None:
    adapter, _ = _make_adapter(tmp_path)
    with (
        patch.object(
            runtime, "run_wiki_session", new=AsyncMock(side_effect=WikiRunnerTimeoutError("slow"))
        ),
        pytest.raises(runtime.RouterError),
    ):
        await adapter.route(
            text="x", telegram_id=1, correlation_id="c", source="text", media_paths=None
        )


@pytest.mark.asyncio
async def test_route_emits_log_anchors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    adapter, _ = _make_adapter(tmp_path)
    block = "```router\ntarget_wiki: null\nintent: clarify\nnotes: Уточни?\n```"
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result(block))
    ):
        await adapter.route(
            text="?", telegram_id=7, correlation_id="tg-2-7", source="text", media_paths=None
        )
    out = capsys.readouterr().out
    for anchor in (
        "inbox.router.staged_raw",
        "inbox.router.run.begin",
        "inbox.router.run.done",
        "inbox.router.parsed",
    ):
        assert anchor in out, f"missing {anchor} in:\n{out}"


@pytest.mark.asyncio
async def test_route_logs_parse_error_on_bad_reply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter, _ = _make_adapter(tmp_path)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("no block here"))
    ):
        decision = await adapter.route(
            text="?", telegram_id=7, correlation_id="c", source="text", media_paths=None
        )
    assert decision.parsed_ok is False
    assert decision.intent is RouterIntent.CLARIFY
    assert "inbox.router.parse_error" in capsys.readouterr().out
