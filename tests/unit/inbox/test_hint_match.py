"""Tests for the deterministic '## Inbox hint' catalog scorer (aisw-5sd, Phase-E.b).

The scorer is the Option-A matching mechanism behind the pre-router fast-path:
normalised token-overlap between the incoming content and each domain WIKI's
cached `## Inbox hint`, with a conservative single-unambiguous-match predicate.
"""

from __future__ import annotations

from ai_steward_wiki.inbox.hint_match import (
    MIN_MARGIN,
    MIN_SCORE,
    HintMatch,
    is_confident,
    score_catalog,
)

_HEALTH = "Ключевые слова: давление, пульс, анализы, лекарства, врач, симптомы, самочувствие."
_INVEST = "Ключевые слова: акции, облигации, дивиденды, портфель, брокер, доходность."


def test_single_clear_winner_is_confident() -> None:
    catalog = {"Health-WIKI": _HEALTH, "Investment-WIKI": _INVEST}
    m = score_catalog(
        "давление 130, сдал анализы, выписали лекарства, пульс высокий, был врач", catalog
    )
    assert m.top_stem == "Health-WIKI"
    assert m.top_score >= MIN_SCORE
    assert m.margin >= MIN_MARGIN
    assert is_confident(m) is True


def test_ambiguous_two_domains_not_confident() -> None:
    catalog = {"Health-WIKI": _HEALTH, "Investment-WIKI": _INVEST}
    # tokens drawn roughly equally from both hints → small margin
    m = score_catalog("давление анализы лекарства акции дивиденды портфель", catalog)
    assert m.top_stem is not None  # something ranks first
    assert m.margin < MIN_MARGIN
    assert is_confident(m) is False


def test_weak_winner_below_threshold_not_confident() -> None:
    catalog = {"Health-WIKI": _HEALTH, "Investment-WIKI": _INVEST}
    m = score_catalog("сегодня был хороший солнечный день погуляли парк", catalog)
    assert m.top_score < MIN_SCORE
    assert is_confident(m) is False


def test_empty_catalog() -> None:
    m = score_catalog("давление 130", {})
    assert m.top_stem is None
    assert m.ranked == ()
    assert m.top_score == 0.0
    assert m.margin == 0.0
    assert is_confident(m) is False


def test_stem_name_token_is_folded_in() -> None:
    # Hint body has no overlap; the English stem token "health" carries the match.
    catalog = {"Health-WIKI": "записи о текущем состоянии", "Investment-WIKI": _INVEST}
    m = score_catalog("health checkup notes today", catalog)
    assert m.top_stem == "Health-WIKI"
    assert m.top_score > 0.0


def test_token_normalisation_drops_short_and_stopwords() -> None:
    catalog = {"Health-WIKI": "анализы"}
    base = score_catalog("анализы", catalog)
    noisy = score_catalog("И В THE на по 12 ok Анализы", catalog)  # stopwords + <3-char + casing
    assert noisy.top_score == base.top_score
    assert base.top_score > 0.0


def test_is_confident_boundaries() -> None:
    assert (
        is_confident(
            HintMatch((("A", MIN_SCORE), ("B", MIN_SCORE - MIN_MARGIN)), "A", MIN_SCORE, MIN_MARGIN)
        )
        is True
    )
    # score just below the floor
    assert (
        is_confident(HintMatch((("A", MIN_SCORE - 1e-6),), "A", MIN_SCORE - 1e-6, MIN_SCORE - 1e-6))
        is False
    )
    # margin just below the floor
    assert (
        is_confident(
            HintMatch(
                (("A", MIN_SCORE + 0.2), ("B", MIN_SCORE + 0.2 - MIN_MARGIN + 1e-6)),
                "A",
                MIN_SCORE + 0.2,
                MIN_MARGIN - 1e-6,
            )
        )
        is False
    )
    assert is_confident(HintMatch((), None, 0.0, 0.0)) is False
