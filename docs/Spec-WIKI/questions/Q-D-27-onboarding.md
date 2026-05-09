# Q-D-27: Onboarding нового юзера

**Tier:** D
**Источник:** [overview §9 п.27](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

TG-flow (`/start` → admin одобряет) vs ручное редактирование `roles.toml`.

## Варианты

1. **A. Manual `roles.toml`.** Admin редактирует на VPS. MVP-просто.
2. **B. `/start` → pending → admin /approve.** TG-кнопка для admin. Хорошо UX.
3. **C. Invite-link с одноразовым токеном.** Admin генерирует ссылку, юзер активирует.

## Решение

- [x] **Вариант D** — hybrid `users.toml` SSoT + готовый `/start`-flow за config-флагом:
  - **SSoT allowlist:** `users.toml` в repo сервиса, git-tracked. View-таблица `sessions.db.users` синхронизируется при hot-reload (см. [Q-D-28](Q-D-28-allowlist-hot-reload.md)).
  - **Single-tenant phase** (current Henry-N): только manual edits `users.toml`; `/start` от unknown → «доступа нет, обратись к admin».
  - **Multi-tenant phase** (флаг `ENABLE_SELF_SIGNUP=true`):
    1. `/start` → запись в `sessions.db.pending_users(chat_id PK, tg_username, ts, requested_lang)`.
    2. Admin shadow channel ([D-020](../decisions/D-020-cron-result-routing.md)) получает msg с inline `[✅ Approve] [❌ Reject]`.
    3. Approve → atomic append в `users.toml` (PR-like commit с message `chore(users): add @<username>`) + sync в DB → ack юзеру.
  - **Onboarding Q&A (всегда, после approve):**
    1. Claude-driven через Inbox-WIKI router; questions per parent-`CLAUDE.md` ai-steward (имя, язык, роль, интересы, любимый герой).
    2. Output: `USERS/<NAME>/CLAUDE.md` (профиль), `USERS/<NAME>/Inbox-WIKI/` (по [D-016](../decisions/D-016-inbox-claude-md-template.md) shared template), `USERS/<NAME>/_output/`.
  - **`USERS/<NAME>/`** создаётся **только** после успешного onboarding completion; до того — pending state в DB.
- [x] оформлено как [D-030](../decisions/D-030-onboarding.md)

## Связанные

1. [Q-D-28: Allowlist hot-reload](Q-D-28-allowlist-hot-reload.md)
