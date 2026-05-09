# D-016: Inbox-WIKI `CLAUDE.md` — hybrid autodiscover + per-domain `## Inbox hint`

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-B-09](../questions/Q-B-09-inbox-claude-md-template.md), overview §9.9, §8.3.1, [D-004](D-004-inbox-wiki-scope.md), [D-009](D-009-classifier-engine.md), [D-015](D-015-system-prompt-inject.md), [D-041](D-041-no-direct-wiki-commands.md)

## Проблема

Где SSoT каталога доменов для router-Claude в `Inbox-WIKI/CLAUDE.md` и как router узнаёт «куда направить запрос» без drift'а при добавлении новых `<Domain>-WIKI/`.

## Варианты

1. **A — Жёсткий enum доменов** в Inbox `CLAUDE.md`: drift, двойная SSoT.
2. **B — Чистое автообнаружение** (читать первую строку каждого `<Domain>-WIKI/CLAUDE.md`): фрагильно, нет явной triage-семантики.
3. **C — Hybrid: autodiscover + обязательная секция `## Inbox hint`** в каждом domain-`CLAUDE.md`. ⭐
4. **D — Hybrid + materialized cache** (`Inbox-WIKI/_cache/domains.md`): дополнительная SSoT, инвалидация — известная проблема; для 5–10 доменов overkill.

## Выбор

**Вариант C (Hybrid + per-domain Inbox hint).**

### Контракт

1. Каждая `<Domain>-WIKI/CLAUDE.md` **обязана** содержать секцию `## Inbox hint` (1–3 строки):
   1. Когда направлять сюда (тематика, типы запросов).
   2. Ключевые слова / триггеры.
   3. 1–2 кратких примера TG-сообщений.
2. Router (Stage-1 Sonnet в Inbox-WIKI) на каждом запросе:
   1. Сканирует `USERS/<NAME>/*-WIKI/` (regex по [D-008](D-008-wiki-marker-format.md)).
   2. Читает только секцию `## Inbox hint` из каждого `CLAUDE.md`.
   3. Агрегирует в runtime-каталог доменов; передаёт в Stage-0 Haiku и Stage-1 Sonnet как часть контекста.
3. **Inbox `CLAUDE.md` НЕ содержит** список доменов. Содержит только мета-правила:
   1. Формат TG-ответа (краткость, mobile-friendly, inline-кнопки).
   2. Fallback-поведение (нет подходящего домена → ответить напрямую / спросить «куда сохранить?»).
   3. Правило «никогда не выдумывай домен — только из агрегированного каталога».
   4. **Intent-таксономия router'а** (закрытый список, см. ниже §«Intent vocabulary»).
   5. **Универсальный pre-flight** для каждой important/page-level operation per [D-041](D-041-no-direct-wiki-commands.md): intent-grounding → blind-spot scan → clarification → confirm → execute+audit.
4. **Lint-правило:** `wiki lint` падает, если в `<Domain>-WIKI/CLAUDE.md` нет секции `## Inbox hint`.

### Intent vocabulary (closed set)

Router'у запрещено классифицировать запрос intent'ом вне этого списка. Неподходящие → `unknown` + clarification.

**Wiki-level (lifecycle WIKI как сущности) — все important per [D-041](D-041-no-direct-wiki-commands.md):**

1. `create_wiki` — «давай заведём вики для X» / «создай раздел про Y».
2. `delete_wiki` — «удали Z-вики» / «больше не нужен раздел Z».
3. `restore_wiki` — «верни Z-вики» / «я зря удалил Z».
4. `rename_wiki` — «переименуй Z-вики в W».
5. `merge_wiki` — «объедини Z-вики и W-вики».
6. `split_wiki` — «раздели Z-вики на две: тематика A и тематика B».
7. `edit_wiki_rules` — «измени правила Z-вики: запрети давать оценки» / «теперь Z-вики читает и партнёр».
8. `edit_wiki_persona` — «общайся в Z-вики строже / как Шерлок / без шуток».
9. `purge_wiki` — admin-only через `/admin`; bypass 30d retention.

**Page-level (внутри одной WIKI) — pre-flight proportional weight per D-041:**

10. `page_create` — «запиши страницу про X» / автоматическая запись новой темы.
11. `page_edit` — «обнови страницу X» / «допиши в X».
12. `page_delete` — «удали страницу X».
13. `page_rename` — «переименуй страницу X в Y».
14. `page_move` — «перенеси X из Z-вики в W-вики».
15. `append_data` — daily check-in, лаб-результат, расход, замер давления, etc.

**Read-only (без pre-flight):**

16. `query` — «покажи / найди / что было / расскажи».
17. `list_wikis` — equivalent `/wiki_list` через NL.
18. `show_wiki` — equivalent `/wiki_show <Domain>` через NL.

**Special:**

19. `unknown` — router не уверен; → fallback (ответить напрямую или спросить).
20. `chitchat` — small-talk без операции.

### Inbox-WIKI CLAUDE.md обязательные секции

1. `## Format` — TG-ответ: краткость, lists not tables, inline-кнопки.
2. `## Fallback` — что делать при `unknown`/нет подходящего домена.
3. `## Intent vocabulary` — closed-set из 20 intent'ов выше (генерируется из шаблона при `wiki_init` Inbox-WIKI).
4. `## Pre-flight` — ссылка на D-041 + чек-лист 5 шагов (intent-grounding → blind-spot scan → clarification → confirm → execute+audit).
5. `## Confirm policy` — graduated per [D-023](D-023-tg-confirmations.md): какой уровень для какого intent'а (см. таблицу D-041 §«Page-level proportional weight»).
6. `## Anti-rules` — никогда не выдумывать домен, никогда не пропускать pre-flight для important/page-level, никогда не классифицировать вне vocab.

### Layout

```
USERS/<NAME>/Inbox-WIKI/CLAUDE.md         # мета-правила классификации, без каталога
USERS/<NAME>/Health-WIKI/CLAUDE.md        # содержит ## Inbox hint
USERS/<NAME>/Investment-WIKI/CLAUDE.md    # содержит ## Inbox hint
...
```

### Кэш

Агрегированный каталог не материализуется на диск. Stage-0 Haiku кэшируется через prompt-cache Anthropic API (D-009), для Sonnet Stage-1 — single-shot чтение через CLI на каждый run (дёшево для 5–10 файлов).

## Последствия

1. SSoT triage-семантики живёт **рядом с доменом** (single-source); zero drift при добавлении домена.
2. Добавление нового домена — через NL-промпт `intent=create_wiki` ([D-041](D-041-no-direct-wiki-commands.md)) создаёт `<New>-WIKI/CLAUDE.md` с готовой секцией `## Inbox hint` (по D-017 шаблон); router подхватывает автоматически.
3. Шаблоны доменов (D-017) обязаны включать секцию `## Inbox hint` с разумным дефолтом.
4. Запреты:
   1. **Не дублировать список доменов** в Inbox `CLAUDE.md`.
   2. **Не писать `## Inbox hint`** в Inbox-WIKI самой (она не triage-target).
5. Будущее (если доменов >20 или scan latency заметен) — добавить materialized cache (Вариант D) поверх контракта C, не ломая интерфейс.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-016-inbox-claude-md-template.md` (когда финализируется)
