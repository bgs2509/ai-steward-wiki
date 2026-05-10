# D-042: Unify user config — `users.toml` как single SSoT для всех user-attributes

**Статус:** accepted
**Дата:** 2026-05-10
**Контекст:** lint-find в [tech-spec-draft.md](../research/tech-spec-draft.md) (2026-05-10): [D-010](D-010-nl-time-parsing.md) ссылается на `roles.toml[<user>].timezone`, [D-031](D-031-allowlist-hot-reload.md) описывает `users.toml` как allowlist SSoT. Два файла под одну entity *user* без явной границы → cross-decision conflict.

## Проблема

User как aggregate root имеет несколько атрибутов: auth (telegram_id, enabled), authorization (role), language, timezone, persona. D-031 разместил часть в `users.toml`, D-010 — часть в `roles.toml`. Ни одно из решений явно не определяет границу между файлами. Возможные failure modes:

1. **Orphan state** — юзер удалён из `users.toml`, но запись в `roles.toml.timezone` осталась → stale lookup.
2. **Дублированный hot-reload** — D-031 уже описывает SIGHUP+watchdog+validate-before-swap для `users.toml`. Тот же механизм нужно дублировать для `roles.toml` → 2× кода и 2× failure surface.
3. **Race** на onboarding — atomic append в `users.toml` (D-030), но TZ в `roles.toml` пишется отдельной операцией, между ними окно несогласованности.

## Варианты

1. **A — Unify в `users.toml`.** ⭐ Все user-attributes в одной записи. `roles.toml` упраздняется.
2. **B — Split-by-concern.** `users.toml` = allowlist (telegram_id, role, lang, enabled), `roles.toml` = profile (timezone, persona). Два файла, два reload-pipeline.
3. **C — Role-templates split (future).** `users.toml` = per-user, `roles.toml` = справочник shared role-templates (`admin`, `power_user`, `read_only`), `users.toml.role` = FK на `roles.toml`. Атрибуты типа TZ остаются в `users.toml`.

## Выбор

**Вариант A.**

### Структура `users.toml`

```toml
[users.henry_n]
telegram_id = 123456789
role = "admin"              # admin | user
lang = "ru"                 # ru | en
timezone = "Europe/Moscow"  # IANA, обязательное (per D-010)
persona = "default"         # ключ persona-пресета
enabled = true
created_at = "2026-05-10T12:00:00Z"
```

### Обоснование

1. **SSoT по entity (DDD aggregate root):** один файл = одна entity = один lifecycle. Атрибуты, читаемые вместе в одном code-path (auth check + TZ lookup при NL-time parse), живут вместе.
2. **Co-location of related config (12-factor):** split оправдан только при разном lifecycle (разные владельцы, разная частота изменений, разные права доступа). Здесь lifecycle идентичен — admin правит оба руками.
3. **Hot-reload atomicity:** один файл — один SIGHUP-pipeline (D-031), одна validate-before-swap, одна `audit.db.users_reload` запись.
4. **YAGNI:** Variant C оправдан только при появлении shared role-templates с нескольких юзерами. По D-013 (single-tenant) этого нет.

## Последствия

1. `roles.toml` **удаляется** из дизайна (никогда не существовал в коде — оба упоминания только в spec).
2. **D-010** требует patch: `roles.toml[<user>].timezone` → `users.toml[<user>].timezone`. Семантика идентична, поведение не меняется.
3. **D-030** (onboarding) — schema добавления юзера расширяется полями `timezone`, `persona`. `/start` от unknown собирает их в pending-flow до admin approve.
4. **D-031** (hot-reload) — без изменений, механизм уже на `users.toml`.
5. **tech-spec-draft.md §6** — упоминание `roles.toml` исправляется на `users.toml`.
6. **Future:** при появлении shared role-templates — отдельный D-NNN, ре-вводящий `roles.toml` в роли C.

## Запреты

1. **Не создавать `roles.toml`** — единый SSoT.
2. **Не вводить per-attribute hot-reload** — атомарность на уровне всего user-record.
3. **Не делать TZ optional** — D-010 строго запрещает default-fallback.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-042-unify-user-config.md` (когда финализируется)
