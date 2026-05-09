# Q-C-20: Аутентификация Claude CLI

**Tier:** C
**Источник:** [overview §9 п.20](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

`ANTHROPIC_API_KEY` per-process / subscription auth / `claude login`. Изоляция `~/.claude/` между юзерами на одном хосте.

## Варианты

1. **A. Один сервисный API key** (всё через `.env`). Проще; биллинг общий.
2. **B. Per-user API key** (хранить зашифрованно). Чистая изоляция, но onboarding сложнее.
3. **C. Subscription auth (`claude login`).** `~/.claude/` на сервис-юзера. Не подходит для multi-tenant.

## Решение

- [x] Вариант D (subscription) — сервис single-tenant, все Henry-N это один человек. `claude login` один раз на машине; `~/.claude/` общая; mapping tg_user_id→Henry-N в `roles.toml`; никаких API key / per-user CLAUDE_CONFIG_DIR. Юзер подтвердил 2026-05-08. См. [D-013](../decisions/D-013-claude-cli-auth.md) (accepted).

## Связанные

1. [Q-D-27: Onboarding](Q-D-27-onboarding.md)
