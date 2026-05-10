# D-013: Claude CLI auth — subscription mode, single-tenant (Henry-N)

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-C-20](../questions/Q-C-20-claude-cli-auth.md), overview §9.20, [D-004](D-004-inbox-wiki-scope.md), [D-009](D-009-classifier-engine.md)

## Проблема

Откуда CLI берёт credentials и как изолируются `~/.claude/` директории между «юзерами» сервиса.

## Контекст-уточнение (важно)

Сервис `ai-steward-wiki` — **single-tenant**: все TG-«юзеры» (`Henry-1`, `Henry-2`, `Henry-3`, …) — это один и тот же человек (Henry) с разных устройств / TG-аккаунтов. Multi-tenant биллинг, attribution, изоляция секретов между юзерами — **не требуются**.

В `USERS/` на проектируемой машине это будет:

```
USERS/
  Henry-1/   # бывший Gena
  Henry-2/   # бывший Tania
  Henry-3/   # бывший Dari
  ...
```

(Замечание: переименование исторических директорий в существующем сервисе `ai-steward` — вне scope; правило применяется только к новой машине `ai-steward-wiki`.)

## Варианты

1. **A. Service API key (`ANTHROPIC_API_KEY`)** — multi-tenant default, не нужен.
2. **B. API key + per-user `CLAUDE_CONFIG_DIR`** — изоляция биллинга, не нужна.
3. **C. Per-user API key** — multi-tenant провisioning, не нужен.
4. **D. Subscription auth (`claude login`)** — один аккаунт на машину, headless после login.

## Выбор

**Вариант D (subscription).** Юзер подтвердил 2026-05-08 («сервис запускается в режиме подписки и все юзеры — на самом деле один юзер»).

## Архитектура

### Auth

1. На машине деплоя выполняется `claude login` под системным юзером сервиса (`aisteward`) **один раз** при инициализации.
2. Credentials хранятся в `~/.claude/` сервисного юзера (стандартное местоположение Anthropic CLI).
3. `ANTHROPIC_API_KEY` в `.env` **не используется** для CLI-вызовов.
4. CLI-spawn — без переопределения env-credentials; читает `~/.claude/`.

### Stage-0 Haiku ([D-009](D-009-classifier-engine.md))

Stage-0 — прямой Anthropic SDK call, **не CLI**. Подписка покрывает оба режима через тот же аккаунт; SDK использует тот же auth state, читая `~/.claude/.credentials.json` (Claude Code reuse-pattern). Если SDK всё же требует явный API key — используется тот же подписочный токен, экспортированный из `~/.claude/`.

### `~/.claude/` — single shared

1. **Не** изолируем `~/.claude/` per TG-user — это один реальный человек.
2. Sessions/history общие в `~/.claude/`; CLI session continuity между TG-юзерами не нужна (разделение по cwd: `Henry-1/Inbox-WIKI`, `Henry-2/Inbox-WIKI` — разные WIKI ⇒ Claude CLI начинает свежую сессию по умолчанию).
3. Изоляция данных юзеров — **на уровне cwd** (`USERS/Henry-N/...`), не auth.

### Naming convention

1. В новой машине `USERS/<Name>` именуется `Henry-N`, где `N` — порядковый номер TG-аккаунта.
2. Mapping `tg_user_id → Henry-N` хранится в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`) ([D-010](D-010-nl-time-parsing.md): `roles.toml[<user>].timezone`):
   ```toml
   [users.henry-1]
   tg_user_id = 123456789
   timezone = "Europe/Moscow"
   display_name = "Henry (phone)"

   [users.henry-2]
   tg_user_id = 987654321
   timezone = "Europe/Moscow"
   display_name = "Henry (laptop)"
   ```
3. Allowlist (`Q-D-28`, TBD) — все Henry-N tg_user_id'ы в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`); никаких внешних юзеров.

## Обоснование

1. Подписка покрывает реальный use-case (один человек, много устройств) без overhead'а multi-tenant.
2. Subscription rate-limits и context-window выше API-tier при сравнимой стоимости для personal use.
3. Никаких per-user secrets, шифрования, vault, onboarding-форм.
4. Изоляция данных — через cwd (`USERS/Henry-N/`) + per-WIKI lock ([D-012](D-012-wiki-lock.md)) — этого достаточно при single-tenant.
5. `claude login` — стандартный headless flow: после первой авторизации credentials persistent в `~/.claude/`.

## Последствия

1. Onboarding нового TG-аккаунта Henry — добавление записи в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`) + `Q-D-27` flow создаёт `USERS/Henry-N/Inbox-WIKI/`. Никаких credentials шагов.
2. Allowlist enforce: TG-сообщение от tg_user_id, не входящего в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`), отклоняется на router-уровне (audit log записывает попытку).
3. Биллинг — единый счёт подписки, attribution не требуется.
4. Если когда-нибудь сервис превратится в multi-tenant (другие реальные юзеры) — D-013 переоткрывается отдельным ADR; default остаётся subscription.
5. Q-C-20 закрывается этим решением.
6. Документировать процедуру `claude login` в README/runbook (вне Spec-WIKI; артефакт переноса).

## Запреты

1. Не использовать `ANTHROPIC_API_KEY` env var в CLI-spawn — credentials берутся только из `~/.claude/`.
2. Не создавать per-TG-user `CLAUDE_CONFIG_DIR` / `HOME` override — единый `~/.claude/`.
3. Не пускать в сервис tg_user_id, отсутствующий в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`) (защита от случайного открытия multi-tenant).
4. Не коммитить `~/.claude/.credentials.json` или его копии в git / в WIKI.
5. Не делать `claude logout` на проде без процедуры plan'd downtime — все CLI-job'ы остановятся.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-claude-cli-auth.md` при финализации.
