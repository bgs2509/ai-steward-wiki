# Q-E-31: Per-user systemd unit

**Tier:** E
**Источник:** [overview §9 п.31](../raw/20260507-ai-steward-wiki-only-overview.md), §7.3 п.3

## Формулировка

В MVP или out-of-scope.

## Варианты

1. **A. MVP scope.** `systemd-run --uid=...` per Claude процесс с `ProtectHome=`, `ReadWritePaths=<home_dir>`.
2. **B. Out-of-scope.** Один сервисный юзер, защита через path-validation.
3. **C. Iter-3.** Отложено до multi-tenant production.

## Решение

- [x] оформлено как [D-038](../decisions/D-038-per-user-systemd.md): hard isolation MVP — `systemd-run --scope --uid=<per-user>` per Claude CLI subprocess; `ProtectSystem=strict` + `ProtectHome=tmpfs` + `ReadWritePaths=<wiki>`; бот требует `CAP_SETUID`. Multi-tenant ready с дня 0.

## Связанные

— нет.
