# D-030: Onboarding нового юзера — hybrid `users.toml` SSoT + `/start`-flow за config-флагом

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-D-27](../questions/Q-D-27-onboarding.md), overview §9.27, [D-013](D-013-claude-cli-auth.md), [D-016](D-016-inbox-claude-md-template.md), [D-020](D-020-cron-result-routing.md), [D-028](D-028-admin-access.md), [D-031](D-031-allowlist-hot-reload.md), [D-041](D-041-no-direct-wiki-commands.md)

## Проблема

Onboarding нового юзера: TG-flow (`/start` → admin /approve) даёт UX, manual edit `users.toml` — простоту. Single-tenant Henry-N не нуждается в self-signup, но multi-tenant потребует. Нужен путь от MVP к scale без архитектурного rewrite.

## Варианты

1. **A — Manual `users.toml`.**
2. **B — `/start` → pending → admin /approve.**
3. **C — Invite-link с одноразовым токеном.**
4. **D — Hybrid: `users.toml` SSoT + готовый `/start`-flow за флагом.** ⭐

## Выбор

**Вариант D.**

### SSoT allowlist

1. **`users.toml`** в repo сервиса, git-tracked.
2. View-таблица `sessions.db.users` синхронизируется при hot-reload (см. [D-031](D-031-allowlist-hot-reload.md)).
3. Структура `users.toml`:
   ```toml
   [[users]]
   chat_id = 123456789
   tg_username = "henry_n"
   role = "admin"  # admin | user
   lang = "ru"
   added_at = "2026-05-09T12:00:00"
   ```

### Single-tenant phase (current Henry-N)

1. Только manual edits `users.toml` на VPS.
2. `/start` от unknown chat_id → ответ «доступа нет, обратись к admin».
3. `ENABLE_SELF_SIGNUP = false` (default).

### Multi-tenant phase (`ENABLE_SELF_SIGNUP=true`)

1. **`/start` от unknown** → запись в `sessions.db.pending_users(chat_id PK, tg_username, ts, requested_lang, source_msg)`.
2. **Admin shadow channel** ([D-020](D-020-cron-result-routing.md)) получает msg с inline-кнопками:
   ```
   👤 Новый: @new_user (ru)
   [✅ Approve] [❌ Reject]
   ```
3. **Approve** →
   1. Atomic append в `users.toml` (write tmp + `os.replace`); commit message-style: `chore(users): add @<username>`.
   2. SIGHUP reload trigger ([D-031](D-031-allowlist-hot-reload.md)) → sync DB.
   3. Ack юзеру: «доступ открыт, начнём знакомство».
4. **Reject** → `pending_users.status='rejected'`, ack юзеру: «admin не одобрил».

### Onboarding Q&A (всегда, после approve)

1. Claude-driven через Inbox-WIKI router ([D-016](D-016-inbox-claude-md-template.md)).
2. Questions per parent-`CLAUDE.md` ai-steward (имя, язык, роль, интересы, любимый герой).
3. Output:
   1. `USERS/<NAME>/CLAUDE.md` — профиль из шаблона.
   2. `USERS/<NAME>/Inbox-WIKI/` — по [D-016](D-016-inbox-claude-md-template.md) shared template.
   3. `USERS/<NAME>/_output/` — пустая папка.

### Onboarding intro: «Что такое WIKI» (mandatory)

Перед Q&A Claude **обязан** объяснить юзеру концепцию WIKI и правила взаимодействия. Без этого юзер не понимает, как работать с системой, и склонен искать кнопки/команды.

**Скрипт intro-сообщения** (адаптируется под язык юзера и persona, но 5 ключевых пунктов сохраняются):

> Привет! Прежде чем начнём — два слова о том, как я устроен.
>
> 1. **WIKI — это твоя AI-библиотека по теме.** Например, `Health-WIKI` для здоровья, `Travel-WIKI` для поездок, `Budget-WIKI` для бюджета. Каждая WIKI знает свои правила: `Health-WIKI` не диагностирует, `Investment-WIKI` не даёт инвест-советов и т.д.
> 2. **Ты не управляешь WIKI напрямую.** Никаких команд `/create`, `/delete` нет. Просто пиши мне естественно: «давай заведём вики для йоги» / «удали Travel» / «переименуй Work в SideJob». Я сам всё сделаю и спрошу подтверждение.
> 3. **Я всегда уточню, если непонятно.** Перед созданием/удалением/переименованием я проверю, нет ли похожей вики, прочитаю твой профиль и историю, и задам 1–2 уточняющих вопроса если нужно. Это нормально, не считай это допросом.
> 4. **Все важные операции я подтверждаю.** Удалить вики? — спрошу. Изменить правила? — спрошу. Записать ежедневный показатель? — просто запишу и скажу куда.
> 5. **Удалённые WIKI хранятся 30 дней.** Можешь восстановить через «верни Z-вики». После 30 дней удаляются окончательно.
>
> Ну и read-only команды если хочешь:
> - `/wiki_list` — показать все твои вики;
> - `/wiki_show <Domain>` — детали конкретной.
>
> Готов? Тогда расскажи о себе.

**Обязательные элементы intro** (lint-checkable):

1. Объяснение концепции WIKI как AI-библиотеки с persona/правилами.
2. Запрет lifecycle-команд + правило «через NL-промпт».
3. Объяснение pre-flight (clarification + duplicate-check) per [D-041](D-041-no-direct-wiki-commands.md).
4. Правило explicit confirm на important operations.
5. 30d retention + restore semantics.
6. Список read-only команд (`/wiki_list`, `/wiki_show`).

**Источник текста:** intro-сообщение генерируется из шаблона `templates/onboarding-intro.<lang>.md` (`ru`/`en`). Шаблон в repo сервиса, git-tracked, эволюционирует через PR. После прочтения — переход к Q&A.

### State machine

1. `unknown` → (`/start` + flag) → `pending`
2. `pending` → (admin approve) → `intro`
3. `intro` → (юзер прочитал WIKI-intro и нажал «готов») → `onboarding`
4. `onboarding` → (Q&A complete) → `active`
5. `active` → (admin remove) → `revoked`

**`USERS/<NAME>/`** создаётся **только** после успешного onboarding completion; до того — pending state в DB.

## Последствия

1. MVP-простота (manual edit) + scale-ready (flip флаг — получаем self-signup).
2. SSoT: `users.toml` в git → revertable, audit-able через git log.
3. Запреты:
   1. **Не создавать `USERS/<NAME>/`** до onboarding completion.
   2. **Не hot-reload broken TOML** (см. [D-031](D-031-allowlist-hot-reload.md) validation).
   3. **Не auto-approve** в multi-tenant — всегда admin gate.
   4. **Не пропускать WIKI-intro** — без него юзер не знает правил взаимодействия и заваливает router некорректными запросами.
   5. **Не править intro-шаблон через TG** — только PR в repo сервиса.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-030-onboarding.md` (когда финализируется)
