from __future__ import annotations

import pytest

from ai_steward_wiki.wiki.name import WikiNameError, normalize_wiki_name


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
