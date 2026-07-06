# FILE: src/ai_steward_wiki/inbox/hint_match.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Deterministic '## Inbox hint' catalog token-overlap scoring + a conservative 'confident single match' predicate for the pre-router fast-path (D-016, tech-spec §4/§8.3.3).
#   SCOPE: token normalisation; score_catalog (text vs {stem: hint_text}); is_confident; HintMatch; threshold constants.
#   DEPENDS: stdlib only (re, dataclasses, collections.abc)
#   LINKS: D-004, D-016, tech-spec §4, §8.3.3, M-INBOX, M-TG-PIPELINE-CLASSIFIER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   HintMatch - frozen result: ranked ((stem, score), ...), top_stem, top_score, margin
#   score_catalog - text vs {stem: hint_text} -> HintMatch (token-overlap count; stem name folded in)
#   is_confident - True iff top_stem is set and top_score >= MIN_SCORE and margin >= MIN_MARGIN
#   MIN_TOKEN_LEN - shortest token length that counts (kills 1-2 char noise)
#   MIN_SCORE - confidence floor: min matched-keyword count to fire the bypass
#   MIN_MARGIN - min lead over the runner-up domain to fire the bypass
#   MAX_FASTPATH_CHARS - max input length for the fast-path; longer → heavy router (aisw-378)
#   tokens - lowercased word-runs, dropping too-short tokens and stop-words (was _tokens)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-xi8 (Phase-B, DEC-9): promote the formerly-private
#                _tokens to a public tokens() — same normalisation, same return
#                type (frozenset[str]); a pure rename, no logic change.
#                scheduler.manage.match_jobs_by_needle reuses it directly.
#   PREVIOUS:    v0.0.1 - initial deterministic hint-match scorer (aisw-5sd, Phase-E.b)
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "MAX_FASTPATH_CHARS",
    "MIN_MARGIN",
    "MIN_SCORE",
    "MIN_TOKEN_LEN",
    "HintMatch",
    "is_confident",
    "score_catalog",
    "tokens",
]

# A token must be at least this long to count — kills "и", "на", "ok", noise.
MIN_TOKEN_LEN: Final[int] = 3

# Tiny ru/en function-word set; not exhaustive — just the high-frequency joiners
# that would otherwise inflate overlap scores on long hint strings.
_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {
        "и",
        "в",
        "во",
        "на",
        "по",
        "от",
        "до",
        "за",
        "из",
        "или",
        "это",
        "для",
        "что",
        "как",
        "the",
        "and",
        "for",
        "with",
        "you",
        "your",
        "are",
        "this",
        "that",
    }
)

# Score = raw count of distinct domain keywords the message hit (a float so the
# margin arithmetic stays uniform). A raw count compares cleanly across domains
# whose hint strings differ wildly in length — a ratio over the hint length does
# not (a 4-word hint and a 40-word hint would not be on the same scale). The
# scoring shape is the OQ-C detail the design left to the plan; the Option-A
# spirit (deterministic token overlap, single unambiguous match) is unchanged.
#
# Conservative floor + required margin over the runner-up (NFR-2: precision over
# recall — a wrong auto-route the user then cancels is worse than a 20 s heavy-
# router run). Need ≥2 matched keywords AND ≥1 more than the runner-up. Tunable
# offline from the tg.pipeline.hint_fastpath.* logs; deliberately NOT exposed via
# settings (no env knob until the logs justify one — YAGNI / D-032).
MIN_SCORE: Final[float] = 2.0
MIN_MARGIN: Final[float] = 1.0

# aisw-378: the keyword fast-path is reliable only for SHORT messages
# ("давление 120/80" → Medical). On a long document, incidental keyword overlap
# (e.g. "анализ" in a coal-industry report colliding with Medical's hint) almost
# always clears MIN_SCORE/MIN_MARGIN by chance → false auto-route. Above this
# length the caller skips the fast-path and lets the context-aware Sonnet router
# decide. Caller-applied (it owns the text length); kept here with the other
# fast-path tunables.
MAX_FASTPATH_CHARS: Final[int] = 600

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)
_STEM_SUFFIX: Final[str] = "-wiki"


def tokens(text: str) -> frozenset[str]:
    """Lowercased word-runs, dropping too-short tokens and stop-words."""
    return frozenset(
        t
        for t in _WORD_RE.findall(text.casefold())
        if len(t) >= MIN_TOKEN_LEN and t not in _STOP_WORDS
    )


def _stem_tokens(stem: str) -> frozenset[str]:
    """Tokens from the WIKI stem name itself (minus a trailing '-WIKI')."""
    name = stem.casefold()
    if name.endswith(_STEM_SUFFIX):
        name = name[: -len(_STEM_SUFFIX)]
    return tokens(name)


@dataclass(frozen=True, slots=True)
class HintMatch:
    """Result of scoring incoming content against a domain hint catalog."""

    ranked: tuple[tuple[str, float], ...]  # (stem, score), score desc then stem asc
    top_stem: str | None
    top_score: float
    margin: float  # top_score - second_score (second = 0.0 if only one domain scored)


# START_CONTRACT: score_catalog
#   PURPOSE: Score incoming content against each domain's cached '## Inbox hint'.
#   INPUTS: { text: str - distilled payload or raw message, catalog: Mapping[str, str] - {wiki_stem: hint_text} }
#   OUTPUTS: { HintMatch - ranked domains by token-overlap count; empty/None on empty catalog }
#   SIDE_EFFECTS: none (pure)
#   LINKS: D-016, M-INBOX, M-TG-PIPELINE-CLASSIFIER
# END_CONTRACT: score_catalog
def score_catalog(text: str, catalog: Mapping[str, str]) -> HintMatch:
    if not catalog:
        return HintMatch((), None, 0.0, 0.0)
    txt = tokens(text)
    scored: list[tuple[str, float]] = []
    for stem, hint_text in catalog.items():
        domain = tokens(hint_text) | _stem_tokens(stem)
        scored.append((stem, float(len(txt & domain))))
    ranked = tuple(sorted(scored, key=lambda kv: (-kv[1], kv[0])))
    top_stem, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    return HintMatch(ranked, top_stem, top_score, top_score - second_score)


# START_CONTRACT: is_confident
#   PURPOSE: Decide whether a HintMatch is a confident single-domain match worth bypassing the heavy router.
#   INPUTS: { m: HintMatch }
#   OUTPUTS: { bool - True iff top_stem set and top_score >= MIN_SCORE and margin >= MIN_MARGIN }
#   SIDE_EFFECTS: none (pure)
#   LINKS: NFR-2, M-TG-PIPELINE-CLASSIFIER
# END_CONTRACT: is_confident
def is_confident(m: HintMatch) -> bool:
    return m.top_stem is not None and m.top_score >= MIN_SCORE and m.margin >= MIN_MARGIN
