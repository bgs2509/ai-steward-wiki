from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_steward_wiki.settings import Settings


def test_defaults_load_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("AISW_LOG_LEVEL", "AISW_WORKSPACE_ROOT", "AISW_CLAUDE_CONFIG_DIR"):
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
