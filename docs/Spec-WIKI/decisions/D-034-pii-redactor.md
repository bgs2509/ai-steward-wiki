# D-034: PII redactor — tiered write-time, без at-rest crypto

**Статус:** accepted
**Дата:** 2026-05-09 (amended 2026-05-10 — identity fields aligned with D-042)
**Контекст:** [Q-E-33](../questions/Q-E-33-audit-pii.md), [D-033](D-033-chat-history.md), [D-006](D-006-state-storage-layout.md), [D-030](D-030-onboarding.md)

## Проблема

[D-033](D-033-chat-history.md) ввёл `chat_log` с plaintext + 30d retention + минимальным denylist (`sk-ant-`, `Bearer `, `password=`) и явно отложил полную redaction-policy в Q-E-33. Аналогично [D-006](D-006-state-storage-layout.md) `audit_events` хранит `command` + `prompt-hash` без plaintext, но `command` может содержать PII (например юзер прислал `/wiki_query "телефон врача 8-...`). Решить: какой PII попадает в redactor, write-time vs read-time, нужно ли SQLCipher/at-rest шифрование, hard-delete процедура.

## Варианты

1. **A — Status quo D-033:** минимальный denylist + 30d retention; без at-rest crypto.
2. **B — Tiered write-time redactor (NIST-style); без at-rest crypto.** ⭐
3. **C — B + SQLCipher для `audit.db`.**
4. **D — B + per-user `pii_redact_level` opt-in.**
5. **E — Drop chat_log целиком (откат D-033).**

## Выбор

**Вариант B.**

### Tier-классификация (NIST SP 800-122 inspired)

