---
feature: media-handling-chunk2-wiki-runner-media
bd_id: aisw-m2m
epic: aisw-hcl
status: approved
date: 2026-05-12
fr:
  - id: FR-1
    text: "run_wiki_session принимает media_paths — каталоги этих файлов добавляются в --add-dir, и Claude может прочитать (увидеть) изображение инструментом Read."
  - id: FR-2
    text: "DefaultPipeline.on_photo: при полном пайплайне фото стейджится → runner вызывается с media_paths=[staging_path] + синтетическим промптом → результат vision доставляется пользователю (а не ACK_PHOTO_RU)."
  - id: FR-3
    text: "Image-документ (text/image mime в on_document) идёт тем же путём через PhotoIngestor + runner."
  - id: FR-4
    text: "Повторная отправка того же изображения → L2 dedup на байтах изображения → ACK_DEDUP_RU."
nfr:
  - id: NFR-1
    text: "WikiRunner Protocol и _WikiRunnerAdapter пробрасывают media_paths; обратная совместимость — media_paths=None → argv как раньше (регрессия)."
  - id: NFR-2
    text: "Логи: tg.pipeline.photo → tg.pipeline.runner.dispatched → tg.pipeline.deliver.sent для фото-пути."
  - id: NFR-3
    text: "make total-test exit 0; mypy strict; grace lint clean."
risks:
  - "claude CLI 2.1.139 не имеет --image; --file <file_id:path> — для remote file_id, не локальных путей. Единственный путь — --add-dir <каталог> + Read tool. Зафиксировано."
  - "Stage-0 классификация синтетического промпта фото бессмысленна, но безвредна (intent адаптером игнорируется — wiki = wiki_root/<owner_telegram_id>); путь кода остаётся единообразным."
  - "media_staging_root/_staging переживёт run (промоушен в target wiki — chunk 4); пока не чистится → накопление (sweep — chunk 4)."
scope_in:
  - "runner.py: media_paths → --add-dir каталоги."
  - "pipeline.py: WikiRunner.run(media_paths=...), _run_text_pipeline(media_paths=, skip_l2_dedup=), on_photo rewrite, _handle_image_branch rewrite, _PHOTO_PROMPT_RU."
  - "__main__.py: _WikiRunnerAdapter.run проброс media_paths."
  - "photo.py: contract update (vision больше не 'deferred to chunk 7')."
  - "Unit-тесты: runner argv с media; on_photo full pipeline + dedup; image-doc full pipeline."
scope_out:
  - "Отдельный per-call vision timeout 30s (D-022) — пока общий wiki_runner_timeout_s; отложено."
  - "promote_to_raw/sweep (chunk 4); F.audio/video_note/caption (chunk 3); e2e (chunk 5)."
scope_later:
  - "Per-call timeout override в WikiRunner.run для media (D-022 vision 30s)."
  - "Routing по intent (когда появятся per-domain wikis)."
decisions:
  - id: DEC-C2-1
    text: "Изображение передаётся в Claude через --add-dir <каталог staged-файла> + путь в user_input (Read tool). claude CLI 2.1.139 не имеет --image; --file — для remote file_id. Это единственный жизнеспособный механизм, не выбор среди альтернатив → auto-apply, без ADR."
  - id: DEC-C2-2
    text: "on_photo получает собственный L2 dedup на байтах изображения (kind='file', как on_document), затем _run_text_pipeline(skip_l2_dedup=True) — синтетический промпт фото константен, дедуп по тексту дал бы ложные срабатывания."
  - id: DEC-C2-3
    text: "source-литерал _run_text_pipeline расширяется до Literal['text','voice','document','photo']."
---

# Discovery — chunk 2: M-WIKI-RUNNER (фото → Claude vision)

## Реальная цель
Сейчас `on_photo` только стейджит и шлёт `ACK_PHOTO_RU`; vision-вызов помечен в контракте `photo.py` как «лежит в M-WIKI-RUNNER (chunk 7)» — не реализован. Цель: фото действительно обрабатывается Claude vision.

## Verified (Read 2026-05-12)
- `claude --help` (2.1.139): нет `--image`/`--attach`; есть `--add-dir <directories...>` (variadic), `--file <file_id:path>` (remote file_id download — не подходит).
- `_WikiRunnerAdapter.run` игнорирует `intent`; `wiki_id = str(owner_telegram_id)`, `wiki_path = wiki_root/<id>` — одна wiki на юзера в текущем MVP.
- `run_wiki_session._build_argv`: `[binary, "-p", "--model", model, "--add-dir", str(wiki_path), *system_prompt_argv(prompt_path), "--setting-sources","", "--disable-slash-commands","--verbose","--output-format","stream-json","--permission-mode","dontAsk", (--allowedTools...), (--disallowedTools...)]`.
- `--add-dir` принимает несколько путей: `"--add-dir", dirA, dirB, *next_flag` — variadic форма.
- `on_document` уже делает L2 dedup на doc_bytes (kind="file") в начале; `_handle_image_branch` после dedup только стейджит+ack.
- `_run_text_pipeline` делает L2 dedup на тексте (kind="text") → для фото надо пропускать (синтетический текст константен).
