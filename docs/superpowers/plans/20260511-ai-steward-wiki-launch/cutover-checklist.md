# Production Cutover Checklist — AI Steward Wiki

**Lifecycle:** one-shot. После подписания (sign-off в конце) каталог `20260511-ai-steward-wiki-launch/` консервируется без изменений.

**Pre-requisite:** все 4 чанка launch-эпика закрыты в Beads (`bd close aisw-* --reason="chunk N done"`). Текущая ветка `master` зелёная: `make total-test` + integration suite (`RUN_INTEGRATION=1`) проходят.

**Источники процедуры:** `docs/runbook/deploy.md` (provisioning), `docs/runbook/operations.md` (daily ops), `docs/runbook/restore.md` (DR). Этот чек-лист — **только cutover**, не дублирует runbook'и.

---

## 0. Заморозка кода

- [ ] **0.1** `master` зелёный: `make total-test` exit 0.
- [ ] **0.2** Integration suite: `RUN_INTEGRATION=1 uv run pytest tests/integration -q` exit 0.
- [ ] **0.3** `git status` чистый, нет незакоммиченных файлов кроме explicitly-allowed (например `.env.example` локальные правки — НЕ деплоим).
- [ ] **0.4** Tag commit: `git tag -a v0.1.0-launch -m "production cutover"` (не пушим автоматически).

## 1. Telegram credentials

- [ ] **1.1** Создать **новый** prod-бот через `@BotFather` (не reuse dev-токена).
- [ ] **1.2** `/setname`, `/setdescription`, `/setuserpic` в BotFather.
- [ ] **1.3** `/setprivacy → Enable` (бот не читает чужие сообщения в группах).
- [ ] **1.4** Сохранить токен в **VPS-локальный** `/etc/ai-steward-wiki/env` (mode 0600, owner `aisw-bot:aisw-bot`):
      `AISW_TG_BOT_TOKEN_PROD=<token>`
      `AISW_ENV=vps`
- [ ] **1.5** НЕ commit'ить токен в git и НЕ копировать в `.env.example`.

## 2. Allowlist

- [ ] **2.1** Создать `/etc/ai-steward-wiki/users.toml` с реальными `telegram_id` первого юзера (mode 0640, owner `aisw-bot:aisw-bot`):
      ```toml
      schema_version = 1
      [[users]]
      telegram_id = <real_id>
      role = "admin"
      ```
- [ ] **2.2** `AISW_USERS_TOML_PATH=/etc/ai-steward-wiki/users.toml` добавить в `/etc/ai-steward-wiki/env`.
- [ ] **2.3** Verify TOML parses: `uv run python -c "from ai_steward_wiki.auth.users_toml import load_users_toml; print(load_users_toml('/etc/ai-steward-wiki/users.toml'))"`.

## 3. Claude CLI auth

- [ ] **3.1** На VPS под UID `aisw-cli` (sudo): `sudo -u aisw-cli env CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code claude login`.
- [ ] **3.2** Browser auth flow — завершить через subscription account (НЕ API token).
- [ ] **3.3** Verify: `sudo -u aisw-cli env CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code claude --version` отвечает без re-auth prompt.
- [ ] **3.4** `chmod 0700 /var/lib/ai-steward-wiki/claude-code` (только `aisw-cli` читает).

## 4. Storage init

- [ ] **4.1** `mkdir -p /var/lib/ai-steward-wiki/{jobs,audit,sessions,snapshots,wiki}` + chown `aisw-bot:aisw-bot`, mode `0750`.
- [ ] **4.2** Set DB URLs в `/etc/ai-steward-wiki/env`:
      `AISW_JOBS_DB_URL=sqlite+aiosqlite:////var/lib/ai-steward-wiki/jobs/jobs.db`
      `AISW_AUDIT_DB_URL=sqlite+aiosqlite:////var/lib/ai-steward-wiki/audit/audit.db`
      `AISW_SESSIONS_DB_URL=sqlite+aiosqlite:////var/lib/ai-steward-wiki/sessions/sessions.db`
- [ ] **4.3** Dry-run миграций как `aisw-bot`:
      `sudo -u aisw-bot env $(cat /etc/ai-steward-wiki/env | xargs) /opt/ai-steward-wiki/.venv/bin/alembic -c /opt/ai-steward-wiki/alembic/jobs/alembic.ini upgrade head --sql` (повторить для audit, sessions).
- [ ] **4.4** Apply: запустить без `--sql` (либо положиться на auto-migrate в `__main__.py`).
- [ ] **4.5** Verify schema: `sqlite3 /var/lib/ai-steward-wiki/audit/audit.db ".schema"` содержит `tg_updates`, `seen_files`, `dedup_hits`.

## 5. Systemd

