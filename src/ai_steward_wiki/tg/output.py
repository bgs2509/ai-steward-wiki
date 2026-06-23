# FILE: src/ai_steward_wiki/tg/output.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: D-025 output-size hybrid policy — ≤3500 inline, ≤10000 chain-split,
#            >10000 Haiku-summary + send_document. Always persists full text to
#            <wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md and indexes the file in
#            audit.run_outputs. Sanitizes outbound HTML for TG parse_mode=HTML.
#   SCOPE: sanitize_html (whitelist-preserving escaper), HtmlBalancer (open/close
#          whitelist), ChainSplitter, deliver_output, HaikuSummarizer Protocol,
#          LengthCapSummarizer fallback, DeliveryReceipt.
#   DEPENDS: SQLAlchemy.async, ai_steward_wiki.storage.audit.models.RunOutput,
#            ai_steward_wiki.tg.bot.TgSender, structlog
#   LINKS: D-024, D-025, M-TG-TEXT
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ALLOWED_TAGS - whitelist of HTML tags supported by TG parse_mode=HTML
#   INLINE_THRESHOLD - 3500 chars
#   CHAIN_THRESHOLD - 10000 chars
#   PART_MAX_CHARS - per-part target (3500) within chain
#   HARD_CAP_PARTS - max chain parts (3)
#   SUMMARY_MAX_CHARS - max chars of >10000 branch summary
#   OutputKind - Literal[reply|digest|ingest_report]
#   sanitize_html - escape non-whitelist markup, keep ALLOWED_TAGS live (aisw-azu)
#   HtmlBalancer - tokeniser-based open/close balancer
#   ChainSplitter - split into ≤N parts at semantic boundaries with (i/M) footer
#   HaikuSummarizer - Protocol with async summarize(text) -> str
#   LengthCapSummarizer - safe fallback truncating to ≤1500 chars with ellipsis
#   DeliveryReceipt - dataclass returned by deliver_output
#   deliver_output - main entry point (size hybrid + persistence)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-azu: sanitize_html() escapes stray <,>,& so parse_mode=HTML never rejects the message; applied at top of deliver_output
#   PREVIOUS:    v0.0.2 - aisw-x92: tg_send flag (skip TG send on streaming
#                slow-path; persist+audit stay unconditional)
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.logging_events import IO_ANCHOR_AUDIT_WRITE
from ai_steward_wiki.logging_setup import anchored
from ai_steward_wiki.storage.audit.models import RunOutput
from ai_steward_wiki.tg.bot import TgSender

__all__ = [
    "ALLOWED_TAGS",
    "CHAIN_THRESHOLD",
    "HARD_CAP_PARTS",
    "INLINE_THRESHOLD",
    "PART_MAX_CHARS",
    "SUMMARY_MAX_CHARS",
    "ChainSplitter",
    "DeliveryReceipt",
    "HaikuSummarizer",
    "HtmlBalancer",
    "LengthCapSummarizer",
    "OutputKind",
    "deliver_output",
    "sanitize_html",
]

_log = structlog.get_logger("tg.output")

ALLOWED_TAGS = frozenset({"b", "i", "u", "s", "a", "code", "pre"})
INLINE_THRESHOLD = 3500
CHAIN_THRESHOLD = 10000
PART_MAX_CHARS = 3500
HARD_CAP_PARTS = 3
SUMMARY_MAX_CHARS = 1500

OutputKind = Literal["reply", "digest", "ingest_report"]

# Matches an HTML start/end tag with optional attributes — non-greedy.
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")

# aisw-azu: outbound HTML sanitization. parse_mode=HTML (D-024) rejects the WHOLE
# message on any stray "<"/"&" that isn't valid markup (prod 2026-06-23:
# "<120/80" → TelegramBadRequest "can't parse entities"). We keep the whitelist
# tags live and escape everything else, so model output can contain "<", ">", "&"
# freely. Built from ALLOWED_TAGS so the whitelist stays single-sourced.
# NOTE: `[^>]*>` ends the tag at the first ">", so a ">" inside a quoted attribute
# (e.g. <a href="a>b">) would split the tag. That input never produces a NEW bare
# "<" in the escaped output (verified), and Telegram itself rejects ">"-in-attr —
# which the parse_mode=None fallback in AiogramSender then catches. Only <a> takes
# attributes and ">" in a URL is effectively nonexistent, so this stays a documented
# limitation rather than a quote-aware regex (KISS / YAGNI for the TG whitelist).
_ALLOWED_TAG_RE = re.compile(
    r"</?(?:" + "|".join(sorted(ALLOWED_TAGS)) + r")\b[^>]*>", re.IGNORECASE
)
# A "&" that does NOT already start a known entity (so escaping is idempotent).
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")


