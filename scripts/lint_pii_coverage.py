#!/usr/bin/env python3
"""Grep-lint: flag logger calls that pass PII kwargs without going through redactor.

Chunk 13 / M-OPS-PII §10.4 coverage matrix.

Strategy: scan `src/ai_steward_wiki/` for `logger.<level>(...)` calls that pass
a kwarg whose name is in the PII-sensitive set (email/phone/password/token/...)
where the calling module does NOT import `ai_steward_wiki.ops.pii` (which
implies it relies on either the global structlog processor or a local redact()
call). Modules under `ai_steward_wiki.ops.pii` itself are exempt.

Exit 1 on first finding; exit 0 if clean.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SENSITIVE = {
    "email",
    "phone",
    "password",
    "token",
    "api_key",
    "secret",
    "bearer",
    "iban",
    "ssn",
    "card",
    "pan",
}

LOG_CALL_RE = re.compile(
    r"\blog(?:ger)?\.(?:info|warning|error|debug|exception|critical)\s*\("
    r"[^()]*?(?:\b(" + "|".join(SENSITIVE) + r")\s*=)",
    re.DOTALL,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "src" / "ai_steward_wiki"
    violations: list[str] = []
    # Modules that legitimately reference these names (the redactor itself + tests).
    exempt_paths = {
        root / "ops" / "pii.py",
        root / "ops" / "retention.py",
    }
    for path in root.rglob("*.py"):
        if path in exempt_paths:
            continue
        text = path.read_text(encoding="utf-8")
        # If the module imports our redactor surface, trust it.
        if "from ai_steward_wiki.ops.pii import" in text or "ai_steward_wiki.ops.pii" in text:
            continue
        for m in LOG_CALL_RE.finditer(text):
            kw = m.group(1)
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(f"{path.relative_to(root.parents[1])}:{line_no} log call uses {kw}=")
    if violations:
        print("lint_pii_coverage.py: violations found:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("lint_pii_coverage.py: ok (no unredacted PII log calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
