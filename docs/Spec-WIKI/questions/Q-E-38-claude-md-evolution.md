# Q-E-38: Schema эволюция `CLAUDE.md`

**Tier:** E
**Источник:** [overview §9 п.38](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

Авто-миграция существующих WIKI при обновлении шаблона или только новые.

## Варианты

1. **A. Только новые.** Существующие не трогаем — bit-rot гарантирован.
2. **B. Авто-миграция + diff в TG.** Bot показывает diff и просит подтверждение.
3. **C. Версионирование схемы** (`schema_version: N` во frontmatter `CLAUDE.md`); upgrade-миграции.

## Решение

- [x] оформлено как [D-039](../decisions/D-039-claude-md-evolution.md): `schema_version` + `template_id` во frontmatter, managed-sections (`<!-- BEGIN/END MANAGED:name -->`), 3-way merge declarative + imperative escape-hatch, TG diff-confirm (graduated explicit), git-commit миграции через D-037.

## Связанные

1. [LLM Wiki method](../concepts/llm-wiki-method.md)
