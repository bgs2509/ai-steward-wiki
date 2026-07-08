# ADR-035: Codex subscription fallback при исчерпании лимита Claude

## Task

`aisw-8gw`

## Status

Accepted

## Context

Все модельные пути сервиса используют одну подписку Claude. После исчерпания лимита одновременно останавливаются классификация, маршрутизация, WIKI-запуски и фоновые задачи. Требуется безопасный fallback без API-key billing, расширения прав и повторных мутаций. Решение должно сохранить существующие публичные контракты потребителей.

## Considered Alternatives

### Option 1: Оставить только Claude

- Плюсы: нет нового провайдера и дополнительной операционной настройки.
- Минусы: один лимит продолжает останавливать все модельные функции.

### Option 2: Добавить fallback отдельно в каждый call site

- Плюсы: локальные изменения около существующих CLI-вызовов.
- Минусы: дублирование state machine, error detection, replay safety и телеметрии.

### Option 3: Общий in-process policy и отдельный Codex CLI adapter

- Плюсы: единый circuit, typed errors, single-flight probe и capability profiles.
- Минусы: WIKI runner требует явного mutation evidence и нормализации другого JSONL.

### Option 4: Sidecar или прямой OpenAI API

- Плюсы: независимый процесс либо стабильный программный API.
- Минусы: дополнительная инфраструктура или API-key billing вне утверждённого scope.

## Decision

Выбран Option 3.

1. Claude остаётся primary provider.
2. Circuit использует состояния `claude`, `codex` и `probe`.
3. Только typed Claude subscription limit переводит circuit из `claude` в `codex`.
4. Безопасная операция продолжается через Codex в рамках того же пользовательского действия.
5. `claude-haiku-4-5` отображается на `gpt-5.4-mini` с reasoning `low`.
6. `claude-sonnet-4-5` отображается на `gpt-5.5` с reasoning `medium`.
7. Codex использует ChatGPT subscription login в отдельном `CODEX_HOME`.
8. API-key billing и интерактивный runtime login запрещены.
9. Codex CLI закрепляется на версии `0.142.5`.
10. Каждый запуск задаёт model, reasoning, sandbox, working directory и output mode явно.
    Read-only, structured, text и web используют neutral cwd.
    Write использует selected WIKI как cwd и единственный writable root.
11. Mutation, delivery и unknown evidence блокируют автоматический replay.
12. Provider state остаётся process-local.
13. Startup проверяет бинарь, версию, auth и non-interactive режим без модельного вызова.
14. Deployment smoke проверяет обе модели, output modes и containment реальными вызовами.

## Consequences

Положительные:

- Временный лимит Claude больше не останавливает безопасные модельные операции.
- Один policy предотвращает расхождение поведения между call sites.
- Single-flight probe предотвращает retry stampede.
- Existing classifier, WIKI, schema и cron contracts сохраняются.
- Codex получает только необходимые права текущего run kind.

Отрицательные:

- Один сервис использует ChatGPT entitlement оператора на доверенной VPS.
- Первый failover-запрос получает дополнительную задержку после ошибки Claude.
- Codex JSONL требует отдельной нормализации и compatibility tests.
- Потерянное или истёкшее Codex authentication отключает fallback.
- Provider state сбрасывается в `claude` после перезапуска процесса.

## Related

- [Discovery](../superpowers/specs/20260703-codex-subscription-fallback-discovery.md)
- [Design](../superpowers/specs/20260703-codex-subscription-fallback-design.md)
- `ADR-009` — Claude subscription authentication location.
- `ADR-010` — trusted single-account deployment boundary.
- `ADR-032` — per-intent web isolation precedent.
- `M-LLM-FAILOVER`, `M-LLM-CODEX`.
