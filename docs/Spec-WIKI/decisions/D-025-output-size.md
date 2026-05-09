# D-025: Claude output size — threshold-based hybrid

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-B-16](../questions/Q-B-16-output-size.md), overview §9.16, [D-006](D-006-state-storage-layout.md), [D-021](D-021-timeouts-kill-policy.md), [D-024](D-024-digest-format.md), [D-026](D-026-tg-streaming.md)

## Проблема

Claude Stage-1 может вернуть длинный output, превышающий TG лимит 4096 chars/msg. Нужен policy: усечение / пагинация / саммари + всегда сохранять full output для аудита и replay.

## Варианты

1. **A — Truncate + ссылка.**
2. **B — Pagination в TG.**
3. **C — Только саммари + файл.**
4. **D — Threshold-based hybrid.** ⭐
5. **E — Always send_document.**

## Выбор

**Вариант D (threshold hybrid).**

### Threshold table

| Размер output | Действие |
|---------------|----------|
| `≤ 3500` chars | Отправить целиком в TG в одном сообщении (596-char буфер на HTML escape inflation). |
| `3500 < N ≤ 10000` | Split по semantic boundary; max 3 сообщения с continuity-маркерами `(1/3)`, `(2/3)`, `(3/3)`. |
| `> 10000` | Post-step саммари (≤1500 chars Claude-generated abstract) в TG + `send_document` с full output как `<run_id>.md` + inline-кнопка `📄 Полный текст`. |

### Semantic boundary (для split)

1. **Приоритет:** `<b>` HTML header → blank line → end of paragraph (`. \n`) → end of sentence (`. `).
2. **Не разрывать `<...>` теги** — boundary только между closed tags.
3. **HTML balance check:** на конце сегмента закрыть все открытые теги, на начале следующего — открыть нужные (auto-close + auto-reopen).

### Post-step саммари (>10000)

1. После окончания Stage-1 — ещё один Claude-вызов («Summarize the following in ≤1500 chars in Russian, preserve key actions and numbers: \<output\>»).
2. **Cost mitigation:** post-step Haiku Stage-0, не Sonnet — дешевле; уже paid через subscription ([D-013](D-013-claude-cli-auth.md)).
3. Саммари идёт в TG по логике `≤3500` (как одно сообщение).
4. Full output всегда attach'ится как `.md` document.

### Always-persist

Full output **всегда** сохраняется на диск, независимо от threshold:

```
<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md
```

Header файла:
```markdown
---
run_id: 01HXYZ…
wiki: Health-WIKI
chat_id: 123456789
ts: 2026-05-09T08:12:34Z
category: wiki_job
prompt_sha256: …
output_sha256: …
size: 12345
duration_sec: 4.2
---

<output text>
```

### Index

```
audit.db.run_outputs(
  run_id TEXT PRIMARY KEY,
  wiki TEXT NOT NULL,
  chat_id INTEGER,
  ts INTEGER NOT NULL,
  size INTEGER NOT NULL,
  path TEXT NOT NULL,
  output_sha256 TEXT NOT NULL,
  summary_used BOOL NOT NULL DEFAULT 0,
  delivery TEXT  -- inline | split | document
)
```

INDEX по `(chat_id, ts DESC)` для `/run last` lookup.

### Retrieval

1. **`/run <run_id>`** — `send_document` с полным `.md`-файлом из `data/runs/`.
2. **`/run last`** — последний run в этом chat'е (LIMIT 1 by `ts DESC`).
3. **Inline-кнопка `📄 Полный текст`** — добавляется автоматически в TG-сообщение для outputs `> 3500` chars.

### Streaming integration ([D-026](D-026-tg-streaming.md))

Streaming работает в edit-режиме до **4000 chars**; при превышении — chain split (новое сообщение `(2/N)`). Final size determines D-25 policy:
- Если итоговый размер `≤ 10000` — оставляем chain split.
- Если `> 10000` — после окончания stream'а: пост-степ саммари + finalize first msg как «вот саммари…» + send_document.

### Retention

`data/runs/<YYYY-MM-DD>/` — без auto-GC (Karpathy: raw/audit immutable). Disk usage будет аккумулироваться; manual cleanup или отдельная retention-policy — в Q-E-36 (backup) / Q-E-33 (audit PII).

## Последствия

1. Короткие answers — instant в TG; длинные — actionable summary + attachment.
2. Audit trail полный: любой output replay'абелен из `data/runs/`.
3. Mobile UX — приоритет mobile (≤3500 — без scrolling, summary — для больших).
4. Запреты:
   1. **Не пропускать сохранение в `data/runs/`** ни для одного output'а (audit invariant).
   2. **Не использовать Sonnet для post-step саммари** (overkill).
   3. **Не редактировать `data/runs/<…>.md`** post-write (immutable).
5. Будущее: retention-policy для `data/runs/` (например, compress >30d), cross-WIKI search по run_outputs.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-025-output-size.md` (когда финализируется)
