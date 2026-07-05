---
feature: classifier-v2
bd_id: aisw-xi8
module_id: M-CLASSIFIER-STAGE0
status: stable
date: 2026-07-03
risk: high
evidence: strong
open_questions: []
fr:
  - FR-1 (taxonomy) — Intent enum (schema.py:56-65) MUST become the closed 6-member list wiki|job|web|chat|admin|unknown; distilled_payload carries the sub-slots — wiki.action ∈ ingest|query|lint|catalog; job.action ∈ create|cancel|list|reschedule; job.kind ∈ once|recurring|check_in|digest (for create); plus time_expr/schedule_expr, text, needle. ClassifierResult shape (dict[str,Any] payload) is already sufficient — no result-model change.
  - FR-2 (single classifier SSoT) — Haiku is the ONLY intent classifier. The classifying Python forks MUST be removed — _RECURRING_KEYWORDS (pipeline.py:415) + its punt (pipeline.py:1562), _DIGEST_DISABLE_RE/_DIGEST_RESCHEDULE_RE/_detect_digest_action (pipeline.py:454-480). _run_text_pipeline becomes a flat intent→subsystem switch.
  - FR-3 (parsers stay as validators) — dateparser time_parse (D-010), parse_recurrence, _extract_hhmm (pipeline.py:483), _extract_lead_minutes (pipeline.py:508) stay in Python as parameter validators over classifier payload slots; they no longer decide intent.
  - FR-4 (job/once) — job.create kind=once preserves today's reminder flow end-to-end: parse_time(prefer_future) validation, confirm keyboard, ReminderPayload(kind='reminder_job') + DateTrigger, plain-TG deterministic firing (firing.py fire_job), lead-time pre-reminder.
  - FR-5 (job/recurring) — kind=recurring schedules a fixed-text cron delivery: the user's verbatim reminder text is sent on a CronTrigger with NO Claude CLI run in the firing path (byte-identical delivery every fire; med-reminder determinism for elderly users).
  - FR-6 (job/check_in) — kind=check_in schedules a cron_user-style CLI run (cron_user.py → PriorityJobQueue Lane.CRON_WRITE → consumer.py) where the bot ASKS the user a generated question on schedule; a CLI failure at fire time MUST degrade to a deterministic ru fallback message, never silence.
  - FR-7 (job/digest) — kind=digest preserves the existing digest-create flow (recurrence confirm, DigestPayload, wiki_scope extraction via extract_wiki_names, fire_digest_job), now reached via the classifier instead of keyword regexes.
  - FR-8 (job management) — job.action=cancel|list|reschedule operates on jobs.db scoped to owner_telegram_id (excluding system kinds like purge): list renders human-readable schedules (reuse humanize_recurrence); cancel/reschedule resolve the target job by needle matching over payload text; >1 match → ru disambiguation list; 0 matches → ru "not found + list"; cancel (destructive) REQUIRES an inline confirm button before mutating.
  - FR-9 (chat-trap negatives) — the chat intent carries explicit negative rules in the prompt (first-person diary fact → wiki/ingest; knowledge question → web; cook-from-my-data → wiki/query), each backed by regression-corpus cases (#50, #53 fixes).
  - FR-10 (sub-threshold safety) — whatever Q1 resolves to, a below-threshold classification MUST NOT fall through to the generic root runner with write access for job/admin messages; the resolved policy is implemented as an explicit, tested branch (kills defect class #78/#96).
  - FR-11 (wiki/catalog) — «покажи мои вики» phrasings classify as wiki(action=catalog) and produce the WIKI list (today's unknown→Stage-1a list_wikis mechanics may be reused); they never classify as admin.
  - FR-12 (verbatim payload language) — all free-text payload slots (time_expr, schedule_expr, text, needle) MUST stay verbatim in the user's language; the prompt forbids translation and the regression eval asserts it (draft-prompt defect: fragments were translated to English).
  - FR-13 (regression harness) — the 100-question corpus + eval script are committed to the repo and runnable as `make classifier-regress` against the real Haiku backend; every classifier.md change requires a full-corpus run before commit; accuracy gate ≥ the measured v2-draft baseline (100/100 intent; 99/100 incl. action+kind).
  - FR-14 (prompt semver) — prompts/classifier.md bumps to semver 2.0.0 with a CHANGELOG entry describing the taxonomy swap (discipline already present at classifier.md:1-13).
  - FR-15 (jobs.db compatibility) — existing scheduled rows (kinds wiki_run|digest|cron_user|purge|reminder_job, payloads.py:56-106) keep validating and firing unchanged; storage kinds are NOT renamed to match classifier kinds; new payload kinds are additive.
  - FR-16 (web carve-out preserved) — intent=web keeps the aisw-dqz Path-B behaviour: WebSearch-enabled read-only run config, no WIKI add-dir (__main__.py:497-503).
  - FR-17 (admin unchanged semantics) — intent=admin keeps the safe declining reply (ACK_ADMIN_RU, pipeline.py:1446-1453); no root-runner execution.
  - FR-18 (observability) — tg.pipeline.classify.done and downstream anchors keep their structured shape; the intent field carries new values and classify.done additionally logs action/kind; every new dispatch branch has a log anchor (job.list/job.cancel/job.reschedule/check_in.fired etc.).
nfr:
  - NFR-1 — TDD (RED before GREEN); make total-test green; coverage ≥80% core; mypy --strict clean; grace lint 0 issues.
  - NFR-2 — Deterministic firing: no LLM call in the delivery path of once/recurring reminders (plain TG send); check_in is the only new kind allowed a CLI run at fire time.
  - NFR-3 — Ru-only user-facing strings (D-032): disambiguation lists, confirms, fallbacks.
  - NFR-4 — MODULE_CONTRACT + MODULE_MAP updated for every touched module; knowledge-graph.xml + verification-plan.xml refreshed via grace-refresh.
  - NFR-5 — Regression corpus ≥100 labelled cases including the 7 broken clusters from the simulation and the canonical recurring-negative («дай мне новости про улов карасей и ежедневный котировки акций» = one-shot web); every future classifier.md change re-runs the full corpus.
  - NFR-6 — No added latency: dispatch stays a single Haiku call per message; no extra LLM round-trips introduced by the flat switch.
  - NFR-7 — No new env/settings knobs; confidence threshold stays a module constant (precedent: REMINDER_CONFIDENCE_THRESHOLD, pipeline.py:413).
constraints:
  - Intent enum + ClassifierResult live in src/ai_steward_wiki/classifier/schema.py:56-78; stage0.py falls back to Intent.UNKNOWN (stage0.py:153,226) — the "unknown" string value survives the rename, fallback logic untouched.
  - Every classifier-intent branch in the dispatcher (measured): pipeline.py:616 (_ROUTABLE_INTENTS={WIKI_INGEST,UNKNOWN} — gates hint fast-path AND Sonnet-router branch), :1148 (SMALLTALK), :1167 (REMINDER + 0.85 gate), :1186 (DIGEST + 0.85 gate), :1446 (ADMIN), :1462-1502 (streaming/generic runner tail). __main__.py adapter branches: :460 (Intent.WIKI_QUERY adaptive scoping, aisw-o6m) and :501/:1374 (Intent.WEB_TASK WebSearch config, aisw-dqz) — must be re-anchored to (wiki,action=query) and web; the WikiRunner Protocol currently threads only `intent: Intent` (pipeline.py:701-714), so action needs a threading decision at design time.
  - Storage kinds ≠ classifier kinds: payloads.py discriminated union (wiki_run|digest|cron_user|purge|reminder_job) is jobs.db SSoT — renaming any Literal breaks parsing of persisted rows (extra='forbid'); only additive union members are allowed.
  - Firing infrastructure to reuse: firing.py create_reminder_job/fire_job (DateTrigger, plain TG, user_state guard aisw-z0s), create_digest_job/fire_digest_job (CronTrigger, 3-strike auto-disable), cron_user.py create_cron_user_job + consumer.py CLI drain loop (Lane.CRON_WRITE, concurrency=1, timeout 600s).
  - Confirm categories are strings persisted in sessions.db pending rows and dispatched in on_confirm_callback (pipeline.py:2265-2300; categories route_ingest|reminder|digest at :1294/:1657/:1758) — in-flight rows survive the deploy restart, so old category names must remain dispatchable (or be drained); new categories (e.g. job_cancel) are additive.
  - RouterIntent (inbox/router.py:77-82: route|create_wiki|list_wikis|clarify|reject) is a SEPARATE enum for Stage-1a decisions — out of scope, untouched. Hint fast-path thresholds (inbox/hint_match) and adaptive scope resolution (wiki/scope.py, aisw-o6m) keep deciding WHICH wiki, not intent.
  - tg/output.py:89 OutputKind Literal["reply","digest","ingest_report"] is a delivery kind — untouched. migration/config.py:162 ("reminder"→"generic") maps ai-steward planner categories in the one-off import subsystem — untouched (re-verify at design).
  - prompts/classifier.md is at semver 1.4.0 with an in-file CHANGELOG (classifier.md:1-13); the draft 2.0.0 prompt exists at /home/bgs/.claude/jobs/226e4379/tmp/classifier_minimal.md (semver 2.0.0-draft, verified) and must be productionised into the repo file.
  - Deploy: Python + prompt change together → atomic service restart on vpn-2 (TTY-sudo constraint, memory project_vps_deploy); md-only hot-reload is NOT sufficient for this feature.
  - Breaking surface (measured 2026-07-03): 6 src modules genuinely branch on or emit classifier intents (classifier/schema.py, classifier/stage0.py, tg/pipeline.py, __main__.py, inbox/route.py payload round-trip, wiki/runner.py comment); ~20 test files pin old intent names — tests/unit/classifier/ (4 files incl. test_cli_envelope, test_fake_runner), tests/unit/tg/test_pipeline*.py (~14 files), tests/unit/test_main_runner_adapter.py, tests/integration/classifier/test_real_cli.py. Grep hits in scheduler/storage/migration/auth tests are storage-kind or role literals, not classifier intents.
risks:
  - R-1 (HIGH) — taxonomy swap regresses phrasings outside the corpus (single-persona corpus). Mitigation — committed regression harness (FR-13) + confirm flows on all destructive/scheduling paths + threshold policy (Q1); corpus expansion to more personas in LATER.
  - R-2 (HIGH) — sub-threshold fallback re-creates the root-runner write-access degradation (#78/#96) under the new names. Mitigation — FR-10 makes the policy an explicit tested branch; RED test asserting a low-confidence job never reaches the generic runner.
  - R-3 (MEDIUM) — in-flight pending confirms (sessions.db) across the deploy break on_confirm_callback dispatch. Mitigation — keep old category strings dispatchable for one release or drain pending rows at deploy; test with a persisted old-category row.
  - R-4 (MEDIUM) — external log consumers expect old intent strings: e2e matrix scorer (project memory: 57-scenario judge via journald), hang-diagnostics tooling. Mitigation — LATER item + rollout note; log field NAMES unchanged, only values.
  - R-5 (MEDIUM) — check_in fire-time CLI failure leaves an elderly user without the scheduled question. Mitigation — FR-6 deterministic ru fallback (send the stored question/topic verbatim) + consumer error branch already messages on exit!=0.
  - R-6 (LOW) — Haiku translates payload fragments to English (observed in draft runs). Mitigation — FR-12 prompt rule + verbatim assertions in the eval.
  - R-7 (LOW) — mid-rollout mixed state impossible to hot-patch: prompt hot-reload without the Python switch (or vice versa) mis-dispatches. Mitigation — single-commit atomic change + restart runbook step.
scope_in:
  - src/ai_steward_wiki/classifier/schema.py — Intent enum v2 (+ payload slot validation policy per Q1/design).
  - prompts/classifier.md — semver 2.0.0, artifact-anchored taxonomy, chat-trap negatives, verbatim rule, CHANGELOG.
  - src/ai_steward_wiki/tg/pipeline.py — flat intent→subsystem switch; removal of classifying regexes; job-management handlers (list/cancel/reschedule + disambiguation + confirm); sub-threshold branch.
  - src/ai_steward_wiki/storage/jobs/payloads.py — additive payload(s) for recurring/check_in (shape per Q2).
  - src/ai_steward_wiki/scheduler/firing.py (+ cron_user.py/consumer.py touchpoints) — recurring fixed-text firing; check_in scheduling/firing.
  - src/ai_steward_wiki/__main__.py — adapter intent re-anchoring (web config, query scoping), wiring of new handlers.
  - tests/unit/{classifier,tg,storage,scheduler}/ — renamed-intent updates (~20 files) + new RED tests per FR.
  - Regression harness: committed corpus + eval + Makefile target classifier-regress (placement per design).
  - docs XML (knowledge-graph, verification-plan, development-plan) via grace-refresh.
scope_out:
  - inbox/router.py RouterIntent, Stage-1a router prompts, hint fast-path thresholds, wiki/scope.py adaptive scoping (aisw-o6m) — the "which WIKI" layer is untouched.
  - tg/output.py OutputKind, digest presentation/cards, digest section toggles.
  - migration/ subsystem (ai-steward import) and auth/ (role literals are unrelated).
  - i18n / non-Russian user-facing strings (D-032 stands).
  - Off-repo tooling: telegram-e2e-runner scenario matrix, log_watch scorer (external consumers — see LATER).
scope_later:
  - Update the e2e matrix scorer + log_watch tooling to the new intent values; re-run the 57-scenario matrix.
  - Multi-persona expansion of the regression corpus (current corpus is one family persona).
  - job.action=reschedule for recurring jobs if Q3 resolves to once-only in MVP.
  - Digest-control long tail (#35/#91/#99 fragile phrasings) beyond what job.cancel/reschedule covers.
---

# Discovery — classifier v2.0: 6 artifact-anchored intents (aisw-xi8)

## Intent analysis (sequential-thinking, 10 thoughts, risk=high)

**Что просили буквально:** заменить 9-интентную схему Stage-0 на 6 artifact-anchored интентов
(wiki/job/web/chat/admin/unknown), сделать Haiku единственным классификатором интентов, убрать
классифицирующие Python-форки (`_RECURRING_KEYWORDS`-punt, `_detect_digest_action`), плоский
switch intent→подсистема, новые job.kind `recurring` (детерминированная cron-доставка фиксированного
текста, без CLI) и `check_in` (бот сам задаёт сгенерированный вопрос по расписанию), управление
задачами (cancel/list/reschedule) с needle-matching, disambiguation и confirm-кнопками.

**Реальная цель:** семейно-пригодный бот — детерминированные медицинские напоминания для
75-летних, ноль потерь дневниковых записей в chat-ловушке, и SSoT классификации: одна точка
принятия решения (Haiku) вместо трёх конкурирующих (Haiku + два слоя регэкспов), которые в
симуляции воевали друг с другом (recurring проглатывался digest-регэкспами).

**Ключевое различение, найденное в коде:** «классифицирующий регэксп умирает, регэксп-валидатор
живёт». `_RECURRING_KEYWORDS`/`_DIGEST_DISABLE_RE` решают ИНТЕНТ — удаляются; `_extract_hhmm`,
`_extract_lead_minutes`, dateparser (D-010), parse_recurrence извлекают ПАРАМЕТРЫ из уже
классифицированного сообщения — остаются как валидаторы поверх payload-слотов.

**Что НЕ было сказано (закрыто в discovery):** jobs.db-миграция не нужна — storage-kinds
(`reminder_job`, `digest`, `cron_user`…) отделены от классификаторных kinds, union расширяется
аддитивно; audit-БД интенты не хранит (проверено grep'ом — только structlog); ClassifierResult
менять не нужно (payload уже `dict[str,Any]`). Осталось 4 настоящих open questions (frontmatter).

## Evidence base (primary, 2026-07-03 session)

1. **100-question family-persona simulation** против реального prod-промпта 1.4.0: ~93/100 intent
   accuracy; 7 сломанных кластеров — recurring/check-in проглочены digest (#13, #43, #54, #57, #71),
   нет управления задачами (#89, #90), chat-ловушка теряет дневниковые записи и knowledge-вопросы
   (#50, #53), хрупкое управление сводкой (#35, #91, #99), суб-пороговый reminder (conf<0.85)
   падает в generic root runner с write-доступом (#78, #96).
2. **Draft 6-intent prompt** `/home/bgs/.claude/jobs/226e4379/tmp/classifier_minimal.md`
   (semver 2.0.0-draft, существование и содержание верифицированы): 100/100 intent accuracy
   (99/100 с action+kind; единственный промах — «Покажи мои вики» → wiki с пустым action) на том же
   корпусе после chat-trap-патча. Корпус: `.../tmp/questions.json`; eval: `eval_minimal_v2.py`;
   raw-прогоны: `.../tmp/min100v2/`.
3. **Одобренные пользователем решения** (best-questions/best-approach): принцип intent=artifact;
   recurring-семантика заякорена на императивах боту (прилагательные регулярности на
   существительных — НЕ подписка: «дай мне новости про улов карасей и ежедневный котировки акций»
   = one-shot web); матрица 2×2 trigger×content схлопывается в job.kind; каждый change
   classifier.md — полный regression-прогон корпуса.

## Code touchpoints (все верифицированы Read/Grep в этой сессии)

1. `classifier/schema.py:56-65` — Intent enum (9 членов); `:68-78` ClassifierResult.
2. `classifier/stage0.py:153,226` — fallback intent=unknown (retry-политика aisw-l3h) — значение
   «unknown» переживает переименование.
3. `tg/pipeline.py:616` — `_ROUTABLE_INTENTS={WIKI_INGEST, UNKNOWN}` (gate hint-fastpath + Sonnet
   router); `:1148` SMALLTALK; `:1167-1186` REMINDER/DIGEST + порог 0.85 (`:413`); `:1446` ADMIN;
   `:1462-1502` streaming/generic-runner хвост; `:415/:1562` recurring-punt; `:454-480`
   digest-control регэкспы; `:2265-2300` confirm-dispatch (категории route_ingest/reminder/digest,
   создаются в `:1294/:1657/:1758`).
4. `storage/jobs/payloads.py:56-106` — discriminated union 5 kinds (extra='forbid').
5. `scheduler/firing.py` — fire_job (plain TG, без Claude) / fire_digest_job (CronTrigger,
   3-strike); `scheduler/cron_user.py:108,135` + `scheduler/consumer.py` — CLI-запуск по cron
   (Lane.CRON_WRITE, concurrency=1) — база для check_in.
6. `__main__.py:460` (WIKI_QUERY scoping aisw-o6m), `:501/:1374` (WEB_TASK carve-out aisw-dqz).
7. `inbox/router.py:77-82` — RouterIntent (отдельный enum, out of scope).
8. `prompts/classifier.md:1-13` — semver 1.4.0 + CHANGELOG-дисциплина.
9. `Makefile` — таргета classifier-regress нет (help/install/lint/test*/grace-lint/inv-lint/
   test-cov/total-test/clean) — харнесс создаётся с нуля.
10. `docs/knowledge-graph.xml` — затронутые модули: M-CLASSIFIER-STAGE0, M-TG-PIPELINE-CLASSIFIER,
    M-SCHEDULER-FIRING, M-SCHEDULER-CRON-USER, M-SCHEDULER-CONSUMER, M-STORAGE-JOBS.

## Measured breaking surface

1. **src:** 6 модулей реально ветвятся на классификаторных интентах или эмитят их значения
   (schema, stage0, pipeline, __main__, inbox/route payload-roundtrip, wiki/runner коммент).
2. **tests:** ~20 файлов пиннят старые имена — tests/unit/classifier/ (4), tests/unit/tg/
   test_pipeline*.py (~14, лидер test_pipeline_router.py: 13 Intent-ссылок),
   test_main_runner_adapter.py (5), tests/integration/classifier/test_real_cli.py.
3. **Ложные срабатывания grep'а** (НЕ трогать): scheduler/storage тесты («digest»/«reminder_job» —
   storage kinds), auth-тесты («admin» — роль), migration-тесты (planner-категории ai-steward).

## Best-practice validation (web, подтверждает уже принятый дизайн)

1. Иерархическая схема «широкий интент + slots» (intent + action/kind в payload) — стандартная
   рекомендация для LLM-классификаторов со structured JSON output: [Vellum — LLM Intent Classification for Chatbots](https://www.vellum.ai/blog/how-to-build-intent-detection-for-your-chatbot).
2. «Merge intents that are often confused» — ровно наш кейс (reminder/digest/web_task сливаются в
   job/web по артефакту); регулярный пересмотр taxonomy как норма: [Label Your Data — Intent Classification 2026](https://labelyourdata.com/articles/machine-learning/intent-classification).
3. Regression-тестирование классификатора на фиксированном labelled-корпусе при каждом изменении
   («failure flywheel» — инциденты становятся тест-кейсами; наши #13/#50/#78 → corpus cases):
   [Quidget — Intent Classification for Chatbots](https://quidget.ai/blog/ai-automation/intent-classification-for-chatbots-guide/).

## Risk = high — обоснование

Единая точка классификации ВСЕГО семейного трафика + деструктивные операции над jobs.db
(cancel) + связанный Python+prompt деплой с рестартом сервиса: ошибка ломает одновременно
напоминания о лекарствах, дневник и сводки для всех пользователей.

## Resolved decisions (Gate 3, 2026-07-03, approved by user)

1. **Q1 (sub-threshold policy):** single module-constant threshold; below-threshold `job`/`admin` → deterministic ru clarification reply; `wiki`/`web`/`chat` proceed (non-destructive). The write-capable generic root runner is never reachable for sub-threshold job/admin (FR-10).
2. **Q2 (storage payload shape):** two NEW payload classes in the jobs.payload discriminated union (additive; no Alembic migration; extra='forbid' preserved).
3. **Q3 (reschedule depth):** MVP covers BOTH one-shot (move DateTrigger) and recurring (rewrite CronTrigger/Recurrence) — digest reschedule/cancel is a measured defect cluster (#35/#91/#99).
4. **Q4 (regression harness policy):** `make classifier-regress` is a MANDATORY MANUAL gate before any prompts/classifier.md commit, documented in the prompt CHANGELOG discipline; NOT wired into total-test/CI (100 real Haiku calls per run).
