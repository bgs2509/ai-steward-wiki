# D-041: WIKI lifecycle — только через NL-промпт, без прямых команд

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** уточнение от юзера 2026-05-09 в обсуждении сценария создания WIKI; supersedes часть [D-029](D-029-wiki-init-auth.md); связано с [D-016](D-016-inbox-claude-md-template.md), [D-017](D-017-domain-claude-md-template.md), [D-023](D-023-tg-confirmations.md), концепт [smart-inbox-routing](../concepts/smart-inbox-routing.md)

## Проблема

D-029 ввёл явные команды `/wiki_init`, `/wiki_delete`, `/wiki_restore`, `/wiki_purge`. Это противоречит концепту smart-inbox-routing («безкомандный UX») и навязывает юзеру роль администратора файловой системы. При этом сам термин «WIKI» — load-bearing: юзер должен понимать, что это **не просто папка**, а специализированная AI-библиотека знаний по домену со своими правилами librarian'а.

## Уточнение от юзера (2026-05-09)

1. Юзер **знает** про существование WIKI и понимает их природу как AI-библиотек.
2. Юзер **не управляет** WIKI напрямую — никаких CRUD-команд.
3. Любой lifecycle-акт (create/delete/restore) — через естественный промпт Claude, который сам проверяет контекст и подтверждает.

## Варианты

1. **A — Оставить D-029 as is** (явные команды + auto-suggest). ❌ слишком технично.
2. **B — Убрать команды, оставить только auto-suggest на create.** ❌ нет пути для delete/restore.
3. **C — Полный NL-only lifecycle через Claude, с обязательной проверкой на дубликаты и подтверждением.** ⭐
4. **D — Скрыть концепт WIKI от юзера полностью.** ❌ юзер явно отверг — он хочет понимать систему.

## Выбор

**Вариант C.**

### Удаляемые команды

Из MVP убираются:

1. `/wiki_init <Domain>` — больше нет.
2. `/wiki_delete <Domain>` — больше нет.
3. `/wiki_restore <Domain>` — больше нет.
4. `/wiki_purge <Domain>` — больше нет.

### Что остаётся юзеру

1. **Read-only команды** допустимы (информационные, не меняют состояние):
   1. `/wiki_list` — показать свои WIKI с описанием.
   2. `/wiki_show <Domain>` — показать `## Назначение` и статистику.
2. **Любая lifecycle-операция** — только через NL-промпт в Inbox-WIKI router'у.

### Important operations (требуют intent-grounding + blind-spot scan)

Все операции, меняющие state WIKI, классифицируются как **important** и обязаны проходить полный pre-flight:

1. **create** — создать новую WIKI.
2. **delete** — soft-delete в `_trash/`.
3. **restore** — восстановить из `_trash/`.
4. **rename** — переименовать `<Old>-WIKI/` → `<New>-WIKI/` (с обновлением всех cross-refs).
5. **merge** — слить две WIKI (одна становится `obsolete`, контент переносится).
6. **split** — разделить одну WIKI на две по тематике.
7. **edit-rules** — изменить `## Назначение` / `## Правила librarian` / managed-sections в `<Domain>-WIKI/CLAUDE.md` ([D-039](D-039-claude-md-evolution.md)).
8. **edit-persona** — сменить persona/стиль общения для домена.
9. **purge** — bypass 30d retention в `_trash/` (admin-only через `/admin`).
10. **bulk-edit / bulk-delete pages** — массовая правка/удаление страниц внутри WIKI (>10 файлов).

### Page-level operations (тот же pre-flight, proportional weight)

Операции внутри одной WIKI — также проходят universal pre-flight, но scan focused на page-level осях:

1. **page-create** — создать новую страницу.
2. **page-edit** — изменить существующую.
3. **page-delete** — удалить страницу (soft в `_trash/` той же WIKI).
4. **page-rename** — переименовать (с обновлением бэклинков).
5. **page-move** — перенести страницу между WIKI юзера.
6. **append-data** — добавить запись в append-only лог (например, daily check-in, лаб-результат, расход).

