# Implementation Plan: Markdown→Telegram-HTML converter (aisw-iyz)

> SSoT for execution. TDD: RED → GREEN → REFACTOR. Module: M-TG-OUTPUT.
> Engine: markdown-it-py 4.2.0 (already in uv.lock) + custom token renderer.

## T1 — promote markdown-it-py to a direct dependency — `pyproject.toml`
- GREEN: add `markdown-it-py == 4.2.0` to project dependencies; `uv sync`; assert
  `import markdown_it` works and `markdown_it.__version__ == "4.2.0"`. uv.lock unchanged
  (already present transitively).

## T2 — converter module — NEW `tg/md_to_html.py` + MODULE_CONTRACT
- RED: `tests/unit/tg/test_md_to_html.py`:
  - inline: `**x**`→`<b>x</b>`, `*x*`/`_x_`→`<i>x</i>`, `` `x` ``→`<code>x</code>`, `~~x~~`→`<s>x</s>`
  - fenced ```` ```\ncode\n``` ```` → `<pre>code</pre>`
  - link `[t](http://x?a=1&b=2)` → `<a href="http://x?a=1&amp;b=2">t</a>`; non-http scheme → text only
  - heading `## Резюме` → `<b>Резюме</b>` (no <h2>)
  - bullets `- a\n- b` → `• a` / `• b`; ordered `1. a\n2. b` → `1. a` / `2. b`
  - blockquote `> q` → `<blockquote>q</blockquote>`
  - text escaping: `норма <120/80 & ok` → `норма &lt;120/80 &amp; ok`
  - pipe table `| a | b |\n|---|---|\n| 1 | 2 |` → flattened plain lines, no literal `|---|`
  - PROD payload (headers + **bold** + `code` + bullets + `<120/80`) → no literal `**`/`##`/backtick; `<b>`/`<code>` present; no raw `<` before a digit
  - idempotence-with-sanitize: `sanitize_html(markdown_to_tg_html(x))` keeps the whitelist tags and does not double-escape
- GREEN: implement `markdown_to_tg_html`, `_flatten_pipe_tables`, `_render_tokens`, `_render_inline`,
  `_escape` (local 3-replace, KISS — no cross-module private import), `_safe_href`. Enable
  strikethrough rule if present. Each fn ≤ ~40 lines.

## T3 — integrate in deliver_output — `tg/output.py`
- RED: extend `test_output.py` — `deliver_output(text="## H\n**133/92/69** норма <120/80")` →
  FakeSender receives `<b>` + `<b>133/92/69</b>` + `&lt;120/80`, NO literal `**`/`##`.
- GREEN: at top of `deliver_output`: `text = markdown_to_tg_html(text)` then `text = sanitize_html(text)`.

## T4 — full gate
- `make lint` (ruff+format+mypy), `make grace-lint`, `make inv-lint`.
- `uv run pytest tests/unit` green; new tests pass; deliver_output + aisw-azu sanitize tests pass.

## Self-review vs FR/NFR
- FR-1/2/3 → T2 (token map). FR-4 (degrade) → T2 (heading/bullet/ordered/table). FR-5 → T2 `_escape` + T3 sanitize compose.
- FR-6 → T3 (top of deliver_output). FR-7 → T2 tokenizer + edge-case tests. NFR-1/4 → T1 (markdown-it-py pinned). NFR-5 → parse_mode unchanged.
- Order: T1 → T2 → T3 → T4. Context7 markdown-it-py token API was unavailable; token types verified empirically (probe in bd notes) — re-confirm on any unknown token in execution.
