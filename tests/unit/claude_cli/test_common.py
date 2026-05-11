from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.claude_cli.common import (
    build_env,
    neutral_cwd,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)


def test_resolve_binary_absolute_path_passthrough() -> None:
    assert resolve_binary("/opt/bin/claude") == "/opt/bin/claude"


def test_resolve_binary_unknown_returns_input() -> None:
    # Non-existent binary names fall through to the input unchanged.
    assert resolve_binary("definitely-no-such-binary-zxqw") == "definitely-no-such-binary-zxqw"


def test_resolve_binary_known_resolves_to_absolute() -> None:
    resolved = resolve_binary("python3")
    assert resolved.startswith("/")
    assert resolved.endswith("python3")


def test_build_env_minimal_keys() -> None:
    env = build_env(Path("/var/lib/aisw/claude-code"))
    assert env == {
        "CLAUDE_CONFIG_DIR": "/var/lib/aisw/claude-code",
        "PATH": "/usr/bin:/bin",
    }


def test_build_env_returns_fresh_dict() -> None:
    a = build_env(Path("/x"))
    b = build_env(Path("/x"))
    a["MUTATED"] = "1"
    assert "MUTATED" not in b


def test_neutral_cwd_equals_claude_config_dir() -> None:
    assert neutral_cwd(Path("/var/lib/aisw/claude-code")) == Path("/var/lib/aisw/claude-code")


def test_system_prompt_argv_inlines_file_content(tmp_path: Path) -> None:
    # `--system-prompt-file` does NOT replace the default Claude Code system prompt
    # under subscription auth (verified 2026-05-12, claude 2.1.139, bd aisw-adj).
    # The helper must inline the file content via `--system-prompt`.
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hello system\n", encoding="utf-8")
    argv = system_prompt_argv(prompt)
    assert argv == ["--system-prompt", "hello system\n"]
    assert "--system-prompt-file" not in argv
    assert "--append-system-prompt" not in argv
    assert "--append-system-prompt-file" not in argv


def test_truncate_stderr_short_passthrough() -> None:
    assert truncate_stderr(b"hello") == "hello"


def test_truncate_stderr_caps_long() -> None:
    payload = ("x" * 1000).encode()
    out = truncate_stderr(payload, limit=10)
    assert out == "xxxxxxxxxx...<truncated>"


def test_truncate_stderr_handles_invalid_utf8() -> None:
    out = truncate_stderr(b"\xff\xfe bad")
    assert "bad" in out
