# D-039: CLAUDE.md schema evolution — versioning + managed sections + 3-way merge + TG confirm

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-E-38](../questions/Q-E-38-claude-md-evolution.md), [D-016](D-016-inbox-claude-md-template.md), [D-017](D-017-domain-claude-md-template.md), [D-023](D-023-tg-confirmations.md), [D-037](D-037-git-in-wiki.md)

## Проблема

Шаблоны `CLAUDE.md` в WIKI ([D-016](D-016-inbox-claude-md-template.md), [D-017](D-017-domain-claude-md-template.md)) будут эволюционировать: новые секции, новые правила (например, после [D-037](D-037-git-in-wiki.md) — «git auto-commit», после [D-038](D-038-per-user-systemd.md) — «hard-isolation note», новые domain-пресеты). Существующие WIKI без миграции получают bit-rot — поведение расходится. Юзер мог добавить локальные правки в `CLAUDE.md` руками — slient overwrite уничтожит их.

## Варианты

1. **A — Только новые WIKI; существующие не трогаем.**
2. **B — Auto-migration + diff в TG, без version-tracking.**
3. **C — Schema versioning + managed sections + 3-way merge + TG diff-confirm.** ⭐
4. **D — C silent (без TG confirm).**
5. **E — C-lite без managed sections (full rewrite + warning).**

## Выбор

**Вариант C.**

### Frontmatter

Каждый сгенерированный `CLAUDE.md` получает YAML frontmatter:

```yaml
---
schema_version: 3
template_id: inbox-wiki        # или health-domain, career-domain, _default-domain, ...
last_migrated_at: 2026-05-09T17:30+03:00
template_sha256: a3f1b2c...     # хэш версии шаблона на момент рендера (дополнительный sanity)
---
```

### Managed sections

Шаблоны разделены на **managed** и **user-zone** части через HTML-комменты:

```markdown
<!-- BEGIN MANAGED:routing -->
## Inbox hint
Health-related queries: вопросы про симптомы, давление, лекарства.
<!-- END MANAGED:routing -->

## Локальные заметки юзера

Любой текст здесь — user-zone. Миграция не трогает.

<!-- BEGIN MANAGED:rules -->
## Правила
1. Не диагностировать.
2. Использовать UTC ISO 8601.
<!-- END MANAGED:rules -->
```

1. **Managed:** перезаписывается при миграции (с показом diff). Имя секции (`routing`, `rules`, ...) — стабильный ID для merge.
2. **User-zone:** всё, что **между** managed-блоками или **до/после** них. Никогда не модифицируется автоматически.
3. **Конфликты:** если юзер удалил `BEGIN MANAGED:routing` маркер — миграция распознаёт как «manually overridden»; запрашивает у юзера через TG (см. ниже) — re-insert / skip / abort.

### Миграция scripts

`src/ai_steward_wiki/templates/<template_id>/`:

```
inbox-wiki/
├── manifest.toml          # current_version = 4
├── v3.md.j2
├── v4.md.j2
└── migrations/
    ├── v3_to_v4.py        # def migrate(current_text: str) -> str
    └── ...
```

1. **Declarative path (default):** `apply_template(target_path, version=4)` рендерит `v4.md.j2` и применяет 3-way merge с `v3.md.j2` (base) + `target_path` (ours) → managed sections updated, user-zone preserved.
2. **Imperative path (escape hatch):** для нелинейных миграций — `migrations/v3_to_v4.py:migrate(text) -> text`. Используется когда managed-section была **переименована** или **разделена**.
3. **Linear chain:** v1 → v2 → v3 → v4 проходится по очереди; не перепрыгиваем версии.

### Trigger

1. **Startup scan:** при старте сервиса — `scan_wikis()` находит все `CLAUDE.md` и проверяет `schema_version`. Все WIKI с `schema_version < current` → попадают в очередь миграции.
2. **Cron:** APScheduler weekly job `claude_md_migration_check` — для long-running деплоев.
3. **Manual:** `/admin migrate <wiki>` ([D-028](D-028-admin-access.md) elevation) — force trigger для конкретной WIKI.

### TG confirmation flow

Для каждой WIKI с `schema_version < current`:

1. **Notification:** TG-сообщение юзеру (owner WIKI):
   ```
   📝 WIKI <name>: доступна миграция шаблона v3 → v4

   Изменения:
   ✏️ Обновлены правила routing
   ➕ Добавлена секция "git auto-commit"

   [📄 Показать diff] [✅ Применить] [⏭ Пропустить]
   ```
2. **«Показать diff»:** unified diff managed-секций; user-zone не показывается (не меняется).
3. **«Применить»:** apply migration → `git -C <wiki> commit -m "migrate(<template_id>): v3 → v4"` ([D-037](D-037-git-in-wiki.md)) → update frontmatter `last_migrated_at`.
4. **«Пропустить»:** `schema_version` остаётся; повторная notification через 7 дней (configurable `MIGRATION_REMINDER_DAYS`).
5. **Conflict (override detected):** если managed-marker пропал —
   ```
   ⚠️ WIKI <name>: managed-секция "routing" была изменена вручную.
   [🔁 Перезаписать (потеряются ручные правки)]
   [🤝 Merge через 3-way (требует ручного review)]
   [⏭ Пропустить]
   ```
6. Graduated confirmation level = explicit ([D-023](D-023-tg-confirmations.md)) — миграция destructive в смысле user intent.

### Storage of migration state

1. `audit.db.migrations(wiki_id, from_version, to_version, applied_at, applied_by, action)` — full history.
2. `action ∈ {applied, skipped, conflict_overwrite, conflict_merge, aborted}`.
3. Telegram notification deduplication: одна WIKI — одна active prompt; повтор через `MIGRATION_REMINDER_DAYS`.

### Initial bootstrap (existing WIKI without frontmatter)

1. Старые WIKI (созданные до D-039) не имеют `schema_version` во frontmatter.
2. **One-time bootstrap:** при первом startup-scan после D-039 — для каждой WIKI без frontmatter записывается `schema_version: 1, template_id: <best_guess>` через эвристику (имя папки → `template_id`).
3. Bootstrap commit: `git commit -m "bootstrap(<wiki>): assign schema_version=1, template_id=<id>"`.
4. Миграция с v1 на current — обычным flow.

### SSoT шаблонов

1. Шаблоны живут в коде сервиса (`src/.../templates/<template_id>/v<N>.md.j2`).
2. `manifest.toml` каждого `template_id` содержит `current_version: N`.
3. Изменения шаблона = разработческое изменение, code-review через PR (когда репо появится).
4. **Не править** шаблон через TG — explicit запрет, защита от ad-hoc drift.

## Последствия

1. Bit-rot решается systematic'но: explicit version + diff-aware merge.
2. User-customizations защищены через managed-zone модель.
3. Git-history миграций ([D-037](D-037-git-in-wiki.md)) даёт полный audit-trail.
4. UX cost: юзер видит migration-prompts; smoothed через graduated confirmation + reminder cooldown.
5. Bootstrap для существующих WIKI — automatic, one-time.
6. Запреты:
   1. **Не пропускать version chain** — миграции линейные v1→v2→...→vN.
   2. **Не править шаблоны через TG** — только code + PR.
   3. **Не auto-apply без confirm** — destructive в смысле user intent.
   4. **Не трогать user-zone** — все секции вне `BEGIN/END MANAGED` immutable для бота.
   5. **Не перепрыгивать declarative → imperative** без явной потребности (declarative по умолчанию).
   6. **Не bootstrap'ить без `git commit`** — каждая state-mutation = git snapshot.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-039-claude-md-evolution.md` (когда финализируется)
