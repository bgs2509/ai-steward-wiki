# Inbox-WIKI

**Тип:** entity
**Статус:** draft
**Источники:** [overview §8.3.1](../raw/20260507-ai-steward-wiki-only-overview.md), §3a, §8.4

## Суть

Per-user WIKI-папка `USERS/<NAME>/Inbox-WIKI/` — единая точка входа для всего, что юзер кидает в TG без явной команды. Роль: триаж/router (отличается от domain-WIKI = librarian).

## Поведение

1. Бот складывает любой входящий контент в `Inbox-WIKI/raw/<timestamp>_<source>.<ext>`.
2. Триггерится **Router-промпт**: Claude в `Inbox-WIKI/` классифицирует тип контента, выбирает целевую WIKI, предлагает действия (inline-кнопки).
3. После подтверждения юзера — Claude перемещает файл в целевую WIKI, делает ingest, создаёт cron-задачи.

> Цитата (§8.3.1): «*Это похоже на авиабилет SVO→IST 15.06. Положу в `Travel-WIKI`. Напомнить за 24ч до вылета?*»

## Контракт `CLAUDE.md`

Отличается от domain-WIKI (librarian-схема). Содержит router-промпт: список доменов, правила классификации, формат ответа в TG. См. [Q-B-09](../questions/Q-B-09-inbox-claude-md-template.md) (TBD при следующей итерации ingest).

## Связанные

1. [Router-agent](router-agent.md)
2. [Classifier](classifier.md)
3. [Smart inbox routing](../concepts/smart-inbox-routing.md)
4. [Q-A-03: Inbox per-user vs global](../questions/Q-A-03-inbox-scope.md)

## Открытые вопросы

1. Per-user vs global Inbox (Q-A-03).
2. Шаблон router-`CLAUDE.md` (Tier B).
3. Идемпотентность ingest при дубликатах (Tier B, Q11).
