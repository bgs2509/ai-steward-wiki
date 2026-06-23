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


def _fake_run_result_with_narration(narration: list[str], answer: str) -> MagicMock:
    """Events that interleave inter-tool narration with tool calls, then the answer."""

    def _msg(*content: dict[str, object]) -> StreamEvent:
        return StreamEvent(type="assistant_chunk", payload={"message": {"content": list(content)}})

    tool = {"type": "tool_use", "id": "t", "name": "Read", "input": {}}
    events = [_msg({"type": "text", "text": n}, tool) for n in narration]
    events.append(_msg({"type": "text", "text": answer}))
    r = MagicMock()
    r.events = events
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
    # aisw-2n2: ok reply is the final-turn summary only — no duplicated decision.notes
    # prefix (the routing/classification was already shown in the confirmation message).
    assert outcome.reply == "Записал X на стр. trips.md."
    assert not outcome.reply.startswith("Положу в Travel-WIKI.")


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
async def test_ok_reply_strips_inter_tool_narration(tmp_path: Path) -> None:
    """aisw-2n2: only the trailing answer reaches the user, not the tool narration."""
    adapter, _ = _adapter(tmp_path)
    result = _fake_run_result_with_narration(
        narration=["Прочитаю сырьё…", "Вижу query…", "Теперь понятно…"],
        answer="Давление 131/92, в норме.",
    )
    with patch.object(runtime, "run_wiki_session", new=AsyncMock(return_value=result)):
        outcome = await adapter.ingest(
            _decision(RouterIntent.ROUTE, "Medical-WIKI"),
            telegram_id=42,
            user_text="что со здоровьем?",
            source="text",
            media_paths=None,
            correlation_id="c",
        )
    assert outcome.status == "ok"
    assert outcome.reply == "Давление 131/92, в норме."
    assert "Прочитаю сырьё" not in outcome.reply
    assert "query" not in outcome.reply


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


# ---------- aisw-b50: schema generation for unknown-domain creates ----------


def _gen_adapter(tmp_path: Path, generator: object) -> tuple[runtime._LibrarianAdapter, Path, Path]:
    """Adapter wired with a templates_dir (so resolve_template_id works) + a generator."""
    wiki_root = tmp_path / "wikis"
    wiki_root.mkdir()
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "_default.md").write_text("# Default\n## Data layout\ngeneric\n", encoding="utf-8")
    (tdir / "medical.md").write_text("# Medical\n## Data layout\nmetrics\n", encoding="utf-8")
    adapter = runtime._LibrarianAdapter(
        wiki_root=wiki_root,
        prompts_dir=_prompts_dir(tmp_path),
        lifecycle=WikiLifecycleManager(wiki_root, templates_dir=tdir),
        runtime_dir=tmp_path / "runtime",
        acquirer=MagicMock(),
        spawner=MagicMock(),
        run_config=MagicMock(),
        schema_generator=generator,
    )
    return adapter, wiki_root, tdir


@pytest.mark.asyncio
async def test_unknown_domain_triggers_schema_generation(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.schema_gen import FakeSchemaGenerator

    good = (
        "## Data layout\n1. `pages/` md\n## File resolution\nappend\n"
        "## Inbox hint\nintents: page_create\n## Персонажи\nкарточки\n"
    )
    gen = FakeSchemaGenerator(canned=good)
    adapter, wiki_root, _ = _gen_adapter(tmp_path, gen)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("ok"))
    ):
        await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Anime-Maguro"),
            telegram_id=42,
            user_text="инфа про магуро аниме",
            source="text",
            media_paths=None,
            correlation_id="tg-9-42",
        )
    assert len(gen.calls) == 1  # generator invoked for the unknown domain
    claude = wiki_root / "42" / "AnimeMaguro-WIKI" / "CLAUDE.md"
    assert "## Персонажи" in claude.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_known_domain_skips_schema_generation(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.schema_gen import FakeSchemaGenerator

    gen = FakeSchemaGenerator(canned="should not be used")
    adapter, wiki_root, _ = _gen_adapter(tmp_path, gen)
    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(return_value=_fake_run_result("ok"))
    ):
        await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Medical"),
            telegram_id=42,
            user_text="давление 120 80",
            source="text",
            media_paths=None,
            correlation_id="tg-10-42",
        )
    assert gen.calls == []  # known preset -> no generation
    claude = wiki_root / "42" / "Medical-WIKI" / "CLAUDE.md"
    assert "metrics" in claude.read_text(encoding="utf-8")  # medical preset applied


# ---------- aisw-zpn: ingest timeout -> honest partial vs failed ----------


def test_wiki_has_ingested_content_ignores_meta_and_staging(tmp_path: Path) -> None:
    w = tmp_path / "W"
    (w / "raw").mkdir(parents=True)
    (w / "runs").mkdir()
    (w / "CLAUDE.md").write_text("schema", encoding="utf-8")
    (w / "log.md").write_text("", encoding="utf-8")
    (w / "raw" / "doc.md").write_text("source", encoding="utf-8")
    # only meta + staging -> no ingested content yet
    assert runtime._wiki_has_ingested_content(w) is False
    # a real data file under a content dir -> True
    (w / "metrics").mkdir()
    (w / "metrics" / "production.csv").write_text("year,vol\n2021,438\n", encoding="utf-8")
    assert runtime._wiki_has_ingested_content(w) is True


@pytest.mark.asyncio
async def test_ingest_timeout_with_partial_data_reports_partial(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.runner import WikiRunnerTimeoutError

    adapter, _ = _adapter(tmp_path)

    async def _timeout(**kwargs: object) -> object:
        wp = kwargs["wiki_path"]
        assert isinstance(wp, Path)
        (wp / "metrics").mkdir(exist_ok=True)
        (wp / "metrics" / "production.csv").write_text("year,vol\n2021,438\n", encoding="utf-8")
        raise WikiRunnerTimeoutError("ingest exceeded budget")

    with patch.object(runtime, "run_wiki_session", new=AsyncMock(side_effect=_timeout)):
        outcome = await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Coal"),
            telegram_id=42,
            user_text="огромный документ",
            source="text",
            media_paths=None,
            correlation_id="tg-1-42",
        )

    assert outcome.status == "partial"
    assert "частично" in outcome.reply
    assert "ещё раз" in outcome.reply


@pytest.mark.asyncio
async def test_ingest_timeout_with_no_data_reports_failed(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.runner import WikiRunnerTimeoutError

    adapter, _ = _adapter(tmp_path)

    with patch.object(
        runtime, "run_wiki_session", new=AsyncMock(side_effect=WikiRunnerTimeoutError("t"))
    ):
        outcome = await adapter.ingest(
            _decision(RouterIntent.CREATE_WIKI, "Coal"),
            telegram_id=42,
            user_text="x",
            source="text",
            media_paths=None,
            correlation_id="tg-1-42",
        )

    assert outcome.status == "run_failed"
    assert "Не удалось" in outcome.reply