def _escape_text(segment: str) -> str:
    """HTML-escape free text (not a whitelisted tag). Idempotent: only bare ``&``."""
    segment = _BARE_AMP_RE.sub("&amp;", segment)
    return segment.replace("<", "&lt;").replace(">", "&gt;")


# START_CONTRACT: sanitize_html
#   PURPOSE: Make text safe for Telegram parse_mode=HTML — keep ALLOWED_TAGS live, escape all other markup.
#   INPUTS: { text: str - raw model/output text (may contain stray <, >, &) }
#   OUTPUTS: { str - HTML where only whitelisted tags are live; everything else escaped }
#   SIDE_EFFECTS: none
# END_CONTRACT: sanitize_html
def sanitize_html(text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _ALLOWED_TAG_RE.finditer(text):
        out.append(_escape_text(text[last : m.start()]))
        # Keep the whitelisted tag verbatim, but escape a bare "&" in its attrs
        # (e.g. <a href="u?a=1&b=2"> — Telegram rejects raw & in attribute values).
        out.append(_BARE_AMP_RE.sub("&amp;", m.group(0)))
        last = m.end()
    out.append(_escape_text(text[last:]))
    return "".join(out)


@dataclass(frozen=True)
class _Tag:
    name: str
    attrs: str  # raw attrs portion (preserved verbatim for re-open)
    closing: bool

    @property
    def open_str(self) -> str:
        return f"<{self.name}{self.attrs}>"

    @property
    def close_str(self) -> str:
        return f"</{self.name}>"


class HtmlBalancer:
    """Stack-based balancer for the TG HTML whitelist.

    Usage:
      balancer = HtmlBalancer()
      out = balancer.balance(segment)            # close-then-reopen safe segment
      tail_reopen = balancer.reopen_tags()       # tags to prepend on next segment
    """

    def __init__(self, allowed: frozenset[str] = ALLOWED_TAGS) -> None:
        self._allowed = allowed
        self._open_stack: list[_Tag] = []

    @staticmethod
    def _iter_tags(text: str) -> list[tuple[int, int, _Tag]]:
        out: list[tuple[int, int, _Tag]] = []
        for m in _TAG_RE.finditer(text):
            closing = m.group(1) == "/"
            name = m.group(2).lower()
            attrs = m.group(3) or ""
            out.append((m.start(), m.end(), _Tag(name=name, attrs=attrs, closing=closing)))
        return out

    def feed(self, text: str) -> None:
        """Update internal open-stack by scanning text for whitelisted tags."""
        for _s, _e, tag in self._iter_tags(text):
            if tag.name not in self._allowed:
                continue
            if tag.closing:
                # Pop matching most recent open tag (best-effort).
                for i in range(len(self._open_stack) - 1, -1, -1):
                    if self._open_stack[i].name == tag.name:
                        del self._open_stack[i]
                        break
            else:
                self._open_stack.append(tag)

    def close_open(self) -> str:
        """Return </…> for currently-open tags in reverse order (without mutating)."""
        return "".join(t.close_str for t in reversed(self._open_stack))

    def reopen_tags(self) -> str:
        """Return <…> for currently-open tags in original order."""
        return "".join(t.open_str for t in self._open_stack)

    def balance_segment(self, segment: str) -> tuple[str, str]:
        """Return (closed_segment, reopen_prefix) for a piece of text.

        After calling, the balancer's open-stack equals the state at the segment's
        original end (so reopen_prefix opens those tags again for the next segment).
        """
        self.feed(segment)
        closing = self.close_open()
        reopen = self.reopen_tags()
        return segment + closing, reopen


class ChainSplitter:
    """Split a long body into ≤hard_cap parts at semantic boundaries.

    Boundary priority: ``<b>`` header → blank line → sentence ``. \\n`` → ``. ``.
    Falls back to a hard char-count cut. Appends ``(i/M)`` footer (outside tags).
    """

    _BOUNDARY_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"<b>"),
        re.compile(r"\n\s*\n"),
        re.compile(r"\.\s*\n"),
        re.compile(r"\.\s"),
    )

    def __init__(
        self,
        *,
        part_max_chars: int = PART_MAX_CHARS,
        hard_cap: int = HARD_CAP_PARTS,
    ) -> None:
        self._part_max = part_max_chars
        self._hard_cap = hard_cap

    def _find_boundary(self, text: str, lo: int, hi: int) -> int:
        """Walk back from hi to lo, return best boundary char-index or hi."""
        for pat in self._BOUNDARY_PATTERNS:
            best = -1
            for m in pat.finditer(text, lo, hi):
                best = m.end()
            if best >= lo + (hi - lo) // 2:  # require boundary in second half
                return best
        return hi

    def split(self, text: str) -> list[str]:
        if len(text) <= self._part_max:
            return [_with_footer(text, 1, 1)]
        parts: list[str] = []
        i = 0
        n = len(text)
        balancer = HtmlBalancer()
        pending_reopen = ""
        while i < n and len(parts) < self._hard_cap:
            remaining = n - i
            if remaining <= self._part_max:
                seg = pending_reopen + text[i:n]
                parts.append(seg)
                i = n
                break
            cut = self._find_boundary(text, i, i + self._part_max)
            seg_body = text[i:cut]
            closed_body, reopen = balancer.balance_segment(seg_body)
            parts.append(pending_reopen + closed_body)
            pending_reopen = reopen
            i = cut
        if i < n and len(parts) >= self._hard_cap:
            # Fold the rest into the last part (rare; >10000 path handles it).
            parts[-1] = parts[-1] + text[i:n]
        total = len(parts)
        return [_with_footer(p, idx + 1, total) for idx, p in enumerate(parts)]


