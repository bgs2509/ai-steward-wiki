---
feature: md-to-tg-html
bd_id: aisw-iyz
module_id: M-TG-OUTPUT
status: stable
date: 2026-06-23
stack:
  - library: markdown-it-py
    version: 4.2.0 (already in uv.lock; promote to direct dependency, pinned ==)
    used_for: CommonMark tokenization (MarkdownIt().parse → token stream); custom token-walk renderer to TG-HTML
  - library: re (stdlib)
    version: py3.11+
    used_for: pre-pass pipe-table flattener; bare-& handling already in output.sanitize_html
decisions:
  - D-local-1: New module tg/md_to_html.py owns markdown_to_tg_html(text)->str. SRP — input GitHub-Markdown, output TG-HTML using only ALLOWED_TAGS. Imports ALLOWED_TAGS from output.py (no whitelist fork).
  - D-local-2: Parse with MarkdownIt() (CommonMark). Walk tokens (verified types — heading_open/close, paragraph_open/close, inline+children {text, strong_open/close, em_open/close, s_open/close, code_inline, link_open/close}, bullet_list/ordered_list + list_item, fence, blockquote_open/close). Emit a string.
  - D-local-3: Token → TG-HTML map. strong→<b>, em→<i>, s→<s>, code_inline→<code>, fence→<pre>, link_open(href)→<a href="...">, blockquote→<blockquote>. Text nodes HTML-escaped (reuse output._escape_text). heading_open→ emit <b>, heading_close→</b>+newline (DEGRADE: no TG heading). bullet list_item→ prefix "• "; ordered list_item→ "N. " (counter). paragraph_close→ "\n\n" block separator (trimmed at end).
  - D-local-4: Enable strikethrough rule if available (md.enable("strikethrough", ignoreInvalid=True)); s_open/close → <s>. If unavailable, "~~" stays as escaped text (acceptable degradation).
  - D-local-5: Tables — markdown-it core has no table rule. A pre-pass flattens GFM pipe-table rows (lines matching ^\\s*\\|.*\\|\\s*$ and the |---|--- separator) into plain " • cell — cell" lines BEFORE MarkdownIt, dropping the separator row. No mdit-py-plugins dependency.
  - D-local-6: Link href safety — escape the URL for HTML attribute (bare & → &amp;) reusing the sanitize_html convention; skip non-http(s) schemes (emit link text only) to avoid javascript: etc.
  - D-local-7: Pipeline order in deliver_output — text = markdown_to_tg_html(text) FIRST (produces valid TG-HTML), THEN text = sanitize_html(text) as the idempotent safety net (no double-escape, since converter already escaped text and emitted whitelist tags). Persist+send the converted text.
  - D-local-8: Safety net unchanged — the aisw-azu parse_mode=None fallback still catches any residual invalid HTML, so a reply is never lost even if the converter has a bug.
---

# Design: Markdown→Telegram-HTML converter (aisw-iyz)

## Module: tg/md_to_html.py

```python
from markdown_it import MarkdownIt
from ai_steward_wiki.tg.output import ALLOWED_TAGS, _escape_text  # whitelist SSoT

_MD = MarkdownIt()  # CommonMark; .enable("strikethrough", ...) if present

def markdown_to_tg_html(text: str) -> str:
    text = _flatten_pipe_tables(text)          # GFM tables → plain lines (D-local-5)
    tokens = _MD.parse(text)
    return _render_tokens(tokens).strip()       # token walk → TG-HTML (D-local-2/3)
```

`_render_tokens` walks the flat token list, maintaining a small list-context stack (bullet vs
ordered + item counter) and emitting whitelist tags. Inline children are rendered recursively.
Heading/list/table/paragraph are DEGRADED to bold/•/N./blank-line — never to unsupported tags.

## Pipeline integration (output.deliver_output)

```python
started = _utcnow_naive()
text = markdown_to_tg_html(text)   # NEW: GitHub-md → TG-HTML
text = sanitize_html(text)         # aisw-azu safety net (idempotent over valid TG-HTML)
output_path, ... = _persist_to_disk(... text=text ...)
# inline / chain / summary unchanged — now operate on valid TG-HTML
```

## Degradation table (Telegram has no native form)

| Markdown            | Telegram output            |
|---------------------|----------------------------|
| `# H` / `## H`      | `<b>H</b>` + newline       |
| `- a` / `* a`       | `• a`                      |
| `1. a`              | `1. a` (kept)              |
| `\| a \| b \|` table | `• a — b` flat lines       |
| `**x**`/`__x__`     | `<b>x</b>`                 |
| `*x*`/`_x_`         | `<i>x</i>`                 |
| `~~x~~`             | `<s>x</s>` (if rule on)    |
| `` `x` ``           | `<code>x</code>`           |
| ```` ```x``` ````   | `<pre>x</pre>`             |
| `[t](u)`            | `<a href="u">t</a>`        |
| `> q`               | `<blockquote>q</blockquote>`|

## Why this is robust

A real tokenizer handles nested emphasis, `*` inside code spans, escaped `\*`, and links with
parens/`&` — the exact cases a regex converter gets wrong. The aisw-azu parse_mode=None fallback
remains as the last-resort guarantee of delivery.
