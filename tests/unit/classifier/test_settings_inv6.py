from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.settings import Settings


def test_default_backend_is_cli() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.stage0_backend == "claude_cli"


def test_api_backend_requires_credential() -> None:
    with pytest.raises(ValueError, match="stage0_api_credential_path required"):
        Settings(_env_file=None, stage0_backend="anthropic_api")  # type: ignore[call-arg]


def test_api_credential_must_differ_from_oauth_dir() -> None:
    oauth_dir = Path.home() / ".claude"
    with pytest.raises(ValueError, match="MUST NOT equal claude_config_dir"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            stage0_backend="anthropic_api",
            stage0_api_credential_path=oauth_dir,
        )


def test_api_backend_happy() -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        stage0_backend="anthropic_api",
        stage0_api_credential_path=Path("/run/credentials/aisw/anthropic_api_key"),
    )
    assert s.stage0_backend == "anthropic_api"
