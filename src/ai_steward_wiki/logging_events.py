# FILE: src/ai_steward_wiki/logging_events.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: SSoT catalog of stable snake_case dotted event-key constants for structured logging.
#   SCOPE: module-level Final[str] constants only. No functions, no classes.
#   DEPENDS: -
#   LINKS: M-FOUNDATION-LOGGING
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TG_UPDATE_RECEIVED - CorrelationMiddleware entry event
#   TRACED_START_SUFFIX / TRACED_DONE_SUFFIX / TRACED_ERROR_SUFFIX - @traced lifecycle suffixes
#   TG_PIPELINE_DISPATCH / CLASSIFIER_STAGE0 / WIKI_RUN / INBOX_STAGING - canonical @traced prefixes for chunk 1 entrypoints
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial SSoT catalog (chunk 1 entrypoints + correlation middleware)
# END_CHANGE_SUMMARY
from __future__ import annotations

from typing import Final

# CorrelationMiddleware entry event
TG_UPDATE_RECEIVED: Final[str] = "tg.update.received"

# @traced lifecycle suffixes (appended to prefix by the decorator)
TRACED_START_SUFFIX: Final[str] = ".start"
TRACED_DONE_SUFFIX: Final[str] = ".done"
TRACED_ERROR_SUFFIX: Final[str] = ".error"

# Canonical @traced prefixes for chunk-1 entrypoints
TG_PIPELINE_DISPATCH: Final[str] = "tg.pipeline.dispatch"
CLASSIFIER_STAGE0: Final[str] = "classifier.stage0"
WIKI_RUN: Final[str] = "wiki.run"
INBOX_STAGING: Final[str] = "inbox.staging"
