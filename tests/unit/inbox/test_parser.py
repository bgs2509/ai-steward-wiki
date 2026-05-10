"""Parser tests for extract_inbox_hint (D-016)."""

from __future__ import annotations

from ai_steward_wiki.inbox.parser import extract_inbox_hint


def test_hint_present_at_end() -> None:
    text = "# Title\n\nSome prose.\n\n## Inbox hint\n\nRoute health stuff here.\n"
    assert extract_inbox_hint(text) == "Route health stuff here."


def test_hint_between_headings() -> None:
    text = (
        "# Title\n\n"
        "## Section A\n\nbody A\n\n"
        "## Inbox hint\n\nKeywords: давление, анализы.\nExamples: «померил давление».\n\n"
        "## Section B\n\nbody B\n"
    )
    hint = extract_inbox_hint(text)
    assert hint is not None
    assert hint.startswith("Keywords:")
    assert "Examples" in hint
    assert "body B" not in hint


def test_hint_absent() -> None:
    text = "# Title\n\n## Other\n\ncontent\n"
    assert extract_inbox_hint(text) is None


def test_multiple_hash_after_does_not_swallow() -> None:
    text = "## Inbox hint\nfirst body\n## Trailing\nshould be excluded\n"
    assert extract_inbox_hint(text) == "first body"


def test_exotic_whitespace() -> None:
    # Trailing spaces after heading; CRLF-ish content; leading/trailing blank lines.
    text = "## Inbox hint   \n\n   hint with spaces   \n\n\n## Next\nx\n"
    assert extract_inbox_hint(text) == "hint with spaces"


def test_empty_section_returns_none() -> None:
    text = "## Inbox hint\n\n\n## Next\nx\n"
    assert extract_inbox_hint(text) is None


def test_case_sensitive_heading() -> None:
    # D-016 contract: exact heading "## Inbox hint".
    text = "## inbox hint\nbody\n"
    assert extract_inbox_hint(text) is None