**Operation-specific blind-spots:**

| Операция | Blind spots для проверки |
|---|---|
| page-create | дубликаты по title/Levenshtein + semantic match по содержимому существующих страниц этой WIKI; правильная категория (`entities/` vs `concepts/` vs `decisions/` vs domain-specific); конвенции имени (kebab-case, prefix); требуется ли запись в `index.md` + `log.md` (per [llm-wiki-method](../concepts/llm-wiki-method.md)) |
| page-edit | impact на бэклинки (структурное изменение?); конфликт со status `stable` — нужен ли `review`-перевод per CLAUDE.md §6.4; не противоречит ли правке librarian-правила домена; нужен ли `## Открытые вопросы`-блок |
| page-delete | бэклинки на эту страницу из других страниц (показать список), последняя правка, есть ли приоритет merge с другой страницей вместо delete |
| page-rename | все бэклинки + cross-WIKI ссылки, файл-имя collision, `index.md` запись |
| page-move | librarian-правила target-WIKI применимы ли (PII-tier, persona); broken backlinks → rewrite; учёт в обоих `log.md` |
| append-data | формат соответствует ли schema страницы (CSV header, JSON шаблон); тип/единицы измерения; частотные дубликаты (та же запись за тот же день?); PII-tier ([D-034](D-034-pii-redactor.md)) |

**Proportional weight** — pre-flight тот же 5-шаговый, но:

1. **Clarification skip rate высокий** — большинство page-edits не требует вопросов (профиль + librarian-правила + содержимое страницы дают полный контекст). Spamming clarification на каждое сообщение ломает UX.
2. **Confirm graduated** ([D-023](D-023-tg-confirmations.md)):
   1. **append-data, page-create в обычной зоне** → **implicit confirm** (бот пишет «записал в `Health-WIKI/metrics/2026-05-09.csv`», юзер может откатить через «отмени»).
   2. **page-edit, page-rename, page-move** → **soft confirm** (показать diff, кнопка `OK / Отмена`).
   3. **page-delete, любая операция в стейле `stable`-страницы** → **explicit confirm** (явное «Да»).
3. **Audit для page-level** — единая запись в `audit.db` `(page_op, wiki, page_path, intent_summary, blind_spots, ts)`, без раздувания chat-истории.

### Не-important (без pre-flight)

1. Чтение, поиск, query (read-only).
2. Read-only команды `/wiki_list`, `/wiki_show`.
3. Системные cron-jobs внутри WIKI (опросы time-tracker и т.п.) — у них свой pre-baked контракт через `## Inbox hint` и job payload, intent-grounding не нужен (intent уже зашит в job-definition).

### Universal pre-flight (для каждой important операции)

**До любого state-change** Claude обязан выполнить трёхступенчатый pre-flight:

1. **Intent-grounding:**
   1. Прочитать `USERS/<USER>/CLAUDE.md` (профиль, persona, известные домены) — auto-walk per [D-007](D-007-add-dir-scope.md).
   2. Прочитать `chat_log` последние 20/24h ([D-033](D-033-chat-history.md)).
   3. Прочитать `## Назначение` + `## Inbox hint` всех существующих WIKI юзера.
   4. Извлечь **real intention** vs буквы запроса.

2. **Blind-spot scan** — operation-specific:

   | Операция | Blind spots для проверки |
   |---|---|
   | create | persona, cron-периодичность, PII-tier, recipients, overlap с известными интересами |
   | delete | актуальность данных, активные cron-jobs внутри WIKI, последняя активность, есть ли дубликаты, можно ли merge вместо delete |
   | restore | конфликт имени с создавшимися после delete WIKI, retention-окно (>30d?), стейл cross-refs |
   | rename | все cross-refs из других WIKI, активные cron-jobs (`add-dir` пути), git history, audit-записи |
   | merge | overlap правил librarian'а, конфликт persona, dedup страниц, какая WIKI остаётся primary |
   | split | граница раздела, что делать с cross-refs между будущими половинами |
   | edit-rules / edit-persona | impact на уже накопленный контент, нужна ли retro-обработка старых страниц |
   | purge | абсолютная необратимость, наличие backup'ов, audit-trail |
   | bulk-* | scope precision, нет ли false-positive в выборке, есть ли rollback |

