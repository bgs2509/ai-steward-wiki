# D-031: Allowlist hot-reload — SIGHUP primary + watchdog secondary + validate-before-swap

**Статус:** accepted
**Дата:** 2026-05-09 (amended 2026-05-10 — sync fields aligned with D-042)
**Контекст:** [Q-D-28](../questions/Q-D-28-allowlist-hot-reload.md), overview §9.28, [D-006](D-006-state-storage-layout.md), [D-020](D-020-cron-result-routing.md), [D-030](D-030-onboarding.md)

## Проблема

`users.toml` (SSoT по [D-030](D-030-onboarding.md)) меняется в runtime — manual edit на VPS или TG-approve flow. Restart-only — downtime + teardown jobs. Pure watchdog — TOCTOU и broken-TOML edge cases. Нужен надёжный live-reload.

## Варианты

1. **A — Restart-only.**
2. **B — Watchdog hot-reload.**
3. **C — Watchdog + TG-команды (`/user_add`, `/user_del`).**
4. **D — Hybrid: SIGHUP primary + watchdog secondary + validate-before-swap.** ⭐

## Выбор

**Вариант D.**

### Trigger channels

1. **SIGHUP** — основной explicit trigger.
   1. D-030 approve flow: после atomic append в `users.toml` → `os.kill(service_pid, SIGHUP)`.
   2. Manual ops: `systemctl kill -s HUP ai-steward-wiki`.
2. **Watchdog** (lib `watchdog`) — fallback safety net для прямых правок на VPS без SIGHUP.
   1. Debounce 500ms на `on_modified` event (защита от editor swap-files и double-write).
   2. Filter: только если `mtime` изменился И file size > 0.

### Validate-before-swap

1. Read `users.toml` → parse в `_PendingAllowlist` (Pydantic schema).
2. **On parse-success:**
   1. Atomic swap: `self.allowlist = pending` (CAS на in-memory ref).
   2. Sync DB (см. ниже).
   3. Log в audit.db.
3. **On parse-error:**
   1. Keep current allowlist as-is.
   2. Admin shadow alert ([D-020](D-020-cron-result-routing.md)) с diff и parse-error message.
   3. Не повторять reload до next SIGHUP (избегаем бесконечной watchdog-loop'ы).

### DB sync (`sessions.db.users`)

1. **Added** (в TOML, нет в DB): `INSERT OR REPLACE` canonical D-042 fields:
   1. `telegram_id`;
   2. `telegram_username`;
   3. `display_name`;
   4. `role`;
   5. `lang`;
   6. `timezone`;
   7. `persona`;
   8. `enabled`;
   9. `unix_user`, `unix_uid`, `unix_gid`;
   10. `added_at`;
   11. `is_active=enabled`.
2. **Removed** (есть в DB, нет в TOML): soft-delete `is_active=0` — preserves FK от `audit.db` events ([D-006](D-006-state-storage-layout.md)).
3. **Updated** (role/lang/timezone/persona/enabled/unix fields change): `UPDATE`.

### Active session handling

1. Юзер удалён из `users.toml` с running job:
   1. Job дорабатывает до завершения (per-WIKI lock не разрывается, согласно [D-012](D-012-wiki-lock.md)).
   2. Новые `/`-команды от него отвергаются с «доступ отозван, обратись к admin».
   3. После job-завершения — сессия закрыта, history доступна только admin (через [D-028](D-028-admin-access.md) elevation).
2. Юзер с role-change (admin → user или наоборот): применяется к **новым** командам; current job сохраняет original scope.

### Atomic-write contract

1. Все писатели `users.toml` используют `write tmp + os.replace` (atomic POSIX rename).
2. Гарантия: watchdog не ловит partial-state.
3. Применимо: D-030 approve flow, manual edit (vim создаёт `.swp`, рекомендация — `editor.vimrc: set nowritebackup`).

### Implementation sketch

```python
# pseudo-code
class AllowlistManager:
    def __init__(self):
        self.allowlist = self._load()
        signal.signal(signal.SIGHUP, self._on_sighup)
        self.observer = watchdog.observe("users.toml", self._on_fs_change)

    def _on_sighup(self, signum, frame):
        self._reload()

    def _on_fs_change(self, event):
        if self._debounce_ok():
            self._reload()

    def _reload(self):
        try:
            pending = _PendingAllowlist.parse_file("users.toml")
        except ValidationError as e:
            self._alert_admin(e); return
        self._sync_db(pending)
        self.allowlist = pending  # atomic swap
```

## Последствия

1. Zero downtime для approve flow и manual edits.
2. Broken TOML не валит сервис; admin alert + keep-old.
3. Active sessions graceful shutdown.
4. Запреты:
   1. **Не reload без validation** — TOML parse error keep-old.
   2. **Не hard-delete user row** в DB — soft-delete preserves audit FK.
   3. **Не разрывать running job** на mid-flight remove — graceful drain.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-031-allowlist-hot-reload.md` (когда финализируется)
