# FILE: src/ai_steward_wiki/wiki/name.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: WIKI name normalisation — Cyrillic→Latin ISO 9, PascalCase,
#            -WIKI suffix, regex validation. Returns frozen WikiName with
#            primary, hyphenated_lookup, slug.
#   SCOPE: normalize_wiki_name, WikiName, WikiNameError. Pure stdlib.
#   DEPENDS: pydantic
#   LINKS: M-WIKI-LIFECYCLE, D-008, D-041, tech-spec §5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiName - frozen Pydantic: primary, hyphenated_lookup, slug
#   WikiNameError - validation failure (empty / regex mismatch)
#   normalize_wiki_name - raw NL string -> WikiName
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 8: ISO 9 transliteration + PascalCase pipeline
# END_CHANGE_SUMMARY

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

# START_BLOCK_ISO9_TABLE
# ISO 9:1995 single-strategy romanisation (Russian alphabet only — MVP scope).
# Multi-char outputs are intentional and lossless; uppercase derived dynamically.
# ruff: noqa: RUF001 (Cyrillic keys are intentional)
_ISO9_LOWER: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "cz",
    "ч": "ch",
    "ш": "sh",
    "щ": "shh",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
# END_BLOCK_ISO9_TABLE

_WIKI_REGEX = re.compile(r"^[A-Z][A-Za-z0-9]*-WIKI$")


class WikiNameError(ValueError):
    """Raised when NL input cannot be normalised to a valid WIKI name."""


class WikiName(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: str
    hyphenated_lookup: str
    slug: str


def _transliterate(raw: str) -> str:
    # START_BLOCK_TRANSLITERATE
    out: list[str] = []
    for ch in raw:
        lower = ch.lower()
        if lower in _ISO9_LOWER:
            mapped = _ISO9_LOWER[lower]
            if ch.isupper() and mapped:
                mapped = mapped[0].upper() + mapped[1:]
            out.append(mapped)
        else:
            out.append(ch)
    return "".join(out)
    # END_BLOCK_TRANSLITERATE


def _pascal_case(parts: list[str]) -> str:
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def _camel_to_hyphen(name: str) -> str:
    """`HealthLite` → `health-lite`. Inserts hyphen at lower→Upper boundaries."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def normalize_wiki_name(raw: str) -> WikiName:
    """Pipeline: transliterate → split non-alnum → PascalCase → -WIKI → validate."""
    if not raw or not raw.strip():
        raise WikiNameError("empty input")

    transliterated = _transliterate(raw.strip())
    # Split first on non-alphanumeric, then further on camel boundaries so
    # input like "HealthLite" yields ["Health", "Lite"] for proper hyphen
    # reconstruction in hyphenated_lookup.
    raw_parts = [p for p in re.split(r"[^A-Za-z0-9]+", transliterated) if p]
    parts: list[str] = []
    for p in raw_parts:
        # Insert split markers at lower-to-upper boundaries.
        split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", p).split()
        parts.extend(split)
    if not parts:
        raise WikiNameError(f"no alphanumeric content after normalisation: {raw!r}")

    # If user passed something that already ended with `WIKI` (case-insensitive)
    # as a trailing token, drop it — we re-append canonically.
    if parts and parts[-1].lower() == "wiki":
        parts = parts[:-1]
    if not parts:
        raise WikiNameError(f"only WIKI suffix supplied: {raw!r}")

    pascal = _pascal_case(parts)
    if not pascal or not pascal[0].isalpha():
        raise WikiNameError(f"first character not a letter: {raw!r}")

    # Force first char uppercase (handles digits-leading edge already rejected above).
    primary = f"{pascal[0].upper()}{pascal[1:]}-WIKI"
    if not _WIKI_REGEX.match(primary):
        raise WikiNameError(f"normalised name fails regex: {primary!r}")

    body = primary[: -len("-WIKI")]
    hyphenated_lookup = _camel_to_hyphen(body)
    slug = hyphenated_lookup.replace("-", "")
    return WikiName(primary=primary, hyphenated_lookup=hyphenated_lookup, slug=slug)
