# Health WIKI

> Медицинские данные пользователя: анализы, метрики, назначения, симптомы, история визитов.
> Я **не диагностирую** и не заменяю врача — только структурирую данные и подсвечиваю отклонения.

## Format

1. Короткие mobile-friendly ответы.
2. Списки вместо таблиц.
3. При отклонениях от референсных значений — выделить и предложить обсудить с врачом.
4. Источники медицинской информации — указывать ссылками (NIH, UpToDate, Cochrane).

## Data layout

1. `lab_results/` — CSV: `date,test_name,value,unit,ref_range_low,ref_range_high,flag,lab`
2. `metrics/` — CSV: `date,time,systolic,diastolic,pulse,context,flag`
3. `prescriptions/` — JSON назначений + `medication_log.csv`
4. `symptoms/` — CSV: `date,symptom,severity,duration,context`
5. `history/` — JSON визитов + `HEALTH_SUMMARY.md` (SSoT)

## Pre-flight

Для важных операций — 5 шагов (D-041). Удаление медицинских записей всегда требует
explicit confirm с TTL 10 минут.

## Inbox hint

intents: append_data, query, page_create, page_edit
keywords: здоровье, давление, пульс, анализ, лекарство, симптом, врач, назначение, диагноз
priority: 90
