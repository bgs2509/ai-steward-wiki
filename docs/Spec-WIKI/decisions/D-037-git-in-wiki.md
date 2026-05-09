# D-037: Git per-WIKI — auto-commit + gitleaks pre-commit + no remote MVP

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-37](../questions/Q-E-37-git-in-wiki.md), [Q-E-36 (deferred)](../questions/Q-E-36-backup.md), [D-007](D-007-add-dir-scope.md), [D-008](D-008-wiki-marker-format.md), [D-012](D-012-wiki-lock.md), [D-019](D-019-cron-failure-mode.md), [D-027](D-027-anti-nesting-admin-boundary.md)

## Проблема

Overview §7a допускает «git опционально» — для отката плохих правок Claude и истории контента. Q-E-36 (backup) был отложен в backlog → content-versioning остаётся **единственной** защитой от bad-edit и content-loss. Granularity репо (per-WIKI / per-USER / global) определяет, как это интегрируется с уже принятыми границами: `--add-dir <wiki>` ([D-007](D-007-add-dir-scope.md)), `WORKSPACE_ROOT` anchor ([D-027](D-027-anti-nesting-admin-boundary.md)), `.wiki.lock` ([D-012](D-012-wiki-lock.md)).

## Варианты

1. **A — Без git вообще.**
2. **B — Git per-WIKI, auto-commit после PostRun, gitleaks pre-commit, no remote MVP.** ⭐
3. **C — Git per-USER (один репо на `USERS/<NAME>/`).**
4. **D — Global single repo `~/.ai-steward-wiki/.git`.**
5. **E — B + git-LFS для бинарей.**

## Выбор

**Вариант B.**

### Init

1. На `wiki init <Domain>` — после материализации шаблона ([D-016](D-016-inbox-claude-md-template.md), [D-017](D-017-domain-claude-md-template.md)):
   ```bash
   git -C <wiki> init -q --initial-branch=main
   git -C <wiki> config user.name  "ai-steward-wiki"
   git -C <wiki> config user.email "bot@ai-steward-wiki.local"
   cp <service>/templates/git/.gitignore <wiki>/.gitignore
   cp <service>/templates/git/pre-commit <wiki>/.git/hooks/pre-commit
   chmod +x <wiki>/.git/hooks/pre-commit
   git -C <wiki> add -A
   git -C <wiki> commit -q -m "init(wiki-<domain>): initial materialize from template v<N>"
   ```
2. Inbox-WIKI ([D-004](D-004-inbox-wiki-scope.md)) — тот же init flow при materialize.

### `.gitignore` template

```gitignore
# voice/photo binary input — immutable raw, не versioned (overhead)
raw/media/**/*.opus
raw/media/**/*.ogg
raw/media/**/*.mp3
raw/media/**/*.wav
raw/media/**/*.m4a
raw/media/**/*.mp4
raw/media/**/*.mov
raw/media/**/*.jpg
raw/media/**/*.jpeg
raw/media/**/*.png
raw/media/**/*.heic

# operational artifacts — не контент
.wiki.lock
data/runs/                  # full run outputs (D-025) — large, dedicated retention
```

`raw/text/`, `raw/pdf/`, `raw/transcripts/` (текстовые формы media) — versioned.

### Auto-commit после PostRun

1. Executor после успешного PostRun-write (классификатор Stage-1 / wiki_job / tracker_followup, etc.):
   ```bash
   git -C <wiki> add -A
   git -C <wiki> commit -m "<job_id>(<category>): <one-line-summary>" --quiet
   ```
