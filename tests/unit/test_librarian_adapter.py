"""Unit tests for _LibrarianAdapter in __main__ (aisw-zd9, Inbox-WIKI Phase-B)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_steward_wiki import __main__ as runtime
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.wiki.lifecycle import WikiLifecycleManager
from ai_steward_wiki.wiki.runner import WikiRunnerTimeoutError
from ai_steward_wiki.wiki.streaming import StreamEvent


def _decision(
    intent: RouterIntent, target: str | None, notes: str = "Положу в Travel-WIKI."
) -> RouterDecision:
    return RouterDecision(
        intent=intent, target_wiki=target, notes=notes, raw="```router\n...\n```", parsed_ok=True
    )


def _prompts_dir(tmp_path: Path) -> Path:
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "wiki.md").write_text("semver: 1.0.0\n\nbase\n", encoding="utf-8")
    (pdir / "domain-default.md").write_text("semver: 1.0.0\n\ndefault overlay\n", encoding="utf-8")
    return pdir


def _adapter(tmp_path: Path, *, cap: int = 20) -> tuple[runtime._LibrarianAdapter, Path]:
    wiki_root = tmp_path / "wikis"
    wiki_root.mkdir()
    adapter = runtime._LibrarianAdapter(
        wiki_root=wiki_root,
        prompts_dir=_prompts_dir(tmp_path),
        lifecycle=WikiLifecycleManager(wiki_root, max_per_user=cap),
        runtime_dir=tmp_path / "runtime",
        acquirer=MagicMock(),
        spawner=MagicMock(),
        run_config=MagicMock(),
    )
    return adapter, wiki_root


def _fake_run_result(text: str) -> MagicMock:
    r = MagicMock()
    r.events = [StreamEvent(type="assistant_chunk", payload={"text": text})]
    r.latency_ms = 50
    return r


@pytest.mark.asyncio
async def test_create_wiki_happy_path(tmp_path: Path) -> None:
    adapter, wiki_root = _adapter(tmp_path)
    with patch.object(
        runtime,
        "run_wiki_session",
        new=AsyncMock(return_value=_fake_run_result("Записал X на стр. trips.md.")),
    ) as run_mock:
        outcome = await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"),
            telegram_id=42,
            user_text="вот авиабилет",
            source="text",
            media_paths=None,
            correlation_id="tg-1-42",
        )

    wiki_dir = wiki_root / "42" / "Travel-WIKI"
    assert (wiki_dir / "CLAUDE.md").exists()
    raw_files = list((wiki_dir / "raw").glob("*_text.md"))
    assert len(raw_files) == 1
    assert raw_files[0].read_text(encoding="utf-8") == "вот авиабилет\n"

    kw = run_mock.await_args.kwargs
    assert kw["wiki_id"] == "42/Travel-WIKI"
    assert kw["wiki_path"] == wiki_dir
    assert kw["overlay_prompt_path"] == adapter._prompts_dir / "domain-default.md"
    assert kw["base_prompt_path"] == adapter._prompts_dir / "wiki.md"
    assert "вот авиабилет" in kw["user_input"]
    assert raw_files[0].name in kw["user_input"]
    assert kw["timeout_s"] is None

    assert outcome.status == "ok"
    assert outcome.run_id is not None
    assert outcome.run_id.startswith("ingest-")
    assert outcome.target_wiki == "Travel-WIKI"
    assert outcome.created is True
    assert outcome.reply.startswith("Положу в Travel-WIKI.")
    assert "Записал X" in outcome.reply


@pytest.mark.asyncio
async def test_run_failed_keeps_raw_and_returns_run_failed(tmp_path: Path) -> None:
    adapter, wiki_root = _adapter(tmp_path)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(side_effect=WikiRunnerTimeoutError("slow"))
    ):
        outcome = await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"),
            telegram_id=42,
            user_text="x",
            source="text",
            media_paths=None,
            correlation_id="c",
        )
    assert outcome.status == "run_failed"
    assert "Не удалось разложить" in outcome.reply
    assert outcome.run_id is not None
    assert outcome.target_wiki == "Travel-WIKI"
    assert list((wiki_root / "42" / "Travel-WIKI" / "raw").glob("*_text.md"))  # raw kept


@pytest.mark.asyncio
async def test_cap_reached_returns_rejected(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, cap=1)
    # fill the cap
    adapter._lifecycle.create_wiki(42, "First-WIKI", "_default")
    with patch.object(runtime, "run_wiki_session", new=AsyncMock()) as run_mock:
        outcome = await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Second-WIKI"),
            telegram_id=42,
            user_text="x",
            source="text",
            media_paths=None,
            correlation_id="c",
        )
    run_mock.assert_not_awaited()
    assert outcome.status == "rejected"
    assert outcome.run_id is None
    assert outcome.reply.startswith("Положу в Travel-WIKI.")  # decision.notes prefix
    assert "лимит" in outcome.reply.lower()


@pytest.mark.asyncio
async def test_route_missing_target_creates_and_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter, wiki_root = _adapter(tmp_path)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("ok"))
    ):
        outcome = await adapter.ingest(
            _decision(RouterIntent.ROUTE, "Garden-WIKI"),
            telegram_id=42,
            user_text="посадил томаты",
            source="text",
            media_paths=None,
            correlation_id="c",
        )
    assert outcome.status == "ok"
    assert outcome.created is True
    assert (wiki_root / "42" / "Garden-WIKI").is_dir()
    assert "inbox.route.route_target_was_missing" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_log_anchors_emitted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    adapter, _ = _adapter(tmp_path)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("ok"))
    ):
        await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"),
            telegram_id=7,
            user_text="t",
            source="text",
            media_paths=None,
            correlation_id="c",
        )
    out = capsys.readouterr().out
    for anchor in (
        "inbox.route.target_resolved",
        "inbox.route.raw_moved",
        "inbox.route.ingest.begin",
        "inbox.route.ingest.done",
    ):
        assert anchor in out, f"missing {anchor} in:\n{out}"


@pytest.mark.asyncio
async def test_media_is_promoted_into_target_wiki(tmp_path: Path) -> None:
    adapter, wiki_root = _adapter(tmp_path)
    staged = tmp_path / "staging" / "shot.jpg"
    staged.parent.mkdir()
    staged.write_bytes(b"\xff\xd8\xff")
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("ok"))
    ) as run_mock:
        await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Photos-WIKI"),
            telegram_id=42,
            user_text="чек",
            source="photo",
            media_paths=[staged],
            correlation_id="c",
        )
    wiki_dir = wiki_root / "42" / "Photos-WIKI"
    promoted = list((wiki_dir / "raw" / "media").glob("*.jpg"))
    assert len(promoted) == 1
    assert not staged.exists()  # moved
    media_paths = run_mock.await_args.kwargs["media_paths"]
    assert media_paths is not None
    assert promoted[0] in media_paths
