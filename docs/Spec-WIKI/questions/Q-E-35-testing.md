# Q-E-35: Тестирование

**Tier:** E
**Источник:** [overview §9 п.35](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Мок Claude CLI (fake binary в `PATH`) для unit; интеграция с реальным CLI — в CI или только локально.

## Варианты

1. **Unit:** fake `claude` shell-скрипт, эхо детерминированного output. Через `tmp_path` + `monkeypatch.setenv("PATH", ...)`.
2. **Integration:** реальный CLI в CI behind флагом `RUN_INTEGRATION=1`. Билинг → только nightly.
3. **E2E:** TG-бот на test-token + sandboxed VPS. Off-CI, manual.

## Решение

- [x] оформлено как [D-036](../decisions/D-036-testing-strategy.md): test pyramid — unit (`ClaudeRunner` Protocol + `FakeClaudeRunner`) + integration (real CLI за `RUN_INTEGRATION=1`) + manual e2e (checklist.md перед релизом).

## Связанные

— нет прямых.
