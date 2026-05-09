# Q-C-24: Anti-nesting граница для admin

**Tier:** C
**Источник:** [overview §9 п.24](../raw/20260507-ai-steward-wiki-only-overview.md), §7a

## Формулировка

Псевдокод §7a останавливается на `home_dir.parent`; для admin `home_dir` нет — какая граница?

## Варианты

1. **A. `WORKSPACE_ROOT`.** Поиск ancestor останавливается на корне workspace. Естественно.
2. **B. `USERS/<NAME>/`** (целевого юзера, чью WIKI редактирует admin). Делает admin семантически = user этого `<NAME>`.
3. **C. Запрет admin-ингесту в чужие WIKI совсем.** Admin только конфигурирует, не работает в чужих WIKI.

## Решение

- [x] **Вариант A** — `WORKSPACE_ROOT` как единый anchor для user и admin:
  - **`WORKSPACE_ROOT`** = `/srv/ai-steward-wiki/USERS/` (env var в config).
  - Ancestor-walk anti-nesting check останавливается на `WORKSPACE_ROOT` для всех ролей.
  - Admin семантически = user с расширенным scope (может войти в любой `<TARGET>` ниже `WORKSPACE_ROOT`); не отдельный режим walk'а.
  - Инвариант: между `<wiki>` и `WORKSPACE_ROOT` не должно встретиться другой `*-WIKI/`-папки (regex по [D-008](../decisions/D-008-wiki-marker-format.md)).
  - На multi-tenant: добавится `audit.db.admin_events(actor_id, target_user, action, ts)` (Вариант D расширение); пока single-tenant Henry-N — только walk-часть.
- [x] оформлено как [D-027](../decisions/D-027-anti-nesting-admin-boundary.md)

## Связанные

1. [Anti-nesting](../concepts/anti-nesting.md), [Q-D-25](Q-D-25-admin-access.md)
