# FILE: src/ai_steward_wiki/tg/__init__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Telegram I/O text-side layer (M-TG-TEXT) — aiogram dispatcher,
#            allowlist middleware, graduated 3-tier confirmations, output-size
#            hybrid policy, HTML-safe streaming edits.
#   SCOPE: BARREL re-export of bot, middleware_auth, confirm, output,
#          stream_edit public API.
#   DEPENDS: ai_steward_wiki.tg.bot, .middleware_auth, .confirm, .output,
#            .stream_edit
#   LINKS: D-023, D-025, D-026, D-031, D-042, M-TG-TEXT
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   build_bot - factory producing an aiogram Bot (production wiring)
#   build_dispatcher - factory wiring middleware + handlers
#   AllowlistMiddleware - aiogram outer-middleware enforcing allowlist (D-031)
#   ConfirmLevel - Literal alias for auto|implicit|explicit
#   ConfirmationService - graduated confirmation flow + 10min TTL (D-023)
#   HtmlBalancer - balance whitelisted HTML tags across truncation boundaries
#   ChainSplitter - split text into ≤N segments at semantic boundaries
#   deliver_output - hybrid size policy + persistence (D-025)
#   DeliveryReceipt - dataclass returned by deliver_output
#   HaikuSummarizer - Protocol for >10000-char summary path
#   LengthCapSummarizer - default safe-fallback summarizer
#   StreamEditor - throttle 1.5s/Δ50 + chain-split at 4000 + final-flush (D-026)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 10: initial M-TG-TEXT layer
# END_CHANGE_SUMMARY

from ai_steward_wiki.tg.bot import build_bot, build_dispatcher
from ai_steward_wiki.tg.confirm import ConfirmationService, ConfirmLevel
from ai_steward_wiki.tg.middleware_auth import AllowlistMiddleware
from ai_steward_wiki.tg.output import (
    ChainSplitter,
    DeliveryReceipt,
    HaikuSummarizer,
    HtmlBalancer,
    LengthCapSummarizer,
    deliver_output,
)
from ai_steward_wiki.tg.stream_edit import StreamEditor

__all__ = [
    "AllowlistMiddleware",
    "ChainSplitter",
    "ConfirmLevel",
    "ConfirmationService",
    "DeliveryReceipt",
    "HaikuSummarizer",
    "HtmlBalancer",
    "LengthCapSummarizer",
    "StreamEditor",
    "build_bot",
    "build_dispatcher",
    "deliver_output",
]
