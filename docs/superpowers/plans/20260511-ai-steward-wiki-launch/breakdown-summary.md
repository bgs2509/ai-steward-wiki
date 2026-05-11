# AI Steward Wiki — Launch Plan (post-MVP roadmap)

**Lifecycle:** temporary. Archived as-is after production cutover (do not maintain).
**Parent epic:** `20260510-ai-steward-wiki-mvp` (closed 17/17).
**Predecessors:** chunk-18 `M-RUNTIME-WIRING` (done, `9ebd475`), chunk-19 `M-TG-HANDLERS-WIRING` (done, `2acb542`).
**Policy:** pre-Beads draft. `bd_id` materializes only when a chunk is started via `bd create`.

## Цель

Закрыть путь от «бот стартует и ack'ает» (текущее состояние) до «бот реально отвечает Claude'ом и развёрнут в production».

## Чанки

### Chunk 20 — `M-TG-PIPELINE-CLASSIFIER`

**Scope:** заменить ack'и в `DefaultPipeline.on_text/on_voice` на полный flow Stage-0 Haiku Classifier → Inbox staging (с L2 dedup) → WikiRunner (Stage-1a/1b Sonnet) → `deliver_output` (hybrid size policy).

**Depends on:** `aisw-ps8` (chunk 19), `M-CLASSIFIER-STAGE0`, `M-WIKI-RUNNER`, `M-INBOX`, `M-TG-TEXT`.

**Exit criteria:**
1. `DefaultPipeline.on_text("привет")` → classify → stage → run → deliver, ровно один проход.
2. L2 idempotency: повтор текста в окне дедупится в Inbox.
3. `__main__.py` композирует Classifier/WikiRunner/Inbox.
4. ≥10 unit-тестов (happy path, L1/L2 dedup, classifier error, runner timeout).
5. Integration: реальный Claude CLI отвечает текстом ≤60s.
6. Coverage ≥80%, grace 0/0, INV 14/14, mypy strict.

**Rationale:** основной gap MVP-launch. Building blocks готовы, нужна композиция.

### Chunk 21 — `M-TG-PIPELINE-STREAMING`

**Scope:** `StreamEditor` (throttle 1.5s, Δ50, chain-split 4000) поверх long-running `WikiRunner` job'ов когда `estimated_duration_s > 5` (DEC-L2). Placeholder «Думаю…» редактируется in-place; final flush гарантирован даже на исключении.

**Depends on:** chunk 20, `M-TG-TEXT` (StreamEditor), `M-WIKI-RUNNER`.

**Exit criteria:**
1. Slow runner (10s mock) → ≥3 правки placeholder'а до финала.
2. Fast runner (1s) → single `deliver_output` без streaming.
3. Runner exception → final flush + audit.
4. Chain-split 4000 → новое сообщение с `(i/M)` footer.
5. ≥6 unit-тестов; HTML-safe boundaries; full gate.

**Rationale:** UX. 60s+ ответы без feedback'а воспринимаются как timeout.

### Chunk 22 — `M-TG-DOCUMENT-FULL`

**Scope:** mime-routing в `on_document` (DEC-L3) — `pdf → OCR`, `text/* → inline`, `image/* → photo path`, иначе вежливый reject. L2 dedup по `doc_sha256`. Filename PII tier-2 hash.

**Depends on:** chunk 20, `M-INBOX`, `M-OPS-PII`, `M-TG-MEDIA`.

**Exit criteria:**
1. PDF → OCR → staged → reply.
2. .txt → inline → classifier.
3. Unsupported mime → ru-only reject, no audit error.
4. Identical doc → DedupHit, no re-stage.
5. ≥8 unit-тестов (4 mime ветки + dedup + rejection).

**Rationale:** последний category-level gap. Ack-only недопустим для launch.

### Chunk 23 — `M-INTEGRATION-E2E`

**Scope:** `tests/integration/test_e2e_pipeline.py` против реального Claude CLI + `FakeAiogramBot`. Сценарии: text turn, voice turn, confirm callback, pdf turn. `make integration` target + раздел в `operations.md`.

**Depends on:** chunks 20, 21, 22.

**Exit criteria:**
1. `RUN_INTEGRATION=1 pytest tests/integration/test_e2e_pipeline.py` ≤180s локально.
2. ≥4 сценария зелёные.
3. `make integration` + nightly hook задокументирован (не включён).

**Rationale:** unit-тесты с моками; integration — последний safety net перед production.

## Cutover

После chunk 23 — **не код**, а sequential runbook. См. `cutover-checklist.md` в этом же каталоге.

## Дальнейшая судьба

После того как все 4 chunk'а закрыты в Beads и cutover-checklist подписан, этот каталог консервируется (как `20260510-ai-steward-wiki-mvp/`) и не редактируется.
