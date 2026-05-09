# D-038: Per-user hard isolation — `systemd-run` MVP

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-31](../questions/Q-E-31-per-user-systemd.md), [D-007](D-007-add-dir-scope.md), [D-012](D-012-wiki-lock.md), [D-013](D-013-claude-cli-auth.md), [D-027](D-027-anti-nesting-admin-boundary.md), [D-028](D-028-admin-access.md), [D-030](D-030-onboarding.md), [D-037](D-037-git-in-wiki.md)

## Проблема

При запуске Claude CLI subprocess из ai-steward-wiki — soft-scope `--add-dir <wiki>` ([D-007](D-007-add-dir-scope.md)) — это политика приложения, не kernel-enforcement. Subprocess наследует UID сервисного юзера и может прочитать любой файл, доступный этому UID (включая `state/*.db`, `users.toml`, `.env`, чужие WIKI). Path-validation в нашем коде ловит только пути, возвращаемые Claude в executor; cross-tenant read через injection в `Bash` tool — невидим.

Overview §10 п.6 предлагал отложить до multi-tenant production. Юзер выбрал hard-isolation **сразу в MVP** — kernel-level granular isolation per Claude CLI subprocess.

## Варианты

1. **A — `systemd-run` per Claude subprocess уже в MVP.** ⭐
2. **B — Out-of-scope MVP (soft scope), trigger на multi-tenant.**
3. **C — Iter-3 без trigger.**
4. **D — Feature-flag `HARD_ISOLATION=1`.**

## Выбор

**Вариант A.**

### Запуск Claude CLI

Каждый `claude` subprocess стартует через `systemd-run` как ephemeral systemd scope:

```bash
systemd-run \
  --scope \
  --quiet \
  --uid=<per-user-uid> \
  --gid=<per-user-gid> \
  --property=ProtectSystem=strict \
  --property=ProtectHome=tmpfs \
  --property=ReadWritePaths=<wiki-абсолютный-путь> \
  --property=ReadOnlyPaths=<service>/prompts \
  --property=PrivateTmp=true \
  --property=PrivateDevices=true \
  --property=NoNewPrivileges=true \
  --property=MemoryMax=2G \
  --property=TasksMax=64 \
  -- \
  claude --print --add-dir <wiki> ...
```

### Per-user UID provisioning

1. На `wiki init <Domain>` для **первого** WIKI юзера — создаётся unix-юзер `aisw-<userN>` (например `aisw-henry1`):
   ```bash
   useradd --system --no-create-home --shell /usr/sbin/nologin aisw-<userN>
   chown -R aisw-<userN>:aisw-<userN> <user_dir>/
   chmod 0700 <user_dir>/
   ```
2. UID хранится в `users.toml` ([D-030](D-030-onboarding.md)) рядом с `tg_user_id`:
   ```toml
   [users.henry-1]
   tg_user_id = 123456
   unix_uid = 901
   created_at = "2026-05-09T..."
   ```
3. Бот (`ai-steward-wiki`) бежит под dedicated UID `aisw-bot` с `CAP_SETUID` capability (через systemd unit `AmbientCapabilities=CAP_SETUID`) **либо** под root (less preferred). `CAP_SETUID` — preferred (минимум привилегий, blast-radius меньше).
4. Hard-delete юзера ([D-031](D-031-allowlist-hot-reload.md)): после soft-delete + grace period — `userdel aisw-<userN>` + cleanup home (если есть).

### Service systemd unit

`ai-steward-wiki.service`:

```ini
[Service]
Type=simple
User=aisw-bot
Group=aisw-bot
AmbientCapabilities=CAP_SETUID CAP_SETGID
NoNewPrivileges=false   # бот должен мочь setuid в Claude scope
ExecStart=/usr/local/bin/ai-steward-wiki
Restart=on-failure
```

