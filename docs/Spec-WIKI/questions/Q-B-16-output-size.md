# Q-B-16: Размер вывода Claude

**Tier:** B
**Источник:** [overview §9 п.16](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Лимит на стриминг в TG (4096 chars), обрезка, сохранение полного лога куда (отдельный файл в WIKI? `data/runs/`?).

## Варианты

1. **A. Truncate + ссылка.** Первые 4096 chars в TG, полный вывод — `<wiki>/data/runs/<run_id>.md` со ссылкой.
2. **B. Pagination в TG.** Несколько сообщений; кнопка «дальше».
3. **C. Только саммари.** Claude сам делает короткое резюме для TG, полный вывод — в файл.

## Решение

- [x] **Вариант D** — threshold-based hybrid:
  - **`len(output) ≤ 3500`** → отправить целиком в TG (596-char буфер на HTML escape).
  - **`3500 < len(output) ≤ 10000`** → split по semantic boundary с continuity-маркерами `(1/3)`, `(2/3)`, `(3/3)`; max 3 сообщения в чат.
  - **`len(output) > 10000`** → пост-step саммари (Claude генерирует 1500-char abstract) + `send_document` с full output как `<run_id>.md` + inline-кнопка `📄 Полный текст`.
  - **Always:** full output сохраняется в `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md` + индекс `audit.db.run_outputs(run_id PK, wiki, ts, size, path, sha256, summary_used BOOL)`.
  - **Retrieval:** `/run <run_id>` шлёт document; `/run last` — последний run в этом чате.
  - **Граница HTML split:** boundaries выбираются по `<b>` headers / blank lines, не разрывая `<...>` теги.
- [x] оформлено как [D-025](../decisions/D-025-output-size.md)

## Связанные

1. [Q-B-15: Стриминг](Q-B-15-tg-streaming.md)
