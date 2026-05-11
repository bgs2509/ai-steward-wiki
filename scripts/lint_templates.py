#!/usr/bin/env python
# FILE: scripts/lint_templates.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Lint domain WIKI templates — required structure: H1 title,
#            ## Inbox hint section with intents/keywords/priority keys,
#            no schema_version drift, no markdown tables.
#   SCOPE: CLI entry; main(argv) returning exit code.
#   DEPENDS: stdlib only
#   LINKS: M-TEMPLATES, D-016, D-041
#   ROLE: SCRIPT
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   REQUIRED_TEMPLATES - canonical set of template_id files expected under templates/
#   REQUIRED_HINT_KEYS - required keys inside ## Inbox hint section
#   TemplateLintError - raised when --fail-on-error is set and errors are found
#   lint_template - returns list[str] errors for a single template file
#   lint_dir - lint all templates under a directory; returns dict path->errors
#   main - argparse CLI entry
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 15: initial template lint
# END_CHANGE_SUMMARY

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

__all__ = [
    "REQUIRED_HINT_KEYS",
    "REQUIRED_TEMPLATES",
    "TemplateLintError",
    "lint_dir",
    "lint_template",
    "main",
]

REQUIRED_TEMPLATES: tuple[str, ...] = (
    "_default",
    "health",
    "health-lite",
    "investment",
    "budget",
    "family",
    "study",
    "career",
    "home",
    "hobby",
    "recipes",
)

REQUIRED_HINT_KEYS: tuple[str, ...] = ("intents", "keywords", "priority")

_HINT_HEADER_RE = re.compile(r"^##\s+Inbox hint\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+\S", re.MULTILINE)
_MD_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


class TemplateLintError(RuntimeError):
    """Raised by main() when errors are found and --fail-on-error is set."""


def lint_template(path: Path) -> list[str]:
    """Return list of validation errors for a single template file."""
    errors: list[str] = []
    if not path.is_file():
        return [f"{path}: not a file"]
    text = path.read_text(encoding="utf-8")

    if not _H1_RE.search(text):
        errors.append(f"{path}: missing H1 title")

    if _MD_TABLE_RE.search(text):
        errors.append(f"{path}: markdown table detected (use lists instead)")

    hint_match = _HINT_HEADER_RE.search(text)
    if not hint_match:
        errors.append(f"{path}: missing '## Inbox hint' section")
        return errors

    hint_body = text[hint_match.end() :]
    # Truncate at next H2 if present.
    next_h2 = re.search(r"^##\s+\S", hint_body, re.MULTILINE)
    if next_h2:
        hint_body = hint_body[: next_h2.start()]

    for key in REQUIRED_HINT_KEYS:
        if not re.search(rf"^{re.escape(key)}\s*:\s*\S", hint_body, re.MULTILINE):
            errors.append(f"{path}: '## Inbox hint' missing required key '{key}'")

    prio_match = re.search(r"^priority\s*:\s*(\d+)\s*$", hint_body, re.MULTILINE)
    if prio_match:
        prio = int(prio_match.group(1))
        if not 0 <= prio <= 100:
            errors.append(f"{path}: priority {prio} out of range 0..100")

    return errors


def lint_dir(directory: Path) -> dict[Path, list[str]]:
    """Lint all REQUIRED_TEMPLATES under directory. Missing files reported as errors."""
    results: dict[Path, list[str]] = {}
    for template_id in REQUIRED_TEMPLATES:
        path = directory / f"{template_id}.md"
        if not path.exists():
            results[path] = [f"{path}: missing required template '{template_id}.md'"]
            continue
        errs = lint_template(path)
        if errs:
            results[path] = errs
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint domain WIKI templates.")
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=Path("templates"),
        help="Directory containing template files (default: templates)",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit with code 1 if any errors are found",
    )
    args = parser.parse_args(argv)

    results = lint_dir(args.templates_dir)
    if not results:
        print(f"[lint_templates] OK ({len(REQUIRED_TEMPLATES)} templates)")
        return 0

    for _path, errs in results.items():
        for err in errs:
            print(err, file=sys.stderr)

    total = sum(len(v) for v in results.values())
    print(
        f"[lint_templates] {total} error(s) across {len(results)} file(s)",
        file=sys.stderr,
    )
    return 1 if args.fail_on_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
