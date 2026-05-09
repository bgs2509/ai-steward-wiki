# D-008: WIKI-маркер — regex `^[A-Z][A-Za-z0-9]*-WIKI$`

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-C-23](../questions/Q-C-23-wiki-marker-format.md), overview §5 / §7.1 / §7.2 / §7a / §3a, [D-004](D-004-inbox-wiki-scope.md)

## Проблема

Какое правило определяет «папка является WIKI». Используется в §5 (NotAWikiPath), §7.2 (path-traversal), §7a (anti-nesting), `/wiki_init` валидации. Текущий overview-default `"WIKI" in name.upper()` пропускает false positives (`WIKILEAKS-data`, `my-wiki-tmp`).

## Варианты

1. **A. Substring case-insensitive** (`"WIKI" in name.upper()`) — текущий, слабый.
2. **B. Strict suffix `-WIKI` (case-sensitive)** — `name.endswith("-WIKI")`. Без контроля `<Domain>` части.
3. **C. Regex `^[A-Z][A-Za-z0-9]*-WIKI$`** — полный whitelist `<Domain>-WIKI`.

## Выбор

**Вариант C.** Юзер подтвердил 2026-05-08.

Канонический regex (Python):

```python
import re
WIKI_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*-WIKI$")

def is_wiki_dir(name: str) -> bool:
    return bool(WIKI_NAME_RE.fullmatch(name))
```

Обоснование:
1. Соответствует всем зафиксированным примерам: `Health-WIKI`, `Recipes-WIKI`, `Study-WIKI`, `Schedule-WIKI`, `Expenses-WIKI`, `Crosslinks-WIKI`, `Inbox-WIKI` (D-004).
2. Защищает от false positives текущего substring-правила.
3. Convention enforced на уровне `/wiki_init` — невалидное имя отклоняется с понятной ошибкой.
4. Единое правило для §5, §7.2, §7a, anti-nesting walk.
5. Strict whitelist — best practice для security-relevant boundary.

## Грамматика имени WIKI-папки

1. Первый символ: латинская прописная `A-Z`.
2. Тело `<Domain>`: латинские буквы `A-Za-z` и цифры `0-9`, без пробелов, дефисов, подчёркиваний внутри `<Domain>`.
3. Суффикс: ровно `-WIKI` (тире + 4 заглавных латинских буквы).
4. Полное совпадение (`fullmatch`), без trailing whitespace, без расширений.

Примеры:
1. ✅ `Health-WIKI`, `Recipes-WIKI`, `Inbox-WIKI`, `Crosslinks-WIKI`, `Travel2025-WIKI`.
2. ❌ `WIKILEAKS-data`, `my-wiki-tmp`, `health-WIKI` (lowercase domain), `Здоровье-WIKI` (Cyrillic), `Health_WIKI` (underscore), `Health WIKI` (space), `-WIKI`, `1-WIKI`, `Health-Wiki` (lowercase suffix), `Health-WIKI/` (trailing slash в имени).

## Применение

1. **§5 NotAWikiPath**: `is_wiki_dir(basename(cwd))` вместо `"WIKI" in basename(cwd).upper()`.
2. **§7a anti-nesting**: walk вверх от cwd до `home_dir`, на каждом ancestor применять `is_wiki_dir(ancestor.name)` — если найден ⇒ `NestedWikiNotAllowed`.
3. **§7.2 path-traversal**: проверка финального target по тому же regex.
4. **`/wiki_init <name>`**: валидация имени до создания директории. Невалидное имя → ошибка с примером валидного.
5. **Discovery siblings юзера**: `[d for d in USERS/<NAME>/.iterdir() if d.is_dir() and is_wiki_dir(d.name)]`.
6. **`Inbox-WIKI`** (D-004) — канонический пример служебной WIKI, имя валидируется тем же regex.

## Последствия

1. Все existing checks `"WIKI" in name.upper()` (overview §5/§7.1/§7.2/§7a) заменяются на `is_wiki_dir(name)` при переносе в design/код.
2. `templates/inbox-wiki/` (D-004) рендерится в `Inbox-WIKI/` — имя соответствует regex.
3. При `/wiki_init` юзер получает ошибку с подсказкой: «имя должно соответствовать `<Domain>-WIKI` (например `Health-WIKI`), где `<Domain>` начинается с заглавной латинской буквы».
4. Если когда-нибудь понадобится Cyrillic / underscore / другая морфология — отдельный ADR расширяет regex (override D-008).
5. Q-C-23 закрывается этим решением.

## Запреты

1. Не использовать substring-проверку `"WIKI" in name.upper()` нигде в production-коде после внедрения D-008.
2. Не локализовать ошибку валидации именами на других языках (regex остаётся ASCII).
3. Не вводить «soft mode», в котором regex relaxed — это override D-008 через новый ADR.

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-wiki-marker-format.md` при финализации.