3. **Clarification turn** — 1–3 точечных вопроса если blind spot значим И не угадывается из профиля+диалога. Если всё ясно — пропустить, не задавать вопросы ради вопросов.

4. **Confirm** — explicit confirm ([D-023](D-023-tg-confirmations.md), graduated: implicit для read-side побочек, explicit для destructive). Confirm-сообщение **обязательно включает summary** intent-grounding выводов и blind-spot-ответов, чтобы юзер видел финальную конфигурацию.

5. **Execute + audit** — runtime применяет операцию; запись в `audit.db`: `(operation, wiki, intent_summary, blind_spots, clarifications, ts)`.

### Сценарий create (worked example)

1. Юзер: «давай заведём отдельную вики под мою подработку курьером».
2. Router в Inbox-WIKI ([D-016](D-016-inbox-claude-md-template.md)) распознаёт **intent=create_wiki** → important operation → запускает universal pre-flight (см. выше).
3. **Pre-flight: intent-grounding + blind-spot scan + clarification.** Пример:
   > Профиль: 35 лет, программист, есть `Freelance-WIKI`. Диалог 24h: жаловался на нагрузку.
   > Clarification: «Понял. Уточню: (1) это подработка временная или постоянное направление? (2) хочешь ли вечерний опрос «сколько часов сегодня курьерил»? (3) учёт денег здесь же или в `Budget-WIKI`?».
4. **Mandatory two-layer duplicate-check** (оба слоя обязательны, не OR):
   1. **Layer-1 (cheap, deterministic):** Levenshtein ≤ 2 по нормализованному имени домена ([D-008](D-008-wiki-marker-format.md)) против всех существующих `<Domain>-WIKI/` юзера. Ловит typo-clones (`Healtg` vs `Health`).
   2. **Layer-2 (AI semantic):** Claude (тот же CLI-процесс, без отдельного call) сравнивает intent юзера с `## Назначение` + `## Inbox hint` каждой существующей WIKI. Ловит синонимические дубликаты: «вики для подработки» vs существующая `Freelance-WIKI`, «вики про машину» vs `Car-WIKI`, «здоровье ребёнка» vs `Family-WIKI` (с overlap по детям) или `Health-WIKI` (с overlap по медицине).
   3. Layer-2 опирается на full контекст WIKI юзера, который уже в `--add-dir` workspace ([D-007](D-007-add-dir-scope.md)) — отдельный embedding-pipeline в MVP **не нужен**.
   4. Семантическая близость классифицируется как: `none` / `partial-overlap` / `near-duplicate`. Любое не-`none` → soft-block.
5. Если найдено похожее любым слоем — **soft-block с уточнением:**
   > «У тебя уже есть `Work-WIKI` (подработки и фриланс) и `Delivery-WIKI` (доставки на велосипеде). Возможно, новая вики не нужна?
   > 1. Использовать `Work-WIKI`
   > 2. Использовать `Delivery-WIKI`
   > 3. Всё-таки создать новую — предложу имя
   > 4. Отмена»
6. Если близких нет — Claude предлагает имя по доменному пресету ([D-017](D-017-domain-claude-md-template.md)) + 2 альтернативы:
   > «Создаю новую вики. Варианты имени: `Courier-WIKI` / `SideJob-WIKI` / `Delivery-WIKI`. Какое выбираешь? (или предложи своё)»
7. После выбора — explicit confirm ([D-023](D-023-tg-confirmations.md), graduated explicit для destructive-like операций). Confirm-сообщение **включает summary blind-spot-ответов** юзера, чтобы он видел финальную конфигурацию (persona / cron / PII-tier / recipients) до create.
8. Только после confirm — runtime создаёт `USERS/<USER>/<Domain>-WIKI/` по алгоритму D-017 (lookup пресета, создание структуры, audit-запись). **Blind-spot answers применяются** как initial customization CLAUDE.md новой WIKI (managed-sections per [D-039](D-039-claude-md-evolution.md)).

