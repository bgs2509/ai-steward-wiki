from __future__ import annotations

import pytest

from ai_steward_wiki.wiki.name import (
    WikiNameError,
    normalize_wiki_name,
    wiki_match_key,
    wiki_names_match,
)


def test_pascal_case_english() -> None:
    n = normalize_wiki_name("multi word")
    assert n.primary == "MultiWord-WIKI"
    assert n.hyphenated_lookup == "multi-word"
    assert n.slug == "multiword"


def test_already_pascal() -> None:
    n = normalize_wiki_name("Medical")
    assert n.primary == "Medical-WIKI"
    assert n.hyphenated_lookup == "medical"
    assert n.slug == "medical"


def test_cyrillic_basic() -> None:
    n = normalize_wiki_name("здоровье")
    assert n.primary == "Zdorove-WIKI"
    assert n.slug == "zdorove"


def test_cyrillic_compound_letters() -> None:
    # ж->zh, ц->cz, ш->sh, щ->shh, ю->yu, я->ya
    n = normalize_wiki_name("Жуцщюя")
    assert n.primary.endswith("-WIKI")
    assert n.primary.lower().replace("-wiki", "") == "zhuczshhyuya"


def test_split_on_punctuation() -> None:
    n = normalize_wiki_name("my—super_wiki!")
    # last `wiki` is dropped (duplicate suffix), then PascalCase from rest.
    assert n.primary == "MySuper-WIKI"


def test_empty_rejected() -> None:
    with pytest.raises(WikiNameError):
        normalize_wiki_name("   ")


def test_punctuation_only_rejected() -> None:
    with pytest.raises(WikiNameError):
        normalize_wiki_name("!!!")


def test_only_wiki_suffix_rejected() -> None:
    with pytest.raises(WikiNameError):
        normalize_wiki_name("WIKI")


def test_hyphenated_lookup_camel_boundary() -> None:
    n = normalize_wiki_name("MultiWord")
    assert n.hyphenated_lookup == "multi-word"


def test_digits_preserved() -> None:
    n = normalize_wiki_name("budget 2026")
    assert n.primary == "Budget2026-WIKI"


# --- aisw-4tu: transliteration-aware duplicate-name matching ----------------


def test_match_cyrillic_vs_translit_recipes() -> None:
    # The original bug: router proposed the ISO-9 latin form while the Cyrillic
    # dir already existed -> duplicate. Both must be recognised as the same WIKI.
    assert wiki_names_match("Рецепты", "Reczepty")
    assert wiki_names_match("Рецепты-WIKI", "Reczepty-WIKI")


def test_match_cyrillic_vs_translit_health() -> None:
    assert wiki_names_match("Здоровье", "Zdorove")
    assert wiki_names_match("Бюджет-WIKI", "Byudzhet")


def test_match_is_case_and_suffix_insensitive() -> None:
    assert wiki_names_match("medical", "Medical-WIKI")


def test_no_match_for_distinct_domains() -> None:
    assert not wiki_names_match("Рецепты", "Medical")
    assert not wiki_names_match("Budget", "Travel")


def test_match_key_is_canonical_and_tolerant() -> None:
    # Same canonical key for the Cyrillic original and its translit.
    assert wiki_match_key("Рецепты-WIKI") == wiki_match_key("Reczepty")
    assert wiki_match_key("Рецепты-WIKI") == "reczepty"
    # Tolerant: non-normalisable input yields "" and never raises.
    assert wiki_match_key("!!!") == ""
    assert wiki_match_key("") == ""


def test_match_empty_never_matches() -> None:
    assert not wiki_names_match("", "Reczepty")
    assert not wiki_names_match("!!!", "###")
