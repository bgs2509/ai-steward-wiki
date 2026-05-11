#!/usr/bin/env python3
"""INV-7 lint — no direct destructive FS operations on wiki paths outside
``src/ai_steward_wiki/wiki/lifecycle.py``.

Per D-041, lifecycle.py is the sole SSoT for wiki directory mutations.
Any other module performing ``shutil.rmtree``, ``os.rmdir``, ``Path.unlink``,
``os.replace`` against a wiki path, or shelling out to ``rm -rf`` / ``mv``
of wiki directories violates the invariant.

Exits 1 on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "ai_steward_wiki"
ALLOWLIST = {SRC / "wiki" / "lifecycle.py"}

# Patterns that may mutate wiki directories destructively.
FORBIDDEN = [
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\bos\.rmdir\b"),
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bsubprocess\..*['\"]mv['\"]"),
]


def main() -> int:
    violations: list[str] = []
    for py in SRC.rglob("*.py"):
        if py in ALLOWLIST:
            continue
        text = py.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            for match in pat.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                violations.append(f"{py.relative_to(REPO_ROOT)}:{line_no}: {match.group(0)!r}")
    if violations:
        sys.stderr.write("INV-7 violation: forbidden FS mutation outside wiki/lifecycle.py\n")
        for v in violations:
            sys.stderr.write(f"  {v}\n")
        return 1
    print("INV-7: ok (no forbidden destructive FS calls outside wiki/lifecycle.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
