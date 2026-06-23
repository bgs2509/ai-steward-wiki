# Completion Report: Markdown→Telegram-HTML converter (aisw-iyz)

**Date:** 2026-06-23
**Type:** feature
**Module:** M-TG-OUTPUT (+ new tg/md_to_html.py)

## Why

Bot replies were delivered (aisw-azu fix) but rendered LITERAL markdown in Telegram:
`**133/92/69**`, `## Резюме`, `### Изменённые файлы`, `` `code` ``, `- bullets`. Cause:
Claude emits GitHub-flavored Markdown, the bot sends `parse_mode=HTML` (D-024), HTML mode
doesn't interpret markdown → syntax shown verbatim. NOT a Telegram client/version issue.
Telegram supports only bold/italic/underline/strike/spoiler/code/pre/blockquote/links — no
headings, tables, or native lists.

## What shipped

1. **tg/md_to_html.py** (NEW) — `markdown_to_tg_html(text)`: parse with markdown-it-py, walk the
   token stream, emit ONLY the TG whitelist + degradations:
   - `**`→`<b>`, `*`/`_`→`<i>`, `~~`→`<s>`, `` ` ``→`<code>`, ```` ``` ````→`<pre>`,
     `[t](u)`→`<a href>` (http(s) only; drops `javascript:`), `> q`→`<blockquote>`
   - DEGRADE: `#`/`##`→`<b>` line, `-`/`*` bullets→`• `, ordered→`N.`, pipe tables→flat lines
   - text nodes HTML-escaped (idempotent, bare-`&` only)
2. **output.py** — in `deliver_output`: `if kind == "reply": text = markdown_to_tg_html(text)`
   then `text = sanitize_html(text)`. Digests/ingest_reports are app-built HTML → NOT converted
   (would escape their tags). `blockquote` added to `ALLOWED_TAGS`.
3. **pyproject.toml** — `markdown-it-py == 4.2.0` promoted from transitive to direct dep
   (uv.lock: dependency edge only, no version churn).

## Decision (user-approved fork)

markdown-it-py (already locked) + custom TG renderer — robust CommonMark tokenizer (handles
nested emphasis, `*` in code spans, escaped `\*`) without reinventing a parser, near-zero dep
cost. Custom regex (fragile) and telegramify-markdown (new dep, MarkdownV2 not HTML) rejected.

## Verification (evidence)

- `make lint` (ruff+format+mypy 95 files): clean. `grace lint`: 0 issues. `make inv-lint`: 14/14.
- `uv run pytest tests/unit`: all pass (+17 new: 15 converter incl. prod payload + 1 deliver
  integration + 1 blockquote-survives-sanitize regression + nested-list).
- markdown-it-py token types + strikethrough rule verified empirically (Context7 was down).

## Review (Step 12, py-quality)

1 CRITICAL + 1 MAJOR + 2 MINOR + 1 NIT. Resolved:
- **CRITICAL** — converter emitted `<blockquote>` but it wasn't in `ALLOWED_TAGS`, so the
  downstream `sanitize_html` escaped it to literal text. Fixed: added `blockquote` to
  `ALLOWED_TAGS` + full-pipeline regression test.
- **MAJOR** — nested lists glued parent+child text (`• item1• nested`). Fixed: emit a newline
  on a nested `*_list_open` + regression test.
- **MINOR** — `~~~` fences now toggle the table-flatten guard; ordered-list `start=0` honored
  (`is not None`).
- **NIT** — code content extracted to a variable (py3.11 disallows `\` in f-string expressions),
  replacing `chr(10)`.

## Out of scope (deferred)

parse_mode stays HTML (D-024). Full table rendering (TG has none — flatten only). Streaming
slow-path in-place edits (separate path). wiki-prompt tuning. The aisw-azu parse_mode=None
fallback remains the last-resort delivery guarantee.
