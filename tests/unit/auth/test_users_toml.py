from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.auth.users_toml import UsersTomlError, load_users_toml


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "users.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_minimal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 123
""",
    )
    cfg = load_users_toml(p)
    assert cfg.schema_version == 1
    assert len(cfg.users) == 1
    u = cfg.users[0]
    assert u.telegram_id == 123
    assert u.enabled is True
    assert u.role == "user"


def test_loads_full(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 1
enabled = false
role = "admin"
display_name = "G"
tz = "Europe/Moscow"
lang = "ru"
aisw_uid = 1001
""",
    )
    u = load_users_toml(p).users[0]
    assert u.enabled is False
    assert u.role == "admin"
    assert u.aisw_uid == 1001


def test_rejects_duplicate_telegram_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 5
[[users]]
telegram_id = 5
""",
    )
    with pytest.raises(UsersTomlError, match="duplicate"):
        load_users_toml(p)


def test_rejects_unknown_schema_version(tmp_path: Path) -> None:
    p = _write(tmp_path, "schema_version = 99\n")
    with pytest.raises(UsersTomlError, match="schema_version"):
        load_users_toml(p)


def test_rejects_unknown_role(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 1
role = "superadmin"
""",
    )
    with pytest.raises(UsersTomlError):
        load_users_toml(p)


def test_rejects_extra_field(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 1
unknown = "x"
""",
    )
    with pytest.raises(UsersTomlError):
        load_users_toml(p)


def test_rejects_non_positive_telegram_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
schema_version = 1
[[users]]
telegram_id = 0
""",
    )
    with pytest.raises(UsersTomlError):
        load_users_toml(p)


def test_rejects_malformed_toml(tmp_path: Path) -> None:
    p = _write(tmp_path, "this is not = = toml")
    with pytest.raises(UsersTomlError, match="parse"):
        load_users_toml(p)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UsersTomlError, match="cannot read"):
        load_users_toml(tmp_path / "nope.toml")
