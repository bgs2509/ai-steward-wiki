"""_DigestRunnerAdapter — section mode (aisw-269, Phase-D.b.2b)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ai_steward_wiki.__main__ as main_mod
from ai_steward_wiki.__main__ import _DigestRunnerAdapter, _RunConfig
from ai_steward_wiki.scheduler.locks import WikiLockManager
from ai_steward_wiki.wiki.acquire import WikiLockAdapter
from ai_steward_wiki.wiki.runner import AsyncioSpawner


class _Result:
    def __init__(self) -> None:
        self.events: list[Any] = []


def _adapter(tmp_path: Path) -> _DigestRunnerAdapter:
    return _DigestRunnerAdapter(
        base_prompt_path=tmp_path / "wiki.md",
        digest_prompt_path=tmp_path / "digest.md",
        digest_expand_prompt_path=tmp_path / "digest_expand.md",
        runtime_dir=tmp_path / "rt",
        acquirer=WikiLockAdapter(WikiLockManager(max_concurrent_cli=2)),
        spawner=AsyncioSpawner(),
        run_config=_RunConfig(claude_config_dir=tmp_path / "cc"),
    )


@pytest.fixture
def capture_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_run_wiki_session(**kw: Any) -> _Result:
        captured.update(kw)
        return _Result()

    monkeypatch.setattr(main_mod, "run_wiki_session", _fake_run_wiki_session)
    monkeypatch.setattr(main_mod, "aggregate_text", lambda _events: "OUT")
    return captured


async def test_digest_adapter_full_digest_uses_digest_prompt(
    tmp_path: Path, capture_run: dict[str, Any]
) -> None:
    adapter = _adapter(tmp_path)
    out = await adapter(
        wiki_id="medical",
        wiki_path=tmp_path / "Medical-WIKI",
        extra_add_dirs=[],
        planner_context="PLAN",
        correlation_id="c",
    )
    assert out == "OUT"
    assert capture_run["overlay_prompt_path"] == tmp_path / "digest.md"
    assert capture_run["user_input"] == "PLAN"
    assert capture_run["run_id"].startswith("digest-")


async def test_digest_adapter_full_digest_injects_extra_wiki_layouts(
    tmp_path: Path, capture_run: dict[str, Any]
) -> None:
    # aisw-o6m Phase A: the primary WIKI CLAUDE.md arrives via assemble_prompt;
    # non-primary WIKIs (extra_add_dirs) get their managed zones appended to
    # planner_context so the digest run sees every layout.
    from ai_steward_wiki.wiki.migration import MANAGED_END, MANAGED_START

    budget = tmp_path / "Budget-WIKI"
    budget.mkdir()
    (budget / "CLAUDE.md").write_text(
        f"---\nschema_version: 2\n---\n{MANAGED_START}\n## Data layout\n"
        f"1. `expenses/`\n{MANAGED_END}\n",
        encoding="utf-8",
    )
    adapter = _adapter(tmp_path)
    await adapter(
        wiki_id="medical",
        wiki_path=tmp_path / "Medical-WIKI",
        extra_add_dirs=[budget],
        planner_context="PLAN",
        correlation_id="c",
    )
    assert capture_run["user_input"].startswith("PLAN")
    assert "Карта WIKI пользователя" in capture_run["user_input"]
    assert "## Budget-WIKI" in capture_run["user_input"]
    assert "expenses/" in capture_run["user_input"]


async def test_digest_adapter_section_expand_skips_layouts(
    tmp_path: Path, capture_run: dict[str, Any]
) -> None:
    budget = tmp_path / "Budget-WIKI"
    budget.mkdir()
    adapter = _adapter(tmp_path)
    await adapter(
        wiki_id="medical",
        wiki_path=tmp_path / "Medical-WIKI",
        extra_add_dirs=[budget],
        planner_context="",
        correlation_id="c",
        section="trackers",
    )
    assert "Карта WIKI пользователя" not in capture_run["user_input"]


async def test_digest_adapter_section_uses_expand_prompt(
    tmp_path: Path, capture_run: dict[str, Any]
) -> None:
    adapter = _adapter(tmp_path)
    out = await adapter(
        wiki_id="medical",
        wiki_path=tmp_path / "Medical-WIKI",
        extra_add_dirs=[],
        planner_context="",
        correlation_id="c",
        section="trackers",
    )
    assert out == "OUT"
    assert capture_run["overlay_prompt_path"] == tmp_path / "digest_expand.md"
    assert "trackers" in capture_run["user_input"]
    assert capture_run["run_id"].startswith("expand-")
