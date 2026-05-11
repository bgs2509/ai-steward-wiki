#!/usr/bin/env python
# FILE: scripts/lint_onboarding.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Lint the onboarding intro template — required slug presence/order/uniqueness.
#   SCOPE: CLI entry; main(argv) returning exit code.
#   DEPENDS: stdlib only
#   LINKS: M-ONBOARD-ADMIN, D-030
#   ROLE: SCRIPT
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   REQUIRED_SLUGS - canonical ordered tuple of mandatory slug names
#   SLUG_PATTERN - regex matching <!-- slug:name -->
#   OnboardingLintError - raised on validation failure
#   lint_template - returns list[str] of errors (empty = ok)
#   main - argparse CLI entry
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 12: initial onboarding intro lint
# END_CHANGE_SUMMARY

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_SLUGS: tuple[str, ...] = (
    "greeting",
    "purpose",
    "capabilities",
    "privacy",
    "next-steps",
    "contact",
)

SLUG_PATTERN = re.compile(r"<!--\s*slug:([a-z][a-z0-9_-]*)\s*-->")


class OnboardingLintError(Exception):
    """Raised by lint_template when validation fails (callers using as library)."""


# START_CONTRACT: lint_template
#   PURPOSE: Validate slug presence, uniqueness, order, and non-empty sections.
#   INPUTS: { path: Path - file to lint }
#   OUTPUTS: { list[str] - human-readable errors (empty list = passes) }
#   SIDE_EFFECTS: none (read-only file access)
#   LINKS: M-ONBOARD-ADMIN
# END_CONTRACT: lint_template
def lint_template(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]

    matches = list(SLUG_PATTERN.finditer(text))
    found = [m.group(1) for m in matches]

    # START_BLOCK_CHECK_DUPLICATES
    seen: set[str] = set()
    for slug in found:
        if slug in seen:
            errors.append(f"duplicate slug: {slug}")
        seen.add(slug)
    # END_BLOCK_CHECK_DUPLICATES

    # START_BLOCK_CHECK_MISSING
    for required in REQUIRED_SLUGS:
        if required not in seen:
            errors.append(f"missing required slug: {required}")
    # END_BLOCK_CHECK_MISSING

    # START_BLOCK_CHECK_ORDER
    required_in_order = [s for s in found if s in REQUIRED_SLUGS]
    # Deduplicate first occurrence preserving order
    seen2: set[str] = set()
    unique_order: list[str] = []
    for s in required_in_order:
        if s not in seen2:
            unique_order.append(s)
            seen2.add(s)
    if unique_order and unique_order != [s for s in REQUIRED_SLUGS if s in seen2]:
        errors.append(f"slug order drift: expected {list(REQUIRED_SLUGS)}, got {unique_order}")
    # END_BLOCK_CHECK_ORDER

    # START_BLOCK_CHECK_NON_EMPTY
    # Section content = text between this slug marker and the next slug marker (or EOF)
    for i, m in enumerate(matches):
        slug = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_body = text[start:end].strip()
        if not section_body:
            errors.append(f"empty section: {slug}")
    # END_BLOCK_CHECK_NON_EMPTY

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint onboarding intro template.")
    parser.add_argument("--template", required=True, type=Path, help="Path to the template file.")
    ns = parser.parse_args(argv)
    errors = lint_template(ns.template)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
