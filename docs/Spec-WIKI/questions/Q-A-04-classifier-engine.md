# Q-A-04: Auto-classification engine

**Tier:** A
**Источник:** [overview §9 п.4](../raw/20260507-ai-steward-wiki-only-overview.md), §8.3.3

## Формулировка

Claude CLI в `Inbox-WIKI` (дорого, ~10–30 сек) vs прямой Anthropic API call с Haiku (~1–2 сек) vs гибрид (Haiku triage → CLI ingest).

## Варианты

1. **A. Только Claude CLI.** Единая сессия с router-`CLAUDE.md`. Минусы: latency и $$$, но согласовано со всем pipeline.
2. **B. Только Haiku API.** Дёшево, но теряем доступ к контексту WIKI и истории.
3. **C. Гибрид (рекомендованный в overview).** Haiku → intent {`reminder`/`wiki_action`/`unclear`}; `reminder` → reminder_job напрямую; `wiki_action`/`unclear` → CLI Router-Claude.

## Решение

- [x] Вариант C — гибрид: Haiku Stage-0 (intent + reminder distill) → Stage-1 CLI Sonnet в Inbox-WIKI для wiki_action/unclear. Юзер подтвердил 2026-05-08 с явным указанием моделей. См. [D-009](../decisions/D-009-classifier-engine.md) (accepted).
- [x] оформлено как [D-009](../decisions/D-009-classifier-engine.md)

## Связанные

1. [Classifier](../entities/classifier.md)
2. [Router-agent](../entities/router-agent.md)
3. [Smart inbox routing](../concepts/smart-inbox-routing.md)
