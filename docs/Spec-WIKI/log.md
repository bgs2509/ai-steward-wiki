# Spec-WIKI — Log

> Append-only хронология действий LLM. Никогда не редактировать прошлые записи.
> Формат заголовка: `## [YYYY-MM-DD] <op> | <title>`, где `<op>` ∈ {init, ingest, query, lint, refactor, decision, wave}.
> Legacy: записи до schema-bump 2026-05-09 могут содержать `wave-N`; их не редактировать из-за append-only правила.

---

## [2026-05-08] init | Spec-WIKI создана

**Контекст:** юзер запросил организацию `docs/Spec-WIKI/` по методу Karpathy LLM Wiki для research/проектирования сервиса `ai-steward-wiki`.

**Действия:**
1. Создана структура: `Spec-WIKI/{CLAUDE.md, index.md, log.md, raw/}`.
2. `CLAUDE.md` зафиксировал режим: life-зона, без GRACE/feature-workflow внутри папки.
3. Граница с dev-артефактами: research живёт здесь, финальные `discovery.md` / `design.md` / `plan.md` / ADR — переносятся в `docs/superpowers/` и `docs/adr/`.
4. Подкаталоги (`entities/`, `concepts/`, `decisions/`, `questions/`, `research/`) появятся при первом ingest — пока не создаём пустыми.

**Источники:**
1. `../20260507-ai-steward-wiki-only-overview.md` — главный внешний документ.
2. Karpathy LLM Wiki gist.

**Следующий шаг:** ingest open questions из overview §9 → страницы в `questions/`.

---

## [2026-05-08] ingest | overview-2026-05-07.md (первая волна)

**Контекст:** юзер положил `raw/20260507-ai-steward-wiki-only-overview.md` (39.7K, 428 строк) и запустил ingest.

**Действия:**
1. Создана структура подпапок: `entities/`, `concepts/`, `decisions/`, `questions/`, `research/`.
2. Сводная страница: [research/overview-2026-05-07.md](research/overview-2026-05-07.md).
3. Entities (5): `inbox-wiki`, `classifier`, `router-agent`, `job-model`, `domain-wiki`.
4. Concepts (5): `two-stage-launch`, `anti-nesting`, `sibling-only-domains`, `smart-inbox-routing`, `llm-wiki-method`.
5. Questions Tier A (8): `Q-A-01` … `Q-A-08` — все архитектурные развилки из overview §9 Tier A.
6. `index.md` обновлён — все новые страницы внесены в каталог.

**Источник:** [raw/20260507-ai-steward-wiki-only-overview.md](raw/20260507-ai-steward-wiki-only-overview.md).

**Не сделано (next ingest waves):**
1. Tier B (Q9–Q19) — контракты данных и роутинга.
2. Tier C (Q20–Q24) — Claude CLI runtime.
3. Tier D (Q25–Q30) — UX и роли.
4. Tier E (Q31–Q39) — эксплуатация и безопасность.
5. Decisions (`D-NNN-*.md`) — оформляются после обсуждения вариантов с юзером.

**Следующий шаг:** обсудить с юзером Tier A развилки по порядку, оформить выбранные варианты как `decisions/D-NNN-*.md`.

---

## [2026-05-08] ingest | overview §9 Tier B–E (вторая волна)

**Контекст:** юзер запросил досоздать страницы вопросов Tier B–E.

**Действия:**
1. Tier B (контракты данных и роутинга), Q9–Q19 — 11 страниц.
2. Tier C (Claude CLI runtime), Q20–Q24 — 5 страниц.
3. Tier D (UX и роли), Q25–Q30 — 6 страниц.
4. Tier E (эксплуатация и безопасность), Q31–Q39 — 9 страниц.
5. `index.md` пополнен ссылками на Q-B/C/D/E.

**Источник:** [raw/20260507-ai-steward-wiki-only-overview.md](raw/20260507-ai-steward-wiki-only-overview.md) §9.

**Итого вопросов в `questions/`:** 39 (Tier A 8 + B 11 + C 5 + D 6 + E 9).

**Следующий шаг:** обсудить с юзером решения по Tier A → оформить как `decisions/D-NNN-*.md`. Tier B–E подтянутся в зависимостях от Tier A.

---

## [2026-05-08] ingest | time-tracker idea

**Контекст:** юзер описал фичу трекера времени (опросы каждые 2/5ч, окно 06:00–23:00, 3 кнопки-предсказания, обязательные дела с follow-up «сделал?», память паттернов по дням недели). Запросил организацию в Spec-WIKI.

**Решение:** Вариант 3 (фасад-entity + переиспользование `job-model`/`classifier`/`planner.json`). См. D-001.

**Действия:**
1. Создан `entities/time-tracker.md` (фасад, draft).
2. Созданы концепты: `predictive-replies.md`, `schedule-profiles.md`, `mandatory-checkins.md`.
3. Создан `decisions/D-001-time-tracker-vs-job-model.md` (proposed, выбран B = надстройка).
4. Создан `questions/Q-A-09-tracker-memory-model.md` (Tier A, 4 варианта хранения, не решено).
5. `index.md` обновлён: +1 entity, +3 concepts, +1 decision, +1 question.

**Открытые вопросы поднялись наверх:**
- Q-A-09 (storage паттернов) — блокер для реализации predictive-replies.
- Источник календаря праздников (концепт schedule-profiles).
- Эскалация при пропущенных таблетках (концепт mandatory-checkins).

**Следующий шаг:** юзер ревьюит draft-страницы, переводит в review/stable.

---

## [2026-05-08] refactor | Q-A-01 варианты обновлены под D-001

**Контекст:** после ingest time-tracker и принятия D-001 (трекер = надстройка над job-model) изменился набор требуемых `kind` (минимум 6: reminder, wiki, digest, tracker_survey, tracker_followup, boundary_message) и появились требования к общим полям (`mandatory`, `follow_up_delay_min`).

