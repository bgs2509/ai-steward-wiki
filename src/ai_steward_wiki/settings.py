# FILE: src/ai_steward_wiki/settings.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Runtime configuration loaded from environment via pydantic-settings.
#   SCOPE: Settings BaseSettings (frozen), including provider enablement, pinned
#          Codex runtime, model mapping, reasoning effort, and failover cooldown.
#   DEPENDS: pydantic, pydantic-settings
#   LINKS: M-FOUNDATION-LOGGING, M-CLASSIFIER-STAGE0, M-LLM-FAILOVER, M-LLM-CODEX, aisw-8gw
#   ROLE: CONFIG
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LogLevel - Literal alias for accepted log levels
#   ReasoningEffort - Literal alias for supported Codex reasoning levels
#   Stage0Backend - Literal alias for Stage-0 classifier backends (D-009)
#   TenancyMode - Literal alias for single|multi admin tenancy (chunk 12)
#   Env - Literal alias for runtime profile (local|vps)
#   Settings - frozen pydantic-settings BaseSettings, env-prefixed AISW_
#   get_settings - cached accessor returning the singleton Settings instance
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: add subscription-backed Codex enablement,
#                pinned runtime, model mapping, reasoning, and cooldown settings.
#   PREVIOUS:    v0.0.15 - aisw-8gw: contract-only plan for Codex fallback settings.
#   PREVIOUS:    v0.0.14 - aisw-d3h (ADR-009 final): removed the claude_config_dir
#                field + AISW_CLAUDE_CONFIG_DIR entirely; bot uses the run user's
#                default ~/.claude. INV-6 now compares against ~/.claude.
#   PREVIOUS:    v0.0.13 - aisw-wt5 (ADR-009): claude_config_dir is now a single
#                explicit field (AISW_CLAUDE_CONFIG_DIR), decoupled from env;
#                dropped claude_config_dir_local/_vps slots + env-resolving property.
#   PREVIOUS:    v0.0.12 - aisw-nrt (chunk 2 logging): storage_slow_query_threshold_ms.
#   PREVIOUS:    v0.0.11 - aisw-12t (Phase-E.a): - media_staging_root (media staging is
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

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Env",
    "LogLevel",
    "ReasoningEffort",
    "Settings",
    "Stage0Backend",
    "TenancyMode",
    "get_settings",
]

Env = Literal["local", "vps"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
ReasoningEffort = Literal["low", "medium", "high"]
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

    # Claude Code CLI config dir (ADR-009, final): no setting. The bot uses the
    # run user's default ~/.claude (resolved at runtime via
    # claude_cli.common.default_claude_config_dir); there is no dedicated dir and
    # no AISW_CLAUDE_CONFIG_DIR override.

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

    # aisw-8gw: Codex CLI fallback uses ChatGPT subscription authentication.
    # No API-key setting exists by design (ADR-035).
    llm_codex_enabled: bool = True
    llm_failover_cooldown_s: float = Field(default=900.0, gt=0)
    codex_cli_binary: str = "codex"
    codex_cli_version: str = "0.142.5"
    codex_home: Path = Path("/var/lib/ai-steward-wiki/codex")
    codex_light_model: str = "gpt-5.4-mini"
    codex_light_reasoning: ReasoningEffort = "low"
    codex_complex_model: str = "gpt-5.5"
    codex_complex_reasoning: ReasoningEffort = "medium"

    # aisw-378: debounce window for aggregating a burst of split text messages
    # into one classify/route (Telegram splits a long paste across messages).
    tg_aggregate_delay_s: float = 3.0

    # Chunk 7: Stage-1a/1b Sonnet runner (M-WIKI-RUNNER).
    wiki_runner_model: str = "claude-sonnet-4-5"
    wiki_runner_timeout_s: float = 300.0
    wiki_runner_term_grace_s: float = 10.0
    # aisw-zpn: create+ingest of a large document is heavier than a normal query —
    # a separate, larger budget so a big doc fits in one run (general queries keep 300s).
    wiki_ingest_timeout_s: float = 600.0

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

    # Chunk 2 logging: SQLAlchemy slow-query threshold. Queries whose
    # measured wall-clock exceeds this value emit storage.slow_query at WARNING.
    storage_slow_query_threshold_ms: int = 200

    # aisw-xbc: event-loop hang diagnostics (diagnostics-only, hybrid cost).
    # Heartbeat ticks every interval (cheap INFO; its absence marks a freeze);
    # everything else is threshold-gated to keep happy-path log volume near-zero.
    obs_heartbeat_interval_s: float = 20.0
    # lag_ms over this → runtime.loop.lag WARNING (sync-blocking detector).
    obs_loop_lag_warn_ms: int = 500
    # lag_ms over this → auto task/thread stack dump (higher bar than warn).
    obs_loop_lag_dump_ms: int = 5000
    # external I/O calls slower than this → boundary anchor .done (else silent).
    obs_io_slow_threshold_ms: int = 1000
    # update handler slower than this → tg.update.handler_slow WARNING.
    obs_handler_slow_threshold_ms: int = 5000
    # minimum spacing between auto stack dumps (rate-limit, anti log-flood).
    obs_dump_min_interval_s: float = 60.0

    # Media handling (D-022). Voice → faster-whisper STT; photo → staged for vision.
    # Staging is per-sender under <wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging
    # (see inbox.materialize.inbox_wiki_path; aisw-12t / Phase-E.a) — no global root.
    voice_enabled: bool = True
    voice_whisper_model_size: Literal["small", "medium"] = "small"
    voice_stt_timeout_s: float = 60.0
    photo_enabled: bool = True
    photo_vision_timeout_s: float = 30.0

    # L2 ingest dedup TTL (D-018 amended 2026-05-13, ADR-028).
    # Per-kind window in seconds — text/voice retry-storm protection,
    # photo/file long artifact dedup.
    l2_ttl_text_seconds: int = 60
    l2_ttl_binary_seconds: int = 30 * 24 * 3600

    # Chunk 18: M-RUNTIME-WIRING. Path to users.toml for allowlist.
    # None or missing file → empty allowlist (frictionless local first-run).
    users_toml_path: Path | None = None

    # aisw-kcz: fallback IANA timezone for NL time parsing when a user's
    # users.toml entry has no `tz`. Reminders/digests parse wall-clock times
    # against this zone (UTC invariant in storage; user TZ only at I/O).
    default_user_tz: str = "Europe/Moscow"

    # aisw-02v (M-SCHEDULER-CONSUMER): Claude CLI binary name/path + system
    # prompt for cron-user runs. `claude_cli_binary` is resolved via
    # claude_cli.common.resolve_binary (shutil.which or absolute path).
    # `cron_user_prompt_path` defaults to <prompts_dir>/cron_user.md.
    claude_cli_binary: str = "claude"
    cron_user_prompt_filename: str = "cron_user.md"
    cron_user_timeout_s: float = 600.0

    @property
    def tg_bot_token(self) -> SecretStr | None:
        """Active TG bot token chosen by `env` (local|vps)."""
        return self.tg_bot_token_prod if self.env == "vps" else self.tg_bot_token_local

    @property
    def cron_user_prompt_path(self) -> Path:
        """Absolute path to the cron-user CLI system prompt (aisw-02v)."""
        return self.prompts_dir / self.cron_user_prompt_filename

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
            if self.stage0_api_credential_path == Path.home() / ".claude":
                raise ValueError(
                    "stage0_api_credential_path MUST NOT equal claude_config_dir (INV-6)"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