### Сценарий delete (worked example)

1. Юзер: «удали Travel вики, я больше не путешествую».
2. Router распознаёт **intent=delete_wiki** → important operation → universal pre-flight.
3. **Pre-flight blind-spot scan:** актуальность данных (последняя запись?), активные cron-jobs внутри (опросы, напоминания), последняя активность, дубликаты, возможен ли merge вместо delete.
4. Claude показывает **summary что будет удалено**: количество страниц, последняя активность, размер `_output/`, активные cron-jobs (если есть — отдельно warning).
5. Если найдены активные cron-jobs или recent activity (<7d) — clarification: «Активны 2 опроса и есть запись 3 дня назад. Точно удаляем?».
6. Explicit confirm с явной формулировкой ([D-023](D-023-tg-confirmations.md)):
   > «Перенести `Travel-WIKI` в `_trash/` (восстанавливается 30 дней) и отключить 2 cron-job'а? [Да / Нет]»
7. После confirm — soft delete (механизм из D-029): `USERS/<USER>/_trash/<Domain>-WIKI-<ts>/`. Cron-jobs помечаются `paused` (не удаляются — восстанавливаются вместе с WIKI).
8. **30d hard-delete** остаётся silent housekeeping-job ([D-020](D-020-cron-result-routing.md)).

### Сценарий restore (worked example)

1. Юзер: «верни Travel-WIKI» / «я зря удалил Travel».
2. Router распознаёт **intent=restore_wiki** → important operation → universal pre-flight.
3. **Pre-flight blind-spot scan:** конфликт имени (создалась ли новая `Travel-WIKI` после delete?), retention-окно (>30d уже hard-deleted?), стейл cross-refs от других WIKI.
4. Claude показывает дату удаления + что было внутри + статус cron-jobs.
5. Confirm → восстановление из `_trash/` обратно в `USERS/<USER>/<Domain>-WIKI/`, cron-jobs `paused` → `active`.

### Сценарий rename (worked example)

1. Юзер: «переименуй Travel-WIKI в Trips-WIKI».
2. Router распознаёт **intent=rename_wiki** → important operation → universal pre-flight.
3. **Pre-flight blind-spot scan:** все cross-refs из других WIKI на старое имя, активные cron-job paths (`--add-dir`), git history (commit message convention), audit-записи, конфликт с существующим именем.
4. Clarification если найдены cross-refs: «Найдено 3 ссылки на `Travel-WIKI` из `Family-WIKI`. Обновить все автоматически?».
5. Explicit confirm.
6. Atomic rename: directory move + cross-ref rewrite + cron-jobs path update + git commit `rename(travel→trips): per user request`.

### Сценарии edit-rules / edit-persona / merge / split / purge / bulk-*

Следуют тому же паттерну: **router → universal pre-flight (intent-grounding + operation-specific blind-spot scan + clarification) → confirm с summary → execute + audit**. Worked examples будут в plan.md при реализации; здесь зафиксирован контракт.

### Что юзер должен знать про WIKI

Текст в `/start`-онбординге ([D-030](D-030-onboarding.md)) и в `/help`:

> WIKI — это твоя персональная AI-библиотека знаний по теме (здоровье, финансы, путешествия и т.д.). Каждая WIKI знает свои правила: например, `Health-WIKI` не диагностирует, `Investment-WIKI` не даёт инвест-советов. Ты не управляешь ими напрямую — просто скажи мне «давай заведём вики для X» или «удали Y-WIKI», я сам всё сделаю и спрошу подтверждение.

### Anti-spam защита

Все механизмы D-029 сохраняются, **но активируются на стороне Claude**, а не команды:

