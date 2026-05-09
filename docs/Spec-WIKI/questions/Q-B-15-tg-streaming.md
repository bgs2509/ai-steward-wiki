# Q-B-15: Стриминг Claude → TG

**Tier:** B
**Источник:** [overview §9 п.15](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Chunked-edit одного сообщения vs serial-сообщения; политика при rate-limit 1 msg/sec.

## Варианты

1. **A. Edit одного сообщения.** Меньше нотификаций, но `editMessage` тоже rate-limited (~1/sec).
2. **B. Serial-сообщения по N токенов.** Больше нотификаций, проще логика.
3. **C. Гибрид.** Стримим edit-ом, при достижении 4096 chars — новое сообщение.

## Решение

- [x] **Вариант D** — hybrid edit + chain split при 4096:
  - **Placeholder msg** из [D-021](../decisions/D-021-timeouts-kill-policy.md) («⏳ работаю над `<title>`…» + кнопка `❌ Отмена`) — стартовая точка stream'а.
  - **Source:** Claude CLI `--output-format stream-json` (или text-stream); buffer accumulator на стороне бота.
  - **Throttle:** `editMessageText` раз в **1.5s** при условии ≥50 chars нового content в буфере (debounce-pattern).
  - **Chain split:** при буфере ≥3900 chars — finalize current msg (убрать `⏳`), открыть новое со счётчиком `(2/N)`, продолжить stream в него; `active_runs.inline_message_id` обновляется на актуальный msg.
  - **Backpressure:** на `429` — exponential backoff edit-цикла, accumulate продолжается; final flush гарантирован.
  - **Final:** последний edit добавляет footer `(N/N)`; затем D-mode переключается на политику [D-025](../decisions/D-025-output-size.md) (если total >10000 — `send_document` с full output).
  - **На `/cancel`:** final-flush буфера + маркер «❌ отменено».
  - **HTML escape safety:** boundary split не разрывает `<...>` теги; partial buffer может содержать «hanging» теги — закрывать на финализации сегмента.
  - **Per-category override:** будущая оптимизация (Вариант E) — blocking для `inbox_classify`/`tracker_question`, streaming для `digest_job`/`wiki_job`. В MVP единый D-mode для всех Claude-вызывающих категорий.
- [x] оформлено как [D-026](../decisions/D-026-tg-streaming.md)

## Связанные

1. [Q-B-16: Размер вывода](Q-B-16-output-size.md)
