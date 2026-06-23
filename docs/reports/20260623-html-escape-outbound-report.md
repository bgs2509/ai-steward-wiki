# Completion Report: escape outbound HTML (aisw-azu)

**Date:** 2026-06-23
**Type:** bugfix
**Module:** M-TG-OUTPUT (+ AiogramSender in M-TG bot)

## Why

Prod 2026-06-23 (vpn-gpu-1): a Medical-WIKI reply containing `<120/80` made
`AiogramSender.send_message` raise `aiogram.exceptions.TelegramBadRequest: can't parse
entities: Unsupported start tag "120/80,"`. With `parse_mode=HTML` (D-024), Telegram treats
`<` as a tag opener and rejects the WHOLE message. The `📝 Записываю в вики…` ACK was never
replaced → user perceived a hang (event loop was healthy — heartbeat `lag_ms` 8; NOT the
2026-06-20 freeze). Caught by the `aisw-xbc` boundary anchor `tg.io.send_message.error`.

Root cause: `HtmlBalancer` (output.py) only *balances* whitelisted tags — it never *escaped*
stray `<`/`>`/`&`. `<120/80` doesn't even match `_TAG_RE`, so it passed through untouched.

## What shipped (two-layer defence)

1. **`sanitize_html(text)`** (output.py, primary) — escapes all markup EXCEPT `ALLOWED_TAGS`
   (`b,i,u,s,a,code,pre`). Stray `<`/`>`/bare-`&` → `&lt;`/`&gt;`/`&amp;`; whitelisted tags kept
   live; bare `&` in an `<a>` attribute escaped. Idempotent (only bare `&` escaped via
   negative-lookahead). Applied once at the top of `deliver_output` → covers inline / chain /
   summary paths uniformly.
2. **parse_mode=None fallback** (AiogramSender.send_message, safety net) — on a
   `TelegramBadRequest` containing `can't parse entities`, resend once as plain text and emit
   `tg.io.send_message.parse_fallback` WARNING (with `text_len`). Any other `TelegramBadRequest`
   re-raises. Guarantees a reply is never lost to a residual escaping edge case.

## Verification (evidence)

- `make lint` (ruff + format + mypy --strict): clean. `grace lint`: 0 issues. `make inv-lint`: 14/14.
- `uv run pytest tests/unit`: **989 passed** (+11 vs baseline). New: 8 sanitize unit tests
  (incl. the prod payload `норма <120/80`, `<a href="u?a=1&b=2">`, idempotence), 1 deliver_output
  regression, 2 send fallback tests.

## Review (Step 12, py-quality)

0 CRITICAL, 2 MAJOR, 2 MINOR, 1 NIT. Resolved:
- **MAJOR-1** — fallback log now carries `text_len` for incident diagnosability.
- **MAJOR-2** — `[^>]*>` ends a tag at the first `>`, so `>` inside a quoted attr (`<a href="a>b">`)
  splits the tag. Verified it produces NO new bare `<` and is caught by the parse_mode=None
  fallback; documented as a KISS/YAGNI limitation (only `<a>` has attrs; `>` in a URL is
  effectively nonexistent) rather than a quote-aware regex.
- **MINOR-2** — fallback test now asserts the retry resends the same `text`.
- **NIT-1** — `"can't parse entities"` extracted to `_PARSE_ENTITIES_ERR`.
- **MINOR-1** (named entities like `&mdash;` → `&amp;mdash;`) — left as-is: correct for the TG
  HTML whitelist (only amp/lt/gt/quot are supported), idempotence preserved.

## Relationship to aisw-xbc

Distinct bug. aisw-xbc (hang diagnostics) is what *caught* this in one shot via the
`tg.io.send_message.error` anchor — turning a silent "hang" into a logged traceback. Default
parse_mode stays HTML (D-024 unchanged).
