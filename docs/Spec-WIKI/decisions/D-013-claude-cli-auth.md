# D-013: Claude CLI auth — subscription mode, single-tenant (Henry-N)

**Статус:** accepted
**Дата:** 2026-05-08 (amended 2026-05-10 — shared `CLAUDE_CONFIG_DIR` + Stage-0 API boundary clarified)
**Контекст:** [Q-C-20](../questions/Q-C-20-claude-cli-auth.md), overview §9.20, [D-004](D-004-inbox-wiki-scope.md), [D-009](D-009-classifier-engine.md)

## Проблема

Откуда CLI берёт credentials и как этот auth state сочетается с per-user runtime UID isolation.

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
4. **D. Subscription auth (`claude auth login`)** — один аккаунт на машину, headless после login.

## Выбор

**Вариант D (subscription).** Юзер подтвердил 2026-05-08 («сервис запускается в режиме подписки и все юзеры — на самом деле один юзер»).

## Архитектура

### Auth

1. На машине деплоя выполняется `claude auth login` **один раз** под контролируемым service auth directory:
   ```bash
   install -d -m 0750 -o aisw-bot -g aisw-claude /var/lib/ai-steward-wiki/claude-code
   sudo -u aisw-bot CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code claude auth login
   ```
2. Credentials хранятся в shared `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`, а не в runtime `$HOME` per-user UID.
3. Доступ к config dir:
   1. directory owner: `aisw-bot:aisw-claude`, mode `0750`;
   2. credential files: mode `0640`;
   3. runtime users `aisw-<N>` входят в supplementary group `aisw-claude`;
   4. systemd scope монтирует config dir как read-only path per [D-038](D-038-per-user-systemd.md).
4. `ANTHROPIC_API_KEY` в `.env` **не используется** для CLI-вызовов.
5. CLI-spawn всегда выставляет `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`.
6. `~/.claude/` обычного `$HOME` не является runtime dependency, потому что [D-038](D-038-per-user-systemd.md) использует `ProtectHome=tmpfs`.

### Stage-0 Haiku ([D-009](D-009-classifier-engine.md))

Stage-0 Haiku ([D-009](D-009-classifier-engine.md)) имеет два backend'а:

1. **Default:** Claude CLI Haiku backend, использует тот же shared `CLAUDE_CONFIG_DIR`, что и Stage-1. Это subscription-only путь MVP.
2. **Optional:** Anthropic SDK/API backend, включается только при `STAGE0_BACKEND=anthropic_api` и отдельном API credential, выданном через systemd credentials / secret manager.

Стандартный Anthropic SDK/API credential **не считается** тем же самым, что Claude Code subscription OAuth. Не экспортировать subscription token из `.credentials.json` в API key.

### Claude Code config — single shared

1. **Не** создаём per-TG-user Claude Code config — это один реальный человек и один subscription login.
2. Shared config path один: `/var/lib/ai-steward-wiki/claude-code`.
3. CLI session continuity между TG-юзерами не нужна (разделение по cwd: `Henry-1/Inbox-WIKI`, `Henry-2/Inbox-WIKI` — разные WIKI ⇒ Claude CLI начинает свежую сессию по умолчанию).
4. Изоляция данных юзеров — **на уровне cwd + kernel sandbox** (`USERS/Henry-N/...`, [D-038](D-038-per-user-systemd.md)), не auth.
5. Config dir readable для CLI runtime по необходимости. Prompt-injection mitigation живёт в [D-038](D-038-per-user-systemd.md): `--allowedTools` только для файловых tools, `--disallowedTools` для `Bash`/`WebFetch`/`Read(auth-dir)`, `--permission-mode dontAsk`, config path вне `--add-dir`.

### Naming convention

1. В новой машине `USERS/<Name>` именуется `Henry-N`, где `N` — порядковый номер TG-аккаунта.
2. Mapping `telegram_id → Henry-N` хранится в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`):
   ```toml
   [users.henry_1]
   telegram_id = 123456789
   telegram_username = "henry_phone"
   timezone = "Europe/Moscow"
   display_name = "Henry (phone)"
   role = "admin"
   lang = "ru"
   persona = "default"
   enabled = true
   unix_user = "aisw-henry1"

   [users.henry_2]
   telegram_id = 987654321
   telegram_username = "henry_laptop"
   timezone = "Europe/Moscow"
   display_name = "Henry (laptop)"
   role = "user"
   lang = "ru"
   persona = "default"
   enabled = true
   unix_user = "aisw-henry2"
   ```
3. Allowlist (`Q-D-28`, TBD) — все Henry-N `telegram_id` в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`); никаких внешних юзеров.

## Обоснование

1. Подписка покрывает реальный use-case (один человек, много устройств) без overhead'а multi-tenant.
2. Subscription rate-limits и context-window выше API-tier при сравнимой стоимости для personal use.
3. Никаких per-user secrets, шифрования, vault, onboarding-форм.
4. Изоляция данных — через cwd (`USERS/Henry-N/`) + per-WIKI lock ([D-012](D-012-wiki-lock.md)) + kernel scope ([D-038](D-038-per-user-systemd.md)).
5. `claude auth login` — стандартный auth flow; после первой авторизации credentials persistent в shared `CLAUDE_CONFIG_DIR`.

## Последствия

1. Onboarding нового TG-аккаунта Henry — добавление записи в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`) + `Q-D-27` flow создаёт `USERS/Henry-N/Inbox-WIKI/`. Никаких per-user Claude credentials шагов.
2. Allowlist enforce: TG-сообщение от `telegram_id`, не входящего в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`), отклоняется на router-уровне (audit log записывает попытку).
3. Биллинг — единый счёт подписки, attribution не требуется.
4. Если когда-нибудь сервис превратится в multi-tenant (другие реальные юзеры) — D-013 переоткрывается отдельным ADR; default остаётся subscription.
5. Q-C-20 закрывается этим решением.
6. Документировать процедуру `claude auth login` в runbook (вне Spec-WIKI; артефакт переноса).

## Запреты

1. Не использовать `ANTHROPIC_API_KEY` env var в CLI-spawn — CLI credentials берутся только из shared `CLAUDE_CONFIG_DIR`.
2. Не создавать per-TG-user `CLAUDE_CONFIG_DIR` / `HOME` override — единый shared config dir.
3. Не пускать в сервис `telegram_id`, отсутствующий в `users.toml` (per [D-042](D-042-unify-user-config.md), ранее `roles.toml`) (защита от случайного открытия multi-tenant).
4. Не коммитить Claude Code credential files из shared `CLAUDE_CONFIG_DIR` или их копии в git / в WIKI.
5. Не делать `claude logout` на проде без процедуры plan'd downtime — все CLI-job'ы остановятся.
6. Не использовать optional Stage-0 API credential для Stage-1 CLI.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-claude-cli-auth.md` при финализации.
