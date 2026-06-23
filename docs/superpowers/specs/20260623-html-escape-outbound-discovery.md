---
feature: html-escape-outbound
bd_id: aisw-azu
module_id: M-TG-OUTPUT
status: stable
date: 2026-06-23
risk: medium
fr:
  - FR-1: Outbound reply text MUST be deliverable regardless of stray "<", ">", "&" in model output — Telegram parse_mode=HTML must never reject the whole message because of unescaped markup
  - FR-2: Whitelisted formatting tags (ALLOWED_TAGS = b,i,u,s,a,code,pre) MUST be preserved as live HTML; everything else that looks like markup MUST be HTML-escaped (&lt; &gt; &amp;)
  - FR-3: Bare "&" inside a preserved tag's attributes (e.g. <a href="x?a=1&b=2">) MUST be escaped to &amp; (Telegram rejects raw & in attribute values too)
  - FR-4: Sanitization MUST apply to ALL outbound paths in deliver_output — inline send, chain-split parts, and the summary+document path
  - FR-5: Defensive fallback — if a send still fails with TelegramBadRequest "can't parse entities", retry once with parse_mode=None so the reply is delivered as plain text rather than lost
  - FR-6: Regression tests with real offending payloads ("норма <120/80", "a < b", "x & y", "<div>", "<a href='u?a=1&b=2'>ok</a>")
nfr:
  - NFR-1: No loss of intentional formatting — valid <b>/<i>/… still render
  - NFR-2: Idempotent-ish — sanitizing already-valid whitelisted HTML does not double-escape the allowed tags
  - NFR-3: mypy --strict + ruff + grace lint clean; deliver_output existing tests stay green
  - NFR-4: Fallback emits a structlog warning (tg.io.send_message.parse_fallback) so silent degradation is visible
constraints:
  - parse_mode=HTML is the product default (D-024) — do NOT switch the default to Markdown/plain; keep HTML, just make the payload valid
  - ALLOWED_TAGS and _TAG_RE already defined in src/ai_steward_wiki/tg/output.py — reuse, do not fork the whitelist
  - aiogram 3.15 raises aiogram.exceptions.TelegramBadRequest with message "can't parse entities ..." for invalid HTML
risks:
  - Over-escaping a legitimate allowed tag (e.g. <b>) → regression in formatting. Mitigation - tokenize by allowed-tag regex, escape only the gaps + non-whitelist tags; test with mixed payloads
  - <a href> with entity-bearing URL — naive "&"→"&amp;" could double-escape an existing &amp;. Mitigation - negative-lookahead regex (skip &amp;/&lt;/&gt;/&quot;/&#nn;)
  - Fallback retry could double-send if the first send partially succeeded. Mitigation - only retry on TelegramBadRequest BEFORE any message is returned (the call raised, nothing delivered)
scope_in:
  - src/ai_steward_wiki/tg/output.py (NEW sanitize_html(); apply at top of deliver_output)
  - src/ai_steward_wiki/tg/bot.py (AiogramSender.send_message — parse_mode=None fallback on can't-parse-entities)
  - tests/unit/tg/test_output.py + tests/unit/tg/test_bot_anchors.py (regression tests)
scope_out:
  - Switching default parse_mode away from HTML (D-024 unchanged)
  - Markdown rendering / a full HTML sanitizer library dependency (bleach etc.) — stdlib only, scoped to the TG whitelist
  - Changing the wiki prompt to emit safer output (model-side) — defence is at the delivery boundary
---

# Discovery: escape outbound HTML so stray "<" cannot break Telegram delivery (aisw-azu)

## Symptom & evidence (prod 2026-06-23, vpn-gpu-1)

After `tg.pipeline.route.confirm_executed`, `deliver_output → AiogramSender.send_message`
raised:

```
aiogram.exceptions.TelegramBadRequest: Bad Request: can't parse entities:
  Unsupported start tag "120/80," at byte offset 1260
```

The 1197-char Medical-WIKI reply contained `<120/80,` (blood-pressure text). With
`parse_mode=HTML` (D-024), Telegram reads `<` as a tag opener, fails to parse, and rejects
the **entire** message. The `📝 Записываю в вики…` ACK was never replaced → the user saw a
"hang" although the event loop was healthy (heartbeat `lag_ms` 8 — NOT the 2026-06-20 freeze).
The `aisw-xbc` boundary anchor `tg.io.send_message.error` captured it with the full traceback.

## Root cause (verified, output.py:75,85,103-157)

- `ALLOWED_TAGS = {b,i,u,s,a,code,pre}`; `_TAG_RE = <(/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>`.
- `HtmlBalancer` only *balances* whitelisted tags (closes dangling ones). It does NOT escape
  stray `<`/`>`/`&`. `<120/80` doesn't even match `_TAG_RE` (tag name must start with a
  letter), so it passes through untouched — and Telegram's laxer parser treats `<120` as a tag.

## Why it looks like the freeze but isn't

Loop healthy, ingest succeeded, only the final `send_message` raised. Distinct bug from
aisw-xbc (which is the diagnostics that *caught* this). Affects ANY reply containing `<`.
