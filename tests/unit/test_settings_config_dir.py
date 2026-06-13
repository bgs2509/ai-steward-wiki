"""ADR-009: claude_config_dir is a single explicit field decoupled from AISW_ENV.

Covers: the env-resolving two-slot mechanism is gone, AISW_ENV no longer affects
the config dir, AISW_CLAUDE_CONFIG_DIR overrides the default, and the runtime
fail-fast (_require_claude_config_dir) trips when the dir is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.__main__ import _require_claude_config_dir
from ai_steward_wiki.settings import Settings


def test_default_config_dir() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.claude_config_dir == Path("/var/lib/ai-steward-wiki/claude-code")


def test_config_dir_is_explicit_override() -> None:
    custom = Path("/custom/claude-code")
    s = Settings(_env_file=None, claude_config_dir=custom)  # type: ignore[call-arg]
    assert s.claude_config_dir == custom


def test_config_dir_independent_of_env() -> None:
    custom = Path("/custom/claude-code")
    local = Settings(_env_file=None, env="local", claude_config_dir=custom)  # type: ignore[call-arg]
    vps = Settings(_env_file=None, env="vps", tg_bot_token_prod="x", claude_config_dir=custom)  # type: ignore[call-arg]
    assert local.claude_config_dir == vps.claude_config_dir == custom


def test_old_slots_are_gone() -> None:
    fields = Settings.model_fields
    assert "claude_config_dir_local" not in fields
    assert "claude_config_dir_vps" not in fields
    assert "claude_config_dir" in fields


def test_require_config_dir_raises_when_missing(tmp_path: Path) -> None:
    s = Settings(_env_file=None, claude_config_dir=tmp_path / "absent")  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="claude_config_dir does not exist"):
        _require_claude_config_dir(s)


def test_require_config_dir_ok_when_present(tmp_path: Path) -> None:
    s = Settings(_env_file=None, claude_config_dir=tmp_path)  # type: ignore[call-arg]
    _require_claude_config_dir(s)  # must not raise
