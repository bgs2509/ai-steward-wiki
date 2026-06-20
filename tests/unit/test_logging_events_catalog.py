from __future__ import annotations

import re

from ai_steward_wiki import logging_events

_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$|^\.(start|done|error|slow)$")


def _public_constants() -> dict[str, object]:
    return {n: v for n, v in vars(logging_events).items() if not n.startswith("_") and n.isupper()}


def test_all_constants_are_strings_and_match_snake_dotted() -> None:
    public = _public_constants()
    assert public, "catalog is empty"
    for name, value in public.items():
        assert isinstance(value, str), name
        assert _KEY.match(value), (name, value)


def test_no_duplicate_values() -> None:
    values = list(_public_constants().values())
    assert len(values) == len(set(values))


def test_only_string_constants_exported() -> None:
    for name, value in _public_constants().items():
        assert isinstance(value, str), name
