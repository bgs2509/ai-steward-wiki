# D-010: NL-парсинг времени — гибрид dateparser → Haiku fallback

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-A-05](../questions/Q-A-05-nl-time-parsing.md), overview §9.5, [D-009](D-009-classifier-engine.md)

## Проблема

Reminder-intent в Stage-0 ([D-009](D-009-classifier-engine.md)) требует превратить NL-фразу времени («завтра в 18:00», «через две пятницы», «каждый понедельник в 9») в ISO8601 с user timezone. Чисто-LLM-парсинг недетерминирован и галлюцинирует даты; чисто-rule-based падает на edge-cases.

## Варианты

1. **A. Локальный `dateparser`** — детерминированно, ~50ms, но None на edge-cases.
2. **B. LLM-парсинг внутри Haiku Stage-0** — универсально, но недетерминированно и сложно тестировать.
3. **C. Гибрид: `dateparser` → Haiku fallback** — best-practice индустрии.

## Выбор

**Вариант C.** Юзер подтвердил 2026-05-08.

## Архитектура

### Контракт Stage-0 Haiku (уточнение D-009)

Haiku возвращает **сырую** фразу времени, не парсит её сама:

```json
{
  "intent": "reminder",
  "reminder": {
    "title": "позвонить маме",
    "time_phrase": "завтра в 18:00",
    "repeat_phrase": null,
    "confidence": 0.92
  }
}
```

### Pipeline парсинга

1. **Step 1 — `dateparser.parse`**:
   ```python
   import dateparser
   dt = dateparser.parse(
       time_phrase,
       languages=["ru", "en"],
       settings={
           "TIMEZONE": user_tz,                # из roles.toml
           "RETURN_AS_TIMEZONE_AWARE": True,
           "PREFER_DATES_FROM": "future",
           "RELATIVE_BASE": now_in_user_tz,
       },
   )
   ```
2. **Step 2 — Haiku fallback** (если `dt is None` или ambiguous):
   - Узкий system-промпт: «Парсер времени. Текущее время `<ISO>`, TZ `<tz>`. Верни ISO8601 для фразы `<phrase>` или `null`».
   - `claude-haiku-4-5`, structured output `{when: ISO8601 | null, confidence: float}`.
3. **Step 3 — Stage-1 escalation**: если Haiku-fallback вернул `null` ИЛИ `confidence<0.85` ⇒ маршрут уходит в Stage-1 CLI Sonnet с промптом «уточни время у юзера».

### Repeat-phrase (cron)

1. Аналогично: `dateparser` сам не выводит cron. Сначала простой rule-based mapper (regex для «каждый день в HH:MM», «по будням», «каждый понедельник») → cron string.
2. На промах → Haiku fallback с промптом «верни cron expression 5-fields или `null`».
3. Cron записывается в `jobs.cron_expr` ([D-002](D-002-job-model-storage.md)).

### Timezone

1. SSoT user TZ — `roles.toml[<user>].timezone` (обязательное поле).
2. Default-fallback **запрещён** — отсутствие TZ ⇒ `/wiki_init` отклоняет онбординг.
3. Все datetime в `jobs.db` хранятся в UTC; user-TZ применяется только на ввод (parse) и вывод (TG-сообщения).

## Обоснование

1. Best-practice: Google Assistant, Todoist, Microsoft Recognizers-Text — все используют hybrid rule-based + ML fallback.
2. dateparser — лучший FOSS-парсер для русского (поддержка «завтра», «послезавтра», «через N дней/недель», нативные числительные).
3. Детерминизм 95%+ горячего пути → unit-тесты тривиальны (`assert parse("завтра в 18:00", tz="Europe/Moscow", now=...) == datetime(...)`).
4. Stage-0 Haiku не парсит время сама — снижается риск галлюцинаций (Haiku известна путаницей дней недели), упрощается system-промпт Stage-0.
5. Явная эскалация в Stage-1 на ambiguous — никогда не «угадываем» время молча.

## Последствия

1. Зависимость: `dateparser>=1.2` (зафиксировать `==` в production).
2. Появляется модуль `time_parser.py` с функциями `parse_time(phrase, user_tz, now) -> datetime | None` и `parse_repeat(phrase) -> CronExpr | None`.
3. Test fixtures: набор золотых фраз на русском (минимум 50 кейсов: today/tomorrow/relative/weekday/explicit-date/repeat).
4. `roles.toml` schema обязана содержать `timezone: str` (IANA, например `Europe/Moscow`).
5. Метрика `time_parser.fallback_rate` — KPI (target ≤5% для горячего пути).
6. Q-A-05 закрывается этим решением.

## Запреты

1. Не парсить время внутри Stage-0 Haiku — Haiku возвращает только сырую фразу.
2. Не использовать system clock без user-TZ — все вычисления относительны `now_in_user_tz`.
3. Не задавать default TZ (например UTC или Europe/Moscow) — отсутствие TZ юзера ⇒ ошибка онбординга.
4. Не конвертировать datetime в локальную TZ при записи в `jobs.db` — хранение в UTC, конвертация только на input/output.
5. Не использовать `parsedatetime` (устарел, плохо с русским) и не комбинировать несколько локальных парсеров — один rule-based + один LLM-fallback.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-nl-time-parsing.md` при финализации.
