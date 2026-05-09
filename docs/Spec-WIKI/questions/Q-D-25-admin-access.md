# Q-D-25: Доступ admin к чужим `USERS/`

**Tier:** D
**Источник:** [overview §9 п.25](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Read-only / full-run / запрет.

## Варианты

1. **A. Full-run.** Admin может `/run` в любой WIKI. Согласовано с §7.1 п.1.
2. **B. Read-only.** Admin только читает чужие WIKI (для отладки), не пишет.
3. **C. Запрет.** Admin не лезет в чужие WIKI совсем.

## Решение

- [x] **Вариант D** — single-tenant full-run + multi-tenant break-glass elevation:
  - **`TENANCY_MODE`** в config: `single` (default, текущий Henry-N) | `multi`.
  - **`single`:** admin == owner; full read+write в любой `USERS/*/`-WIKI без extra friction; audit-events пишутся всё равно (`actor_id == target_user`).
  - **`multi`:** full access гасится, заменяется на TG-команду:
    1. **`/admin elevate <USER>`** → временная сессия access к `USERS/<USER>/*` на 30 мин (override через `payload.duration_min`).
    2. **Audit:** `audit.db.admin_events(elevation_id PK, actor_id, target_user, scope, started_at, expires_at, reason TEXT, action_log JSON)` — все ops в окне elevation логируются с этим `elevation_id`.
    3. **Notification target юзеру** опционально (per-tenant config flag `notify_on_admin_elevation`): «admin Henry зашёл в твой Health-WIKI на 30 мин: причина `<reason>`».
    4. По истечении — silent expire; продолжение требует new elevation.
  - **Privacy boundary** ([D-020](../decisions/D-020-cron-result-routing.md)): admin shadow channel получает failures и operational events, не контент юзера; контент доступен только в окне elevation.
- [x] оформлено как [D-028](../decisions/D-028-admin-access.md)

## Связанные

1. [Q-C-24](Q-C-24-anti-nesting-admin.md)
