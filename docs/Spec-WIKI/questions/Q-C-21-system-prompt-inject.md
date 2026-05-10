# Q-C-21: Инжект LLM Wiki system prompt

**Tier:** C
**Источник:** [overview §9 п.21](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

`--system-prompt` / `--append-system-prompt` / через `CLAUDE.md` / stdin. SSoT текста промпта (файл в репо `prompts/wiki.md`?).

## Варианты

1. **A. `--append-system-prompt @prompts/wiki.md`.** SSoT — файл в репо. Версионируется.
2. **B. Только `CLAUDE.md` per-WIKI.** Дрейф между WIKI; нет глобального enforce.
3. **C. Hybrid.** Глобальный wiki-prompt инжектится `--append-system-prompt`, per-WIKI `CLAUDE.md` дополняет.

## Решение

**Принято 2026-05-08:** Hybrid (вариант C). Global doctrine `prompts/wiki.md` через `--append-system-prompt @file`, per-WIKI профиль через CLAUDE.md auto-walk (D-007). Stage-0 Haiku — `prompts/classifier.md`. Inbox-router наследует `wiki.md` + `inbox.md`. Версия prompt'а логируется в audit.db.

- [x] оформлено как [D-015](../decisions/D-015-system-prompt-inject.md)

## Связанные

1. [LLM Wiki method](../concepts/llm-wiki-method.md)
