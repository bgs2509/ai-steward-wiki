# FILE: tests/unit/wiki/test_scope.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for wiki.scope — ScopeDecision resolution for wiki_query
#            and layouts-block collection for sighted cross-WIKI runs (aisw-o6m).
#   SCOPE: resolve_query_scope confident/ambiguous/empty/long/missing-dir paths;
#          collect_layouts happy/missing/no-markers/cap/empty paths.
#   DEPENDS: pytest, ai_steward_wiki.wiki.scope, ai_steward_wiki.wiki.migration
#   LINKS: M-WIKI-SCOPE, ADR-034, docs/superpowers/plans/20260702-adaptive-scope-plan.md
#   ROLE: TEST
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-o6m: initial tests for M-WIKI-SCOPE (ADR-034).
# END_CHANGE_SUMMARY

from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.inbox.hint_match import MAX_FASTPATH_CHARS
from ai_steward_wiki.wiki.migration import MANAGED_END, MANAGED_START
from ai_steward_wiki.wiki.scope import (
    MAX_LAYOUT_CHARS_PER_WIKI,
    ScopeDecision,
    collect_layouts,
    resolve_query_scope,
)

_CATALOG = {
    "Medical-WIKI": "keywords: здоровье, давление, калории, ккал, еда, анализ",
    "Budget-WIKI": "keywords: бюджет, расходы, траты, рубли, магазин",
}


def _wikis(tmp_path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for stem in _CATALOG:
        d = tmp_path / stem
        d.mkdir()
        out[stem] = d
    return out


# START_BLOCK_TEST_RESOLVE_SCOPE
class TestResolveQueryScope:
    def test_confident_single_match_is_scoped(self, tmp_path: Path) -> None:
        wikis = _wikis(tmp_path)
        # NB: hint_match is exact-token overlap (no morphology) — the query must
        # hit catalog word forms verbatim, same constraint as the prod fast-path.
        decision = resolve_query_scope("еда и ккал за сегодня", _CATALOG, wikis)
        assert decision.kind == "scoped"
        assert decision.target_stem == "Medical-WIKI"
        assert decision.target_path == wikis["Medical-WIKI"]
        assert decision.top_score >= 2.0
        assert decision.margin >= 1.0

    def test_ambiguous_two_domains_is_cross(self, tmp_path: Path) -> None:
        wikis = _wikis(tmp_path)
        # One keyword from each catalog entry -> margin 0 -> not confident.
        decision = resolve_query_scope("калории и расходы", _CATALOG, wikis)
        assert decision.kind == "cross"
        assert decision.target_stem is None
        assert decision.target_path is None

    def test_empty_catalog_is_cross(self, tmp_path: Path) -> None:
        decision = resolve_query_scope("сколько калорий я ем", {}, {})
        assert decision.kind == "cross"

    def test_long_text_is_cross_even_when_confident(self, tmp_path: Path) -> None:
        wikis = _wikis(tmp_path)
        long_text = "еда и ккал за сегодня " + "x" * MAX_FASTPATH_CHARS
        decision = resolve_query_scope(long_text, _CATALOG, wikis)
        assert decision.kind == "cross"

    def test_matched_stem_missing_from_wikis_is_cross(self, tmp_path: Path) -> None:
        # Catalog knows Medical, but the dir mapping does not (race: WIKI deleted).
        decision = resolve_query_scope("еда и ккал за сегодня", _CATALOG, {})
        assert decision.kind == "cross"
        assert decision.target_path is None

    def test_decision_is_frozen(self, tmp_path: Path) -> None:
        decision = resolve_query_scope("привет", {}, {})
        assert isinstance(decision, ScopeDecision)
        try:
            decision.kind = "scoped"  # type: ignore[misc]
            raise AssertionError("ScopeDecision must be frozen")
        except AttributeError:
            pass


# END_BLOCK_TEST_RESOLVE_SCOPE


# START_BLOCK_TEST_COLLECT_LAYOUTS
class TestCollectLayouts:
    def _write_claude_md(self, wiki_dir: Path, managed_body: str) -> None:
        wiki_dir.mkdir(parents=True, exist_ok=True)
        (wiki_dir / "CLAUDE.md").write_text(
            f"---\nschema_version: 2\n---\n{MANAGED_START}\n{managed_body}\n{MANAGED_END}\n",
            encoding="utf-8",
        )

    def test_happy_path_contains_header_and_zones(self, tmp_path: Path) -> None:
        med = tmp_path / "Medical-WIKI"
        cook = tmp_path / "Cooking-WIKI"
        self._write_claude_md(med, "## Data layout\n1. `diet/food_log.csv`")
        self._write_claude_md(cook, "## Data layout\n1. `recipes/`")
        block = collect_layouts([("Medical-WIKI", med), ("Cooking-WIKI", cook)])
        assert "# Карта WIKI пользователя" in block
        assert "## Medical-WIKI" in block
        assert "diet/food_log.csv" in block
        assert "## Cooking-WIKI" in block
        assert "recipes/" in block

    def test_missing_claude_md_falls_back(self, tmp_path: Path) -> None:
        empty = tmp_path / "Empty-WIKI"
        empty.mkdir()
        block = collect_layouts([("Empty-WIKI", empty)])
        assert "## Empty-WIKI" in block
        assert "(схема не описана)" in block

    def test_markers_absent_falls_back(self, tmp_path: Path) -> None:
        legacy = tmp_path / "Legacy-WIKI"
        legacy.mkdir()
        (legacy / "CLAUDE.md").write_text("just prose, no markers", encoding="utf-8")
        block = collect_layouts([("Legacy-WIKI", legacy)])
        assert "(схема не описана)" in block

    def test_per_wiki_excerpt_is_capped(self, tmp_path: Path) -> None:
        big = tmp_path / "Big-WIKI"
        self._write_claude_md(big, "x" * (MAX_LAYOUT_CHARS_PER_WIKI * 3))
        block = collect_layouts([("Big-WIKI", big)])
        # Header + stem line + capped excerpt: generous upper bound.
        assert len(block) < MAX_LAYOUT_CHARS_PER_WIKI * 2

    def test_empty_wikis_returns_empty_string(self) -> None:
        assert collect_layouts([]) == ""


# END_BLOCK_TEST_COLLECT_LAYOUTS
