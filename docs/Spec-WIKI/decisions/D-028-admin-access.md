# D-028: Admin access к чужим `USERS/` — single-tenant full-run + multi-tenant break-glass elevation

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-D-25](../questions/Q-D-25-admin-access.md), overview §7.1, §9.25, [D-013](D-013-claude-cli-auth.md), [D-020](D-020-cron-result-routing.md), [D-027](D-027-anti-nesting-admin-boundary.md)

## Проблема

Admin доступ к чужим `USERS/`-WIKI должен балансировать operational ergonomics (debug, support) и privacy. Single-tenant Henry-N сейчас, multi-tenant — будущее.

## Варианты

1. **A — Full-run всегда.** Admin == owner, без friction.
2. **B — Read-only.**
3. **C — Запрет совсем.**
4. **D — Hybrid: single-tenant full-run + multi-tenant break-glass elevation.** ⭐

## Выбор

**Вариант D.**

### Config

`TENANCY_MODE` в config: `single` (default, current Henry-N) | `multi`.

### `single` режим

1. Admin == owner; full read+write в любой `USERS/*/`-WIKI без extra friction.
2. Audit-events пишутся всегда (`actor_id == target_user` для owner-acts на own scope).

### `multi` режим

1. Default full access гасится; заменяется на TG-команду:
   1. **`/admin elevate <USER> [--reason <text>] [--duration_min <N>]`** → временная сессия access к `USERS/<USER>/*` на 30 мин (override через `payload.duration_min`).
2. **Audit table:** `audit.db.admin_events`:
   ```
   elevation_id PK, actor_id, target_user, scope,
   started_at, expires_at, reason TEXT, action_log JSON
   ```
   Все ops в окне elevation логируются с этим `elevation_id`.
3. **Notification target юзеру** опционально (per-tenant config flag `notify_on_admin_elevation`):
   «admin Henry зашёл в твой Health-WIKI на 30 мин: причина `<reason>`».
4. По истечении — silent expire; продолжение требует new elevation.

### Privacy boundary

1. Admin shadow channel ([D-020](D-020-cron-result-routing.md)) получает failures и operational events, **не контент юзера**.
2. Контент доступен только в окне elevation.
3. `audit.db.admin_events.action_log` хранит metadata (op, file, ts), не contents.

### Anti-nesting

Admin использует тот же [D-027](D-027-anti-nesting-admin-boundary.md) `WORKSPACE_ROOT` walk — semantically user с расширенным scope.

## Последствия

1. Single-tenant — zero overhead для текущего Henry-N.
2. Multi-tenant — privacy-by-default + audit trail.
3. Запреты:
   1. **Не делать admin-ops в multi без elevation** (config-enforced).
   2. **Не логировать content юзера** в admin shadow.
   3. **Не продлевать elevation silently** — каждое продление = new event.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-028-admin-access.md` (когда финализируется)
