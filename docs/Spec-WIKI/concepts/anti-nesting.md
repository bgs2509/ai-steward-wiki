# Anti-nesting

**Тип:** concept
**Статус:** draft
**Источники:** [overview §3a п.4](../raw/20260507-ai-steward-wiki-only-overview.md), §5 п.2

## Правило

Папка с `WIKI` в имени **не может** находиться внутри другой папки с `WIKI` в имени. Бот при `/run`, `/wiki_init` и cron-запуске идёт от cwd вверх до `home_dir`; если хоть один ancestor содержит `WIKI` — запрос отклоняется ошибкой `NestedWikiNotAllowed`.

## Причины

1. Конфликт `raw/` — двойной ingest одного файла.
2. Конфликт `index.md` / `log.md` — двойной SSoT.
3. Конфликт `CLAUDE.md` — смешение схем разных доменов.
4. Неоднозначность детектора WIKI-папок.
5. Ломающиеся бэклинки.

## Следствие

1. Подкатегории внутри одной WIKI = **обычные папки без `WIKI` в имени**, без своих `raw/`/`index.md`/`log.md`/`CLAUDE.md`.
2. Один домен = одна WIKI = один артефакт.

## Граница для admin

Псевдокод §7a останавливается на `home_dir.parent`; для admin `home_dir` нет. См. [Q-A-08: anti-nesting boundary for admin](../questions/Q-A-08-lock-on-wiki.md) (вопрос смежный — уточнить в Tier C, Q24).

## Связанные

1. [Sibling-only domains](sibling-only-domains.md)
2. [Domain-WIKI](../entities/domain-wiki.md)
