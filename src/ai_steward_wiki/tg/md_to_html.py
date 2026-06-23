# FILE: src/ai_steward_wiki/tg/md_to_html.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Convert GitHub-flavored Markdown (model output) into Telegram parse_mode=HTML, emitting only the TG whitelist and degrading unsupported constructs.
#   SCOPE: markdown_to_tg_html(text) via markdown-it-py token walk + pipe-table flattening + heading/list degradation.
#   DEPENDS: markdown-it-py (MarkdownIt), re (stdlib). No import of tg.output (one-way: output imports this) to avoid a cycle.
#   LINKS: M-TG-OUTPUT, D-024, aisw-iyz, aisw-azu (sanitize_html composes downstream)
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   markdown_to_tg_html - GitHub-Markdown string -> Telegram-HTML string (whitelist + degradations)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-iyz: initial markdown-it-py token renderer to TG-HTML
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt

__all__ = ["markdown_to_tg_html"]

# CommonMark + strikethrough (verified available in markdown-it-py 4.2.0).
_MD = MarkdownIt().enable("strikethrough")

# Bare "&" not already starting a known TG entity (idempotent escaping — matches
# tg.output.sanitize_html so downstream sanitize is a no-op over this output).
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")

# Inline open/close token -> TG whitelist tag.
_INLINE_OPEN = {"strong_open": "<b>", "em_open": "<i>", "s_open": "<s>"}
_INLINE_CLOSE = {"strong_close": "</b>", "em_close": "</i>", "s_close": "</s>"}

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _escape(text: str) -> str:
    """HTML-escape free text for parse_mode=HTML (idempotent; only bare &)."""
    text = _BARE_AMP_RE.sub("&amp;", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _safe_href(href: str | None) -> str | None:
    """Return an attribute-escaped href if scheme is http(s), else None (drop link)."""
    if not href:
        return None
    if not re.match(r"(?i)^https?://", href):
        return None  # reject javascript:, data:, etc. — emit link text only
    return _BARE_AMP_RE.sub("&amp;", href).replace('"', "&quot;")


# START_BLOCK_FLATTEN_TABLES
def _flatten_pipe_tables(text: str) -> str:
    """GFM pipe tables -> plain "cell — cell" lines (TG has no tables). Fence-aware."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _TABLE_ROW_RE.match(line):
            if _TABLE_SEP_RE.match(line) and "-" in line:
                continue  # drop the |---|---| separator row
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append(" — ".join(c for c in cells if c))
        else:
            out.append(line)
    return "\n".join(out)


# END_BLOCK_FLATTEN_TABLES


def _render_inline(children: list[Any]) -> str:
    out: list[str] = []
    link_emitted: list[bool] = []  # stack: did this <a> emit an opening tag?
    for c in children:
        t = c.type
        if t == "text":
            out.append(_escape(c.content))
        elif t in _INLINE_OPEN:
            out.append(_INLINE_OPEN[t])
        elif t in _INLINE_CLOSE:
            out.append(_INLINE_CLOSE[t])
        elif t == "code_inline":
            out.append(f"<code>{_escape(c.content)}</code>")
        elif t == "link_open":
            href = _safe_href(c.attrGet("href"))
            link_emitted.append(href is not None)
            if href is not None:
                out.append(f'<a href="{href}">')
        elif t == "link_close":
            if link_emitted.pop() if link_emitted else False:
                out.append("</a>")
        elif t in ("softbreak", "hardbreak"):
            out.append("\n")
        elif c.content:
            out.append(_escape(c.content))  # unknown inline (image, etc.) -> text
    return "".join(out)


# START_CONTRACT: markdown_to_tg_html
#   PURPOSE: Render GitHub-Markdown as Telegram-HTML (whitelist tags + degradations).
#   INPUTS: { text: str - model reply in GitHub-flavored Markdown }
#   OUTPUTS: { str - Telegram parse_mode=HTML safe string (only b/i/u/s/a/code/pre tags) }
#   SIDE_EFFECTS: none
# END_CONTRACT: markdown_to_tg_html
def markdown_to_tg_html(text: str) -> str:
    tokens = _MD.parse(_flatten_pipe_tables(text))
    out: list[str] = []
    # list context stack: each entry {"ordered": bool, "n": int}
    lists: list[dict[str, Any]] = []
    for tok in tokens:
        t = tok.type
        if t == "inline":
            out.append(_render_inline(tok.children or []))
        elif t == "heading_open":
            out.append("<b>")
        elif t == "heading_close":
            out.append("</b>\n\n")
        elif t == "paragraph_close" and not tok.hidden:
            out.append("\n\n")
        elif t == "bullet_list_open":
            if lists:  # nested list — separate from the parent item's text
                out.append("\n")
            lists.append({"ordered": False, "n": 0})
        elif t == "ordered_list_open":
            if lists:  # nested list — separate from the parent item's text
                out.append("\n")
            start = tok.attrGet("start")
            lists.append({"ordered": True, "n": int(start) if start is not None else 1})
        elif t in ("bullet_list_close", "ordered_list_close"):
            if lists:
                lists.pop()
            out.append("\n")
        elif t == "list_item_open":
            if lists and lists[-1]["ordered"]:
                out.append(f"{lists[-1]['n']}. ")
                lists[-1]["n"] += 1
            else:
                out.append("• ")
        elif t == "list_item_close":
            out.append("\n")
        elif t in ("fence", "code_block"):
            code = _escape(tok.content.rstrip("\n"))  # rstrip outside f-string (py3.11)
            out.append(f"<pre>{code}</pre>\n\n")
        elif t == "blockquote_open":
            out.append("<blockquote>")
        elif t == "blockquote_close":
            out.append("</blockquote>\n\n")
    # collapse >2 consecutive newlines and trim
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
