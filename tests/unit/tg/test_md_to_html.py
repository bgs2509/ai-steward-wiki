from __future__ import annotations

from ai_steward_wiki.tg.md_to_html import markdown_to_tg_html
from ai_steward_wiki.tg.output import sanitize_html


def test_bold_italic_strike_code() -> None:
    assert markdown_to_tg_html("**x**") == "<b>x</b>"
    assert markdown_to_tg_html("*x*") == "<i>x</i>"
    assert markdown_to_tg_html("_x_") == "<i>x</i>"
    assert markdown_to_tg_html("~~x~~") == "<s>x</s>"
    assert markdown_to_tg_html("`x`") == "<code>x</code>"


def test_fenced_code_to_pre() -> None:
    out = markdown_to_tg_html("```\nline1\nline2\n```")
    assert "<pre>" in out
    assert "</pre>" in out
    assert "line1\nline2" in out


def test_heading_degrades_to_bold() -> None:
    out = markdown_to_tg_html("## Резюме")
    assert "<b>Резюме</b>" in out
    assert "<h2>" not in out
    assert "##" not in out


def test_bullets_to_dot() -> None:
    out = markdown_to_tg_html("- один\n- два")
    assert "• один" in out
    assert "• два" in out
    assert "<ul>" not in out
    assert "<li>" not in out


def test_ordered_list_kept() -> None:
    out = markdown_to_tg_html("1. первый\n2. второй")
    assert "1. первый" in out
    assert "2. второй" in out


def test_link_http_kept_amp_escaped() -> None:
    out = markdown_to_tg_html("[t](http://x?a=1&b=2)")
    assert out == '<a href="http://x?a=1&amp;b=2">t</a>'


def test_link_unsafe_scheme_drops_tag_keeps_text() -> None:
    out = markdown_to_tg_html("[click](javascript:alert(1))")
    assert "<a" not in out
    assert "click" in out


def test_blockquote() -> None:
    out = markdown_to_tg_html("> цитата")
    assert "<blockquote>" in out
    assert "</blockquote>" in out
    assert "цитата" in out


def test_text_escaped() -> None:
    out = markdown_to_tg_html("норма <120/80 & ok")
    assert "&lt;120/80" in out
    assert "&amp; ok" in out
    assert "<120" not in out


def test_pipe_table_flattened() -> None:
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = markdown_to_tg_html(md)
    assert "|---|" not in out
    for cell in ("a", "b", "1", "2"):
        assert cell in out
    # no raw markdown table pipes left as a separator row
    assert "---" not in out


def test_prod_payload_renders_clean() -> None:
    md = (
        "## Резюме\n\n"
        "**133/92/69** — это давление (систолическое/диастолическое). "
        "Норма <120/80.\n\n"
        "### Изменённые файлы\n"
        "- `log.md` — событие `ingest (duplicate skip)`\n"
    )
    out = markdown_to_tg_html(md)
    assert "**" not in out
    assert "## " not in out
    assert "### " not in out
    assert "<b>Резюме</b>" in out
    assert "<b>133/92/69</b>" in out
    assert "<code>log.md</code>" in out
    assert "&lt;120/80" in out
    assert "• " in out


def test_idempotent_with_sanitize_html() -> None:
    # converter output is valid TG-HTML; sanitize_html must not break/double-escape it
    once = markdown_to_tg_html("## H\n**b** норма <120/80 `c`")
    assert sanitize_html(once) == once


def test_blockquote_survives_sanitize_html() -> None:
    # aisw-iyz CRITICAL regression: <blockquote> from the converter must NOT be
    # escaped by the downstream sanitize_html in deliver_output (full pipeline).
    once = markdown_to_tg_html("> цитата")
    after = sanitize_html(once)
    assert "<blockquote>" in after
    assert "&lt;blockquote&gt;" not in after


def test_nested_list_items_not_glued() -> None:
    out = markdown_to_tg_html("- item1\n  - nested\n- item2")
    assert "item1• nested" not in out  # nested list must not glue to parent text
    assert "• item1" in out
    assert "• nested" in out
    assert "• item2" in out


def test_plain_text_passthrough_escaped() -> None:
    assert markdown_to_tg_html("просто текст") == "просто текст"
