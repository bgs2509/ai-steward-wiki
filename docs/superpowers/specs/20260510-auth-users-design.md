---
feature: auth-users-allowlist
bd_id: aisw-hnl
status: stable
date: 2026-05-10
stack:
  - python: "3.11+"
  - tomllib: stdlib (read-only TOML parser)
  - pydantic: "v2 (already in deps)"
  - watchdog: "6.0.0 (already in deps)"
  - sqlalchemy: "async (already in deps)"
---

# Design: M-AUTH-USERS

## users.toml schema

```toml
schema_version = 1

[[users]]
telegram_id = 123456789       # canonical identity (D-042)
enabled = true
role = "user"                 # "admin" | "user"
display_name = "Геннадий"
tz = "Europe/Moscow"
lang = "ru"
aisw_uid = 1001               # per-user systemd UID (D-038, optional pre-allocate)
```

`telegram_id` MUST be unique. `display_name`, `tz`, `lang` optional. `role` default `user`.

## Module decomposition

### users_toml.py
- `UserRecord(BaseModel, frozen=True)` — Pydantic schema
- `UsersConfig(BaseModel, frozen=True)` — top-level (schema_version, users: list)
- `load_users_toml(path: Path) -> UsersConfig` — read + parse + validate; raises `UsersTomlError`
- Validates: schema_version == 1, telegram_id unique, role enum

### allowlist.py
- `Allowlist` — class wrapping frozen dict[telegram_id → UserRecord]
- Module-level instance updated atomically via `replace_global(new_config)`
- API: `is_allowed(tg_id) -> bool`, `get_user(tg_id) -> UserRecord | None`, `all_users() -> list[UserRecord]`
- `sync_to_sessions_db(config, session_factory)` — async upsert to sessions.db.users; users absent from toml → role unchanged but a soft-delete flag NOT in current schema, so we skip removal in MVP and log warning (D-031 says soft-delete; deferred to later when Users gets `enabled` column)

  **Decision:** add `enabled: bool` column to `sessions.users` via Alembic migration in this chunk to honor soft-delete semantics from spec.

### sighup.py
- `AllowlistReloader` — coordinator: single async reload coroutine guarded by asyncio.Lock
- `install_sighup_handler(loop, reloader)` — registers `signal.SIGHUP`
- `WatchdogObserver` (watchdog) — observes `users.toml` parent dir; on event triggers reload via 500ms debounce timer (asyncio call_later, reset on new event)
- sha256 short-circuit: if file content sha == last_loaded_sha → noop (avoids redundant DB writes on watchdog double-fires)
- On load failure → keep prior cache, call `admin_alert(error_text)` (injected callable, default = log only)

## Reload flow

```
SIGHUP / watchdog event
    │
    ▼
AllowlistReloader.reload()  (Lock)
    │
    ├─ read file → sha256
    ├─ if sha == last → return
    ├─ load_users_toml() → UsersConfig
    │   └─ on error → admin_alert + log + return  (cache unchanged)
    ├─ replace_global(new)  (atomic)
    ├─ sync_to_sessions_db()
    └─ structlog event "allowlist.reloaded"
```

## Sessions.users migration

Add column `enabled BOOLEAN NOT NULL DEFAULT 1`. Alembic migration in `alembic/sessions/versions/`.

## Tests

- `tests/unit/auth/test_users_toml.py` — schema validation, duplicate detection, missing fields
- `tests/unit/auth/test_allowlist.py` — replace_global atomicity, query API
- `tests/unit/auth/test_reloader.py` — debounce, sha-noop, validate-before-swap, admin_alert on bad file
- `tests/unit/auth/test_sync.py` — upsert to sessions.db, soft-disable removed users
