# Q-D-28: Allowlist hot-reload

**Tier:** D
**Источник:** [overview §9 п.28](../raw/20260507-ai-steward-wiki-only-overview.md)

> **Update ([D-042](../decisions/D-042-unify-user-config.md), 2026-05-10):** allowlist-файл переименован `roles.toml` → `users.toml`. Hot-reload механика и watchdog — без изменений.

## Формулировка

`roles.toml` — hot-reload или restart-only? Кто правит — admin через TG или вручную на VPS.

## Варианты

1. **A. Restart-only.** systemctl restart. Просто.
2. **B. Hot-reload через `watchdog`.** На изменение `roles.toml` — переинициализация allowlist в памяти.
3. **C. Hot-reload + админ-команды TG** (`/user_add`, `/user_del`, `/role_set`).

## Решение

- [x] **Вариант D** — SIGHUP primary + watchdog secondary + validate-before-swap:
  - **SIGHUP** — основной триггер reload. Используется D-030 approve flow: после atomic append в `users.toml` → `os.kill(pid, SIGHUP)`.
  - **Watchdog** (lib `watchdog`) как fallback для manual edits на VPS; debounce 500ms на on-modify event.
  - **Validate-before-swap:** parse в `_PendingAllowlist`-структуру. On-success — атомарный `self.allowlist = pending` + sync DB. On-parse-error — keep old + admin shadow alert ([D-020](../decisions/D-020-cron-result-routing.md)) с diff и parse-error message.
  - **DB sync** в `sessions.db.users`:
    1. Added: `INSERT OR REPLACE` (chat_id, tg_username, lang, role, added_at).
    2. Removed: soft-delete `is_active=0` (preserves FK от `audit.db` events).
    3. Updated (lang/role change): `UPDATE`.
  - **Active session handling:** удалённый юзер с running job — job дорабатывает до завершения (per-WIKI lock не разрывается); новые `/`-команды от него отвергаются «доступ отозван, обратись к admin».
  - **Atomic-write contract:** все писатели `users.toml` (manual editor, TG approve flow) используют `write tmp + os.replace` — гарантирует watchdog не ловит partial-state.
- [x] оформлено как [D-031](../decisions/D-031-allowlist-hot-reload.md)

## Связанные

1. [Q-D-27: Onboarding](Q-D-27-onboarding.md)