1. **Tier-1 — DROP (полностью удаляется, заменяется placeholder'ом).**
   1. API/auth tokens: `sk-…` (Anthropic), `xoxb-…` (Slack), `ghp_…` (GitHub PAT), `Bearer <token>`, JWT-shape `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`.
   2. Passwords: `password\s*[:=]\s*\S+`, `passwd\s*[:=]\s*\S+`, `pwd\s*[:=]\s*\S+`.
   3. Private keys: `-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----` … `-----END …-----`.
   4. Credit cards: 13–19 digits passing Luhn-checksum.
   5. Placeholder: `[REDACTED:tier1:<category>]`.

2. **Tier-2 — MASK (маскируется, shape сохраняется).**
   1. Email: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` → `j***@example.com` (первая буква + `***` + домен).
   2. Phone: `(\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}` → `+7***1234` (страна + `***` + last-4).
   3. IBAN/счёт: `\d{16,28}` (контекст-aware: bank context) → `****1234` (last-4).
   4. Placeholder вписывает hash оригинала рядом для cross-ref: `j***@example.com[#a3f1]`.

3. **Tier-3 — PLAINTEXT (защита через retention + unix-перм).**
   1. Имена, адреса, free-text сообщения.
   2. Защита: 30d retention (chat_log), 90d retention (audit_events.command), unix-перм 0600 на `state/*.db`.

### Применение

1. **Write-time hook** в `redactor.py` (новый модуль): принимает `text: str`, возвращает `RedactedText(text=str, redactions: list[RedactionEvent])`.
2. **Точки применения:**
   1. `chat_log.text` — перед INSERT для `direction='in'` (юзер прислал) **и** `direction='out'` (бот ответил, защита от echoing PII в reply).
   2. `audit_events.command` — перед INSERT.
   3. **structlog processor** ([D-035](D-035-service-logging.md)) — третья точка применения (защита application-логов).
3. **Read-time** — НЕ применяется. Redactor только write-time. Read-time post-redaction усложняет debug и не даёт реального gain (если plaintext попал в БД — уже compromise).
4. **Performance:** регексы pre-compile module-level; ожидаемый latency <1ms на typical TG-сообщение (≤4096 chars).

### Trash sweep (soft-delete WIKI / page → `_trash/`)

Дополнительная точка применения redactor'а на soft-delete операциях ([D-041](D-041-no-direct-wiki-commands.md) intent'ы `delete_wiki`, `page-delete`):

1. **Trigger:** atomic move `<Domain>-WIKI/` → `_trash/<Domain>-WIKI-<ts>/` (или page-level move).
2. **Sweep**: post-move hook рекурсивно проходит `*.md` файлы перенесённой ветки и применяет к содержимому write-time-ный pipeline (Tier-1 DROP, Tier-2 MASK in-place — `tmp + os.replace` per [D-012](D-012-wiki-lock.md) atomic-write convention; pre-compile regex те же).
3. **Tier-3 plaintext** не трогается (имена/адреса/free-text) — он защищён общей retention-политикой `_trash/` (30d, [D-041](D-041-no-direct-wiki-commands.md)) + unix-перм 0700 на родительском `_trash/`.
4. **Media (`raw/media/`)** sweep'ом не сканируется (immutable бинари per [D-022](D-022-voice-photo-input.md)) — на hard-delete (см. п.6) сносится `shred -u` для содержимого `raw/media/`.
5. **Audit:** sweep пишет `audit_events` `{event_type='trash_sweep', wiki, files_processed, redactions_count_by_tier}`.
6. **Hard-delete по истечении 30d** (`trash_purge` APScheduler-job per [D-041](D-041-no-direct-wiki-commands.md)): `shred -u` для media, `unlink` для `*.md`, audit-event `trash_purged`.
7. **Rationale:** soft-delete — *user-visible* момент «удалил». Если в страницах был tier-1/tier-2 PII, который попал туда in time когда regex-pack был слабее — это последний шанс убрать его до hard-delete. Дёшево (редкая операция, < single-digit раз в месяц per user) и closes loophole.

### At-rest crypto

1. **Не вводится в MVP.** Причины: single-tenant Henry-N ([D-013](D-013-claude-cli-auth.md)) — subject = owner данных; SQLCipher passphrase в `.env` рядом с БД = security theatre если VPS взломан; +зависимость `pysqlcipher3` усложняет backup/migration.
2. **Триггер пересмотра:** активный второй tenant через [D-030](D-030-onboarding.md) approve flow ИЛИ external compliance review.
3. **Defense сейчас:** unix-перм 0600 на `state/*.db`, dedicated UID `ai-steward-wiki`, hard isolation Claude CLI ([D-038](D-038-per-user-systemd.md)).

### Hard-delete процедура

1. Команда `/admin gdpr_purge <telegram_id>` (admin-only, доступна через [D-028](D-028-admin-access.md) elevation):
   ```sql
   DELETE FROM chat_log WHERE telegram_id=?;
   DELETE FROM audit_events WHERE telegram_id=?;
   DELETE FROM tracker_answers WHERE owner_telegram_id=?;
   ```
2. Логируется в `audit.db.admin_events` (что/когда/кем удалено + count).
3. Tier-1/Tier-2 redactions, попавшие до hard-delete, тоже стираются (они были в тех же rows).
4. Не purge: `users.toml` (это membership, не PII-данные); WIKI-папки юзера (отдельной командой `/admin user_remove <telegram_id>` — soft-delete per [D-031](D-031-allowlist-hot-reload.md), затем manual rm для full erasure).

### Retention

1. `chat_log` — 30d (D-033, без изменений).
2. `audit_events` — 90d (новый default; ранее не зафиксирован). Cleanup через APScheduler-job daily.
3. `tracker_answers` — 90d ([D-014](D-014-tracker-memory-model.md), без изменений).
4. Все настраиваемы через env: `CHAT_LOG_RETENTION_DAYS`, `AUDIT_EVENTS_RETENTION_DAYS`, `TRACKER_RETENTION_DAYS`.

### Audit redaction events

1. `audit_events` логирует **факт** redaction (не plaintext): `event_type='pii_redacted'`, `payload={tier, category, count}`. Помогает мониторингу качества regex-pack без leak'а самих данных.
2. False-positive feedback loop: если юзер замечает ошибочную маску (`/feedback redaction <id>`) — событие в `audit_events` для будущих regex-улучшений.

## Последствия

1. D-033 минимальный denylist расширяется до полного Tier-1/Tier-2.
2. Application-логи ([D-035](D-035-service-logging.md)) защищены через тот же redactor.
3. Multi-tenant ready ([D-030](D-030-onboarding.md)) без переписывания схемы.
4. SQLCipher отложен — meaningful только при multi-tenant + external compliance.
5. Hard-delete процедура — defensible default для GDPR-style запросов.
6. Запреты:
   1. **Не применять redactor read-time** — только write-time.
   2. **Не хранить regex-pack в БД** — pre-compile в `redactor.py` (audit-able через git).
   3. **Не отключать redactor** через env-flag — нет «turn off» ручки.
   4. **Не логировать original text** ни в одной точке (redacted version only).
   5. **Не вводить SQLCipher** до триггера multi-tenant + compliance — explicit gate.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-034-pii-redactor.md` (когда финализируется)