`NoNewPrivileges=false` на уровне бота **обязателен** для CAP_SETUID работы; на уровне `systemd-run --scope` для каждого Claude — обратно `NoNewPrivileges=true` (Claude не должен escalate'ить).

### Permissions matrix

| Path | Owner | Mode | Доступ Claude scope |
|------|-------|------|--------------------|
| `<wiki>/` | `aisw-<userN>` | 0700 | RW (через `ReadWritePaths`) |
| `<other-user>/` | `aisw-<other>` | 0700 | **нет** (kernel deny) |
| `<service>/prompts/` | `aisw-bot` | 0755 | RO (через `ReadOnlyPaths`) |
| `state/*.db` | `aisw-bot` | 0600 | **нет** (kernel deny) |
| `users.toml` | `aisw-bot` | 0600 | **нет** |
| `.env` | `aisw-bot` | 0600 | **нет** |
| `/etc`, `/usr` | root | 0755 | RO (`ProtectSystem=strict`) |
| `$HOME` любого юзера | — | — | tmpfs (`ProtectHome=tmpfs`) |

### Resource limits

1. `MemoryMax=2G` — kill-on-OOM защищает от runaway Claude (важно при vision на большом фото).
2. `TasksMax=64` — limit на forked/threaded subprocess (Claude может вызвать `Bash`).
3. CPU: не лимитируется в MVP; `CPUQuota=200%` опционально позже.

### Lifecycle

1. Бот acquire'ит [D-011](D-011-concurrent-claude.md) semaphore + [D-012](D-012-wiki-lock.md) lock.
2. Бот вызывает `systemd-run --scope ... claude ...` — это форк +setuid +setup namespaces, потом execve `claude`.
3. Бот ждёт exit code через `subprocess.wait()` или `--wait` flag systemd-run; timeouts ([D-021](D-021-timeouts-kill-policy.md)) — kill через `systemctl stop <scope-name>` (kill всю scope-группу, включая Claude tool subprocess'ы).
4. Logs Claude'а — в journald (наследует stdout родителя? нет — scope имеет свой output; настраиваем `--property=StandardOutput=journal --property=StandardError=journal`).
5. Cleanup: scope умирает с процессом (ephemeral); никаких persistent unit-файлов.

### Audit-trail

1. `audit.db.audit_events` для каждого CLI-запуска: `{scope_name, unix_uid, wiki_path, started_at, exited_at, exit_code, kill_reason}`.
2. Дублируется в journald (`journalctl -u <scope-name>`); но `audit.db` — primary SSoT для query.

### Trade-offs принятые

1. **CAP_SETUID на бота** — реальный security trade-off: бот скомпрометирован → атакующий setuid'ит в любого `aisw-*`. Mitigation: бот сам — minimal attack surface (Python aiogram + structlog + sqlite + httpx); pre-commit gitleaks для самого сервиса.
2. **Operational complexity:** `useradd` на каждого юзера, debug сложнее (каждый CLI run — отдельная scope-unit). Принято — выигрыш в isolation важнее.
3. **Single-tenant Henry-N сейчас не получает практического gain**, но (а) rehearsal для multi-tenant без late-stage refactor, (б) защита от self-inflicted prompt-injection (Claude через injection не сможет читать `state/*.db`), (в) defense-in-depth.
4. **Linux-only.** macOS/WSL не support'ят `systemd-run` — dev-окружение Henry должно быть Linux VPS или VM. Для local dev опционально fallback (см. ниже).

### Local dev fallback

1. Если `systemd-run` недоступен (macOS dev workstation) — env-flag `DISABLE_HARD_ISOLATION=1` (только non-production).
2. Production unit — без флага; production startup-check `assert systemctl --version` exits 0, иначе fail-fast.
3. CI (integration-tests, [D-036](D-036-testing-strategy.md)) — Linux runner с `systemd-run`; macOS local — без isolation, известная разница.

### Multi-tenant readiness (когда придёт)

1. UID provisioning уже встроен ([D-030](D-030-onboarding.md) approve flow триггерит `useradd`).
2. Cross-tenant read физически невозможен (kernel-enforced).
3. Никаких будущих миграций структуры — она уже multi-tenant ready с дня 0.

## Последствия

1. Hard kernel-isolation per Claude CLI; `--add-dir` ([D-007](D-007-add-dir-scope.md)) теперь **defense-in-depth**, а не единственный механизм.
2. Бот требует `CAP_SETUID` (или root); systemd unit обязателен; macOS dev требует fallback flag.
3. UID provisioning встроен в `wiki init` flow — связь с [D-030](D-030-onboarding.md).
4. `state/*.db`, `users.toml`, `.env` физически недоступны Claude.
5. Resource limits (`MemoryMax`, `TasksMax`) защищают от runaway.
6. Multi-tenant ready с MVP — никаких будущих breaking-migrations.
7. Запреты:
   1. **Не запускать Claude CLI напрямую через `subprocess.exec`** — только через `systemd-run --scope` wrapper.
   2. **Не давать боту root** если можно ограничиться `CAP_SETUID`.
   3. **Не использовать `DISABLE_HARD_ISOLATION=1` в production** — startup-check fail-fast.
   4. **Не shared UID** между юзерами — один tenant = один unix-uid.
   5. **Не писать `state/*.db` от Claude UID** — kernel block; failure = bug в коде, не в политике.
   6. **Не пропускать `userdel`** при hard-delete — orphan UID создаёт security debt.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-038-per-user-systemd.md` (когда финализируется)
