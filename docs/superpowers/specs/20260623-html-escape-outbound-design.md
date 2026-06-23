---
feature: html-escape-outbound
bd_id: aisw-azu
module_id: M-TG-OUTPUT
status: stable
date: 2026-06-23
stack:
  - library: html (stdlib)
    version: py3.11+
    used_for: entity escaping of non-whitelist text (&, <, >)
  - library: re (stdlib)
    version: py3.11+
    used_for: tokenize text by ALLOWED_TAGS regex; entity-aware & escaping in attrs
  - library: aiogram
    version: 3.15.0 (uv.lock)
    used_for: TelegramBadRequest detection for the parse_mode=None fallback
decisions:
  - D-local-1: Two-layer defence. Layer 1 (primary, root cause) — sanitize_html() escapes all markup EXCEPT whitelisted tags before send. Layer 2 (safety net) — AiogramSender.send_message retries once with parse_mode=None if Telegram still rejects with "can't parse entities".
  - D-local-2: sanitize_html tokenizes by an ALLOWED_TAGS-only regex (built from the existing ALLOWED_TAGS set — no fork). Gaps between matched tags + any non-whitelisted "<...>"-like text are escaped via _escape_text (& → &amp;, then < → &lt;, then > → &gt;, order matters). Whitelisted tags are emitted verbatim except bare "&" in their attrs is escaped with a negative-lookahead (skip existing &amp;/&lt;/&gt;/&quot;/&#nn;).
  - D-local-3: Apply once at the TOP of deliver_output (text = sanitize_html(text)) so inline / chain-split / summary paths all operate on already-valid HTML. HtmlBalancer then sees only whitelisted tags (its existing contract holds).
  - D-local-4: Fallback scope — only on aiogram.exceptions.TelegramBadRequest whose message contains "can't parse entities"; resend with parse_mode=None (plain text, markup shown literally) and emit tg.io.send_message.parse_fallback WARNING. Any other TelegramBadRequest re-raises unchanged. Retry happens because the first call raised before returning — nothing was delivered, so no double-send.
  - D-local-5: sanitize_html lives in output.py next to ALLOWED_TAGS/_TAG_RE (SSoT for the whitelist) and is exported for reuse/testing.
---

# Design: escape outbound HTML (aisw-azu)

## Layer 1 — sanitize_html (primary, fixes root cause)

```python
import html  # noqa  (or manual ordered replace)

_ALLOWED_TAG_RE = re.compile(
    r"</?(?:" + "|".join(sorted(ALLOWED_TAGS)) + r")\b[^>]*>", re.IGNORECASE
)
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")

def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def sanitize_html(text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _ALLOWED_TAG_RE.finditer(text):
        out.append(_escape_text(text[last:m.start()]))     # escape the gap
        out.append(_BARE_AMP_RE.sub("&amp;", m.group(0)))  # keep tag, fix bare & in attrs
        last = m.end()
    out.append(_escape_text(text[last:]))
    return "".join(out)
```

- `<120/80` is in a gap (doesn't match an allowed tag) → `&lt;120/80`. Telegram accepts it.
- `<b>x</b>` matches → preserved. `<div>` does NOT match the allowed-only regex → escaped to `&lt;div&gt;`.
- `<a href="u?a=1&b=2">` matches → kept; bare `&` → `&amp;`; existing `&amp;` left intact.

## Layer 2 — send fallback (safety net)

```python
# AiogramSender.send_message, inside the anchored() block
try:
    msg = await self._bot.send_message(chat_id, text, parse_mode=parse_mode, ...)
except TelegramBadRequest as exc:
    if "can't parse entities" not in str(exc):
        raise
    _log.warning(IO_SEND_PARSE_FALLBACK, ...)        # new event in logging_events
    msg = await self._bot.send_message(chat_id, text, parse_mode=None, ...)
```

Covers residual edge cases (malformed allowed tag, exotic attribute) so a reply is never lost.
The `anchored` error anchor still fires only if BOTH attempts fail.

## Application point

`deliver_output` line ~349: `text = sanitize_html(text)` immediately after `started = ...`,
before `_persist_to_disk` (persist the sanitized text too — disk copy matches what was sent).

## Out of scope

Default parse_mode stays HTML (D-024). No bleach/lxml dependency — stdlib + the existing whitelist.
