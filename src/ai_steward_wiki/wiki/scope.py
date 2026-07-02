# FILE: src/ai_steward_wiki/wiki/scope.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Scope resolution for group-2 intents (aisw-o6m) — decide whether a
#            wiki_query run is confidently single-domain (scoped run inside that
#            WIKI, like ingest) or cross-WIKI, and build the bounded layouts
#            block («Карта WIKI пользователя») injected into cross runs so the
#            model knows every WIKI's Data layout paths.
#   SCOPE: ScopeDecision, resolve_query_scope, collect_layouts,
#          MAX_LAYOUT_CHARS_PER_WIKI. Pure logic, stdlib only; no IO beyond
#          reading per-WIKI CLAUDE.md in collect_layouts.
#   DEPENDS: ai_steward_wiki.inbox.hint_match, ai_steward_wiki.wiki.migration
#   LINKS: M-WIKI-SCOPE, ADR-034, ADR-032, D-016, aisw-50z, aisw-o6m
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ScopeDecision - frozen result: kind ("scoped"|"cross"), target stem/path, score, margin
#   resolve_query_scope - (text, catalog, wikis) -> ScopeDecision via hint_match thresholds
#   collect_layouts - ((stem, path), ...) -> ru layouts block from managed zones, capped per WIKI
#   MAX_LAYOUT_CHARS_PER_WIKI - per-WIKI excerpt cap protecting the prompt budget
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-o6m: initial module (Variant 2 of /best-approach,
#                ADR-034 adapter-side scope resolution).
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from ai_steward_wiki.inbox.hint_match import MAX_FASTPATH_CHARS, is_confident, score_catalog
from ai_steward_wiki.wiki.migration import MANAGED_END, MANAGED_START

__all__ = [
    "MAX_LAYOUT_CHARS_PER_WIKI",
    "ScopeDecision",
    "collect_layouts",
    "resolve_query_scope",
]

# Managed zones are template-controlled and small (≈3-4 KB); the cap only guards
# against a pathological hand-edited CLAUDE.md blowing the system prompt.
MAX_LAYOUT_CHARS_PER_WIKI: Final[int] = 4000

_LAYOUTS_HEADER: Final[str] = "# Карта WIKI пользователя"
_NO_SCHEMA_FALLBACK: Final[str] = "(схема не описана)"


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """Outcome of pre-run scope resolution for a wiki_query."""

    kind: Literal["scoped", "cross"]
    target_stem: str | None
    target_path: Path | None
    top_score: float
    margin: float


_CROSS: Final[ScopeDecision] = ScopeDecision("cross", None, None, 0.0, 0.0)


# START_CONTRACT: resolve_query_scope
#   PURPOSE: Decide whether a wiki_query confidently belongs to ONE domain WIKI.
#   INPUTS: { text: str - the user query,
#             catalog: Mapping[str, str] - {wiki_stem: inbox hint text},
#             wikis: Mapping[str, Path] - {wiki_stem: wiki dir} }
#   OUTPUTS: { ScopeDecision - "scoped" with target only when the prod ingest
#              fast-path gates pass (score/margin/length) AND the stem has a dir }
#   SIDE_EFFECTS: none (pure)
#   LINKS: M-WIKI-SCOPE, ADR-034, inbox.hint_match (MIN_SCORE/MIN_MARGIN/MAX_FASTPATH_CHARS)
# END_CONTRACT: resolve_query_scope
def resolve_query_scope(
    text: str,
    catalog: Mapping[str, str],
    wikis: Mapping[str, Path],
) -> ScopeDecision:
    # START_BLOCK_SCOPE_RESOLVE
    if len(text) > MAX_FASTPATH_CHARS:
        return _CROSS
    match = score_catalog(text, catalog)
    if not is_confident(match) or match.top_stem is None:
        return _CROSS
    target_path = wikis.get(match.top_stem)
    if target_path is None:
        return _CROSS
    return ScopeDecision(
        kind="scoped",
        target_stem=match.top_stem,
        target_path=target_path,
        top_score=match.top_score,
        margin=match.margin,
    )
    # END_BLOCK_SCOPE_RESOLVE


def _managed_zone_excerpt(wiki_dir: Path) -> str:
    claude_md = wiki_dir / "CLAUDE.md"
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError:
        return _NO_SCHEMA_FALLBACK
    start = text.find(MANAGED_START)
    end = text.find(MANAGED_END)
    if start == -1 or end == -1 or end <= start:
        return _NO_SCHEMA_FALLBACK
    body = text[start + len(MANAGED_START) : end].strip("\n").strip()
    if not body:
        return _NO_SCHEMA_FALLBACK
    return body[:MAX_LAYOUT_CHARS_PER_WIKI]


# START_CONTRACT: collect_layouts
#   PURPOSE: Build the ru layouts block injected into cross-WIKI runs (Phase A).
#   INPUTS: { wikis: Sequence[tuple[str, Path]] - (stem, wiki dir) pairs }
#   OUTPUTS: { str - "# Карта WIKI пользователя" + per-WIKI managed-zone excerpts;
#              "" when wikis is empty }
#   SIDE_EFFECTS: reads <wiki>/CLAUDE.md files (read-only)
#   LINKS: M-WIKI-SCOPE, ADR-034, wiki.migration (MANAGED_START/MANAGED_END)
# END_CONTRACT: collect_layouts
def collect_layouts(wikis: Sequence[tuple[str, Path]]) -> str:
    # START_BLOCK_COLLECT_LAYOUTS
    if not wikis:
        return ""
    pieces = [_LAYOUTS_HEADER]
    for stem, wiki_dir in wikis:
        pieces.append(f"## {stem}\n{_managed_zone_excerpt(wiki_dir)}")
    return "\n\n".join(pieces)
    # END_BLOCK_COLLECT_LAYOUTS
