# FILE: src/ai_steward_wiki/ops/pii.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Tiered PII redactor (NIST SP 800-122) for structlog + DB write paths.
#   SCOPE: PIIRedactor dataclass, redact()/redact_event(), make_structlog_processor(),
#          compiled tier-1 DROP and tier-2 MASK regex sets, HMAC-blake2b-128 hashing.
#   DEPENDS: hmac, re, hashlib (blake2b via hmac.new)
#   LINKS: D-034, §10.4, M-OPS-PII
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TIER1_PATTERNS - compiled tier-1 DROP regex list
#   PIIRedactor - stateful redactor with HMAC secret + on/off toggles
#   redact - module-level convenience using a default redactor
#   make_structlog_processor - structlog chain adapter
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 13: initial tiered redactor (D-034)
# END_CHANGE_SUMMARY

from __future__ import annotations

import hmac
import re
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TIER1_PATTERNS",
    "PIIRedactor",
    "make_structlog_processor",
    "redact",
]

# ---- Tier-1 DROP patterns (no plaintext retained, no hash). ----
#
# Order matters: PEM block first (multiline), then JWT, then bearer header,
# then k/v secret/api_key/password/token, then credit-card PAN, then SSN.

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*?PRIVATE KEY-----.*?-----END [A-Z ]*?PRIVATE KEY-----",
    re.DOTALL,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}\b")
# k/v: password=..., api_key="...", secret=..., token=...
_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|api[_-]?key|secret|token|access[_-]?token|refresh[_-]?token)\s*[:=]\s*"
    r"\"?[A-Za-z0-9._\-/+=]{6,}\"?",
)
# 13-19 digit card numbers with optional spaces or dashes (Luhn check applied).
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Naive US SSN.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _luhn_valid(digits: str) -> bool:
    s = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0 and len(digits) >= 13


def _sub_card(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"[ -]", "", raw)
    if digits.isdigit() and _luhn_valid(digits):
        return "[REDACTED:tier1:card]"
    return raw


TIER1_PATTERNS: tuple[tuple[re.Pattern[str], str | Any], ...] = (
    (_PEM_RE, "[REDACTED:tier1:pem]"),
    (_JWT_RE, "[REDACTED:tier1:jwt]"),
    (_BEARER_RE, "[REDACTED:tier1:bearer]"),
    (_SECRET_KV_RE, "[REDACTED:tier1:secret]"),
    (_CARD_RE, _sub_card),
    (_SSN_RE, "[REDACTED:tier1:ssn]"),
)

# ---- Tier-2 MASK patterns (shape-preserving placeholder + HMAC-blake2b-128). ----

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Phones: international (+CC ...) or Russian local starting with 7/8 then 10 more digits.
# Conservative — won't gobble UUID hex-dash blocks.
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:"
    r"\+\d{1,3}[\s().\-]*\d{1,4}[\s().\-]*\d{1,4}[\s().\-]*\d{1,4}[\s().\-]*\d{0,4}"
    r"|"
    r"[78][\s().\-]*\(?\d{3,4}\)?[\s().\-]*\d{3}[\s().\-]*\d{2}[\s().\-]*\d{2}"
    r")(?![\w])",
)
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


def _iban_valid(iban: str) -> bool:
    s = iban[4:] + iban[:4]
    converted = "".join(str(int(c, 36)) if c.isalpha() else c for c in s)
    try:
        return int(converted) % 97 == 1
    except ValueError:
        return False


def _normalize_phone(text: str) -> str:
    return re.sub(r"\D", "", text)


def _normalize_email(text: str) -> str:
    return text.strip().lower()


def _normalize_iban(text: str) -> str:
    return text.replace(" ", "").upper()


# ---- Default singleton (lazy via redact()) ----

_DEFAULT_SECRET = b"aisw-default-pii-salt-do-not-use-in-prod"


@dataclass(frozen=True)
class PIIRedactor:
    """Tier-1 DROP / Tier-2 MASK redactor (D-034).

    INV: idempotent — redact(redact(x)) == redact(x).
    INV: tier-2 hash is deterministic given the same secret.
    """

    hash_secret: bytes = _DEFAULT_SECRET
    drop_enabled: bool = True
    mask_enabled: bool = True
    # Internal: skip patterns inside already-redacted markers to keep idempotency.
    _marker_re: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"\[(?:REDACTED|MASK):[^\]]+\]")
    )

    # --------- public API ---------

    def redact(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        out = text
        # 1. Stash already-redacted markers so we never touch them.
        markers: list[str] = []

        def _stash(m: re.Match[str]) -> str:
            markers.append(m.group(0))
            return f"\0AISW_PII_MARK_{len(markers) - 1}\0"

        out = self._marker_re.sub(_stash, out)

        if self.drop_enabled:
            for pat, repl in TIER1_PATTERNS:
                # Both branches use the same call; pat.sub accepts str or callable.
                out = pat.sub(repl, out)

        if self.mask_enabled:
            out = self._apply_tier2(out)

        # 2. Restore markers.
        def _unstash(m: re.Match[str]) -> str:
            idx = int(m.group(1))
            return markers[idx]

        out = re.sub(r"\0AISW_PII_MARK_(\d+)\0", _unstash, out)
        return out

    def redact_event(self, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        for k, v in list(event_dict.items()):
            event_dict[k] = self._redact_value(v)
        return event_dict

    def hash_token(self, normalized: str) -> str:
        digest = hmac.new(self.hash_secret, normalized.encode("utf-8"), "blake2b").hexdigest()
        return digest[:16]

    # --------- internals ---------

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(v) for v in value)
        return value

    def _apply_tier2(self, text: str) -> str:
        # Email
        def _email(m: re.Match[str]) -> str:
            return f"[MASK:tier2:email:{self.hash_token(_normalize_email(m.group(0)))}]"

        text = _EMAIL_RE.sub(_email, text)

        # IBAN
        def _iban(m: re.Match[str]) -> str:
            raw = _normalize_iban(m.group(0))
            if _iban_valid(raw):
                return f"[MASK:tier2:iban:{self.hash_token(raw)}]"
            return m.group(0)

        text = _IBAN_RE.sub(_iban, text)

        # Phone (after email to avoid eating the local-part)
        def _phone(m: re.Match[str]) -> str:
            digits = _normalize_phone(m.group(0))
            if len(digits) < 10 or len(digits) > 15:
                return m.group(0)
            # Russian local form: leading 8 → +7 normalization.
            if len(digits) == 11 and digits.startswith("8"):
                digits = "7" + digits[1:]
            return f"[MASK:tier2:phone:{self.hash_token(digits)}]"

        text = _PHONE_RE.sub(_phone, text)
        return text


def redact(text: str, *, redactor: PIIRedactor | None = None) -> str:
    return (redactor or PIIRedactor()).redact(text)


def make_structlog_processor(
    redactor: PIIRedactor,
) -> Any:
    """Return a structlog processor that redacts string values in event_dict."""

    def _processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        return redactor.redact_event(event_dict)

    return _processor
