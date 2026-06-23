from __future__ import annotations

from ai_steward_wiki.tg.output import sanitize_html


def test_stray_lt_before_digits_is_escaped() -> None:
    # The prod incident: "<120/80," is not a valid tag → must be escaped.
    out = sanitize_html("норма <120/80, пульс 69")
    assert "&lt;120/80," in out
    assert "<120" not in out


def test_whitelisted_tags_preserved() -> None:
    assert sanitize_html("<b>жирный</b>") == "<b>жирный</b>"
    assert sanitize_html("<i>x</i> и <code>y</code>") == "<i>x</i> и <code>y</code>"


def test_non_whitelisted_tag_escaped() -> None:
    out = sanitize_html("<div>x</div>")
    assert out == "&lt;div&gt;x&lt;/div&gt;"


def test_bare_lt_gt_amp_in_text_escaped() -> None:
    assert sanitize_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_existing_entities_not_double_escaped_in_text() -> None:
    # already-escaped entity in plain text stays single-escaped
    assert sanitize_html("a &amp; b") == "a &amp; b"
    assert sanitize_html("x &lt; y") == "x &lt; y"


def test_anchor_tag_kept_and_bare_amp_in_attr_escaped() -> None:
    out = sanitize_html('<a href="u?a=1&b=2">ok</a>')
    assert out == '<a href="u?a=1&amp;b=2">ok</a>'


def test_anchor_tag_existing_amp_entity_not_double_escaped() -> None:
    out = sanitize_html('<a href="u?a=1&amp;b=2">ok</a>')
    assert out == '<a href="u?a=1&amp;b=2">ok</a>'


def test_idempotent_on_valid_whitelisted_html() -> None:
    once = sanitize_html("<b>АД</b> норма <120/80")
    assert sanitize_html(once) == once
    assert "<b>АД</b>" in once
    assert "&lt;120/80" in once
