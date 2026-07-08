#!/usr/bin/env python
# FILE: scripts/check_classifier_regress_stamp.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Pre-commit guard enforcing the "mandatory manual gate" on
#            prompts/classifier.md — block the commit unless
#            `make classifier-regress` has stamped this exact prompt content.
#   SCOPE: CLI entry; main(argv) returning exit code. Reads
#          .classifier_regress.stamp (written by scripts/classifier_regress.py
#          on GATE PASS); does not run the regression itself (100 real Haiku
#          calls per run — deliberately kept out of pre-commit/CI cost).
#   DEPENDS: hashlib, pathlib (stdlib only)
#   LINKS: M-CLASSIFIER-REGRESS, DEC-13, FR-13, aisw-xi8
#   ROLE: SCRIPT
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   STAMP_PATH - path to the sha256 stamp written by classifier_regress.py
#   check_stamp - compare a prompt file's sha256 against the recorded stamp
#   main - argparse-free CLI entry (pre-commit passes matched filenames)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - full-audit 2026-07-08: classifier-regress was
#                documented as a mandatory gate before any prompts/classifier.md
#                change but had zero enforcement. This hook makes skipping it
#                loud instead of silent.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

__all__ = ["STAMP_PATH", "check_stamp", "main"]

REPO_ROOT = Path(__file__).resolve().parent.parent
STAMP_PATH = REPO_ROOT / ".classifier_regress.stamp"

_OVERRIDE_ENV_VAR = "CLASSIFIER_REGRESS_OK"


def check_stamp(prompt_path: Path, stamp_path: Path) -> str | None:
    """Return an error message if the stamp is missing or stale, else None."""
    if not stamp_path.exists():
        return (
            f"{prompt_path} changed but no regress stamp found at {stamp_path}.\n"
            "Run `make classifier-regress` (100-case corpus gate, DEC-13/FR-13) first."
        )
    digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    stamped = stamp_path.read_text(encoding="utf-8").strip()
    if digest != stamped:
        return (
            f"{prompt_path} changed since the last passing `make classifier-regress` run.\n"
            "Re-run `make classifier-regress` to refresh the stamp before committing."
        )
    return None


def main(argv: list[str]) -> int:
    changed_prompt = next((Path(arg) for arg in argv if Path(arg).name == "classifier.md"), None)
    if changed_prompt is None:
        return 0
    if os.environ.get(_OVERRIDE_ENV_VAR) == "1":
        print(
            f"[check_classifier_regress_stamp] {_OVERRIDE_ENV_VAR}=1 override — "
            "skipping stamp check, gate NOT verified."
        )
        return 0
    error = check_stamp(changed_prompt, STAMP_PATH)
    if error is None:
        return 0
    print(f"[check_classifier_regress_stamp] BLOCKED: {error}", file=sys.stderr)
    print(
        f"[check_classifier_regress_stamp] Override (not recommended): "
        f"{_OVERRIDE_ENV_VAR}=1 git commit ...",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
