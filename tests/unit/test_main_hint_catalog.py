"""Tests for __main__.make_hint_catalog_resolver (aisw-5sd, Phase-E.b).

The factory composes the surrogate-id lookup, the owner→WIKIs resolver and the
hint cache into a telegram_id → {wiki_stem: hint_text} resolver for the
pre-router fast-path. get_or_refresh_hint is monkeypatched here so the test
needs no real sessions.db or CLAUDE.md files — the catalog-assembly logic
(skip on missing surrogate id, drop domains with no hint) is what matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ai_steward_wiki.__main__ as main_mod


@pytest.mark.asyncio
async def test_make_hint_catalog_resolver_assembles_and_filters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_get_or_refresh_hint(repo: object, uid: int, claude_md_path: Path) -> str | None:
        stem = claude_md_path.parent.name
        return None if stem == "Empty-WIKI" else f"hint for {stem}"

    monkeypatch.setattr(main_mod, "get_or_refresh_hint", fake_get_or_refresh_hint)

    async def owner_wikis(telegram_id: int) -> list[tuple[str, Path]]:
        return [
            ("Health-WIKI", tmp_path / "Health-WIKI"),
            ("Empty-WIKI", tmp_path / "Empty-WIKI"),
            ("Investment-WIKI", tmp_path / "Investment-WIKI"),
        ]

    async def surrogate_id_of(telegram_id: int) -> int | None:
        return 1 if telegram_id == 42 else None

    resolver = main_mod.make_hint_catalog_resolver(
        hint_repo=object(),  # never touched — get_or_refresh_hint is faked
        owner_wikis_resolver=owner_wikis,
        surrogate_id_of=surrogate_id_of,
    )

    assert await resolver(42) == {
        "Health-WIKI": "hint for Health-WIKI",
        "Investment-WIKI": "hint for Investment-WIKI",
    }
    # unknown sender (no users row) → empty catalog, fast-path falls through
    assert await resolver(99) == {}


@pytest.mark.asyncio
async def test_make_hint_catalog_resolver_no_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_refresh_hint(repo: object, uid: int, claude_md_path: Path) -> str | None:
        raise AssertionError("should not be called when the sender has no domain WIKIs")

    monkeypatch.setattr(main_mod, "get_or_refresh_hint", fake_get_or_refresh_hint)

    async def owner_wikis(telegram_id: int) -> list[tuple[str, Path]]:
        return []

    async def surrogate_id_of(telegram_id: int) -> int | None:
        return 7

    resolver = main_mod.make_hint_catalog_resolver(
        hint_repo=object(),
        owner_wikis_resolver=owner_wikis,
        surrogate_id_of=surrogate_id_of,
    )
    assert await resolver(42) == {}
