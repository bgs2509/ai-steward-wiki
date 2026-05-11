# FILE: src/ai_steward_wiki/inbox/parser.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Pure parser for the "## Inbox hint" Markdown section (D-016).
#   SCOPE: extract_inbox_hint(text) -> str | None.
#   DEPENDS: re (stdlib)
#   LINKS: D-016, M-INBOX
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   INBOX_HINT_HEADING - canonical heading literal
#   extract_inbox_hint - parse CLAUDE.md text and return trimmed hint body or None
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial regex parser for "## Inbox hint" (chunk 6)
# END_CHANGE_SUMMARY

from __future__ import annotations

import re

__all__ = [
    "INBOX_HINT_HEADING",
    "extract_inbox_hint",
]

INBOX_HINT_HEADING = "## Inbox hint"

# START_BLOCK_INBOX_HINT_REGEX
# Match a line that is exactly the heading "## Inbox hint" (no trailing text on the
# heading line beyond optional whitespace), capture everything until the next "## "
# heading at line start, or EOF. Multiline + DOTALL not needed because we anchor on \n.
_INBOX_HINT_RE = re.compile(
    r"(?m)^\#\#\s+Inbox\s+hint\s*$\n(?P<body>.*?)(?=^\#\#\s+|\Z)",
    re.DOTALL,
)
# END_BLOCK_INBOX_HINT_REGEX


# START_CONTRACT: extract_inbox_hint
#   PURPOSE: Pull the "## Inbox hint" section body from CLAUDE.md text.
#   INPUTS: { claude_md_text: str - full CLAUDE.md content }
#   OUTPUTS: { str | None - trimmed body without heading; None if section absent }
#   SIDE_EFFECTS: none
#   LINKS: D-016 §"Контракт"
# END_CONTRACT: extract_inbox_hint
def extract_inbox_hint(claude_md_text: str) -> str | None:
    match = _INBOX_HINT_RE.search(claude_md_text)
    if match is None:
        return None
    body = match.group("body").strip()
    return body or None
