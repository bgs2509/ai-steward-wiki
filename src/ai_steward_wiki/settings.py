# FILE: src/ai_steward_wiki/settings.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Runtime configuration loaded from environment via pydantic-settings.
#   SCOPE: Settings BaseSettings (frozen). Initial fields cover Chunk 1 only;
#          subsequent chunks extend with their own fields.
#   DEPENDS: pydantic-settings
#   LINKS: M-FOUNDATION-LOGGING (consumes log_level)
#   ROLE: CONFIG
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Settings - frozen pydantic-settings BaseSettings, env-prefixed AISW_
#   get_settings - cached accessor returning the singleton Settings instance
# END_MODULE_MAP

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AISW_",
        frozen=True,
        extra="ignore",
    )

    log_level: LogLevel = "INFO"
    workspace_root: Path = Path("/var/lib/ai-steward-wiki/workspace")
    claude_config_dir: Path = Path("/var/lib/ai-steward-wiki/claude-code")

    # Storage URLs (D-006). Default to async sqlite under workspace_root/data.
    jobs_db_url: str = "sqlite+aiosqlite:///data/jobs.db"
    audit_db_url: str = "sqlite+aiosqlite:///data/audit.db"
    sessions_db_url: str = "sqlite+aiosqlite:///data/sessions.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
