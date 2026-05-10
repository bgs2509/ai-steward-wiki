from __future__ import annotations

from ai_steward_wiki.auth.allowlist import Allowlist, get_global, replace_global
from ai_steward_wiki.auth.users_toml import UserRecord, UsersConfig


def _cfg(*recs: UserRecord) -> UsersConfig:
    return UsersConfig(schema_version=1, users=recs)


def test_allowlist_query_api() -> None:
    al = Allowlist(_cfg(UserRecord(telegram_id=10), UserRecord(telegram_id=20, role="admin")))
    assert al.is_allowed(10)
    assert al.is_allowed(20)
    assert not al.is_allowed(99)
    assert al.get_user(10).role == "user"
    assert al.get_user(20).role == "admin"
    assert al.get_user(99) is None
    assert {u.telegram_id for u in al.all_users()} == {10, 20}


def test_disabled_users_excluded() -> None:
    al = Allowlist(_cfg(UserRecord(telegram_id=1), UserRecord(telegram_id=2, enabled=False)))
    assert al.is_allowed(1)
    assert not al.is_allowed(2)


def test_replace_global_atomic_swap() -> None:
    replace_global(_cfg(UserRecord(telegram_id=1)))
    assert get_global().is_allowed(1)
    replace_global(_cfg(UserRecord(telegram_id=2)))
    assert not get_global().is_allowed(1)
    assert get_global().is_allowed(2)
