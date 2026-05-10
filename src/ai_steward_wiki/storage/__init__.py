# FILE: src/ai_steward_wiki/storage/__init__.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Storage package root — exposes the three engine factories and their bases.
#   SCOPE: Re-exports for convenience; all heavy lifting lives in sub-packages.
#   DEPENDS: SQLAlchemy, aiosqlite
#   LINKS: M-STORAGE-JOBS, M-STORAGE-AUDIT, M-STORAGE-SESSIONS
#   ROLE: BARREL
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   apply_sqlite_pragmas - register PRAGMA listener on a (sync) Connection
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - storage barrel re-exporting apply_sqlite_pragmas
# END_CHANGE_SUMMARY

from ai_steward_wiki.storage.pragmas import apply_sqlite_pragmas

__all__ = ["apply_sqlite_pragmas"]