1. **Soft limit 20 WIKI/user** (наследник [D-029](D-029-wiki-init-auth.md), числа явно зафиксированы здесь как нового SSoT после supersede):
   1. **Hard cap = 20** active WIKI per user. `_trash/` **не** учитывается в счётчике.
   2. **Warning at 16/20** — Claude в ответе на create-intent добавляет ремарку: «осталось 4 слота».
   3. **Hard reject at 20/20** — Claude отказывает в create и предлагает удалить ненужные через NL-промпт («удали Y-WIKI»). Bypass только через `/admin` ([D-028](D-028-admin-access.md)).
   4. **Counting rule:** scan `USERS/<USER>/*-WIKI/` regex ([D-008](D-008-wiki-marker-format.md)), исключая `_trash/`.
2. **Two-layer duplicate protection** — Layer-1 Levenshtein ≤2 по нормализованному имени домена ([D-008](D-008-wiki-marker-format.md)) + Layer-2 AI semantic-match, оба обязательны (см. шаг 4 сценария create). Layer-2 — load-bearing: typo-protection без семантики пропустит «новая вики для подработки» при существующей `Freelance-WIKI`.
3. **Intent-grounding с blind-spot scan** — обязателен **до** duplicate-check (см. шаг 3 сценария create). Claude читает профиль юзера (`USERS/<USER>/CLAUDE.md`) + последние 20/24h `chat_log` ([D-033](D-033-chat-history.md)), извлекает real intention, перечисляет blind spots (persona, cron, PII-tier, recipients, overlap с известными интересами) и задаёт 1–3 clarification-вопроса если что-то критичное не покрыто. Цель: setup новой WIKI с правильными правилами с первого раза, без последующих миграций ([D-039](D-039-claude-md-evolution.md)).
4. **Reversible delete `_trash/` retention 30d** (наследник D-029):
   1. Soft-delete переносит `<Domain>-WIKI/` → `USERS/<USER>/_trash/<Domain>-WIKI-<ISO8601-ts>/`.
   2. **30d** rolling — housekeeping APScheduler-job `trash_purge` daily ([D-020](D-020-cron-result-routing.md) `silent`), по истечении — hard-delete (см. PII-sweep в [D-034](D-034-pii-redactor.md) §"Trash sweep").
   3. `_trash/` исключается из autodiscover, soft-limit-counter, anti-nesting walk ([D-027](D-027-anti-nesting-admin-boundary.md)).
   4. Restore-окно — через NL `intent=restore_wiki` (см. сценарий выше); по истечении 30d — restore невозможен.

## Последствия

1. UX полностью conversational — юзер никогда не пишет команды для lifecycle.
2. Концепт WIKI как «AI-библиотеки» сохраняется в коммуникации (`/help`, онбординг, ответы Claude).
3. Read-only команды (`/wiki_list`, `/wiki_show`) разрешены как навигационные shortcuts.
4. Запреты:
   1. **Не возвращать `/wiki_init`/`/wiki_delete`/`/wiki_restore`/`/wiki_purge` в MVP.**
   2. **Не создавать WIKI без проверки на похожие** — load-bearing UX-инвариант.
   3. **Не делать silent create без confirm** — даже если intent однозначный.
   4. **Не скрывать сам термин WIKI** от юзера (юзер явно хочет его видеть).
5. Admin-side команды в `/admin` namespace ([D-028](D-028-admin-access.md)) — могут оставаться для break-glass (`/admin wiki_force_delete`), это не противоречит правилу — admin ≠ обычный юзер.

## Влияние на другие decisions

1. **D-029** → статус **superseded-by D-041**. Сохраняется как history. Механизмы (soft limit 20, fuzzy-match, `_trash/` retention 30d, audit) переезжают в D-041 без изменений; убираются только UI-команды.
2. **D-016** (Inbox CLAUDE.md) → router должен распознавать intent'ы `create_wiki`/`delete_wiki`/`restore_wiki` как first-class. Дополнить `## Inbox hint` шаблона.
3. **D-017** (domain CLAUDE.md) → без изменений (создание структуры идентично).
4. **D-030** (onboarding) → дополнить блоком «Что такое WIKI» (см. выше).
5. **smart-inbox-routing** концепт → усиливается: теперь буквально все lifecycle-операции проходят через router.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-041-no-direct-wiki-commands.md` (когда финализируется)