2. Commit-message format: `<job_id>(<category>): <title>` — параллель с conventional commits, с `job_id` для cross-ref с `audit.db`.
3. Author: уже configured `ai-steward-wiki <bot@ai-steward-wiki.local>` — отделение от ручных правок Henry в Obsidian/IDE (которые тоже коммитятся, но с другим author'ом).
4. `.wiki.lock` ([D-012](D-012-wiki-lock.md)) держится поверх git-операций — atomic write + git операции внутри одного lock-окна.

### gitleaks pre-commit hook

1. Hook (`<wiki>/.git/hooks/pre-commit`):
   ```bash
   #!/bin/sh
   gitleaks protect --staged --no-banner --redact || {
     echo "[ai-steward-wiki] gitleaks: secret detected, commit blocked"
     exit 1
   }
   ```
2. Если gitleaks fails:
   1. Commit НЕ создаётся; страница остаётся изменённой на FS (юзер видит результат, но без git-snapshot).
   2. В DLQ ([D-019](D-019-cron-failure-mode.md)) пишется entry типа `secret_detected` с `wiki_id`, `job_id`, `gitleaks_output` (redacted).
   3. Admin-shadow notification ([D-020](D-020-cron-result-routing.md)): `🔐 secret-leak prevented in <wiki>: <category>`.
   4. На следующем write — попытка повторится; если юзер сам внёс secret, он должен убрать вручную.
3. `gitleaks` config — default rules; custom rules per-WIKI отложены до реальной потребности.

### Remote push

1. **No remote в MVP.** Push private health/career data на github by accident — slip-risk.
2. Opt-in per-WIKI позже отдельным решением: `wiki config <wiki> set git.remote <url>` + manual push.
3. Backup концерн (Q-E-36 deferred) **не** решается через git remote — git ≠ disaster-recovery (см. concepts/git-vs-backup.md если будет создан).

### Granularity rationale

1. **Per-WIKI:** repo-граница = `--add-dir`-граница ([D-007](D-007-add-dir-scope.md)) = `.wiki.lock`-граница ([D-012](D-012-wiki-lock.md)) = future hard-isolation граница ([D-038](D-038-per-user-systemd.md)). Один уровень разделения.
2. **Per-USER (отвергнут):** смешивает WIKI разной приватности (Health vs Career) в одной истории; gitleaks-fail в одной WIKI блокирует commits в другие.
3. **Global (отвергнут):** нарушает [D-027](D-027-anti-nesting-admin-boundary.md) (admin-nesting через `WORKSPACE_ROOT`); cross-user mixing в multi-tenant — катастрофа.

### `git gc` policy

1. Auto: APScheduler weekly job `git -C <wiki> gc --auto --quiet` (per WIKI).
2. Без явных retention limits — markdown-история компактная, не растёт критично; revisit при репо >100MB.

### Manual undo flow

1. Юзер: `/wiki_history <wiki>` — список последних 20 commits.
2. Юзер: `/wiki_revert <wiki> <commit_sha>` — `git -C <wiki> revert <sha>` + auto-commit с message `revert(<wiki>): undo <original_sha>`.
3. Уровень graduated confirmation ([D-023](D-023-tg-confirmations.md)) = explicit (revert — destructive в смысле user intent).

## Последствия

1. Per-WIKI granularity = clean alignment с уже принятыми границами.
2. gitleaks ловит self-leak (юзер прислал секрет в чате → Claude записал в страницу).
3. Undo для content (`git revert`/`git checkout @{1}`) даёт защиту от bad Claude edit.
4. Voice/photo не versioned — экономия места + они immutable raw-input.
5. Учитывая Q-E-36 deferred — git становится **первичной** защитой от content-loss (после bad edit, не после disk-failure).
6. Запреты:
   1. **Не использовать per-USER или global repo** — explicit запрет per D-027.
   2. **Не пропускать gitleaks hook** через `--no-verify` (даже под root) — secret в git history неудаляемо без force-push.
   3. **Не настраивать remote по умолчанию** — opt-in only.
   4. **Не коммитить `.wiki.lock`, `data/runs/`, voice/photo binary** — gitignore enforced.
   5. **Не использовать git-LFS** — overkill для immutable media.
   6. **Не путать git с backup** — git = content-versioning одного диска; off-site backup = отдельный (Q-E-36 deferred) layer.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-037-git-in-wiki.md` (когда финализируется)
