"""ADR-009 (final): no claude_config_dir setting; the bot uses the run user's
default ~/.claude.

Covers: the Settings field and AISW_CLAUDE_CONFIG_DIR are gone, the
default_claude_config_dir() helper resolves to ~/.claude, and the runtime
fail-fast (_require_claude_config_dir) trips when that dir is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ai_steward_wiki.__main__ as main_mod
from ai_steward_wiki.claude_cli.common import default_claude_config_dir
from ai_steward_wiki.settings import Settings


def test_default_claude_config_dir_is_home_dot_claude() -> None:
    assert default_claude_config_dir() == Path.home() / ".claude"


def test_no_claude_config_dir_setting() -> None:
    fields = Settings.model_fields
    assert "claude_config_dir" not in fields
    assert "claude_config_dir_local" not in fields
    assert "claude_config_dir_vps" not in fields


def test_aisw_claude_config_dir_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISW_CLAUDE_CONFIG_DIR", "/tmp/whatever")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(s, "claude_config_dir")


def test_require_config_dir_raises_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_mod, "default_claude_config_dir", lambda: tmp_path / "absent")
    with pytest.raises(RuntimeError, match="Claude config dir does not exist"):
        main_mod._require_claude_config_dir()


def test_require_config_dir_ok_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_mod, "default_claude_config_dir", lambda: tmp_path)
    main_mod._require_claude_config_dir()  # must not raise
