# D-026: Claude → TG streaming — edit-mode + chain split при 4096

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-B-15](../questions/Q-B-15-tg-streaming.md), overview §9.15, [D-021](D-021-timeouts-kill-policy.md), [D-024](D-024-digest-format.md), [D-025](D-025-output-size.md)

## Проблема

Claude CLI поддерживает streaming, но TG ограничивает: 4096 chars/msg, ~1 edit/sec, 30 ops/sec bot-wide. Без streaming — длинная задержка (5–60s) без сигнала «бот работает». С плохим throttle — flood control / vibration spam.

## Варианты

1. **A — Blocking (no streaming).**
2. **B — Edit одного сообщения, truncate на 4096.**
3. **C — Serial-сообщения по N токенов.**
4. **D — Hybrid: edit + chain split при ≈4000.** ⭐
5. **E — D + per-category heuristic** (streaming только для long).

## Выбор

**Вариант D (hybrid edit + chain split). MVP применяет ко всем Claude-вызывающим категориям; per-category override (Вариант E) — отложено как оптимизация.**

### Stream source

1. **CLI flag:** `--output-format stream-json` (preferred — структурированные events) или text-stream fallback.
2. **Bot side:** async iterator над stdout; accumulate в local buffer.

### Placeholder msg

1. Берётся из [D-021](D-021-timeouts-kill-policy.md): «⏳ работаю над `<title>`…» + inline-кнопка `❌ Отмена`.
2. `active_runs.inline_message_id` указывает на текущий edit-target. На chain split обновляется.

### Throttle

1. **Tick interval:** edit раз в **1.5s**.
2. **Min delta:** ≥50 chars нового content в буфере с last-edit'а; иначе skip tick (debounce).
3. **Implementation:** `asyncio.Event` + `asyncio.sleep(1.5)` loop в parallel task; main task пишет в buffer.

### Chain split при 4000 chars

1. На приближении к 4000 chars (96-char буфер на closing tags + footer):
   1. Finalize current msg: edit с финальным content (закрыть все open HTML теги) + footer `(N) ... ⏳ продолжаю в следующем …`.
   2. Открыть новое сообщение: `sendMessage` с `(<N+1>) ⏳ работаю…`, inline-кнопка `❌ Отмена` (та же).
   3. `active_runs.inline_message_id ← new_id`.
   4. Переключить edit-target на новое сообщение.
2. Boundary внутри буфера: предпочитать `<b>` header / blank line / sentence boundary; не разрывать `<...>` теги.
3. **HTML balance:** open tags на конце сегмента — закрыть в financialize; те же — снова открыть на старте нового сегмента.

### Backpressure

1. На `429 Too Many Requests` (TG flood control): exponential backoff на edit-loop (1.5s → 3s → 6s, max 30s); accumulate буфера продолжается без потерь.
2. На сетевые timeout'ы edit'а — retry up to 3 times, потом отдать в [D-019](D-019-cron-failure-mode.md) как TransientError всему run'у.
3. **Final flush гарантирован:** в `finally` stream'а — один последний edit с полным content (даже если throttle skip'ал последние 1500ms).

### Final transition

1. После окончания stream'а:
   1. Убрать `⏳` индикатор из последнего msg.
   2. Добавить footer `(N/N)`.
   3. Применить policy [D-025](D-025-output-size.md):
      - Если total `≤ 10000` — оставляем chain как есть.
      - Если `> 10000` — пост-step Haiku-саммари + edit первого msg в саммари + `send_document` full output.
2. `audit.db.run_outputs.delivery` ← `inline` | `split` | `document` (по итоговому routing'у).

### Cancellation

1. На `/cancel` ([D-021](D-021-timeouts-kill-policy.md)) или клик на inline-кнопке:
   1. SIGTERM Claude CLI subprocess.
   2. Final-flush текущего буфера в edit-target msg.
   3. Добавить footer «❌ отменено».
2. Partial output **сохраняется** в `data/runs/` (с пометкой `cancelled=true` в frontmatter).

### HTML safety

1. Stream-buffer может содержать «hanging» tags (`<b>foo` без `</b>`). На каждом edit'е перед send: пройти simple HTML balancer — закрыть всё открытое в конце snapshot'а.
2. Special chars `<`, `>`, `&` в content — escape **до** добавления в buffer (чтобы balancer работал на final tags only).

### Per-category override (отложено)

`payload.streaming_mode` может быть:
- `auto` (default — D-mode).
- `blocking` (для `inbox_classify`, `tracker_question` — ETA <2s, streaming overhead не нужен).

В MVP — все категории `auto`. Реализация per-category — после measure'а реальной latency.

## Последствия

1. Live progress UX для всех Claude-вызовов.
2. Backpressure-safe; final flush гарантирован.
3. Согласовано с D-021 (placeholder msg, cancel) и D-025 (final routing).
4. Запреты:
   1. **Не превышать throttle 1.5s** — flood control.
   2. **Не разрывать `<...>` теги** на split'е.
   3. **Не пропускать final flush** в `finally`.
5. Будущее: per-category `streaming_mode` (Вариант E) после latency-measurement.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-026-tg-streaming.md` (когда финализируется)
