# Inbox-WIKI

> Router-WIKI пользователя. Сюда падают сообщения из Telegram, пока Claude не определил
> подходящую `<Domain>-WIKI/`. Inbox-WIKI **не triage-target** — здесь нет секции
> `## Inbox hint` (D-016 §"Запреты").

## Format

1. Ответы в Telegram — короткие, mobile-friendly.
2. Списки вместо таблиц (Markdown-таблицы плохо рендерятся в TG).
3. По возможности — inline-кнопки для подтверждений и выбора.
4. Длинные ответы (>3500 символов) — суммаризировать; полный текст — в приложение
   per D-025.

## Fallback

1. Если для запроса нет подходящей `<Domain>-WIKI/` — ответить пользователю напрямую
   и предложить: «Сохранить в существующую вики X?» или «Создать новую вики Y?».
2. Никогда не выдумывать домен. Каталог доменов агрегируется в runtime из
   `## Inbox hint` секций каждой `<Domain>-WIKI/CLAUDE.md`.
3. На `unknown` intent — задать одну уточняющую question.

## Intent vocabulary

Закрытый список (D-016, D-041). Любой запрос вне vocabulary → `unknown`.

**Wiki-level** (lifecycle WIKI как сущности, важные операции — все требуют pre-flight):

1. `create_wiki`
2. `delete_wiki`
3. `restore_wiki`
4. `rename_wiki`
5. `merge_wiki`
6. `split_wiki`
7. `edit_wiki_rules`
8. `edit_wiki_persona`
9. `purge_wiki` (admin-only)

**Page-level** (внутри одной WIKI, pre-flight по proportional weight из D-041):

10. `page_create`
11. `page_edit`
12. `page_delete`
13. `page_rename`
14. `page_move`
15. `append_data`

**Read-only** (без pre-flight):

16. `query`
17. `list_wikis`
18. `show_wiki`

**Special:**

19. `unknown`
20. `chitchat`

## Pre-flight

Для каждой important / page-level операции (D-041) — обязательны 5 шагов:

1. **Intent-grounding** — сформулировать вслух, что хочет пользователь, и сослаться
   на конкретный intent из vocabulary.
2. **Blind-spot scan** — какие данные/файлы/решения могут быть затронуты, кроме
   очевидных.
3. **Clarification** — задать уточняющий вопрос, если intent или цель неоднозначны.
4. **Confirm** — graduated по D-023 (auto / implicit / explicit), уровень из таблицы
   D-041 §"Page-level proportional weight".
5. **Execute + audit** — выполнить и записать в audit-trail (job_id, intent, outcome).

## Confirm policy

1. Read-only intents (`query`, `list_wikis`, `show_wiki`) — без подтверждения.
2. `append_data`, `page_create` — implicit (показать, что записал; пользователь может
   откатить).
3. `page_edit`, `page_rename`, `page_move`, любые `*_wiki` — explicit с TTL 10 минут
   (D-023).
4. `delete_wiki`, `purge_wiki` — explicit + admin elevation (D-028).

## Anti-rules

1. Никогда не выдумывать домен — только из агрегированного каталога runtime.
2. Никогда не пропускать pre-flight для important / page-level операций.
3. Никогда не классифицировать intent вне closed-set vocabulary.
4. Никогда не дублировать список доменов в этом файле — SSoT живёт рядом с каждой
   `<Domain>-WIKI/`.
5. Никогда не писать секцию `## Inbox hint` в Inbox-WIKI — она не triage-target.
