# FILE: src/ai_steward_wiki/migration/__init__.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Migration ETL package — one-shot import of user data from legacy
#            ai-steward bot (flat file tree) into ai-steward-wiki (SQLite +
#            per-WIKI Karpathy layout). Read-only on source, writes target
#            DBs and FS only when --execute is passed (default is --dry-run).
#   SCOPE: Public surface = the CLI in __main__ + the LoadReport / TargetPlan
#          dataclasses re-exported here for testability.
#   DEPENDS: ai_steward_wiki.auth.users_toml, ai_steward_wiki.storage.jobs,
#            ai_steward_wiki.wiki.lifecycle, ai_steward_wiki.classifier.recurrence,
#            tomli_w, pydantic v2, sqlalchemy (async).
#   LINKS: M-MIGRATION, aisw-0a5
#   ROLE: SCRIPT
#   MAP_MODE: NONE
# END_MODULE_CONTRACT
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-0a5: initial scaffolding (P1.1)
# END_CHANGE_SUMMARY

from __future__ import annotations
