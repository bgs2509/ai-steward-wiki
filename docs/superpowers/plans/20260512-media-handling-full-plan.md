# План: полное подключение обработки медиа (voice / photo / document / audio / video_note)

> Статус: draft (требует прохождения USER APPROVAL gate перед исполнением)
> Дата: 2026-05-12
> Зона: `dev` — каждый chunk исполняется через `feature-workflow` (bd issue → Discovery → [APPROVAL] → Brainstorming → [APPROVAL] → GRACE → Writing Plans → [APPROVAL] → Execution → Review → Finish → bd close).
> SSoT решений: [D-022](../../Spec-WIKI/decisions/D-022-voice-photo-input.md), [D-018](../../Spec-WIKI/decisions/D-018-ingest-idempotency.md), [D-021](../../Spec-WIKI/decisions/D-021-timeouts-kill-policy.md), tech-spec §9.
> Этот документ — breakdown в духе `/superautocoder`: упорядоченная цепочка контекст-оконных chunk'ов. Каждый chunk fit в одно окно Opus.

---

## 0. Контекст и корневая проблема

### Что уже есть (read-confirmed 2026-05-12)

| Слой | Файл | Состояние |
|------|------|-----------|
| TG handlers | `src/ai_steward_wiki/tg/handlers.py` | `F.voice`, `F.photo`, `F.document` зарегистрированы, скачивают bytes, делегируют в `pipeline.on_*`. `F.audio` / `F.video_note` НЕ зарегистрированы. `message.caption` игнорируется. |
| Pipeline | `src/ai_steward_wiki/tg/pipeline.py` | `DefaultPipeline.on_voice / on_photo / on_document` реализованы. `on_document` уже делает mime-роутинг (pdf→pypdf→text pipeline; text/*→decode→text pipeline; image/*→PhotoIngestor; else→reject) + L2 dedup + PII-hash filename. `on_voice` транскрибирует и гонит транскрипт в text pipeline. `on_photo` — **только стейджит и шлёт `ACK_PHOTO_RU`, в Claude vision не уходит.** |
| Voice STT | `src/ai_steward_wiki/tg/voice.py` (`M-TG-VOICE`) | `FasterWhisperTranscriber` (lazy `WhisperModel`), `VoiceHandler.handle` → `(MediaRef, Transcript)`. Готов. |
| Photo | `src/ai_steward_wiki/tg/photo.py` (`M-TG-PHOTO`) | `PhotoIngestor.handle` → `MediaRef`. Только стейджинг; vision-вызов в контракте помечен как «лежит в M-WIKI-RUNNER (chunk 7)» — **не реализован**. |
| Staging | `src/ai_steward_wiki/inbox/staging.py` | `stage_media` / `promote_to_raw` / `sweep_staging` готовы. `promote_to_raw` и `sweep_staging` **нигде не вызываются** из рантайма. |
| Runner | `src/ai_steward_wiki/wiki/runner.py` (`M-WIKI-RUNNER`) | `run_wiki_session(...)` принимает `user_input: str`, `wiki_path`, `--add-dir <wiki_path>`. **Не принимает медиа-пути**, нет `--image`/`--add-dir <staging>`. |
| Runtime wiring | `src/ai_steward_wiki/__main__.py` (`M-RUNTIME-WIRING`) | `DefaultPipeline(...)` собирается **без `voice=` и `photo=`** → `self._voice is None` / `self._photo is None` → ветка `*.no_handler` → ack-заглушка `"Принято."`. Это и есть симптом «отправил голосовое — получил "принято" и всё». |
| Зависимость | `pyproject.toml:27` | `faster-whisper==1.1.0` — в extra `stt`, **не ставится** `uv sync` по умолчанию; нет в Dockerfile/деплое. |
| Scheduler | `src/ai_steward_wiki/scheduler/maintenance.py` | нет задачи `sweep_staging` (24h retention из D-022). |

### Корневая причина симптома

`DefaultPipeline` в `__main__.py` не получает `VoiceHandler` / `PhotoIngestor`. Плюс `faster-whisper` не установлен. Всё остальное для voice уже написано — это недоделанный wiring между chunk 11 (медиа-модули) и chunk 20 (text pipeline).

### Открытые design-вопросы (решаются в Discovery/Brainstorming соответствующих chunk'ов, НЕ здесь)

1. **Photo → Claude vision: как именно?** Варианты: (a) `claude --image <path>` (если CLI поддерживает в текущей версии 2.1.139 — проверить `claude --help` + Context7), (b) `--add-dir <staging_dir>` + текстовый prompt «прочитай файл X», (c) промоут в Inbox-WIKI до запуска и `--add-dir` на неё. → **chunk 2**.
2. **Routing для photo без caption.** Stage-0 классификатор работает по тексту; у голого фото текста нет. D-022: «vision-extract идёт в обычный flow router» — значит Stage-1 (Claude в Inbox-WIKI) сам решает, куда подшить. То есть photo, возможно, идёт в обход Stage-0 сразу в Stage-1 над Inbox-WIKI. → решить в **chunk 2** Brainstorming.
3. **`inbox_root` / куда стейджить.** Сейчас `stage_media(..., inbox_root=...)`. D-022: `USERS/<NAME>/Inbox-WIKI/raw/media/_staging/`. Нужен helper «путь к Inbox-WIKI юзера» (есть ли он в `M-WIKI-LIFECYCLE`? — проверить `src/ai_steward_wiki/wiki/lifecycle.py` в Discovery chunk 1). Если нет per-user Inbox-WIKI — для MVP допустим единый `workspace_root/_staging`. → **chunk 1**.
4. **`faster-whisper` в core vs extra.** +ctranslate2 + onnxruntime ≈ +200 МБ. Решить: вынести в основные deps ИЛИ оставить extra и добавить `uv sync --extra stt` в Dockerfile + systemd unit. Если решение «extra» — нужен graceful-degradation: при `ImportError(faster_whisper)` отвечать «голос пока не поддержан» вместо тихого «Принято». → **chunk 1** (ADR-кандидат).
5. **`media_ingest` job category** (D-022 §"Job category") — нужна ли в MVP? D-022 сам пишет «используется только для async ack pattern; processing работает в реальном времени под TG handler». → вероятно YAGNI для MVP, зафиксировать в chunk 1 Discovery.

---

## 1. Карта chunk'ов

```
chunk 1  M-RUNTIME-WIRING  : wire VoiceHandler+PhotoIngestor + settings + faster-whisper dep + graceful-degradation
chunk 2  M-WIKI-RUNNER     : runner принимает media_paths → photo реально уходит в Claude vision
chunk 3  M-TG-HANDLERS-WIRING : F.audio / F.video_note / message.caption
chunk 4  M-INBOX (staging) : promote_to_raw после успешного run + sweep_staging как scheduler job (24h)
chunk 5  M-INTEGRATION-E2E + docs : e2e сценарии voice/photo/doc, refresh KG + verification-plan, completion report
```

Зависимости: `2` зависит от `1`. `3` независим от `2`, но после `1`. `4` зависит от `2` (нужен факт «run завершился, target WIKI известна»). `5` — последний.

Каждый chunk fit в одно контекстное окно (оценка: 4–10 файлов × ~300 строк + контракты + тесты + plan.md ≤ 60% окна Opus).

---

## chunk 1 — `M-RUNTIME-WIRING`: подключить VoiceHandler + PhotoIngestor

**bd:** `feat(M-RUNTIME-WIRING): wire voice+photo handlers into DefaultPipeline`

### Цель
`DefaultPipeline` в `__main__.py` получает `voice=VoiceHandler(...)` и `photo=PhotoIngestor(...)`; `faster-whisper` доступен в рантайме; при его отсутствии — внятное сообщение, а не тихий ack.

### Discovery (вопросы к юзеру / коду)
- Проверить `src/ai_steward_wiki/wiki/lifecycle.py`: есть ли helper «путь к Inbox-WIKI юзера / staging root»? Если нет — какой путь стейджинга для MVP (`workspace_root/_staging` vs per-user)?
- Решение по `faster-whisper`: core dep или extra+`--image` deploy step? (ADR-кандидат — `docs/adr/ADR-NNN-faster-whisper-packaging.md`).
- Размер whisper-модели: `small` (дефолт в `FasterWhisperTranscriber`) — оставить параметром в `Settings`?

### Изменения
1. `src/ai_steward_wiki/settings.py` (`M-RUNTIME-WIRING` / CONFIG):
   - `media_staging_root: Path` (или derive из `workspace_root`).
   - `voice_enabled: bool = True`, `voice_whisper_model_size: Literal["small","medium"] = "small"`, `voice_stt_timeout_s: float = 60.0` (D-021).
   - `photo_enabled: bool = True`, `photo_vision_timeout_s: float = 30.0` (D-021).
   - bump `CHANGE_SUMMARY`.
2. `src/ai_steward_wiki/__main__.py` — новый блок `START_BLOCK_MEDIA_PIPELINE_WIRING`:
   - построить `transcriber = FasterWhisperTranscriber(model_size=settings.voice_whisper_model_size)` (lazy — модель не грузится при импорте);
   - `voice_handler = VoiceHandler(transcriber, inbox_root=settings.media_staging_root) if settings.voice_enabled else None`;
   - `photo_ingestor = PhotoIngestor(inbox_root=settings.media_staging_root) if settings.photo_enabled else None`;
   - передать `voice=voice_handler, photo=photo_ingestor` в `DefaultPipeline(...)`;
   - `logger.info("runtime.media_pipeline.wired", voice=voice_handler is not None, photo=photo_ingestor is not None, whisper_model=...)`.
3. `pyproject.toml` / `uv.lock`:
   - либо перенести `faster-whisper==1.1.0` в основные deps (`uv add faster-whisper`),
   - либо оставить extra + правки `deploy/systemd/*` и Dockerfile: `uv sync --extra stt`. (По решению Discovery.)
4. Graceful-degradation: в `VoiceHandler.handle` (или в `FasterWhisperTranscriber.transcribe`) ловить `ImportError`/`ModuleNotFoundError` для `faster_whisper` → бросать доменный `VoiceUnavailableError`; в `DefaultPipeline.on_voice` ловить его → `_log.warning("tg.pipeline.voice.stt_unavailable")` + ответ `ACK_VOICE_UNAVAILABLE_RU = "Голосовые сообщения пока не поддерживаются, напишите текстом."` (новая константа в `pipeline.py`). *(Если решение «core dep» — этот пункт можно сократить до защитного минимума.)*
5. ADR (если решение по deps нетривиально).

### Тесты (TDD, RED→GREEN)
- `tests/unit/tg/test_pipeline_voice.py` (расширить): `on_voice` с фейковым `VoiceHandler` → транскрипт уходит в `_run_text_pipeline` (mock classifier+runner+output вызваны).
- `on_voice` когда `_voice` бросает `VoiceUnavailableError` → отправлен `ACK_VOICE_UNAVAILABLE_RU`, не `ACK_TEXT_RU`.
- `tests/unit/tg/test_pipeline_photo.py`: `on_photo` с фейковым `PhotoIngestor` → `handle` вызван, `ACK_PHOTO_RU` отправлен (поведение vision ещё в chunk 2).
- `tests/unit/test_main_wiring.py` (если есть аналог) — `DefaultPipeline` получил не-None `voice`/`photo` при `*_enabled=True`.
- `make lint` + `make total-test` зелёные.

### Контракты / KG
- Обновить MODULE_CONTRACT `__main__.py` (`M-RUNTIME-WIRING`): `DEPENDS` += `M-TG-VOICE, M-TG-PHOTO`.
- `grace-refresh` после изменения модулей.

### Acceptance
Запуск `uv run python -m ai_steward_wiki`, отправка голосового → бот отвечает транскриптом/результатом Stage-1 (или `ACK_VOICE_UNAVAILABLE_RU` если STT не установлен), в логах `runtime.media_pipeline.wired voice=true photo=true` и НЕТ `tg.pipeline.voice.no_handler`.

---

## chunk 2 — `M-WIKI-RUNNER`: фото реально уходит в Claude vision

**bd:** `feat(M-WIKI-RUNNER): accept media paths so photos reach Claude vision`

### Цель
Закрыть «Stage-1b vision call» из контракта `photo.py`. После этого фото (и image-документ) обрабатывается Claude vision, а не просто стейджится.

### Discovery / Brainstorming (открытые вопросы 1–2 из §0)
- Проверить через `claude --help` + Context7 (`@anthropic-ai/claude-code` / CLI docs), поддерживает ли установленная версия (2.1.139) передачу изображения: флаг `--image`? attach? Если нет — fallback на `--add-dir <staging_parent>` + текстовый prompt «во вложении файл `<basename>`, открой его инструментом Read».
- Routing: photo без caption идёт в обход Stage-0 сразу в Stage-1 над Inbox-WIKI (Claude сам решает, куда подшить) — подтвердить с юзером. Photo С caption — caption классифицируется Stage-0 как обычный текст, а image передаётся в Stage-1 как контекст.

### Изменения
1. `src/ai_steward_wiki/wiki/runner.py` (`M-WIKI-RUNNER`):
   - `run_wiki_session(..., media_paths: list[Path] | None = None)`;
   - `_build_argv(...)` += для каждого `media_paths` либо `--image <path>` (если поддерживается), либо добавить `--add-dir <path.parent>` и упомянуть файл в `user_input`;
   - `_RunConfig` без изменений (timeout берётся вызывающим — для vision 30s);
   - bump `CHANGE_SUMMARY`.
2. `src/ai_steward_wiki/tg/pipeline.py` (`M-TG-PIPELINE-CLASSIFIER`):
   - расширить `WikiRunner` Protocol: `run(..., media_paths: list[Path] | None = None)`;
   - `on_photo`: после `PhotoIngestor.handle` → если `_full_pipeline_available()` и `_photo` есть → собрать synthetic `user_input` (caption или дефолт «Пользователь прислал изображение — разбери и при необходимости занеси в WIKI») + `media_paths=[ref.staging_path]` → вызвать `_run_text_pipeline`-аналог или новый `_run_media_pipeline` (Stage-0 пропускается / intent=default для голого фото); деливерить результат вместо `ACK_PHOTO_RU`;
   - то же для `_handle_image_branch` (image-документ);
   - новые ack/edge строки при необходимости.
3. `src/ai_steward_wiki/__main__.py` — `_WikiRunnerAdapter.run` пробрасывает `media_paths`; timeout для media-вызова = `settings.photo_vision_timeout_s`.

### Тесты
- `tests/unit/wiki/test_runner.py`: `media_paths` → argv содержит ожидаемые флаги; пустой/None → argv как раньше (регрессия).
- `tests/unit/tg/test_pipeline_photo.py`: `on_photo` при полном пайплайне → `runner.run` вызван с `media_paths=[<staging_path>]`, `output.deliver` вызван с текстом vision-ответа.
- `tests/unit/tg/test_pipeline_document.py`: image-документ → тот же путь.
- Регрессия: text/voice/pdf-ветки не задеты.
- `RUN_INTEGRATION=1` сценарий (nightly): реальное фото → реальный `claude` vision → непустой ответ (помечен `@pytest.mark.integration`, не в `total-test`).

### Контракты / KG
- MODULE_CONTRACT `runner.py`, `photo.py` (убрать «лежит в M-WIKI-RUNNER (chunk 7)» — реализовано), `pipeline.py`. `grace-refresh`.

### Acceptance
Отправка фото билета/рецепта → бот возвращает осмысленный текст (распознанное содержимое / куда подшил), в логах `tg.pipeline.photo` → `tg.pipeline.runner.dispatched` → `...deliver.sent`.

---

## chunk 3 — `M-TG-HANDLERS-WIRING`: audio / video_note / caption

**bd:** `feat(M-TG-HANDLERS-WIRING): handle audio, video_note content types and message captions`

### Цель
D-022 §"Trigger": voice / **audio** / **video_note**. Сейчас только `F.voice`. Плюс `message.caption` у photo/document/audio сейчас теряется.

### Изменения
1. `src/ai_steward_wiki/tg/handlers.py`:
   - `@router.message(F.audio)` → `_download_bytes(message.audio.file_id)` → `pipeline.on_voice(...)` (audio = тот же STT-путь);
   - `@router.message(F.video_note)` → скачать → извлечь аудиодорожку? video_note — это короткое видео; faster-whisper принимает медиа-контейнер, ctranslate2/ffmpeg может извлечь дорожку. Если ffmpeg не гарантирован — для MVP: ответить «видео-кружки пока не поддерживаются» (новая строка), и зафиксировать в Discovery как осознанный cut. **Решить в Discovery chunk 3.**
   - прокинуть `caption=message.caption` в `on_photo` / `on_document` (и в `on_voice` для `audio`, у которого бывает caption);
   - порядок хендлеров: убедиться, что `F.voice`/`F.audio`/`F.video_note`/`F.photo`/`F.document` не конфликтуют (aiogram матчит по первому подходящему — текущий `F.text & ~startswith("/")` уже первый).
2. `src/ai_steward_wiki/tg/pipeline.py`: сигнатуры `on_voice`/`on_photo`/`on_document` += `caption: str | None = None`; для voice — caption (если есть) приклеивается к транскрипту; для photo/document — caption становится `user_input` для Stage-1 (см. chunk 2).
3. `MessagePipeline` Protocol — обновить сигнатуры синхронно.

### Тесты
- `tests/unit/tg/test_handlers.py`: `F.audio` → `on_voice` вызван с bytes; `F.video_note` → ожидаемое поведение (обработка или reject-строка); `caption` пробрасывается.
- `tests/unit/tg/test_pipeline_*.py`: caption учитывается.

### Контракты / KG
- MODULE_CONTRACT `handlers.py` (`SCOPE`: «Five handlers» → «Seven handlers …»), `pipeline.py`. `grace-refresh`.

### Acceptance
Отправка `audio` файла → транскрибируется как voice; фото с подписью «занеси в health» → подпись доходит до Stage-1.

---

## chunk 4 — `M-INBOX` (staging lifecycle): promote_to_raw + sweep_staging job

**bd:** `feat(M-INBOX): promote staged media to target WIKI on success + scheduled 24h staging sweep`

### Цель
D-022 §"Storage": двухфазное хранение. Сейчас `promote_to_raw` и `sweep_staging` написаны, но не вызываются → стейджинг растёт бесконечно, медиа не попадает в `<wiki>/raw/media/` (immutable, Karpathy).

### Изменения
1. После успешного `run_wiki_session` (когда target WIKI известна — это знает `_WikiRunnerAdapter` / `M-WIKI-RUNNER`): вызвать `promote_to_raw(ref, wiki_root=<target_wiki_path>)`. Нужно протащить `MediaRef` от `on_voice`/`on_photo` через пайплайн до момента, когда известен target WIKI. Варианты протаскивания обсудить в Discovery (через `_run_media_pipeline` параметр vs через возврат из runner). No-WIKI intent (reminder/read-only/rejected) → файл остаётся в `_staging`, подметётся через 24h.
2. `src/ai_steward_wiki/scheduler/maintenance.py`: зарегистрировать periodic job `media_staging_sweep` (раз в час или в сутки) → `sweep_staging(settings.media_staging_root, ttl_s=DEFAULT_STAGING_TTL_S)`; лог `maintenance.media_sweep.done removed=N`.
3. (Опц., если решено в chunk 1) idempotency-row `content_kind='voice_transcript'`/`'photo_vision'` для нормализованного транскрипта/extract'а (D-022 §"Idempotency hook" п.2) — или зафиксировать как отложенное (L3).

### Тесты
- `tests/unit/inbox/test_staging.py` (есть) — регрессия `promote_to_raw`/`sweep_staging`.
- `tests/unit/scheduler/test_maintenance.py`: job `media_staging_sweep` зарегистрирован, вызывает `sweep_staging`.
- `tests/integration` (nightly): voice → run → файл в `<wiki>/raw/media/<ISO8601>_<sha8>.ogg`, `_staging` пуст.

### Контракты / KG
- MODULE_CONTRACT `maintenance.py` (`SCOPE` += media sweep), при необходимости `runner.py`. `grace-refresh --verify` (поменялись log-маркеры).

### Acceptance
После обработки голосового файл лежит в `<target-wiki>/raw/media/`, `_staging` очищается; устаревший staging-файл (>24h) удаляется фоном.

---

## chunk 5 — E2E + документация + закрытие

**bd:** `test(M-INTEGRATION-E2E): media handling e2e + docs refresh`

### Изменения
1. `tests/e2e/` (или `tests/integration/`): сквозные сценарии (под `RUN_INTEGRATION=1`, не в `total-test`):
   - voice memo → транскрипт → Stage-1 → reply;
   - photo → vision → reply + файл в `raw/media/`;
   - PDF-документ → текст → Stage-1 → reply;
   - .txt-документ → decode → Stage-1;
   - неподдерживаемый mime → `ACK_DOC_UNSUPPORTED_RU`;
   - повтор того же файла → `ACK_DEDUP_RU`.
2. `grace-refresh` (knowledge-graph) + `grace-refresh --verify` (verification-plan: новые log-маркеры `runtime.media_pipeline.wired`, `tg.pipeline.voice`, `tg.pipeline.photo`→runner, `maintenance.media_sweep.done`, `tg.pipeline.voice.stt_unavailable`).
3. `docs/reports/20260512-media-handling-full-report.md` — completion report (что сделано по chunk'ам, результаты `make total-test`, integration-прогон).
4. `docs/Spec-WIKI/decisions/D-022` → если по ходу что-то уточнилось (например video_note cut) — `amended` пометка; перенос в `docs/adr/ADR-022-...` если финализируется.
5. Обновить `CLAUDE.md` (корень репо) если поменялись `Запуск`/`Стек` (например `uv sync --extra stt`).
6. `bd close` всех chunk-issue.

### Acceptance
`make total-test` зелёный; `RUN_INTEGRATION=1 uv run pytest tests/integration` зелёный; `grace lint --failOn errors` exit 0; отчёт в `docs/reports/`.

---

## 2. Риски и заметки

1. **`faster-whisper` тяжёлый** (ctranslate2 + onnxruntime). Первая транскрипция грузит модель `small` (~480 МБ скачивание + ~1–2 ГБ RAM при `int8`). Lazy-load уже есть; на VPS проверить RTF ≤ 0.5 (D-022 bench-критерий) — если не проходит, `medium-int8` или Whisper-API fallback (отдельное решение, не в этом плане).
2. **`claude --image`**: если установленная версия CLI не поддерживает передачу изображения как аргумента — fallback на `--add-dir` + Read-инструмент в prompt. Это меняет объём chunk 2; проверить в Discovery до Writing Plans.
3. **video_note**: требует ffmpeg для извлечения аудио. Если не гарантирован в окружении — MVP-cut (reject-строка), не блокер.
4. **Routing голого фото**: обход Stage-0 — отклонение от «всё идёт через классификатор». Это намеренно по D-022; зафиксировать в Brainstorming chunk 2, чтобы не считалось drift'ом.
5. **Plan sizing**: если chunk 2 после Discovery окажется >60% окна (vision + routing + adapter + тесты + integration) — расколоть на `2.a` (runner `media_paths` + argv + unit) и `2.b` (pipeline `on_photo`/image-doc wiring + integration).

## 3. Порядок исполнения

```
bd create (chunk 1) → feature-workflow → ... → bd close
bd create (chunk 2) → feature-workflow → ... → bd close   # после 1
bd create (chunk 3) → feature-workflow → ... → bd close   # после 1, ║ 2
bd create (chunk 4) → feature-workflow → ... → bd close   # после 2
bd create (chunk 5) → feature-workflow → ... → bd close   # последний
```

Либо одним заходом через `/superautocoder` с этим документом как draft.