def _with_footer(text: str, idx: int, total: int) -> str:
    return f"{text}\n({idx}/{total})"


class HaikuSummarizer(Protocol):
    """Stage-0 Haiku summarizer surface (real impl wired in chunk 12)."""

    async def summarize(self, text: str) -> str: ...


class LengthCapSummarizer:
    """Default fallback summarizer — truncates to SUMMARY_MAX_CHARS with ellipsis.

    Used when no real Haiku adapter is wired (D-025 spirit: best-effort summary).
    """

    async def summarize(self, text: str) -> str:
        if len(text) <= SUMMARY_MAX_CHARS:
            return text
        cut = SUMMARY_MAX_CHARS - 1
        # Avoid splitting inside an HTML tag — walk back to '>' if needed.
        if "<" in text[:cut] and text.rfind("<", 0, cut) > text.rfind(">", 0, cut):
            cut = text.rfind(">", 0, cut) + 1
        return text[:cut] + "\u2026"


@dataclass(frozen=True)
class DeliveryReceipt:
    run_id: str
    kind: OutputKind
    n_messages: int
    summary_chars: int | None
    output_path: Path
    output_bytes: int
    output_sha256: str
    document_sent: bool


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _persist_to_disk(
    *,
    runs_dir: Path,
    run_id: str,
    wiki_id: str,
    chat_id: int,
    text: str,
    started_at_utc: datetime,
) -> tuple[Path, int, str]:
    """Atomic write text to <runs_dir>/<YYYY-MM-DD>/<run_id>.md with frontmatter."""
    day = started_at_utc.strftime("%Y-%m-%d")
    target_dir = runs_dir / day
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{run_id}.md"
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    body = (
        "---\n"
        f"run_id: {run_id}\n"
        f"wiki_id: {wiki_id}\n"
        f"chat_id: {chat_id}\n"
        f"ts: {started_at_utc.isoformat()}Z\n"
        f"size: {len(text)}\n"
        f"sha256: {sha}\n"
        "---\n\n"
        f"{text}\n"
    )
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return target, len(text.encode("utf-8")), sha