- [ ] **5.1** `sudo install -m 0755 -D deploy/systemd/aisw-bot.service /etc/systemd/system/aisw-bot.service`.
- [ ] **5.2** Slice + sysusers: следуй `docs/runbook/deploy.md` §systemd.
- [ ] **5.3** `EnvironmentFile=/etc/ai-steward-wiki/env` в unit — verify.
- [ ] **5.4** `systemctl daemon-reload`.
- [ ] **5.5** `systemctl enable --now aisw-bot.service`.
- [ ] **5.6** `systemctl status aisw-bot.service` → `active (running)`.
- [ ] **5.7** `journalctl -u aisw-bot.service -n 100 --no-pager` → видно `runtime.start`, `runtime.migrations.done` (×3), `runtime.allowlist.loaded`, `runtime.scheduler.started`, `runtime.handlers.registered`, `runtime.polling.start`. Нет `ERROR`/`CRITICAL`.

## 6. Smoke session (реальный TG bot ↔ реальный Claude CLI)

Из Telegram под admin telegram_id из §2.1:

- [ ] **6.1** **Text turn:** «Привет, расскажи про себя в трёх предложениях». Жду ответ ≤90s. Ответ — связный текст на русском.
- [ ] **6.2** **Voice turn:** записать 5-секундное voice «Какая сегодня дата». Жду ответ. Логи: `tg.pipeline.voice.received` → `tg.pipeline.classify.done` → `tg.pipeline.runner.dispatched`.
- [ ] **6.3** **Photo turn:** отправить любое фото с подписью «Что на фото?». Жду ответ. Лог `tg.pipeline.photo.received`.
- [ ] **6.4** **Document turn:** отправить маленький .txt («тест cutover»). Жду reply.
- [ ] **6.5** **Confirm callback:** дождаться граф. confirm-кнопок, нажать «Подтвердить». Лог `tg.pipeline.confirm.received` → `confirm.resolve.confirmed`.
- [ ] **6.6** **Idempotency:** повторить text turn (1) дословно. Лог `tg.pipeline.text.l1_duplicate` (или L2 dedup hit). Ответа не приходит / приходит idempotent.

## 7. Background jobs

- [ ] **7.1** `journalctl -u aisw-bot.service | grep "scheduler.maintenance"` показывает зарегистрированные cron'ы: snapshot, retention purge, staging sweep, onboarding pending purge.
- [ ] **7.2** Manually trigger snapshot (или подождать 03:00 UTC): verify `/var/lib/ai-steward-wiki/snapshots/<date>_audit.db` появился.
- [ ] **7.3** Per-WIKI git: после первой text-turn в §6.1 — `cd /var/lib/ai-steward-wiki/wiki/<wiki_name> && git log --oneline | head -3` показывает commit'ы формата `<job_id>(<category>): <title>`.

## 8. PII / security verification

- [ ] **8.1** Отправить тестовое сообщение с email/phone в боте: «my email is leak@example.com».
- [ ] **8.2** `journalctl -u aisw-bot.service --since "5 min ago" | grep -E "example.com|leak"` → **пусто** (tier-1 DROP сработал).
- [ ] **8.3** `journalctl -u aisw-bot.service --since "5 min ago" | grep "REDACTED"` или masked-hash → present.
- [ ] **8.4** Verify file modes: `/etc/ai-steward-wiki/env` = 0600, `/var/lib/ai-steward-wiki/claude-code` = 0700, `users.toml` = 0640.
- [ ] **8.5** `ss -tlnp | grep aisw` → бот НЕ слушает входящие порты (long-polling out-only).

## 9. Backup verification (DR readiness)

- [ ] **9.1** Manually trigger `db_snapshot` job (или дождаться 03:00 UTC).
- [ ] **9.2** Restore drill на staging-копию: следуй `docs/runbook/restore.md` step 1-4 на копии snapshot'а, не на live DB.
- [ ] **9.3** Verify `VACUUM INTO` snapshot имеет тот же `audit.tg_updates` row-count, что live.

## 10. Monitoring baseline

- [ ] **10.1** `journalctl -u aisw-bot.service -p err --since "1 hour ago"` → пусто.
- [ ] **10.2** `systemctl show aisw-bot.service -p MemoryCurrent` → разумно (<500MB на старте).
- [ ] **10.3** Записать в operations.md baseline-метрики: RSS, idle CPU%, log volume/hour. Это reference для будущих регрессий.

## 11. Sign-off

- [ ] **11.1** Все пункты 0–10 — `[x]`.
- [ ] **11.2** Commit подписанного чек-листа: `git commit -m "chore(launch): production cutover sign-off"` + `git push` (по явному запросу пользователя).
- [ ] **11.3** `bd remember "Production cutover signed off YYYY-MM-DD, commit <sha>, tag v0.1.0-launch"`.
- [ ] **11.4** Каталог `docs/superpowers/plans/20260511-ai-steward-wiki-launch/` — НЕ редактировать после этого момента. Дальнейшие изменения — через новые epics.

**Дата подписания:** `____________`
**Подписал:** `____________`
**Production commit:** `____________`