**Действия:**
1. `questions/Q-A-01-job-table.md` переписан:
   1. Добавлен раздел «Изменившийся контекст (после D-001)».
   2. Варианты A/B/C уточнены: добавлены pros/cons именно по росту числа `kind`.
   3. Рекомендация смещена: 80% вариант C (Flat + JSON payload), 15% A, 5% B. Раньше было 70/20/10.
   4. Указано: финальное решение пойдёт в `D-002-job-model-storage.md` (D-001 занят time-tracker'ом).
2. Связанные: добавлены ссылки на `D-001`, `time-tracker`, `Q-A-09`.

**Источник:** диалог 2026-05-08 + [D-001](decisions/D-001-time-tracker-vs-job-model.md).

**Следующий шаг:** дождаться выбора юзера (A/B/C); оформить как D-002.

---

## [2026-05-08] lint | fix broken links + D-002 stub

**Действия:**
1. `entities/time-tracker.md`: исправлены 3 битых ссылки (overview → `raw/`, `planner.json` → ссылка на `ai-steward/CLAUDE.md`, `Q-A-01` → `Q-A-09`).
2. Создана заглушка `decisions/D-002-job-model-storage.md` (вариант C, Flat + JSON payload) — закрывает ссылку из `Q-A-01:90`.
3. `index.md`: добавлена запись D-002.

**Источник:** lint-аудит 2026-05-08.

---

## [2026-05-08] decision | D-002 accepted (Flat + JSON payload)

**Контекст:** интерактивный разбор Q-A-01 через `/questions-answers`. Юзер выбрал вариант C.

**Действия:**
1. `decisions/D-002-job-model-storage.md`: статус `proposed` → `accepted`, расширено обоснование (Q-A-01 80%, требования D-001, общие колонки `mandatory`/`follow_up_delay_min`), уточнены последствия (без Alembic-миграций на kind, Pydantic discriminated union, индексы на горячих общих полях).
2. `questions/Q-A-01-job-table.md`: чекбокс «Решение» закрыт, ссылка на D-002 (accepted).
3. `index.md`: статус D-002 → accepted.

**Открыто:**
1. Обновить `entities/job-model.md` (всё ещё описывает 3 kinds) — следующая задача.
2. Q-A-02 (scheduler backend) — следующий по очереди разбор.

---

## [2026-05-08] decision | D-008 accepted (WIKI marker regex)

**Контекст:** разбор Q-C-23 через `/questions-answers`. Юзер выбрал вариант C.

**Действия:**
1. Создан `decisions/D-008-wiki-marker-format.md` (accepted): regex `^[A-Z][A-Za-z0-9]*-WIKI$` (fullmatch). Грамматика имени, примеры valid/invalid, применение в §5/§7.2/§7a/`/wiki_init`/sibling discovery.
2. `questions/Q-C-23-wiki-marker-format.md`: чекбокс закрыт, ссылка на D-008.
3. `index.md`, `queue.md`: обновлены. **Волна 1 (Foundation/SSoT) закрыта** — все 7 вопросов разрешены (Q-A-01, Q-A-02, Q-A-03, Q-A-06, Q-A-32, Q-C-22, Q-C-23).

**Каскадные TODO при переносе в design:** заменить все `"WIKI" in name.upper()` (overview §5/§7.1/§7.2/§7a) на `is_wiki_dir(name)` (D-008).

**Следующий шаг:** Волна 2 — Q-A-04 (classifier engine).

---

## [2026-05-08] decision | D-007 accepted (--add-dir only `<wiki>`)

**Контекст:** разбор Q-C-22 через `/questions-answers`. Юзер выбрал вариант A.

**Действия:**
1. Создан `decisions/D-007-add-dir-scope.md` (accepted): `--add-dir <wiki>` only; профиль `USERS/<NAME>/CLAUDE.md` доступен в context через Claude CLI CLAUDE.md auto-walk; digest-job — поимённый список WIKI, не `--add-dir USERS/<NAME>/`.
2. `questions/Q-C-22-add-dir-scope.md`: чекбокс закрыт, ссылка на D-007.
3. `index.md`, `queue.md`: обновлены.

**Связи:** опирается на overview §7.3 / §3a п.6, согласовано с D-004 и D-002 (per-kind применение).

---

## [2026-05-08] decision | D-006 accepted (3 БД: jobs/audit/sessions)

**Контекст:** разбор Q-A-32 через `/questions-answers`. Юзер выбрал вариант B.

**Действия:**
1. Создан `decisions/D-006-state-storage-layout.md` (accepted): три SQLite-файла (`data/jobs.db`, `data/audit.db`, `data/sessions.db`), WAL+NORMAL+foreign_keys, Alembic per БД, audit best-effort (не cross-DB транзакции).
2. `questions/Q-A-32-state-storage.md`: чекбокс закрыт, ссылка на D-006.
3. `index.md`, `queue.md`: обновлены.

**Каскад:** при необходимости упомянуть в `entities/job-model.md` структуру `data/` (опционально, основная информация уже в D-006).

---

## [2026-05-08] refactor | каскад под D-005 (time-tracker, job-model)

**Контекст:** TODO из D-005 — убрать упоминания `planner.json` из существующих entity-страниц.

**Действия:**
1. `entities/time-tracker.md`: п.3 «Дневной план» — `planner.json` → `jobs.db` с `kind=reminder_job` + `mandatory`/`follow_up_delay_min` колонки. Раздел «Используемые механизмы» — заменён `planner.json` на `jobs.db` (D-002) + APScheduler (D-003) + ссылка на D-005.
2. `entities/job-model.md`: блок «Развилки» дополнен закрытым D-003. Добавлена секция «Что НЕ существует в сервисе» с явной фиксацией отсутствия `planner.json` (D-005).

---

## [2026-05-08] decision | D-005 accepted (NO planner.json AT ALL)

**Контекст:** разбор Q-A-06 через `/questions-answers`. Юзер выбрал вариант C в строгой форме: «NO planner.json AT ALL. DELETE IT».

**Действия:**
1. Создан `decisions/D-005-no-planner-json.md` (accepted): SSoT расписаний — `jobs.db` (D-002), файла `planner.json` нет в сервисе ни в каком виде. Запреты явно прописаны.
2. `questions/Q-A-06-planner-ssot.md`: чекбокс закрыт, ссылка на D-005.
3. `index.md`, `queue.md`: обновлены.

**Открытые задачи (TODO):**
1. `entities/time-tracker.md` — текущий текст ссылается на `planner.json`, обновить под D-005 (заменить на ссылки на `jobs.db` / kinds D-002).
2. `entities/job-model.md` — добавить пункт «`planner.json` отсутствует, см. D-005».
3. При переносе в design-документы: переписать упоминания `planner.json` из overview §2.2 / §8.3.2 / §8.4 п.4 на `jobs.db`-семантику.

---

## [2026-05-08] refactor | CLAUDE.md §1.1 — граница с ai-steward

**Контекст:** юзер указал, что `ai-steward` (TG-бот) и `ai-steward-wiki` (проектируемый сервис) — полностью разные сервисы. Никаких пересечений/миграций/cross-service чтений до явного запроса.

**Действия:**
1. `CLAUDE.md` §1.1: добавлен раздел «Граница с `ai-steward` (TG-бот)» — фиксирует zero-coupling default, parent-CLAUDE.md как НЕ-источник, любые предложения миграции из `ai-steward` автоматически НЕ-вариант.

**Применение:** все будущие разборы вопросов исключают варианты «читать/мигрировать из ai-steward» по умолчанию.

---

## [2026-05-08] decision | D-004 accepted (Inbox-WIKI per-user + shared template)

**Контекст:** разбор Q-A-03 через `/questions-answers`. Юзер выбрал вариант C.

**Действия:**
1. Создан `decisions/D-004-inbox-wiki-scope.md` (accepted): per-user `USERS/<NAME>/Inbox-WIKI/` + shared template в `templates/inbox-wiki/`, materialize при `/wiki_init`.
2. `questions/Q-A-03-inbox-scope.md`: чекбокс закрыт, ссылка на D-004.
3. `index.md`: добавлена запись D-004.
4. `queue.md`: Q-A-03 перемещён в «Принятые».

**Самокоррекция:** в первой версии разбора Q-A-03 ошибочно сослался на parent `ai-steward/CLAUDE.md` (TG-бот) как на источник для проектирования `ai-steward-wiki` (другой сервис). Юзер указал на ошибку, варианты переоформлены строго по overview §3a/§7.2/§8.3.1.

**Открытые подвопросы:**
1. Конкретный template engine (str.format vs Jinja2) — отдельный микро-вопрос при реализации.
2. Политика per-user override для `Inbox-WIKI/CLAUDE.md` — оформить в Q-B-09.
3. Миграция шаблона при обновлении — Q-E-38.

---

## [2026-05-08] refactor | priority queue + Q-E-32 → Q-A-32

**Контекст:** relevance-аудит оставшихся 37 вопросов после D-001/D-002/D-003. Зафиксирована архитектурная очередь.

**Действия:**
1. Создан `queue.md` — SSoT приоритета (Волны 1–8 с галочками). `index.md` остаётся stable SSoT каталога.
2. Переименован `Q-E-32-state-storage.md` → `Q-A-32-state-storage.md`. Frontmatter Tier `E → A` с пометкой «повышен 2026-05-08 — foundational storage layout, blocks D-002/D-003».
3. `index.md`: ссылка на `queue.md` добавлена в Schema; запись Q-E-32 обновлена на Q-A-32.
4. Микро-правка порядка: Q-A-05 (NL time parsing) поднят перед Q-A-07 в Волне 2.

**Источник:** диалог 2026-05-08, best-practice ревью (ADR / Karpathy / Diátaxis).

**Следующий шаг:** разбор Волны 1 — Q-A-03 (Inbox scope).

---

## [2026-05-08] decision | D-003 accepted (APScheduler AsyncIOScheduler)

**Контекст:** интерактивный разбор Q-A-02 через `/questions-answers`. Юзер выбрал вариант A.

**Действия:**
1. Создан `decisions/D-003-scheduler-backend.md` (accepted): APScheduler `AsyncIOScheduler` + `SQLAlchemyJobStore` в процессе бота, общий SQLAlchemy-engine с `jobs.db` (D-002).
2. `questions/Q-A-02-scheduler-backend.md`: чекбокс закрыт, ссылка на D-003.
3. `index.md`: добавлена запись D-003.

**Открытые подвопросы:** misfire_grace_time per-kind, Q-A-07 concurrency, Q-B-19 failure mode.

---

## [2026-05-08] refactor | entities/job-model.md под D-002

**Действия:**
1. `entities/job-model.md`: переписан под D-002 — добавлена секция «Схема таблицы» (общие колонки + `payload JSON` + Pydantic discriminated union), список kinds расширен до 6 (reminder/wiki/digest + tracker_survey/tracker_followup/boundary_message), развилка Q-A-01 помечена закрытой через D-002. Статус `draft` → `review`.
2. `index.md`: однострочник для job-model обновлён («6+ kinds, Flat + JSON payload»).

---

## [2026-05-08] lint | fix CLAUDE.md §7 overview path

**Действия:**
1. `CLAUDE.md` §7: `../20260507-ai-steward-wiki-only-overview.md` → `raw/20260507-ai-steward-wiki-only-overview.md` (overview лежит в `raw/`, не на уровне выше).

**Источник:** lint-аудит 2026-05-08 (вторая итерация).

## [2026-05-08] decision | D-009 accepted (classifier engine: Haiku → CLI Sonnet)

- Q-A-04 закрыт. Гибрид: Stage-0 Haiku API (intent + reminder distill) → Stage-1 CLI Sonnet в Inbox-WIKI для wiki_action/unclear/low-confidence.
- Threshold confidence=0.85, fallback на Stage-1 при недоступности Haiku.
- Запреты: Stage-0 не выполняет действий, не читает CLAUDE.md; Sonnet не на Stage-0; Haiku не на Stage-1.
- Создан `decisions/D-009-classifier-engine.md`. Обновлены `index.md`, `queue.md`, `questions/Q-A-04`, `entities/classifier.md` (draft→review).
- Следующий шаг: Q-A-05 (NL time parsing) — тесно связан с D-009 (где именно парсится время в Stage-0).

## [2026-05-08] decision | D-010 accepted (NL time parsing: dateparser → Haiku fallback)

- Q-A-05 закрыт. Гибрид: Stage-0 Haiku возвращает сырую `time_phrase`, оркестратор парсит через `dateparser` (ru/en, user TZ из roles.toml, PREFER_DATES_FROM=future); на None — Haiku-fallback с узким промптом; на ambiguous — escalation в Stage-1 CLI.
- Repeat: rule-based regex → cron, Haiku fallback.
- Хранение datetime в `jobs.db` — UTC; user TZ применяется только на input/output.
- Запреты: парсить время внутри Stage-0; default TZ; `parsedatetime`.
- Создан `decisions/D-010-nl-time-parsing.md`. Обновлены `index.md`, `queue.md`, `questions/Q-A-05`.
- Следующий шаг: Q-A-07 (concurrent Claude CLI).

## [2026-05-08] decision | D-011 accepted (concurrent Claude CLI: Semaphore + per-WIKI Lock + PriorityQueue)

- Q-A-07 закрыт. Global `asyncio.Semaphore(MAX_CONCURRENT_CLI=4)` + `asyncio.Lock` per resolved WIKI path + `asyncio.PriorityQueue` (interactive=0, tracker=1, scheduled=2, digest=3, ingest=4).
- Acquire-порядок строго: semaphore → wiki_lock → spawn CLI.
- APScheduler не запускает CLI напрямую — `enqueue_cli_task(...)` в priority queue.
- Stage-0 Haiku не проходит через семафор (это не CLI).
- Метрики `cli_queue_wait_ms`, `cli_run_ms`, `cli_semaphore_saturation`, `wiki_lock_contention` → audit.db.
- Запреты: subprocess в обход pool; threading/multiprocessing lock; нарушение acquire-порядка; lock мельче WIKI без отдельного ADR.
- Создан `decisions/D-011-concurrent-claude.md`. Обновлены `index.md`, `queue.md`, `questions/Q-A-07`.
- Следующий шаг: Q-A-08 (file-level lock внутри WIKI).

## [2026-05-08] decision | D-012 accepted (WIKI on-disk lock: .wiki.lock advisory)

- Q-A-08 закрыт. `<wiki>/.wiki.lock` advisory `fcntl.flock`/`fasteners.InterProcessLock` поверх D-011 in-memory lock. Содержит pid+start_time+host+job_kind.
- Acquire-порядок: semaphore → in-memory lock → on-disk flock → spawn CLI.
- Stale recovery: `kill -0 <pid>`; ProcessLookupError ⇒ забираем lock, audit `wiki_lock_recovered`.
- Atomic write страниц: `tmp + os.replace`; правило enforced в WIKI CLAUDE.md template.
- External writers (Obsidian) — детектим по mtime, audit `external_edit_detected`.
- Запреты: mandatory lock; нарушение acquire-порядка; запись без tmp+rename; коммит `.wiki.lock` в git; file-level lock внутри WIKI.
- Создан `decisions/D-012-wiki-lock.md`. Обновлены `index.md`, `queue.md`, `questions/Q-A-08`.
- Следующий шаг: Q-C-20 (Claude CLI auth + ~/.claude/ изоляция) — закрывает Волну 2.

## [2026-05-08] decision | D-013 accepted (Claude CLI auth: subscription, single-tenant Henry-N)

- Q-C-20 закрыт. Сервис — single-tenant: все TG-юзеры это один человек Henry с разных устройств. `claude login` один раз на машине; `~/.claude/` общая; `ANTHROPIC_API_KEY` env var НЕ используется для CLI.
- USERS/ naming: `Henry-1`, `Henry-2`, `Henry-3`, … (вместо Gena/Tania/Dari) на новой машине ai-steward-wiki. Существующий ai-steward bot — вне scope.
- Mapping `tg_user_id → Henry-N` в `roles.toml`. Allowlist: только Henry-N tg_user_id'ы.
- Изоляция данных — через cwd (`USERS/Henry-N/`) + per-WIKI lock (D-012). Без per-user CLAUDE_CONFIG_DIR.
- Stage-0 Haiku (D-009) использует тот же подписочный auth.
- Запреты: `ANTHROPIC_API_KEY` в CLI; per-user CLAUDE_CONFIG_DIR; tg_user_id вне roles.toml; коммит credentials.
- Создан `decisions/D-013-claude-cli-auth.md`. Обновлены `index.md`, `queue.md`, `questions/Q-C-20`.
- **Волна 2 — Engine / runtime — ✅ закрыта** (Q-A-04, Q-A-05, Q-A-07, Q-A-08, Q-C-20 → D-009..D-013).
- Следующий шаг: Волна 3 — Q-A-09 (tracker memory model).

## [2026-05-08] decision | Q-A-09 → D-014: tracker memory model

- Принято: append-only `tracker_answers` table в jobs.db (вариант A).
- Схема: `(id, owner_telegram_id, slot_dow, slot_hour, answer, answered_at, source, job_id)` + индекс `(owner, dow, hour, answered_at)`.
- Топ-3: SQL `GROUP BY answer ORDER BY COUNT DESC LIMIT 3` on-the-fly при рендере inline-keyboard, окно 90 дней.
- Retention: 90 дней rolling, daily maintenance-job (`DELETE WHERE answered_at < now-90d`).
- v1 без recency-bias (простой COUNT); v2 — опциональный `exp(-Δt/30d) weight`.
- Slot granularity: `(dow, hour)` — 168 слотов/неделю.
- Source-tag: `predicted` | `other` | `tracker_followup` — для debug и будущих exclusions.
- FK `job_id → jobs(id)` даёт связь ответа с инициировавшим job.
- Согласовано с D-005 (jobs.db SSoT) и D-006 (3 БД, tracker memory резервирован).
- Создан `decisions/D-014-tracker-memory-model.md`. Обновлены `index.md`, `questions/Q-A-09`.
- Волна 3 — Tracker / patterns — стартовала.

## [2026-05-08] decision | Q-C-21 → D-015: system prompt inject (hybrid)

- Принято: `--append-system-prompt @prompts/wiki.md` (Stage-1) + `@prompts/classifier.md` (Stage-0) + per-WIKI `CLAUDE.md` через auto-walk (D-007).
- SSoT Wiki-doctrine: `ai-steward-wiki/prompts/wiki.md` (один файл в репо сервиса).
- Inbox-router наследует `wiki.md` + `inbox.md` (двойной флаг или pre-build конкатенация).
- `@file` reference, не inline string — для логирования и DRY.
- Версия prompt'а (semver в header + sha256) пишется в audit.db на каждый CLI-запуск.
- Запреты: `--system-prompt` (replace), дублирование doctrine в `<wiki>/CLAUDE.md`, правка prompts через TG.
- Layered SoC: Anthropic defaults → service doctrine → per-WIKI profile → per-call task.
- Создан `decisions/D-015-system-prompt-inject.md`. Обновлены `index.md`, `queue.md`, `questions/Q-C-21`.
- **Волна 3 — Tracker — ✅ закрыта** (Q-A-09 → D-014).
- Волна 4 — Контракты содержимого — стартовала (Q-C-21 → D-015 первое решение).

## [2026-05-08] decision | Q-B-09 → D-016: Inbox-WIKI CLAUDE.md template (hybrid)

- Принято: autodiscover sibling `*-WIKI/` + per-domain секция `## Inbox hint` в каждой `<Domain>-WIKI/CLAUDE.md`.
- SSoT шаблона — `templates/inbox-wiki/CLAUDE.md` в репо сервиса (рендерится `/wiki_init` с `{{user_name}}`, `{{telegram_id}}`, `{{lang}}`).
- Routing-таблица derived, не материализована — пересчитывается Stage-1 router'ом каждый раз через CLAUDE.md auto-walk (D-007).
- Per-user override через маркер `<!-- USER_OVERRIDE_BELOW -->`; шаблонные секции выше — управляются миграцией (Q-E-38).
- Связь с Q-B-10: `## Inbox hint` секция в domain-WIKI — рекомендуется (default optional).
- `wiki lint` проверяет наличие `Inbox hint` в каждой WIKI (overview §8.4).
- Запреты: материализация routing-таблицы в Inbox CLAUDE.md, regex-парсинг hints, override выше маркера.
- Создан `decisions/D-016-inbox-claude-md-template.md`. Обновлены `index.md`, `questions/Q-B-09`.

## [2026-05-08] wave-4 | Контракты содержимого — ✅ закрыта (6 решений)

Принято 6 решений Волны 4 в одной сессии Q&A:

### Q-B-09 → D-016: Inbox-WIKI CLAUDE.md (hybrid autodiscover + `## Inbox hint`)
- Контракт: каждая `<Domain>-WIKI/CLAUDE.md` обязана содержать секцию `## Inbox hint` (1–3 строки + примеры).
- Router сканирует `*-WIKI/` (D-008 regex), агрегирует hints в runtime — каталог не материализуется.
- Inbox `CLAUDE.md` содержит только мета-правила классификации (формат TG-ответа, fallback, anti-hallucination).
- `wiki lint` падает при отсутствии `## Inbox hint`.
- Замечание: предыдущая запись в log.md от 2026-05-08 о D-016 (с маркером `USER_OVERRIDE_BELOW` и шаблоном `templates/inbox-wiki/CLAUDE.md`) считается устаревшей — Q-B-09 был переоткрыт и пересмотрен.

### Q-B-10 → D-017: domain-WIKI per-domain пресеты + `_default`
- `templates/`: `health.md`, `investment.md`, `budget.md`, `family.md`, `study.md`, `career.md`, `home.md`, `hobby.md`, `recipes.md`, `_default.md`.
- `/wiki_init <Domain>` нормализует имя case-insensitive → `templates/<name>.md`, иначе `_default.md`.
- Каждый пресет обязан содержать `## Inbox hint` (D-016 контракт).
- Базовое содержимое — из parent-`CLAUDE.md` ai-steward (раздел «Шаблоны типов проектов»).
- Версия пресета (`# Template v1.x.0`) логируется в audit.db.

### Q-B-11 → D-018: ingest dedup (двухслойный, без LLM)
- L1 (TG retry): `audit.db.tg_updates(update_id PK, chat_id, ts)`, TTL 24h.
- L2 (content hash): `jobs.db.seen_files(hash PK, wiki, first_seen, tg_message_id, content_kind)`, TTL 30d.
- Hash: для текста — `NFKC + strip + lower + collapse ws` → SHA-256; для файлов — raw bytes.
- L2 не блокирует автоматически — inline-кнопки «создать ещё раз / открыть существующий / игнорировать».
- L3 (LLM-сравнение) — отложено; добавится точечно в job-creation.

### Q-B-19 → D-019: cron failure mode (production-grade)
- Error taxonomy: TransientError (timeout, network, rate-limit, lock contention, SQLite locked) → retry; PermanentError (validation, file missing, exit 127/126, auth) → DLQ.
- Retry policy: exp.backoff `1m→5m→30m` (3 попытки, jitter ±20%); override per-category (`medication` 5 попыток, `digest_job` 1).
- DLQ: `jobs.db.dead_letter` retention 30d; `/retry <dlq_id>` для manual redrive.
- Auto-disable recurring jobs после 3 подряд провалов → `paused`, owner-нотификация.
- Alert dedup: `medication` first-fail alert; остальные — после исчерпания retry.

### Q-B-18 → D-020: cron result routing (per-category + admin shadow)
- `notify_policy` в payload: `always` / `on_output` / `silent`.
- Дефолты: `medication`/`reminder`/`digest`/`tracker_question` = `always`; `wiki_job`/`inbox_classify` = `on_output`; `cleanup`/`gc`/`retention`/`audit_rotation` = `silent`.
- Failures (после исчерпания retry) и auto-disable → `owner_chat_id` + `admin_chat_id` (shadow).
- Single-tenant Henry-N: `admin_chat_id == owner_chat_id`; failure шлётся **один раз** (дедупликация перед send).
- `audit.db.job_outputs` логирует все 3 policy (даже `silent`).

### Q-B-17 → D-021: Claude CLI timeouts + UX cancel
- Per-category timeouts: `inbox_classify` 15s, `wiki_job` 120s, `digest_job` 600s, `tracker_question` 30s. Override через `payload.timeout_sec`.
- Kill: `proc.terminate()` → 10s grace → `proc.kill()`; `finally` освобождает lock (D-012), Semaphore (D-011), удаляет `active_runs`-row.
- UX cancel: inline-кнопка `❌ Отмена` под «⏳ работаю над…»; `/cancel` без аргументов = последний active run в чате.
- Storage: `sessions.db.active_runs(run_id PK, chat_id, job_id, started_at, pid, wiki, category, timeout_sec, inline_message_id)`.
- Orphan cleanup на старте сервиса: TERM/KILL процессов из прошлой жизни, очистка `active_runs`.
- Race conditions: транзакционный transition `running → cancelling`; idempotent kill.

### Артефакты
- Созданы: `decisions/D-016`..`D-021`.
- Обновлены: `index.md` (Decisions section), `queue.md` (Волна 4 закрыта), `questions/Q-B-09/10/11/17/18/19` (статус `[x] оформлено`).

### Следующий шаг
- Волна 5 — UX runtime: Q-B-14 (voice/photo), Q-B-12 (TG-подтверждения), Q-B-13 (digest-формат), Q-B-16 (output size), Q-B-15 (TG streaming).

## [2026-05-09] wave-5 | UX runtime — ✅ закрыта (5 решений)

Принято 5 решений Волны 5:

### Q-B-14 → D-022: voice/photo (faster-whisper + Claude vision, no pytesseract)
- Voice: faster-whisper local CPU, RU/EN multilingual, RTF≤0.5 на small model.
- Photo: Claude Sonnet vision через CLI (никакого pytesseract).
- Async ack pattern: «🎙️ слушаю…» / «🖼️ смотрю…» → media_ingest pipeline.
- Storage: `<wiki>/raw/media/<ISO8601>_<sha256[:8]>.<ext>` (immutable).
- Timeouts: STT 60s, vision 30s.
- Idempotency hook (D-018): SHA-256 от bytes + от транскрипта/vision-extract'а.
- Запреты: `pytesseract`, модификация `raw/media/`.

### Q-B-12 → D-023: TG confirmations (graduated per-category)
- 3 уровня: auto-confirm / implicit ack / explicit confirm.
- Explicit: recap HTML + 3 inline-кнопки (Подтвердить / Изменить-или-предсказание / Отмена) + free-form fallback.
- Predictive-replies встроены в кнопку 2 (топ-1 паттерн из tracker_answers).
- Storage: `sessions.db.pending_confirmations` (TTL 10 мин).
- Race conditions: транзакционный transition pending → confirmed/expired.

### Q-B-13 → D-024: digest format (HTML summary + critical cards)
- Parse mode: HTML (не MarkdownV2).
- Структура: TL;DR (3–5 строк) + секции с `<b>` headers (📅 Сегодня / 💊 Лекарства / 📈 Tracker / 📝 Wiki updates).
- Actionable cards только для items в окне ±2h (medication-due-now, event-soon, pending_confirmation).
- Длинный digest: split по секциям, continuity (1/N).
- `/expand <section>` для деталей.
- Запреты: MarkdownV2, кнопки на read-only items.

### Q-B-16 → D-025: output size (threshold-hybrid)
- ≤3500: inline один msg (596-char буфер на HTML escape).
- 3500–10000: split (1/3, 2/3, 3/3) по semantic boundary.
- >10000: пост-step Haiku-саммари (≤1500 chars) + send_document с .md.
- Always-persist: `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md` с frontmatter.
- Index: `audit.db.run_outputs(run_id PK, wiki, ts, size, path, sha256, summary_used, delivery)`.
- Retrieval: `/run <run_id>`, `/run last`, inline `📄 Полный текст`.
- Запреты: пропуск persist, Sonnet для саммари, edit `data/runs/`.

### Q-B-15 → D-026: TG streaming (edit + chain split)
- Source: CLI `--output-format stream-json`.
- Throttle: edit раз в 1.5s при ≥50 chars новых.
- Chain split при ≈4000 chars: finalize current + open next с `(N/M)`.
- Backpressure: exp.backoff на 429 (1.5s → 30s); final flush гарантирован.
- HTML balance: auto-close на конце сегмента, auto-reopen на старте.
- Cancel (D-021): SIGTERM + final-flush + footer «❌ отменено»; partial output сохраняется.
- Final transition: ≤10000 — chain как есть; >10000 — D-025 mode.
- Per-category override (`streaming_mode=blocking` для inbox_classify/tracker_question) — отложено.

### Артефакты
- Созданы: `decisions/D-022`..`D-026`.
- Обновлены: `index.md` (Decisions section), `queue.md` (Волна 5 закрыта), `questions/Q-B-12/13/14/15/16` (статус `[x] оформлено`).

### Следующий шаг
- Волна 6 — Admin / access: Q-C-24 (anti-nesting boundary для admin), Q-D-25 (admin доступ к чужим USERS/), Q-D-26 (`/wiki_init` авторизация), Q-D-27 (onboarding flow), Q-D-28 (allowlist hot-reload).

---

## [2026-05-09] wave-6 | Admin / access — ✅ закрыта (5 решений)

**Контекст:** юзер запустил Волну 6 (`/questions-answers Волну 6`) — admin/access слой.

**Действия:**
1. Q-C-24 (anti-nesting admin boundary) → 65% A → D-027 (`WORKSPACE_ROOT` единый anchor для user и admin).
2. Q-D-25 (admin access чужим USERS/) → 65% D → D-028 (single-tenant full-run + multi-tenant break-glass `/admin elevate <USER>` + `audit.db.admin_events`).
3. Q-D-26 (`/wiki_init` авторизация) → 65% D → D-029 (user creates + auto-suggest + soft limit 20 + Levenshtein typo-protection + reversible delete с retention 30d в `_trash/`).
4. Q-D-27 (onboarding) → 60% D → D-030 (hybrid `users.toml` SSoT git-tracked + `/start`-flow за `ENABLE_SELF_SIGNUP=false`; multi-tenant: pending_users → admin shadow approve → atomic append + SIGHUP).
5. Q-D-28 (allowlist hot-reload) → 65% D → D-031 (SIGHUP primary + watchdog secondary + validate-before-swap; soft-delete для FK; graceful drain running jobs).

**Артефакты:**
1. Созданы `decisions/D-027..D-031.md`.
2. `index.md` — добавлены D-027..D-031 в Decisions section.
3. `queue.md` — Q-C-24, Q-D-25, Q-D-26, Q-D-27, Q-D-28 перенесены в "Принятые"; Волна 6 → ✅ закрыта.
4. Q-files (Q-C-24..Q-D-28) — `[x] оформлено как [D-NNN]`.

**Cross-refs:**
1. D-027 → база для D-028 (admin walk через тот же `WORKSPACE_ROOT`) и D-029 (`_trash/` skip).
2. D-030 ↔ D-031 — взаимная ссылка (approve flow триггерит SIGHUP).
3. D-028 ссылается на D-020 (admin shadow channel — operational events, не контент).

**Дальше:** Волна 7 (Polish UX) — Q-D-29 (multi-language), Q-D-30 (chat history). Опционально Волна 8 (Operations).

---

## [2026-05-09] wave-7 | Polish UX — ✅ закрыта (2 решения)

**Контекст:** юзер запустил Волну 7 (`/questions-answers Волну 7`) — polish UX слой.

**Действия:**
1. Q-D-29 (multi-language) → 65% A → D-032 (MVP-ru-only, no i18n; refactor в catalog отложен до триггера: реальный en-юзер или explicit запрос Henry).
2. Q-D-30 (chat history) → 50% B → D-033 (`chat_log` в audit.db: full plaintext, retention 30d, classifier Stage-1 читает last-20/24h; минимальный hardcoded denylist для tokens/passwords; полная redaction-policy → Q-E-33).

**Артефакты:**
1. Созданы `decisions/D-032-multi-language.md`, `decisions/D-033-chat-history.md`.
2. `index.md` — добавлены D-032/D-033 в Decisions section.
3. `queue.md` — Q-D-29, Q-D-30 перенесены в "Принятые"; Волна 7 → ✅ закрыта.
4. Q-files (Q-D-29, Q-D-30) — `[x] оформлено как [D-NNN]`.

**Cross-refs:**
1. D-032 ссылается на D-013/D-028/D-030 (single-tenant Henry, multi-tenant trigger).
2. D-033 ↔ Q-E-33 — взаимная ссылка (PII-redaction policy решается в Волне 8); D-033 предусматривает schema-readiness и минимальный denylist до закрытия Q-E-33.
3. D-033 различает chat_log (свободный диалог) и tracker_answers D-014 (структурированные опросы) — разные семантики.

**Дальше:** Волна 8 (Operations) — Q-E-33 (audit PII), Q-E-34 (logging), Q-E-35 (testing), Q-E-36 (backup), Q-E-37 (git в WIKI), Q-E-31 (per-user systemd), Q-E-38 (CLAUDE.md schema evolution), Q-E-39 (log date format).

---

## [2026-05-09] wave-8 | Operations — ✅ закрыта (7 решений + 1 deferred)

**Контекст:** юзер запустил Волну 8 (`/questions-answers Волну 8`) — operations layer. Порядок Q-E-33 → ... → Q-E-39 (Q-E-33 первым — блокирует доработку D-033 chat_log redaction).

**Действия:**

1. **Q-E-33 (audit PII)** → 70% B → [D-034](decisions/D-034-pii-redactor.md): tiered write-time redactor (Tier-1 drop API-tokens/passwords/keys/CC; Tier-2 mask email/phone/IBAN; Tier-3 plaintext через retention). Без at-rest crypto в MVP (single-tenant Henry). Hard-delete через `/admin gdpr_purge`. Применяется в `chat_log`, `audit_events.command`, structlog processor (D-035).
2. **Q-E-34 (service logging)** → 80% B → [D-035](decisions/D-035-service-logging.md): `structlog` → stdout JSON → journald. Processors-pipeline: contextvars (correlation_id) + TimeStamper (UTC ISO) + PII redactor (D-034) + JSONRenderer. Stdlib logging interop. Никакой ротации в app — journald делает.
3. **Q-E-35 (testing)** → 70% B → [D-036](decisions/D-036-testing-strategy.md): test pyramid. Unit: `ClaudeRunner` Protocol + `FakeClaudeRunner` (НЕ fake-binary в PATH). Integration: real CLI за `RUN_INTEGRATION=1` (nightly CI). E2E: manual checklist.md перед релизом. Coverage 80% soft на core.
4. **Q-E-36 (backup)** → DEFERRED → [backlog.md](backlog.md): no backup в MVP. Триггер пересмотра: реальный инцидент / multi-tenant / явный запрос. Content-versioning частично закрыт через D-037.
5. **Q-E-37 (git в WIKI)** → 80% B → [D-037](decisions/D-037-git-in-wiki.md): per-WIKI repo (не per-USER), auto-commit после PostRun (`<job_id>(<category>): <title>`), gitleaks pre-commit hook (DLQ entry `secret_detected` при fail), gitignore voice/photo/`.wiki.lock`/`data/runs/`, no remote в MVP. Per-WIKI granularity = `--add-dir`-граница = `.wiki.lock`-граница = future hard-isolation граница.
6. **Q-E-31 (per-user systemd)** → Вариант A → [D-038](decisions/D-038-per-user-systemd.md): hard isolation MVP. Каждый Claude CLI запускается через `systemd-run --scope --uid=<per-user> --property=ProtectSystem=strict --property=ProtectHome=tmpfs --property=ReadWritePaths=<wiki> --property=NoNewPrivileges=true --property=MemoryMax=2G --property=TasksMax=64`. Бот требует `CAP_SETUID`. Per-user UID provisioning при `wiki init`. Multi-tenant ready с дня 0. Local dev fallback `DISABLE_HARD_ISOLATION=1` (non-production only).
7. **Q-E-38 (CLAUDE.md evolution)** → 70% C → [D-039](decisions/D-039-claude-md-evolution.md): `schema_version` + `template_id` + `last_migrated_at` во frontmatter; managed-sections (`<!-- BEGIN/END MANAGED:name -->`); 3-way merge (declarative default + imperative `migrations/vN_to_vM.py` escape-hatch); TG diff-confirm (graduated explicit per D-023); git-commit миграции через D-037; bootstrap для existing WIKI без frontmatter.
8. **Q-E-39 (log date format)** → 80% C → [D-040](decisions/D-040-log-date-format.md): `## [YYYY-MM-DDTHH:MM±HH:MM] <op> | <title>`. Default Europe/Moscow, per-WIKI override через frontmatter `timezone:`. Только для runtime WIKI `log.md`; application-logs (D-035) и `audit.db`/`chat_log` остаются UTC.

**Артефакты:**
1. Созданы `decisions/D-034..D-040.md` (7 файлов).
2. Создан `backlog.md` с записью Q-E-36 (триггеры пересмотра, рекомендации при возврате).
3. `index.md` — добавлены D-034..D-040 в Decisions section, добавлена ссылка на backlog.md в Schema.
4. `queue.md` — Q-E-33..Q-E-39 + Q-E-31 перенесены в "Принятые"; Волна 8 → ✅ закрыта.
5. Q-files (Q-E-31, Q-E-33..Q-E-39) — `[x] оформлено как [D-NNN]` или DEFERRED для Q-E-36.

**Cross-refs (ключевые):**
1. **D-034 → D-033:** дорабатывает минимальный denylist до Tier-1/Tier-2/Tier-3.
2. **D-035 → D-034:** PII processor в structlog pipeline — третья точка применения redactor'а.
3. **D-037 → D-038:** per-WIKI git granularity = per-WIKI hard-isolation граница (один уровень разделения).
4. **D-038 → D-007/D-027/D-013:** hard isolation делает `--add-dir` defense-in-depth, не единственным механизмом; UID provisioning в users.toml.
5. **D-039 → D-016/D-017/D-037:** schema migrations applied через `git commit` с author `ai-steward-wiki`; managed-sections защищают user customizations.
6. **D-040 ≠ D-035/D-006/D-033:** разные TZ-форматы для разных контекстов — runtime markdown (local+offset) vs storage (UTC).
7. **Backlog Q-E-36 → D-037:** content-versioning частично заменяет disaster-recovery до пересмотра.

**Архитектурные сдвиги Волны 8:**
1. Hard-isolation (D-038) — MVP теперь требует Linux+systemd как primary deploy target; macOS dev — fallback flag.
2. Бот требует `CAP_SETUID` — изменение systemd unit + capabilities.
3. Multi-tenant readiness усилен: D-034 (PII per-user), D-038 (UID per-user), D-039 (schema migrations per-WIKI), D-037 (git isolation per-WIKI).
4. Backup отложен — git per-WIKI становится primary content-recovery механизмом.

**Все 8 волн закрыты.** Спецификация Spec-WIKI готова к переносу в feature-workflow design-фазу (`docs/superpowers/specs/discovery.md` + `design.md`) и оформлению ADR.

**Дальше:** ждём решения юзера — переносим артефакты в `docs/superpowers/` / `docs/adr/` или продолжаем в Spec-WIKI (entity/concept refactor под новые D-files, обновление diagrams, etc.).

## [2026-05-09] lint | fix D-001 status

Lint-аудит выявил расхождение: `D-001` имел `Статус: proposed`, тогда как все остальные 39 D-файлов — `accepted`, и решение по логу 2026-05-08 принято. Поправлено на `accepted` для консистентности.

## [2026-05-09] refactor | D-041 supersedes D-029 — WIKI lifecycle через NL-промпт

**Контекст:** юзер в обсуждении сценария создания WIKI отметил: явная команда `/wiki_init` противоречит концепту smart-inbox-routing. Уточнил три правила:
1. Юзер должен **знать** про WIKI как специализированную AI-библиотеку (термин не скрывать).
2. Юзер **не управляет** WIKI напрямую (никаких CRUD-команд).
3. Lifecycle через NL-промпт; Claude обязан проверять похожие WIKI и спрашивать «уверен, может уже есть?».

**Действия:**
1. Создан `decisions/D-041-no-direct-wiki-commands.md` (accepted): убраны `/wiki_init`, `/wiki_delete`, `/wiki_restore`, `/wiki_purge`; оставлены read-only `/wiki_list`, `/wiki_show`; обязательный duplicate-check (Levenshtein + semantic) до create; explicit confirm на delete; механизмы D-029 (soft limit 20, _trash 30d, audit) переехали в D-041 без изменений.
2. `D-029` помечен `superseded-by D-041` (history сохранена).
3. `index.md` — добавлен D-041, D-029 помечен superseded.
4. Влияние: D-016 (router intents `create_wiki`/`delete_wiki`/`restore_wiki`), D-030 (онбординг-блок «Что такое WIKI»), концепт smart-inbox-routing усилен.

**Открыто:** обновить шаблон Inbox `## Inbox hint` в D-016 под новые intent'ы (когда дойдут руки до правки D-016 или соответствующего пресета в `templates/`).

## [2026-05-09] refactor | D-041 — duplicate-check усилен AI semantic layer'ом

**Контекст:** юзер уточнил: проверка на дубликаты должна включать не только Levenshtein, но и AI-семантику. Levenshtein ловит typo, но не синонимы («подработка» vs `Freelance-WIKI`).

**Действия:**
1. D-041 §«Сценарий create» шаг 3: переписан как **two-layer check** (оба слоя обязательны):
   - Layer-1: Levenshtein ≤ 2 по нормализованному имени.
   - Layer-2: AI semantic-match по `## Назначение` + `## Inbox hint` каждой существующей WIKI юзера. Использует full контекст из `--add-dir` workspace (D-007), без отдельного embedding-pipeline в MVP.
   - Классификация семантики: `none` / `partial-overlap` / `near-duplicate`. Не-`none` → soft-block.
2. D-041 §«Anti-spam защита» п.2: typo-protection переименован в **two-layer duplicate protection**, Layer-2 помечен load-bearing.

## [2026-05-09] refactor | D-041 — intent-grounding + blind-spot scan перед create

**Контекст:** юзер уточнил: AI должен читать профиль юзера, находить real intention за буквой запроса, выявлять blind spots и спрашивать clarification если нужно.

**Действия:**
1. D-041 §«Сценарий create» — добавлен шаг 3 **Mandatory intent-grounding** *до* duplicate-check:
   - чтение `USERS/<USER>/CLAUDE.md` (профиль, persona, известные домены) — уже в workspace через D-007 auto-walk;
   - чтение последних 20/24h `chat_log` (D-033);
   - извлечение real intention vs буквы запроса;
   - **blind-spot scan** по 5 осям: persona/правила librarian'а, cron-периодичность, PII-tier (D-034), recipients, overlap с известными интересами;
   - 1–3 clarification-вопроса если значимый blind spot не покрыт профилем+диалогом (skip, если всё ясно).
2. Шаги 4–8 сценария renumbered. Шаг 7 (confirm) теперь **включает summary blind-spot-ответов**. Шаг 8 (create) **применяет ответы** как initial customization CLAUDE.md через managed-sections (D-039).
3. §«Anti-spam защита» — добавлен п.3 **Intent-grounding с blind-spot scan** как load-bearing инвариант.

**Cross-refs:** D-007 (auto-walk профиля), D-033 (chat history для контекста), D-034 (PII-tier из blind spots), D-039 (managed-sections для customization), D-017 (пресет как baseline до customization).

## [2026-05-09] refactor | D-041 — universal pre-flight для всех important operations

**Контекст:** юзер уточнил: intent-grounding + blind-spot scan нужен **до любой важной операции**, не только create. Включая rename, delete, restore, edit-rules, edit-persona, merge, split, purge, bulk-edits.

**Действия:**
1. D-041 — добавлены секции:
   - **§«Important operations»** — список из 10 операций (create, delete, restore, rename, merge, split, edit-rules, edit-persona, purge, bulk-*) + non-important (single-page edit, read, query).
   - **§«Universal pre-flight»** — 5 шагов: intent-grounding → operation-specific blind-spot scan (таблица) → clarification (1–3 вопроса по необходимости) → confirm с summary → execute+audit.
   - Operation-specific blind-spot таблица: для каждой операции свой набор осей проверки (cron-jobs, cross-refs, retention, overlap правил, scope precision и т.д.).
2. **Сценарий create** ужат до worked-example (pre-flight теперь универсальный).
3. **Сценарий delete** дополнен blind-spot шагом про активные cron-jobs + правило: cron-jobs ставятся `paused` при soft-delete, не удаляются (восстанавливаются с WIKI).
4. **Добавлены worked examples** restore и rename. Остальные операции (merge / split / edit-rules / edit-persona / purge / bulk-*) фиксированы как контракт «следуют тому же паттерну», конкретика — при plan.md.
5. Запись в `audit.db` теперь content: `(operation, wiki, intent_summary, blind_spots, clarifications, ts)`.

**Cross-refs:** D-007 (auto-walk профиля), D-033 (chat history), D-023 (confirms), D-039 (managed-sections), D-020 (silent jobs для housekeeping).

**Open:** worked examples merge/split/edit-rules/edit-persona/purge/bulk-* — будут в plan.md когда дойдём до execution-фазы.

## [2026-05-09] refactor | D-041 — pre-flight распространён на page-level операции

**Контекст:** юзер уточнил: тот же pre-flight применяется и к page-level edits, не только к wiki-level. Цель — единый контракт, без щелей где AI может писать в WIKI без проверки контекста.

**Действия:**
1. D-041 — добавлена секция **§«Page-level operations»** с 6 операциями: page-create, page-edit, page-delete, page-rename, page-move, append-data.
2. Operation-specific blind-spot таблица для page-level: дубликаты по title+содержимому, бэклинки, status (stable→review при структурном edit), librarian-правила, schema (для append-data), PII-tier.
3. **Proportional weight** для page-level pre-flight:
   - Clarification skip rate высокий (большинство правок не требует вопросов).
   - Graduated confirm (D-023): append/page-create → implicit; page-edit/rename/move → soft (diff+OK); page-delete и edit `stable`-страниц → explicit.
   - Audit единой записью per page-op, без раздувания chat-истории.
4. **Не-important** ужат до: read-only operations + системные cron-jobs (intent уже зашит в job payload).

**Cross-refs:** D-023 (graduated confirms), D-034 (PII-tier для append-data), llm-wiki-method (index.md/log.md контракт), CLAUDE.md §6.4 (status review при конфликте).

## [2026-05-09] lint | op-vocabulary canonical set

**Контекст:** проверка консистентности `log.md` против CLAUDE.md §6.2 (`<op> ∈ {init, ingest, query, lint, refactor}`).

**Найдено:** прошлые записи содержат off-canon op'ы:
1. `decision` (5×: D-002, D-003, D-004, D-007, D-008) — должно было быть `refactor` с title `D-XXX accepted: <тема>`.
2. `wave-4`, `wave-5`, `wave-6`, `wave-7`, `wave-8` (5×) — должно было быть `refactor` с title `wave-N closed (M decisions)`.

**Решение:**
1. CLAUDE.md §6.2 не трогаем — canonical set `{init, ingest, query, lint, refactor}` остаётся как есть.
2. Прошлые off-canon заголовки **не правим** — append-only, история сохраняется.
3. Все будущие записи (с YYYY-MM-DD ≥ 2026-05-09 после этой записи) используют **только canonical 5**. Новый D-файл = `refactor`, batch closure = `refactor`.

## [2026-05-09] lint | Spec-WIKI date-format scope clarified

**Контекст:** проверка drift между CLAUDE.md §4.6 и D-040.

**Найдено:** D-040 уже содержит явный exempt для Spec-WIKI (§«Spec-WIKI собственный log.md», строки 82–84): runtime WIKI получает ISO+TZ, Spec-WIKI остаётся `[YYYY-MM-DD]`. Однако CLAUDE.md §4.6 п.6 не ссылался обратно на D-040, что создавало риск misclassification для следующей LLM-сессии.

**Решение:** в CLAUDE.md §4.6 п.6 добавлена явная ссылка: «Применяется только к Spec-WIKI/log.md (design-time мета-зона). Runtime `<Domain>-WIKI/log.md` — D-040». Также вписан canonical op-set inline.

**Эффект:** drift между двумя SSoT устранён, D-040 не меняется (exempt уже был). Будущие записи Spec-WIKI/log.md остаются `[YYYY-MM-DD]`, runtime — ISO+TZ.

## [2026-05-09] refactor | D-016 — добавлен intent vocabulary под D-041

**Контекст:** follow-up из D-041. Router в Inbox-WIKI должен распознавать closed-set intent'ов покрывающий все wiki-level и page-level операции.

**Действия:**
1. D-016 §«Контракт» п.3 расширен: добавлены п.3.4 (intent-таксономия) и п.3.5 (универсальный pre-flight per D-041).
2. D-016 — добавлена секция **§«Intent vocabulary» (closed set)** с 20 intent'ами:
   - **Wiki-level (9):** create/delete/restore/rename/merge/split/edit-rules/edit-persona/purge.
   - **Page-level (6):** page-create/edit/delete/rename/move + append-data.
   - **Read-only (3):** query, list_wikis, show_wiki.
   - **Special (2):** unknown, chitchat.
3. D-016 — добавлена секция **§«Inbox-WIKI CLAUDE.md обязательные секции»** с 6 обязательными подразделами: Format, Fallback, Intent vocabulary, Pre-flight, Confirm policy, Anti-rules.
4. Удалена референция на устаревшую команду `/wiki_init` в §«Последствия» п.2 — заменена на NL-промпт с `intent=create_wiki` per D-041.
5. Контекст-ссылка D-016 — добавлен D-041.

**Cross-refs:** D-041 (pre-flight contract), D-023 (graduated confirms), D-017 (per-domain пресеты с `## Inbox hint`).

**Open:** обновить пресеты в `templates/` (parallel, не в Spec-WIKI scope) — добавить `## Inbox vocabulary` placeholder в Inbox-`_default.md`. Сделается при переносе в plan.md.

## [2026-05-09] refactor | D-030 — добавлен WIKI-intro в onboarding flow

**Контекст:** follow-up из D-041. Юзер должен понимать концепцию WIKI и правила NL-only lifecycle до Q&A, иначе будет искать команды и фрустрироваться.

**Действия:**
1. D-030 — добавлена секция **§«Onboarding intro: Что такое WIKI»** с готовым скриптом intro-сообщения (5 ключевых пунктов): концепция WIKI, запрет lifecycle-команд, pre-flight (clarification + duplicate-check), explicit confirm, 30d retention + restore, read-only команды.
2. Шаблон intro вынесен в `templates/onboarding-intro.<lang>.md` (git-tracked в repo сервиса), эволюция через PR.
3. **State machine** дополнена: новое состояние `intro` между `pending` (после admin-approve) и `onboarding` (Q&A). Переход `intro → onboarding` по подтверждению юзера «готов».
4. **Lint-checkable элементы intro** (6): концепция WIKI / запрет команд / pre-flight / confirm / retention / read-only список.
5. **Запреты** §«Последствия» п.3 расширены: п.4 не пропускать WIKI-intro, п.5 не править шаблон через TG.
6. Контекст-ссылка D-030 — добавлен D-041.

**Cross-refs:** D-041 (правила NL-lifecycle, pre-flight), D-016 (router-driven flow), D-029→D-041 supersession (retention semantics).

**Open:** создать `templates/onboarding-intro.ru.md` и `.en.md` (vis-a-vis D-032 multi-language) — parallel-репо вне Spec-WIKI scope; сделается при переносе в plan.md.

## [2026-05-09] refactor | CLAUDE.md §4 п.6 — canonical op-set расширен до 7

Расширен closed canonical set операций log.md с 5 до 7: добавлены `decision` (артефакт-решение принят/обновлён) и `wave` (открытие/закрытие тематического батча). Каждая op получила однострочное определение.

**Причина:** lint обнаружил 20 заголовков (15 `decision` + 5 `wave-N`), не попадающих в исходный набор `{init, ingest, query, lint, refactor}`. Переписывание под существующие op (`decision`→`refactor`/`ingest`) теряло семантику: принятие решения и реорганизация страниц — разные события графа знаний. Best practice (Conventional Commits §"types other than feat/fix MAY be used"; controlled vocabularies — расширение через явный schema-bump, не drift в данных) предписывает атомарное обновление схемы вместо подгонки данных.

**Cross-refs:** CLAUDE.md §4 п.6, существующие записи log.md L121–L355 (`decision`), L366–L525 (`wave-N`) теперь schema-valid.

## [2026-05-09] lint | Spec-WIKI audit + schema-fixes

**Op:** `lint` (audit) → перешёл в `refactor` для применения правок по результатам.

**Findings (read-only audit):**
1. Index ↔ disk: 102/102 sync. Orphans: 0. Overview §9 coverage: 40/40. Log gaps: 0. D-NNN schema: 41/41 валидны.
2. **Q-D-26** ссылался на superseded D-029 без отметки D-041 (live decision per D-041 §2.3 — wiki-команды удалены из spec).
3. **10 Q-файлов** (`Q-A-01..Q-A-09`, `Q-A-32`) имели semantic decision-line `[x] Вариант X ... См. [D-NNN]`, но не canonical `- [ ] оформлено как [D-NNN]` per CLAUDE.md §5 Q-template.

**Действия (refactor):**
1. `Q-D-26-wiki-init-auth.md:28` — checkbox дополнен `, superseded by [D-041]`.
2. В каждый из 10 Q-файлов добавлена canonical строка `- [x] оформлено как [D-NNN](...)` сразу после существующего decision-line:
   1. Q-A-01 → D-002, Q-A-02 → D-003, Q-A-03 → D-004, Q-A-04 → D-009, Q-A-05 → D-010
   2. Q-A-06 → D-005, Q-A-07 → D-011, Q-A-08 → D-012, Q-A-09 → D-014, Q-A-32 → D-006

**Не изменялось:** содержание решений, статусы D-NNN, index.md, decisions/*. Только schema-conformance в questions/.

**Cross-refs:** CLAUDE.md §5 (Q-template), §6 п.4 (lint без молчаливых перезаписей).

## [2026-05-09] refactor | tech-spec-draft assembled (41 decisions consolidated, status review)

## [2026-05-10] refactor | tech-spec-draft: D-029 supersession verified, removed double-ref (D-041 owns all mechanisms)

## [2026-05-10] lint | D-010 vs D-031 conflict detected: D-010 user-TZ source = `roles.toml`, D-031 allowlist SSoT = `users.toml` — нужно решение, какой файл owns что (или unify)
## [2026-05-10] refactor | tech-spec-draft review fixes: removed stale `/wiki_init` ref, added soft-limit 20 line, added D-007 to §1, D-040 to §10, removed pytesseract meta-comment, bumped date

## [2026-05-10] decision | D-042 proposed: unify user config в `users.toml` (resolves D-010↔D-031 conflict); D-010 нуждается в patch на approve

## [2026-05-10] decision | D-042 accepted; D-010 patched (3 строки: roles.toml → users.toml); tech-spec-draft §6 + §9 обновлены

## [2026-05-10] lint | Spec-WIKI audit (read-only)

**Op:** `lint` (read-only).

**Findings:**
1. **Index ↔ disk sync:** 42/42 D, 40/40 Q, 6/6 entities, 8/8 concepts, 2/2 research, 1/1 raw — ✅ зеркало.
2. **Orphans:** 0. Минимальный inbound count = 5 (`schedule-profiles`); все entities/concepts имеют 5+ ссылок.
3. **Schema:** все D-NNN/Q-TIER-NN соответствуют шаблонам §5; queue.md и backlog.md актуальны.
4. **Stale claims — `roles.toml` (после D-042):** не помечены ссылкой на D-042 в:
   1. `decisions/D-013-claude-cli-auth.md:72,84,95` (3×)
   2. `questions/Q-A-05-nl-time-parsing.md`, `Q-C-20-claude-cli-auth.md`, `Q-D-27-onboarding.md`, `Q-D-28-allowlist-hot-reload.md`, `Q-D-29-multi-language.md`, `Q-D-30-chat-history.md` (по 1× каждая)
5. **Stale claims — `/wiki_init|wiki_delete|wiki_restore|wiki_purge` (после D-041):** не помечены ссылкой на D-041 в:
   1. `decisions/D-004` (3×), `D-008` (4×), `D-010` (1×), `D-016` (1×), `D-017` (3×), `D-036` (1×)
   2. `research/tech-spec-draft.md:96` — упоминание `/wiki_restore` без отметки «через NL» (текст рядом корректен).
6. **queue.md:73** — финальная строка «7 решений принято + 1 отложен» устарела; фактически 41 accepted + 1 superseded (D-029→D-041) + 1 deferred (Q-E-36).

**Действия:** не предприняты — read-only audit. Per CLAUDE.md §6 п.4 правки не молчаливые: pending решение пользователя, оформлять ли follow-up `refactor`-проход (добавить `(superseded by D-041/D-042)` маркеры в указанные строки + поправить queue.md финал).

**Не findings:** broken markdown-links (sample-чек чист), противоречий в принятых решениях не обнаружено, конституция (CLAUDE.md) consistent с фактическим содержимым.

## [2026-05-10] refactor | follow-up к lint: маркировка stale-claims (D-041 / D-042) + обновление queue.md

**Контекст:** apply findings 4–6 из предыдущего lint-аудита. Per CLAUDE.md §6 п.4 правки не молчаливые — каждая stale-страница получает inline-`> Update` блок со ссылкой на актуализирующее решение, исходный текст сохранён (history).

**Действия:**
1. **`roles.toml` → `users.toml` (per [D-042](decisions/D-042-unify-user-config.md)):**
   1. `decisions/D-013-claude-cli-auth.md` — 3 inline-замены `roles.toml` → `users.toml (per D-042, ранее roles.toml)`.
   2. 6 Q-файлов (`Q-A-05`, `Q-C-20`, `Q-D-27`, `Q-D-28`, `Q-D-29`, `Q-D-30`) — добавлен `> Update (D-042, 2026-05-10): …` блок после `**Источник:**`.
2. **`/wiki_*` команды → NL-intent (per [D-041](decisions/D-041-no-direct-wiki-commands.md)):**
   1. `decisions/D-004`, `D-008`, `D-010`, `D-016`, `D-017`, `D-036` — добавлен `> Update (D-041, 2026-05-09): …` блок после `**Контекст:**`. В `D-017`/`D-036` D-041 также добавлен в список Контекст-ссылок.
   2. `research/tech-spec-draft.md:96` — заменено «`/wiki_restore` через NL» на «восстановление NL-промптом (intent `restore_wiki`, см. D-041)».
3. **queue.md финальная строка** обновлена: «41 решение accepted (D-001…D-042, из них D-029 superseded-by D-041) + 1 отложено в backlog (Q-E-36). Все 40 вопросов из overview §9 закрыты».

**Не изменялось:** содержание решений, статусы D-NNN, index.md, варианты/решения внутри Q-файлов, schema-конституция.

**Cross-refs:** D-041, D-042, lint-запись выше.

## [2026-05-10] refactor | tech-spec-draft post-review fixes (status: review → stable)

**Контекст:** apply review-findings 1–3, 5–8 из ревью `research/tech-spec-draft.md`. Finding 4 (`recipes` в §11) снят как ложный — D-017 явно включает `recipes.md` в список пресетов, parent ai-steward CLAUDE.md тут не каноничен.

**Действия:**
1. Frontmatter `sources:` дополнен `D-042-unify-user-config.md`.
2. Вступление (стр. 53) — «41 решение (D-001…D-041)» → «42 решения (D-001…D-042); D-029 superseded by D-041 → 41 active + 1 historical».
3. §9 (стр. 132) — «Mandatory intro (5 пунктов)» → «6 lint-checkable элементов» с явным перечислением (концепция / запрет команд / pre-flight + duplicate-check / explicit confirm / 30d retention + restore / read-only список) — соответствует D-030 §«Onboarding intro» и log-записи 2026-05-09.
4. §4 (стр. 87) — в текст 5-шагового pre-flight добавлена ссылка `(см. D-041)`.
5. §3 (стр. 77) — у таблицы таймаутов добавлено `— каноническая таблица в D-021`.
6. §12 Open Backlog — расширено: явное перечисление Q-E-36 + контекст (single-tenant, D-037 частичный compensator).
7. Frontmatter `status: review` → `status: stable`. Документ готов к переносу в `docs/superpowers/specs/`.

**Не изменялось:** §11 (`recipes` оставлен — соответствует D-017), содержимое D-NNN, index.md.

**Cross-refs:** review-сообщение 2026-05-10, D-021, D-030, D-041, D-042.

## [2026-05-10] refactor | tech-spec-draft critical-fix wave (review feedback)

**Файлы:** `research/tech-spec-draft.md`, `backlog.md`.

**Контекст:** review tech-spec-draft вернул 3 critical-finding'а. Этот wave закрывает их по best-practice без новых D-файлов (изменения — реализационные детали поверх уже принятых D-006/D-037/D-038).

**Изменения:**
1. **§12 coverage map.** Исправлен счёт «40 вопросов» → 39 (overview §9 нумерован 1–39). Добавлена полная таблица Q→D (38 прямых ответов, 3 derived: D-001/D-014/D-042, D-029 superseded D-041). Q-E-36 переведён в MVP-partial.
2. **§10 backup (Q-E-36 MVP-partial).** В MVP добавлен in-app safety net: APScheduler `db_snapshot` (VACUUM INTO, 7d retention) + `git push` per-WIKI на auto-commit + restore-test runbook. Off-site / 3-2-1 / GFS / borg-restic — остаются deferred с уточнёнными триггерами (TENANCY_MODE=multi, state >1GB, hosting failure).
3. **§10 process isolation (D-038 уточнён).** `systemd-run --scope` — per-CLI-invocation (per-process limits MemoryMax=2G), не per-user. Aggregate cap через родительский `aisw-bot.slice` (MemoryHigh=12G, MemoryMax=16G). STT/Whisper вынесен в `aisw-stt.slice` вне CLI-scope. Privilege model: `aisw-bot` non-root + `AmbientCapabilities=CAP_SETUID CAP_SETGID` + bounding set, через `setresuid/setresgid`.

**Не изменялось:** D-NNN артефакты (изменения — над D-006/D-037/D-038 как реализация), index.md, остальные §1–§9, §11.

**Cross-refs:** review-сообщение 2026-05-10 (critical/middle/minor), D-006, D-037, D-038, backlog.md#Q-E-36.

## [2026-05-10] refactor | tech-spec-draft middle-fix wave (review feedback)

**Файлы:** `research/tech-spec-draft.md`.

**Контекст:** review middle-findings #5–#10. Изменения — реализационные детали поверх D-006/D-009/D-015/D-018/D-030/D-034/D-038, без новых D-файлов. #4 (CAP_SETUID privilege model) уже закрыт в critical-fix wave.

**Изменения:**
1. **§4 router hint-cache.** Добавлен `sessions.db.inbox_hint_cache` per-user, ключ `(user_id, wiki_path, mtime, sha256)`, mtime-keyed invalidation. Снимает N×file-read на каждое сообщение.
2. **§4 dedup bounded context.** `seen_files` перенесён из `jobs.db` в `audit.db` (вместе с `tg_updates`) — оба слоя idempotency теперь в одном observability/dedup контексте per D-006.
3. **§5 trash + PII.** Добавлено явное правило: PII-redactor проходит по `_trash/` на write-time hook (Tier-1 DROP, Tier-2 MASK), Tier-3 plaintext под общей trash-retention 30d, по истечении — hard-delete (`shred -u` для media, `unlink` для md), audit-event `trash_purged`.
4. **§6 терминология disambiguated.** Stage-1 разделён на Stage-1a (router-Sonnet, Inbox-context) и Stage-1b (executor-Sonnet, Domain-context). Промпты разнесены: `prompts/inbox.md` для 1a, `prompts/domain-<type>.md` для 1b.
5. **§9 onboarding lint.** Описан конкретный механизм: HTML-маркеры `<!-- INTRO_ELEMENT_ID:<slug> -->` + closed set из 6 slug'ов + `scripts/lint_onboarding.py` (CI + pre-commit) + runtime-показ в `audit.db.onboarding_events`.
6. **§10 Tier-3 retention.** Развёрнутая таблица retention per-store (10 строк): chat_log 30d, tg_updates 24h, seen_files 30d, audit_events/admin_events/tracker 90d, snapshots 7d, data/runs 90d, raw/media indefinite, _trash 30d. Каждая строка — purge mechanism + rationale.

**Cross-refs:** review-сообщение 2026-05-10 (middle #5–#10), D-006, D-009, D-015, D-018, D-030, D-034.

## [2026-05-10] refactor | tech-spec-draft second review fix wave + D-017 boundary alignment

**Файлы:** `research/tech-spec-draft.md`, `decisions/D-017-domain-claude-md-template.md`.

**Контекст:** второй ревью tech-spec-draft (critical/middle/minor). Закрыты 2 critical + 4 middle.

**Изменения:**
1. **§10 retention-таблица (critical).** Закрыта `|` на строке `_trash`, проза (at-rest crypto, git per-WIKI, тестирование, log-даты) вынесена в 4 отдельных абзаца — таблица перестала «съедать» хвост секции в рендере.
2. **§11 templates SSoT (critical).** Переписано: `templates/` — локальная SSoT репо `ai-steward-wiki`, никакой live-sync с parent-`CLAUDE.md` ai-steward (нарушение Spec-WIKI/CLAUDE.md §1.1). Допустим one-time bootstrap-копирование, runtime-чтение запрещено.
3. **D-017 «Источник доменного знания» (critical sync).** Раздел приведён в соответствие §11 — явный one-time bootstrap, не runtime dependency.
4. **§5 нормализация имени WIKI (middle).** Pipeline: translit ISO 9 → split non-alphanumeric → PascalCase join → `-WIKI` suffix → regex-валидация D-008. Lookup пресета — case-insensitive со fallback на дефис-вариант (`health-lite.md`).
5. **§6 Fast-path routing (middle).** `reminder_job` имеет `wiki_id=null`; firing-handler доставляет TG-message напрямую без CLI и без `<Domain>-WIKI`. Asymmetry с другими kind'ами зафиксирована.
6. **§10 git remote (middle).** `WIKI_GIT_REMOTE` → `WIKI_GIT_REMOTE_TEMPLATE` со строкой-шаблоном с обязательным `{wiki}` placeholder, идемпотентный `git remote add` per-WIKI, fallback на local-only.
7. **§4 hint-cache (middle).** Двухуровневая инвалидация: mtime cheap-check → sha256 verification на mismatch → re-parse только при content drift. Устраняет ложные re-parse на `touch`/rsync.

**Не изменялось:** D-002/D-008/D-009/D-041 (правки — в формулировках tech-spec, не решений), index.md, log.md (history-only append).

**Cross-refs:** review-сообщение 2026-05-10 (critical #1–#2, middle #3–#6), Spec-WIKI/CLAUDE.md §1.1, D-008, D-002, D-009, D-017, D-037.

## [2026-05-10] refactor | tech-spec review fixes — critical+middle resolved

**Контекст:** второй review tech-spec-draft.md (этой же сессии) обнаружил 2 critical SSoT-конфликта и 3 middle-issues. Фикс — выровнять артефакты-решения с tech-spec'ом по best-practice (SSoT в D-файлах, tech-spec остаётся навигационной картой).

**Действия:**

1. **Critical #1 — backup vs D-037 (`research/tech-spec-draft.md` §10).** Удалён `git push` per-WIKI как backup-механизм; tech-spec приведён в соответствие с [D-037](decisions/D-037-git-in-wiki.md) §"Remote push" п.1 (`No remote в MVP`, `git ≠ disaster-recovery`). MVP backup теперь = state-DB snapshots + local git history; HW-failure / off-site / remote git push — все deferred до отдельного решения. Добавлен явный блок «Что MVP-partial backup НЕ покрывает».
2. **Critical #2 — `seen_files` location ([D-018](decisions/D-018-ingest-idempotency.md), [D-006](decisions/D-006-state-storage-layout.md)).** D-018 amended 2026-05-10: `seen_files` перенесён `jobs.db` → `audit.db` (matches L1 `tg_updates`, sealed bounded context per D-006). Добавлена секция «Уточнение 2026-05-10» с rationale + Alembic-миграция (превентивная). Tech-spec §4 «review fix» note удалён (теперь в D-018 как SSoT).
3. **Middle #3 — новые таблицы ([D-006](decisions/D-006-state-storage-layout.md)).** Раскладка таблиц расширена явными ссылками: `audit.db` теперь содержит `chat_log`/`audit_events`/`admin_events`/`tg_updates`/`seen_files`/`dedup_hits`/`prompt_versions`/`onboarding_events`; `sessions.db` — `users`/FSM/`pending_users`/`pending_confirms`/`inbox_hint_cache`. D-006 stamped amended 2026-05-10.
4. **Middle #4 — soft-limit numbers ([D-041](decisions/D-041-no-direct-wiki-commands.md)).** §"Anti-spam защита" п.1 переписан: hard cap 20, warn at 16/20, hard reject 20/20, `_trash/` exclusion из counter/autodiscover/anti-nesting walk — явный SSoT в D-041 (не наследуются из superseded D-029). Tech-spec §5 заменил «переехали без изменений» на ссылку на D-041 §Anti-spam.
5. **Middle #5 — trash sweep ([D-034](decisions/D-034-pii-redactor.md)).** Добавлена секция «Trash sweep» в D-034 §Применение: post-move hook на soft-delete рекурсивно проходит `_trash/<wiki>/*.md` и применяет tier-1 DROP / tier-2 MASK; tier-3 plaintext под retention; media — `shred -u` на hard-delete; audit-event `trash_sweep`/`trash_purged`. Tech-spec §5 ссылается на D-034 §"Trash sweep".

**Side-fixes (попутно):**

1. Anchor `#q-e-36` → `#q-e-36--backup-wiki-и-state-db` в tech-spec §10 + §22 coverage map (GitHub-style heading rendering).

**Не изменялось:** index.md (нет новых страниц), entities/concepts (decisions/D-NNN — артефакты-решения, обновлены in-place per CLAUDE.md §«Drift resolution → Contract/XML wins»). Минорные пункты review #6–#10 (Stage-нумерация в D-009, PriorityQueue numbers в D-011, Pydantic formulation в §2, ENABLE_SELF_SIGNUP orthogonality в D-028) — отложены, не critical/middle.

**Cross-refs:** review-сообщение 2026-05-10 (critical #1, #2; middle #3, #4, #5), D-006, D-018, D-034, D-037, D-041, tech-spec-draft.md §4/§5/§10/§22.

## [2026-05-10] refactor | tech-spec critical+middle review fixes — SSoT realignment

**Файлы:** `research/tech-spec-draft.md`, `backlog.md`, `decisions/D-003-scheduler-backend.md`, `decisions/D-006-state-storage-layout.md`, `decisions/D-015-system-prompt-inject.md`, `decisions/D-016-inbox-claude-md-template.md`, `decisions/D-022-voice-photo-input.md`.

**Контекст:** review `research/tech-spec-draft.md` обнаружил 3 critical и 7 middle issues. User попросил исправить critical и middle по best-practice, с вопросами только если решение неочевидно.

**Изменения:**
1. **D-038 isolation / tech-spec §10.** Исправлен неверный `systemd-run --user=aisw-<N>` на system scope с `--uid=aisw-<N> --gid=aisw-<N>`; добавлена явная причина, почему `--user` не подходит.
2. **Q-E-36 backup / backlog / tech-spec §10/§12.** Remote git push окончательно выведен из MVP; MVP = `db_snapshot` + local per-WIKI git history + restore-test.
3. **D-025 runs retention / tech-spec §10.** `<wiki>/data/runs/` больше не purged автоматически; retained indefinitely до отдельного retention decision.
4. **D-015 prompt injection / tech-spec §6.** Stage-0 Haiku отделён от CLI flags: SDK читает `prompts/classifier.md` как system instructions, а `--append-system-prompt @file` остаётся только для Stage-1 CLI. `prompts/domain-<type>.md` добавлен как optional Stage-1b extension.
5. **D-016/D-006 hint-cache / tech-spec §4.** `sessions.db.inbox_hint_cache` оформлен как runtime-cache, не WIKI SSoT; guards усилены до `size_bytes + mtime_ns + ctime_ns + content_sha256`.
6. **D-022 media routing / tech-spec §8.** До routing media сохраняется в `Inbox-WIKI/raw/media/_staging/`, затем атомарно переносится в target Domain-WIKI после resolution/confirm.
7. **D-003 maintenance boundary / tech-spec §10.** `db_snapshot` описан как internal APScheduler maintenance job, не user-facing `job-model`; host-level backup остаётся OS-cron/systemd timer.
8. **D-006 storage map / tech-spec §2/§8.** Добавлены `audit.db.job_outputs` и `audit.db.run_outputs`.
9. **D-041 lifecycle terminology / tech-spec §10.** Прямое `/wiki_purge` заменено на NL `purge_wiki` / admin GDPR purge.

**Не изменялось:** `status: stable` в `research/tech-spec-draft.md` оставлен как есть; это был minor review item, user попросил critical+middle.

**Cross-refs:** review-сообщение 2026-05-10 (critical/middle), D-003, D-006, D-015, D-016, D-022, D-025, D-037, D-038, D-041, backlog.md#Q-E-36.

## [2026-05-10] refactor | tech-spec auth/isolation and SSoT review fixes

**Файлы:** `research/tech-spec-draft.md`, `decisions/D-009-classifier-engine.md`, `decisions/D-013-claude-cli-auth.md`, `decisions/D-015-system-prompt-inject.md`, `decisions/D-017-domain-claude-md-template.md`, `decisions/D-030-onboarding.md`, `decisions/D-031-allowlist-hot-reload.md`, `decisions/D-038-per-user-systemd.md`, `decisions/D-042-unify-user-config.md`.

**Контекст:** review `research/tech-spec-draft.md` обнаружил critical/middle issues: Claude Code subscription auth конфликтовал с per-user `systemd-run`, Stage-0 SDK auth был невалидно привязан к Claude Code OAuth, а часть draft-деталей обгоняла SSoT decisions.

**Изменения:**

1. **Critical — CLI auth + hard isolation.** D-013 и D-038 согласованы: shared `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`, read-only mount в CLI scope, `SupplementaryGroups=aisw-claude`, `ProtectHome=tmpfs` больше не ломает auth.
2. **Critical — Stage-0 auth.** D-009/D-015 больше не утверждают, что Anthropic SDK читает Claude Code subscription credentials. Default Stage-0 backend — Claude CLI Haiku; direct SDK/API backend — optional только с отдельным API credential.
3. **Security mitigation.** D-038 добавил runtime permission profile: allow file tools, deny `Bash`/`WebFetch`/`Read(auth-dir)`, `--permission-mode dontAsk`; auth dir остаётся вне `--add-dir`.
4. **Middle — D-038 drift.** Aggregate slices, STT slice, `systemd-sysusers` provisioning и prompt/auth read-only paths перенесены из tech-spec в D-038.
5. **Middle — templates.** D-017 теперь явно содержит `health-lite.md` и `recipes.md`; slug lookup поддерживает primary slug + hyphenated alias без расширения D-008 runtime regex.
6. **Middle — identity vocabulary.** D-042 зафиксировал canonical terms: `telegram_id`, `owner_telegram_id`, `chat_id`, `user_id`. D-030/D-031 приведены к полной D-042 schema.
7. **Middle — onboarding invariant.** Tech-spec и D-030 теперь явно говорят: `USERS/<NAME>/` создаётся только после onboarding completion; pre-completion user-record остаётся `enabled=false`.

**Cross-refs:** review-сообщение 2026-05-10 (critical #1–#2, middle #1–#7), official Claude Code CLI/settings docs, D-009, D-013, D-015, D-017, D-030, D-031, D-038, D-042.

## [2026-05-10] refactor | Spec-WIKI critical+middle lint fixes

**Файлы:** `CLAUDE.md`, `log.md`, `queue.md`, `entities/time-tracker.md`, `entities/job-model.md`, `concepts/schedule-profiles.md`, `concepts/mandatory-checkins.md`, `concepts/smart-inbox-routing.md`, `decisions/D-001-time-tracker-vs-job-model.md`, `decisions/D-030-onboarding.md`, `decisions/D-033-chat-history.md`, `decisions/D-034-pii-redactor.md`, `decisions/D-035-service-logging.md`, `questions/Q-A-06-planner-ssot.md`, `questions/Q-A-09-tracker-memory-model.md`, `questions/Q-B-10-domain-claude-md-template.md`, `questions/Q-B-11-ingest-idempotency.md`, `questions/Q-C-20-claude-cli-auth.md`, `questions/Q-C-21-system-prompt-inject.md`, `questions/Q-C-22-add-dir-scope.md`, `questions/Q-C-23-wiki-marker-format.md`, `questions/Q-D-26-wiki-init-auth.md`, `questions/Q-D-27-onboarding.md`.

**Контекст:** user попросил исправить critical и middle findings из read-only lint без дополнительных вопросов, если best-practice решение очевидно. `questions-answers` не запускался: все правки выводятся из уже принятых D-005, D-006/D-018, D-030, D-041 и D-042.

**Изменения:**
1. **D-005 propagation.** Убраны live-упоминания `planner.json` из tracker/job/digest/schedule concept-страниц и D-001; текущий SSoT расписаний — `jobs.db`, память трекера — `tracker_answers`.
2. **Zero-coupling with `ai-steward`.** D-030/Q-D-27 переведены на локальный `templates/onboarding-profile-questions.<lang>.md`; Q-B-10 уточнён: domain templates — локальная SSoT repo сервиса, без runtime-чтения parent `ai-steward/CLAUDE.md`.
3. **D-042 identity vocabulary.** D-033/D-034/D-035 заменили `user_id` как TG id на `telegram_id`; `chat_id` оставлен только как delivery target.
4. **D-018 storage drift.** Q-B-11 теперь указывает `audit.db.seen_files`, а не `jobs.db.seen_files`.
5. **Question schema.** Q-A-09 heading исправлен на `Q-A-09`; Q-C-20…Q-C-23 получили canonical `- [x] оформлено как [D-NNN]`.
6. **D-041 lifecycle terminology.** Q-D-26 обновлён: live create/delete/restore flow описан через NL-intent, а slash-команды помечены историческими.
7. **Log schema.** `log.md` header и `CLAUDE.md` уточнены: новые записи используют canonical op-set `{init, ingest, query, lint, refactor, decision, wave}`; historical `wave-N` до schema-bump 2026-05-09 остаются append-only legacy.
8. **Queue count.** `queue.md` исправлен: overview §9 содержит 39 вопросов; Q-A-09 — отдельный вопрос из time-tracker ingest.

**Не изменялось:** `research/tech-spec-draft.md` оставлен как pre-existing dirty file; untracked `AGENTS.md` не трогались; исторические `wave-N` записи в `log.md` не переписывались.

**Cross-refs:** D-005, D-006, D-018, D-030, D-041, D-042.

## [2026-05-10] lint | structural audit — clean

**Файлы:** read-only.

**Контекст:** user запросил `lint` (read-only audit per CLAUDE.md §4.5).

**Проверено:**
1. **Каталог vs файлы.** `decisions/` 42 файла = D-001..D-042 в `index.md` ✓. `questions/` 40 файлов = 40 entries в index ✓. `entities/` 6, `concepts/` 8 — соответствуют index ✓.
2. **Schema-зоны.** `index.md`, `log.md`, `queue.md`, `backlog.md`, `CLAUDE.md`, `AGENTS.md`→symlink — все на месте.
3. **Status integrity.** D-029 помечен `superseded-by D-041` и в `index.md`, и в queue.md итогах (41 accepted = 42 − 1 superseded) ✓.
4. **Q→D mapping.** Все 40 вопросов имеют либо `→ D-NNN`, либо явный DEFERRED (Q-E-36 → backlog.md) в queue.md ✓.
5. **Backlog discipline.** Q-E-36 содержит status / дату отложки / MVP-объём / триггеры пересмотра / связанные D ✓.
6. **Log schema.** Последние записи используют canonical op-set (`refactor`, `lint`); legacy `wave-N` остаются нетронутыми (append-only).
7. **Orphans.** Бесхозных страниц без бэклинков не обнаружено: все entity/concept ссылаются на D-NNN или Q-IDs; все Q-* ведут к D-* или backlog.

**Findings:** none — критических, средних или мелких расхождений между index/queue/files/decisions не выявлено.

**Не изменялось:** lint read-only; ни одной content-страницы не редактировано.

**Cross-refs:** `index.md`, `queue.md`, `backlog.md`, `CLAUDE.md` §4.5/§6.4.

## [2026-05-10] refactor | tech-spec invariants block + retention table coverage fix

**Trigger:** cross-review tech-spec-draft.md против git-истории 2026-05-09…2026-05-10 выявил повторяющийся pattern drift'а tech-spec ↔ D-файлы (3 волны фиксов: `7abeb0b`, `f0de8b2`, `3af9608`, `d85e3f5`). Текущая регрессия: §10.4 retention-таблица не покрывала все таблицы из D-006 schema, расширенной в `f0de8b2`.

**Изменения:**
1. `research/tech-spec-draft.md` §10.4 — retention-таблица расширена до полного coverage D-006: добавлены `dedup_hits`, `job_outputs`, `run_outputs`, `prompt_versions`, `onboarding_events` (audit.db); `users`, `pending_users`, `pending_confirms`, `inbox_hint_cache`, `fsm` (sessions.db); `Inbox-WIKI/raw/media/_staging/` (24h staging cleanup из §8).
2. `research/tech-spec-draft.md` §0 — новый блок «Edit invariants (mandatory pre-commit checklist)»: 8 grep-проверяемых INV-правил (D-006 coverage, job kinds → priority lanes, backup vs D-037, idempotency location, identity vocabulary, auth isolation, NL-only lifecycle, tech-spec ≠ SSoT) + verification ritual из 6 шагов перед commit'ом.
3. `CLAUDE.md` §6 п.8 — новое правило: aggregator-страницы (≥3 D-файла) ОБЯЗАНЫ нести `## 0. Edit invariants` блок с closed checklist'ом. Превращает silent assumption «помню SSoT» в воспроизводимый ритуал.

**Цель:** закрыть pattern повторяющегося drift'а структурно — invariants-блок виден при каждом редактировании, checklist обновляется одновременно с появлением нового D-файла.

**Cross-refs:** D-006, D-018, D-037, D-041; commits `f0de8b2`, `3af9608`.

## [2026-05-10] refactor | tech-spec critical findings closed structurally

**Trigger:** review pass выявил 3 critical-уровня gap в tech-spec-draft.md:
1. `reminder_job` kind использовался в §6 fast-path без mapping на priority-lane из §3.
2. `job_outputs` / `run_outputs` упоминались в §2 без schema sketch (открытый класс багов «таблица упомянута, структура не определена»).
3. (Уже закрыт коммитом 1ae9dd4 — coverage retention-таблицы под D-006.)

**Изменения:**
1. §3 — добавлена closed taxonomy «Job kinds → priority lanes» (8 столбцов: kind, lane, lane id, CLI?, WIKI workspace, DLQ, Timeout, источник). Покрывает все kinds: `interactive`, `reminder_job`, `wiki_job`, `ingest_job`, `digest_job`, `*_purge`, `db_snapshot`. Закрывает Critical #2.
2. §2 «Прочее» — добавлены минимальные schema sketches для `job_outputs` и `run_outputs` (колонки + типы + retention). Полные DDL остаются SSoT в D-020/D-025. Закрывает Critical #3.
3. §0 — INV-2 переписан в форме closed-table requirement (любой новый kind = одновременная правка §3 + §2 Pydantic union + §10.4 retention). Добавлен INV-9 (audit/sessions tables → schema sketch invariant). Verification ritual расширен с 6 до 7 шагов, шаг #2 заменён на grep-проверяемое правило.

**Цель:** сделать оба класса бага structurally невозможными — новый job kind без §3 строки или новая audit-таблица без §2 sketch'а провалит verification ritual перед commit'ом.

**Cross-refs:** D-002, D-006, D-019, D-020, D-021, D-025; commits `1ae9dd4`, `f0de8b2`.