async def _record_run_output(
    *,
    audit_session_maker: async_sessionmaker[AsyncSession],
    run_id: str,
    job_id: int | None,
    wiki_id: str,
    owner_telegram_id: int,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    output_path: Path,
    output_bytes: int,
    output_sha256: str,
    summary_chars: int | None,
    kind: OutputKind,
) -> None:
    async with audit_session_maker() as session, session.begin():
        session.add(
            RunOutput(
                run_id=run_id,
                job_id=job_id,
                wiki_id=wiki_id,
                owner_telegram_id=owner_telegram_id,
                started_at_utc=started_at_utc,
                finished_at_utc=finished_at_utc,
                output_path=str(output_path),
                output_bytes=output_bytes,
                output_sha256=output_sha256,
                summary_chars=summary_chars,
                kind=kind,
            )
        )


async def deliver_output(
    *,
    sender: TgSender,
    chat_id: int,
    telegram_id: int,
    wiki_id: str,
    run_id: str,
    text: str,
    runs_dir: Path,
    audit_session_maker: async_sessionmaker[AsyncSession],
    kind: OutputKind = "reply",
    job_id: int | None = None,
    summarizer: HaikuSummarizer | None = None,
    tg_send: bool = True,
    audit_io_threshold_ms: int = 1000,
) -> DeliveryReceipt:
    """Deliver `text` to TG using D-025 hybrid policy + always-persist to disk.

    ``tg_send=False`` skips the Telegram send entirely (used by the streaming
    slow-path, which has already delivered the reply via in-place edits) while
    still persisting the full text to disk and recording the audit row.
    """
    started = _utcnow_naive()
    # aisw-azu: make the payload valid for parse_mode=HTML BEFORE any send/persist —
    # stray "<"/">"/"&" in model output would otherwise make Telegram reject the
    # whole message ("can't parse entities"). Whitelisted tags stay live.
    text = sanitize_html(text)
    output_path, output_bytes, sha = _persist_to_disk(
        runs_dir=runs_dir,
        run_id=run_id,
        wiki_id=wiki_id,
        chat_id=chat_id,
        text=text,
        started_at_utc=started,
    )

    summary_chars: int | None = None
    document_sent = False
    n_messages = 0

    if tg_send:
        if len(text) <= INLINE_THRESHOLD:
            balancer = HtmlBalancer()
            balanced, _ = balancer.balance_segment(text)
            await sender.send_message(chat_id, balanced)
            n_messages = 1
        elif len(text) <= CHAIN_THRESHOLD:
            parts = ChainSplitter().split(text)
            for part in parts:
                await sender.send_message(chat_id, part)
            n_messages = len(parts)
        else:
            eff_summarizer: HaikuSummarizer = summarizer or LengthCapSummarizer()
            summary = await eff_summarizer.summarize(text)
            summary_chars = len(summary)
            await sender.send_message(chat_id, summary)
            await sender.send_document(chat_id, path=output_path, caption=f"run_id={run_id}")
            n_messages = 1
            document_sent = True

    finished = _utcnow_naive()
    # aisw-xbc: anchor the audit write — a slow/failing DB write here is one of the
    # post-confirm freeze suspects; threshold-gated so the happy path stays silent.
    async with anchored(IO_ANCHOR_AUDIT_WRITE, threshold_ms=audit_io_threshold_ms, logger=_log):
        await _record_run_output(
            audit_session_maker=audit_session_maker,
            run_id=run_id,
            job_id=job_id,
            wiki_id=wiki_id,
            owner_telegram_id=telegram_id,
            started_at_utc=started,
            finished_at_utc=finished,
            output_path=output_path,
            output_bytes=output_bytes,
            output_sha256=sha,
            summary_chars=summary_chars,
            kind=kind,
        )

    _log.info(
        "tg.output.delivered",
        chat_id=chat_id,
        telegram_id=telegram_id,
        wiki_id=wiki_id,
        run_id=run_id,
        kind=kind,
        size=len(text),
        n_messages=n_messages,
        document_sent=document_sent,
        summary_chars=summary_chars,
        tg_send=tg_send,
    )
    return DeliveryReceipt(
        run_id=run_id,
        kind=kind,
        n_messages=n_messages,
        summary_chars=summary_chars,
        output_path=output_path,
        output_bytes=output_bytes,
        output_sha256=sha,
        document_sent=document_sent,
    )
