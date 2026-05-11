# FILE: src/ai_steward_wiki/settings.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: Runtime configuration loaded from environment via pydantic-settings.
#   SCOPE: Settings BaseSettings (frozen). Initial fields cover Chunk 1 only;
#          subsequent chunks extend with their own fields.
#   DEPENDS: pydantic, pydantic-settings
#   LINKS: M-FOUNDATION-LOGGING (consumes log_level), M-CLASSIFIER-STAGE0 (chunk 5)
#   ROLE: CONFIG
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LogLevel - Literal alias for accepted log levels
#   Stage0Backend - Literal alias for Stage-0 classifier backends (D-009)
#   Settings - frozen pydantic-settings BaseSettings, env-prefixed AISW_
#   get_settings - cached accessor returning the singleton Settings instance
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - chunk 8: wiki lifecycle fields (root, cap, retention, templates)
# END_CHANGE_SUMMARY

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
Stage0Backend = Literal["claude_cli", "anthropic_api"]


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

    # Chunk 5: Stage-0 classifier (D-009 / D-013 / D-015 / INV-6).
    stage0_backend: Stage0Backend = "claude_cli"
    stage0_api_credential_path: Path | None = None
    classifier_stage0_timeout_s: float = 30.0
    classifier_haiku_fallback_timeout_s: float = 15.0
    prompts_dir: Path = Path("/opt/ai-steward-wiki/prompts")

    # Chunk 7: Stage-1a/1b Sonnet runner (M-WIKI-RUNNER).
    wiki_runner_model: str = "claude-sonnet-4-5"
    wiki_runner_timeout_s: float = 300.0
    wiki_runner_term_grace_s: float = 10.0

    # Chunk 8: M-WIKI-LIFECYCLE.
    wiki_root: Path = Path("/var/lib/ai-steward-wiki/workspace/wikis")
    wiki_max_per_user: int = 20
    wiki_trash_retention_days: int = 30
    wiki_template_dir: Path = Path("/opt/ai-steward-wiki/templates")

    @model_validator(mode="after")
    def _check_stage0_credential_isolation(self) -> Settings:
        """INV-6: API backend MUST use a separate credential, never the OAuth dir."""
        if self.stage0_backend == "anthropic_api":
            if self.stage0_api_credential_path is None:
                raise ValueError(
                    "stage0_api_credential_path required when stage0_backend='anthropic_api' (INV-6)"
                )
            if self.stage0_api_credential_path == self.claude_config_dir:
                raise ValueError(
                    "stage0_api_credential_path MUST NOT equal claude_config_dir (INV-6)"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
