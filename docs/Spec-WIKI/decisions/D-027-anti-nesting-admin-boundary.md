# D-027: Anti-nesting boundary — `WORKSPACE_ROOT` как единый anchor

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-C-24](../questions/Q-C-24-anti-nesting-admin.md), overview §7a, §9.24, [D-008](D-008-wiki-marker-format.md)

## Проблема

Псевдокод anti-nesting (overview §7a) останавливает ancestor-walk на `home_dir.parent`. Для admin'а нет персонального `home_dir` — нужна явная граница, иначе walk уходит в `/`.

## Варианты

1. **A — `WORKSPACE_ROOT` как единый anchor для user и admin.** ⭐
2. **B — `USERS/<NAME>/` целевого юзера** (admin семантически = user).
3. **C — Запрет admin-ингесту в чужие WIKI совсем.**

## Выбор

**Вариант A — `WORKSPACE_ROOT` единый anchor.**

### Конфигурация

1. **`WORKSPACE_ROOT`** = `/srv/ai-steward-wiki/USERS/` (env var в config).
2. Все ancestor-walk (anti-nesting check, CLAUDE.md autodiscover [D-007](D-007-add-dir-scope.md), [D-016](D-016-inbox-claude-md-template.md)) останавливаются на `WORKSPACE_ROOT`.

### Семантика

1. Admin семантически = user с расширенным scope; не отдельный режим walk'а.
2. Может войти в любой `USERS/<TARGET>/<wiki>` ниже `WORKSPACE_ROOT` — тот же anti-nesting инвариант.

### Инвариант

1. Между `<wiki>` и `WORKSPACE_ROOT` не должно встретиться другой `*-WIKI/`-папки (regex по [D-008](D-008-wiki-marker-format.md)).
2. На violation — abort с error `nested_wiki_detected`, log в admin shadow ([D-020](D-020-cron-result-routing.md)).

### Multi-tenant расширение

На переход в multi-tenant добавится `audit.db.admin_events(actor_id, target_user, action, ts)` (см. [D-028](D-028-admin-access.md)). Walk-часть остаётся та же.

## Последствия

1. Один inv-чекер на user и admin — нет двойной кодовой ветки.
2. `WORKSPACE_ROOT` становится load-bearing config — должен быть стабилен между релизами; миграция требует rewrite ancestor-walk кэша.
3. Admin не нуждается в fake `home_dir` — единая модель.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-027-anti-nesting-admin-boundary.md` (когда финализируется)
