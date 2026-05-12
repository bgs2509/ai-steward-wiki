# FILE: src/ai_steward_wiki/settings.py
# VERSION: 0.0.11
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
#   TenancyMode - Literal alias for single|multi admin tenancy (chunk 12)
#   Env - Literal alias for runtime profile (local|vps)
#   Settings - frozen pydantic-settings BaseSettings, env-prefixed AISW_
#   get_settings - cached accessor returning the singleton Settings instance
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.11 - aisw-12t (Phase-E.a): - media_staging_root (media staging is
#                now per-user Inbox-WIKI; see inbox.materialize.inbox_wiki_path).
#   PREVIOUS:    v0.0.10 - aisw-kcz: default_user_tz (fallback IANA TZ for NL time parsing).
#   PREVIOUS:    v0.0.9 - aisw-zny (media chunk 1): media_staging_root,
#                voice_enabled, voice_whisper_model_size, voice_stt_timeout_s,
#                photo_enabled, photo_vision_timeout_s (D-022 wiring).
#   PREVIOUS:    v0.0.8 - chunk 18: users_toml_path (optional) for M-RUNTIME-WIRING.
# END_CHANGE_SUMMARY

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Env",
    "LogLevel",
    "Settings",
    "Stage0Backend",
    "TenancyMode",
    "get_settings",
]

Env = Literal["local", "vps"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
Stage0Backend = Literal["claude_cli", "anthropic_api"]
TenancyMode = Literal["single", "multi"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AISW_",
        frozen=True,
        extra="ignore",
    )

    env: Env = "local"
    log_level: LogLevel = "INFO"
    workspace_root: Path = Path("/var/lib/ai-steward-wiki/workspace")

    # Claude Code CLI config dir. Two slots, picked by `env`.
    # local: dedicated dir keeps bot's subscription auth isolated from dev's ~/.claude/.
    # vps:   default None → CLI uses ~/.claude/ of the aisw-bot service user.
    claude_config_dir_local: Path | None = Path("/var/lib/ai-steward-wiki/claude-code")
    claude_config_dir_vps: Path | None = None

    # Telegram credentials. Two slots — selected by `env`. Keeps local test bot
    # isolated from production bot so accidental writes never reach real users.
    tg_bot_token_local: SecretStr | None = None
    tg_bot_token_prod: SecretStr | None = None
    tg_admin_telegram_ids: list[int] = []

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

    # Chunk 12: M-ONBOARD-ADMIN.
    tenancy_mode: TenancyMode = "single"
    admin_chat_id: int | None = None
    admin_elevation_ttl_minutes: int = 30
    pending_user_ttl_days: int = 14

    # Chunk 13: M-OPS-PII (D-034 §10.4).
    pii_hash_secret: SecretStr = SecretStr("aisw-default-pii-salt-do-not-use-in-prod")
    pii_drop_enabled: bool = True
    pii_mask_enabled: bool = True
    retention_dry_run: bool = False

    # Chunk 14: M-OPS-BACKUP (tech-spec §10.2, D-037).
    snapshot_dir: Path = Path("/var/lib/ai-steward-wiki/state/snapshots")
    snapshot_retention_days: int = 7

    # Media handling (D-022). Voice → faster-whisper STT; photo → staged for vision.
    # Staging is per-sender under <wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging
    # (see inbox.materialize.inbox_wiki_path; aisw-12t / Phase-E.a) — no global root.
    voice_enabled: bool = True
    voice_whisper_model_size: Literal["small", "medium"] = "small"
    voice_stt_timeout_s: float = 60.0
    photo_enabled: bool = True
    photo_vision_timeout_s: float = 30.0

    # Chunk 18: M-RUNTIME-WIRING. Path to users.toml for allowlist.
    # None or missing file → empty allowlist (frictionless local first-run).
    users_toml_path: Path | None = None

    # aisw-kcz: fallback IANA timezone for NL time parsing when a user's
    # users.toml entry has no `tz`. Reminders/digests parse wall-clock times
    # against this zone (UTC invariant in storage; user TZ only at I/O).
    default_user_tz: str = "Europe/Moscow"

    @property
    def tg_bot_token(self) -> SecretStr | None:
        """Active TG bot token chosen by `env` (local|vps)."""
        return self.tg_bot_token_prod if self.env == "vps" else self.tg_bot_token_local

    @property
    def claude_config_dir(self) -> Path | None:
        """Active CLAUDE_CONFIG_DIR chosen by `env`. None → CLI uses ~/.claude/."""
        return self.claude_config_dir_vps if self.env == "vps" else self.claude_config_dir_local

    @model_validator(mode="after")
    def _check_tg_token_for_env(self) -> Settings:
        """Active env MUST provide its token slot; the other may stay empty."""
        if self.env == "vps" and self.tg_bot_token_prod is None:
            raise ValueError("tg_bot_token_prod required when env='vps'")
        if self.env == "local" and self.tg_bot_token_local is None:
            # Local default: allow None so unit tests can construct Settings()
            # without a real token; runtime TG bringup checks tg_bot_token itself.
            pass
        return self

    @model_validator(mode="after")
    def _check_stage0_credential_isolation(self) -> Settings:
        """INV-6: API backend MUST use a separate credential, never the OAuth dir."""
        if self.stage0_backend == "anthropic_api":
            if self.stage0_api_credential_path is None:
                raise ValueError(
                    "stage0_api_credential_path required when stage0_backend='anthropic_api' (INV-6)"
                )
            if (
                self.claude_config_dir is not None
                and self.stage0_api_credential_path == self.claude_config_dir
            ):
                raise ValueError(
                    "stage0_api_credential_path MUST NOT equal claude_config_dir (INV-6)"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
