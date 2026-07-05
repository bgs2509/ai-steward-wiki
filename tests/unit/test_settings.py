from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_steward_wiki.settings import Settings


def test_defaults_load_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("AISW_LOG_LEVEL", "AISW_WORKSPACE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.log_level == "INFO"
    assert s.workspace_root == Path("/var/lib/ai-steward-wiki/workspace")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AISW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AISW_WORKSPACE_ROOT", str(tmp_path / "ws"))
    s = Settings()
    assert s.log_level == "DEBUG"
    assert s.workspace_root == tmp_path / "ws"


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AISW_LOG_LEVEL", "TRACE")
    with pytest.raises(ValidationError):
        Settings()


def test_default_user_tz(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AISW_DEFAULT_USER_TZ", raising=False)
    s = Settings()
    assert s.default_user_tz == "Europe/Moscow"
    assert ZoneInfo(s.default_user_tz)

    monkeypatch.setenv("AISW_DEFAULT_USER_TZ", "Asia/Yekaterinburg")
    assert Settings().default_user_tz == "Asia/Yekaterinburg"


def test_settings_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    s = Settings()
    with pytest.raises(ValidationError):
        s.log_level = "DEBUG"  # type: ignore[misc]


def test_codex_subscription_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.llm_codex_enabled is True
    assert settings.llm_failover_cooldown_s == 900.0
    assert settings.codex_cli_binary == "codex"
    assert settings.codex_cli_version == "0.142.5"
    assert settings.codex_home == Path("/var/lib/ai-steward-wiki/codex")
    assert settings.codex_light_model == "gpt-5.4-mini"
    assert settings.codex_light_reasoning == "low"
    assert settings.codex_complex_model == "gpt-5.5"
    assert settings.codex_complex_reasoning == "medium"


def test_codex_settings_accept_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AISW_LLM_CODEX_ENABLED", "false")
    monkeypatch.setenv("AISW_LLM_FAILOVER_COOLDOWN_S", "120")
    monkeypatch.setenv("AISW_CODEX_CLI_BINARY", "/opt/codex/bin/codex")
    monkeypatch.setenv("AISW_CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("AISW_CODEX_LIGHT_MODEL", "light-model")
    monkeypatch.setenv("AISW_CODEX_LIGHT_REASONING", "medium")
    monkeypatch.setenv("AISW_CODEX_COMPLEX_MODEL", "complex-model")
    monkeypatch.setenv("AISW_CODEX_COMPLEX_REASONING", "high")

    settings = Settings()

    assert settings.llm_codex_enabled is False
    assert settings.llm_failover_cooldown_s == 120.0
    assert settings.codex_cli_binary == "/opt/codex/bin/codex"
    assert settings.codex_home == tmp_path / "codex-home"
    assert settings.codex_light_model == "light-model"
    assert settings.codex_light_reasoning == "medium"
    assert settings.codex_complex_model == "complex-model"
    assert settings.codex_complex_reasoning == "high"


def test_codex_cooldown_must_be_positive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        Settings(llm_failover_cooldown_s=0)


def test_codex_reasoning_is_closed_literal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        Settings(codex_complex_reasoning="extreme")  # type: ignore[arg-type]


def test_settings_expose_no_openai_api_key() -> None:
    assert "openai_api_key" not in Settings.model_fields
