# FILE: tests/unit/test_main_runner_adapter.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for _WikiRunnerAdapter adaptive scoping (aisw-o6m) —
#            wiki_query scoped/cross/degraded paths, web_task isolation, and
#            backward-compat when resolvers are not wired.
#   SCOPE: run() wiki_path/wiki_id selection, scratch-overlay layouts injection,
#          graceful degradation, per-intent config selection untouched.
#   DEPENDS: pytest, ai_steward_wiki.__main__, ai_steward_wiki.wiki.runner
#   LINKS: M-WIKI-SCOPE, ADR-034, docs/superpowers/plans/20260702-adaptive-scope-plan.md
#   ROLE: TEST
#   MAP_MODE: NONE
# END_MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ai_steward_wiki.__main__ as main_mod
from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.wiki.migration import MANAGED_END, MANAGED_START
from ai_steward_wiki.wiki.runner import WikiRunResult, _RunConfig

_TID = 763463467
_PLACEHOLDER = "semver: 1.0.0\n\n# User turn\n"
_CATALOG = {
    "Medical-WIKI": "keywords: здоровье, давление, калории, ккал, еда, анализ",
    "Budget-WIKI": "keywords: бюджет, расходы, траты, рубли, магазин",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Adapter + captured run_wiki_session kwargs + on-disk user root with 2 WIKIs."""
    wiki_root = tmp_path / "wikis"
    user_root = wiki_root / str(_TID)
    wikis: dict[str, Path] = {}
    for stem, layout in (
        ("Medical-WIKI", "## Data layout\n1. `diet/food_log.csv`"),
        ("Budget-WIKI", "## Data layout\n1. `expenses/`"),
    ):
        d = user_root / stem
        d.mkdir(parents=True)
        (d / "CLAUDE.md").write_text(
            f"---\nschema_version: 2\n---\n{MANAGED_START}\n{layout}\n{MANAGED_END}\n",
            encoding="utf-8",
        )
        wikis[stem] = d

    base = tmp_path / "wiki.md"
    base.write_text("semver: 1.0.0\n\n# base\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"

    captured: dict[str, Any] = {}

    async def fake_run_wiki_session(**kwargs: Any) -> WikiRunResult:
        captured.update(kwargs)
        captured["overlay_text"] = Path(kwargs["overlay_prompt_path"]).read_text(encoding="utf-8")
        return WikiRunResult(
            run_id=kwargs["run_id"],
            exit_code=0,
            events=[],
            transcript_path=tmp_path / "t.jsonl",
            latency_ms=5,
        )

    monkeypatch.setattr(main_mod, "run_wiki_session", fake_run_wiki_session)

    resolver_calls: list[str] = []

    async def hint_catalog_resolver(telegram_id: int) -> dict[str, str]:
        resolver_calls.append(f"catalog:{telegram_id}")
        return _CATALOG

    async def owner_wikis_resolver(telegram_id: int) -> list[tuple[str, Path]]:
        resolver_calls.append(f"wikis:{telegram_id}")
        return sorted(wikis.items())

    cfg = _RunConfig(claude_config_dir=tmp_path / "cc")
    web_cfg = _RunConfig(claude_config_dir=tmp_path / "cc", web_search=True)
    adapter = main_mod._WikiRunnerAdapter(
        wiki_root=wiki_root,
        base_prompt_path=base,
        overlay_prompt_path=base,
        runtime_dir=runtime_dir,
        acquirer=object(),  # type: ignore[arg-type]  # never reached: run_wiki_session faked
        spawner=object(),  # type: ignore[arg-type]
        run_config=cfg,
        web_run_config=web_cfg,
        hint_catalog_resolver=hint_catalog_resolver,
        owner_wikis_resolver=owner_wikis_resolver,
    )
    return {
        "adapter": adapter,
        "captured": captured,
        "wikis": wikis,
        "user_root": user_root,
        "resolver_calls": resolver_calls,
        "cfg": cfg,
        "web_cfg": web_cfg,
        "wiki_root": wiki_root,
        "base": base,
        "runtime_dir": runtime_dir,
    }


# START_BLOCK_TEST_ADAPTER_SCOPE
@pytest.mark.asyncio
async def test_confident_query_runs_scoped_to_wiki(env: dict[str, Any]) -> None:
    await env["adapter"].run(
        text="еда и ккал за сегодня",
        owner_telegram_id=_TID,
        correlation_id="c1",
        intent=Intent.WIKI_QUERY,
    )
    captured = env["captured"]
    assert captured["wiki_path"] == env["wikis"]["Medical-WIKI"]
    assert captured["wiki_id"] == f"{_TID}/Medical-WIKI"
    # Scoped run: CLAUDE.md arrives via assemble_prompt — no layouts injection.
    assert "Карта WIKI пользователя" not in captured["overlay_text"]


@pytest.mark.asyncio
async def test_ambiguous_query_runs_cross_with_layouts(env: dict[str, Any]) -> None:
    await env["adapter"].run(
        text="привет что там у меня",
        owner_telegram_id=_TID,
        correlation_id="c2",
        intent=Intent.WIKI_QUERY,
    )
    captured = env["captured"]
    assert captured["wiki_path"] == env["user_root"]
    assert captured["wiki_id"] == str(_TID)
    assert "Карта WIKI пользователя" in captured["overlay_text"]
    assert "diet/food_log.csv" in captured["overlay_text"]
    assert "expenses/" in captured["overlay_text"]
    assert captured["overlay_text"].startswith("semver: 1.0.0")


@pytest.mark.asyncio
async def test_resolver_error_degrades_to_cross(env: dict[str, Any]) -> None:
    async def boom(telegram_id: int) -> dict[str, str]:
        raise RuntimeError("db down")

    env["adapter"]._hint_catalog_resolver = boom
    await env["adapter"].run(
        text="еда и ккал за сегодня",
        owner_telegram_id=_TID,
        correlation_id="c3",
        intent=Intent.WIKI_QUERY,
    )
    captured = env["captured"]
    assert captured["wiki_path"] == env["user_root"]
    assert captured["overlay_text"] == _PLACEHOLDER


@pytest.mark.asyncio
async def test_web_task_never_touches_scope(env: dict[str, Any]) -> None:
    await env["adapter"].run(
        text="еда и ккал за сегодня — найди в интернете",
        owner_telegram_id=_TID,
        correlation_id="c4",
        intent=Intent.WEB_TASK,
    )
    captured = env["captured"]
    assert env["resolver_calls"] == []
    assert captured["config"] is env["web_cfg"]
    assert captured["wiki_path"] == env["user_root"]
    assert captured["overlay_text"] == _PLACEHOLDER


@pytest.mark.asyncio
async def test_unwired_resolvers_keep_legacy_behaviour(env: dict[str, Any]) -> None:
    adapter = main_mod._WikiRunnerAdapter(
        wiki_root=env["wiki_root"],
        base_prompt_path=env["base"],
        overlay_prompt_path=env["base"],
        runtime_dir=env["runtime_dir"],
        acquirer=object(),  # type: ignore[arg-type]
        spawner=object(),  # type: ignore[arg-type]
        run_config=env["cfg"],
    )
    await adapter.run(
        text="еда и ккал за сегодня",
        owner_telegram_id=_TID,
        correlation_id="c5",
        intent=Intent.WIKI_QUERY,
    )
    captured = env["captured"]
    assert captured["wiki_path"] == env["user_root"]
    assert captured["overlay_text"] == _PLACEHOLDER


# END_BLOCK_TEST_ADAPTER_SCOPE
