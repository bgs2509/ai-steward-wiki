# FILE: src/ai_steward_wiki/digest/__init__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Digest-time read-side helpers (actionable cards, future renderers).
#   SCOPE: re-export emit_reminder_cards from cards.py.
#   DEPENDS: ai_steward_wiki.digest.cards
#   LINKS: M-DIGEST-CARDS, ADR-026, aisw-163
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   emit_reminder_cards - render ±2h pending reminder cards (re-export)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-163 P3: digest package introduced with cards.emit_reminder_cards
# END_CHANGE_SUMMARY

from ai_steward_wiki.digest.cards import emit_reminder_cards

__all__ = ["emit_reminder_cards"]
