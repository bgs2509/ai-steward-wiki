---
feature: md-to-tg-html
bd_id: aisw-iyz
module_id: M-TG-OUTPUT
status: stable
date: 2026-06-23
risk: medium
fr:
  - FR-1: Model replies in GitHub-flavored Markdown MUST render as Telegram-native formatting, not literal markdown syntax
  - FR-2: Map supported inline markup to TG HTML — **bold**→<b>, *italic*/_italic_→<i>, ~~strike~~→<s>, `code`→<code>, ```fenced```→<pre>, [text](url)→<a href>
  - FR-3: Map block markup — > quote→<blockquote>; paragraphs separated by blank lines
  - FR-4: DEGRADE Telegram-unsupported constructs gracefully — # / ## / ### headings → bold line (<b>…</b>); unordered bullets (-, *, +) → "• "; ordered lists keep "N. "; pipe tables → flattened plain lines (no markup loss as literal "|")
  - FR-5: Text content MUST be HTML-escaped (<,>,& → entities); output composes with sanitize_html so the result is valid for parse_mode=HTML with no double-escaping
  - FR-6: Applied in deliver_output BEFORE the size policy (inline/chain/summary) so all paths emit valid TG-HTML
  - FR-7: Idempotence-safe and robust on edge cases (nested **/_, "*" inside `code`, links with query "&", escaped \\*) via a real tokenizer, not regex
nfr:
  - NFR-1: Use markdown-it-py (ALREADY in uv.lock 4.2.0, transitive) promoted to a direct dependency — robust CommonMark tokenizer; a small custom renderer walks the token stream and emits ONLY the TG whitelist + degradations. No new external dep beyond promoting the locked one.
  - NFR-2: No behavioural change to non-markdown text (plain text passes through escaped, unchanged meaning)
  - NFR-3: mypy --strict + ruff + grace lint clean; deliver_output existing tests stay green
  - NFR-4: "Pin markdown-it-py == 4.2.0 (project policy: exact ==)"
  - NFR-5: Default parse_mode stays HTML (D-024 unchanged) — this makes the HTML payload correct, it does not switch modes
constraints:
  - ALLOWED_TAGS = {b,i,u,s,a,code,pre} (output.py) — converter MUST emit only these; everything else degrades or escapes
  - markdown-it-py default preset is CommonMark — strikethrough/tables are NOT in core; enable strikethrough rule if present, handle tables via a pre-pass flattener (no extra plugin dep)
  - Telegram has NO headings, NO tables, NO native list entities — degradation is mandatory, not optional
  - "Compose with sanitize_html (aisw-azu): the converter's text nodes are escaped; sanitize_html on already-valid converter output must be idempotent"
risks:
  - markdown-it core lacks table/strikethrough → unhandled tables render literally. Mitigation - pre-pass pipe-table flattener + enable strikethrough rule (verified at execution); fall back to escaped text otherwise
  - Converter bug could drop/garble content. Mitigation - the parse_mode=None fallback (aisw-azu) still guarantees delivery; extensive token-level unit tests incl. the exact prod payload
  - Double-escaping between converter and sanitize_html. Mitigation - converter escapes text nodes itself and emits final TG-HTML; sanitize_html is then idempotent over it (tested)
scope_in:
  - src/ai_steward_wiki/tg/md_to_html.py (NEW — markdown_to_tg_html(text) via MarkdownIt token walk + degradations + pre-pass table flatten)
  - src/ai_steward_wiki/tg/output.py (call markdown_to_tg_html at top of deliver_output, before/with sanitize_html)
  - pyproject.toml (promote markdown-it-py == 4.2.0 to direct dependency)
  - tests/unit/tg/test_md_to_html.py (NEW — token mappings, degradations, prod payload, idempotence with sanitize_html)
scope_out:
  - Switching parse_mode to MarkdownV2 (D-024 stays HTML)
  - Full table rendering (TG has no tables — flatten only)
  - Changing the wiki prompt (defence at delivery boundary; prompt tuning is a separate optional follow-up)
  - Custom-emoji / spoiler / images
---

# Discovery: Markdown→Telegram-HTML converter (aisw-iyz)

## Symptom (prod 2026-06-23, screenshot)

Bot replies are delivered (aisw-azu fix works) but show literal markdown: `**133/92/69**`,
`## Резюме`, `### Изменённые файлы`, `` `metrics/...csv` ``, `- bullets`. Cause: Claude emits
GitHub-Markdown; the bot sends `parse_mode=HTML` (D-024); HTML mode doesn't interpret markdown,
so the syntax shows verbatim. NOT a Telegram client/version issue (Desktop 6.9.3 is fine).

## Telegram formatting reality

Telegram message entities support only: bold, italic, underline, strikethrough, spoiler, code,
pre, blockquote, links. NO headings, NO tables, NO native markdown lists. So `##`, `|tables|`,
`- ` must be DEGRADED, not "rendered".

## Approach (user-approved fork)

markdown-it-py (already in uv.lock 4.2.0) → token stream → small custom renderer emitting only
the TG whitelist + degradations. Robust parsing (no regex fragility), near-zero dependency cost
(promote the already-locked transitive dep). Verified token types empirically: heading_open,
paragraph_open, inline (children: text/strong/em/code_inline/link), bullet_list/list_item,
fence, blockquote. Composes with sanitize_html (aisw-azu) and the parse_mode=None fallback as a
safety net.
