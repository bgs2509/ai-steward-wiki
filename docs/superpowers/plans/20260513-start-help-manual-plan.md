# Plan — `/start`, `/help`, `/manual` TG commands

- **bd_id:** aisw-s5i
- **Discovery:** `docs/superpowers/specs/20260513-start-help-manual-discovery.md`
- **Design:** `docs/superpowers/specs/20260513-start-help-manual-design.md`
- **Status:** ready-for-execution

> TDD discipline: every code task is RED → GREEN → REFACTOR. Tests first. Commit per phase using Conventional Commits + GRACE MODULE_ID scope.

---

## Phase A — Extract slug-validated loader to package root

**Goal:** Move slug-validation logic out of `auth/onboarding.py` into a new
package-level module `src/ai_steward_wiki/templates.py` so handlers can reuse
it. `format_intro_message` becomes a thin back-compat adapter.

### A.1 RED — write loader tests

Create `tests/unit/test_templates.py`:

```python
# FILE: tests/unit/test_templates.py
from pathlib import Path
import pytest
from ai_steward_wiki.templates import (
    TemplateError, render_template,
)

def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p

def test_render_template_happy(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\nhello {bot_name}\n<!-- slug:b -->\nbye")
    out = render_template(p, required_slugs=frozenset({"a","b"}), bot_name="Aisw")
    assert "hello Aisw" in out and "bye" in out

def test_render_template_extra_slug(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\nx\n<!-- slug:b -->\ny\n<!-- slug:c -->\nz")
    with pytest.raises(TemplateError, match="extra"):
        render_template(p, required_slugs=frozenset({"a","b"}))

def test_render_template_missing_slug(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\nx")
    with pytest.raises(TemplateError, match="missing"):
        render_template(p, required_slugs=frozenset({"a","b"}))

def test_render_template_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_template(tmp_path / "nope.md", required_slugs=frozenset({"a"}))

def test_render_template_missing_var(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\n{missing}")
    with pytest.raises(KeyError):
        render_template(p, required_slugs=frozenset({"a"}))
```

Run: `uv run pytest tests/unit/test_templates.py` → expect FAIL (module not exists).

### A.2 GREEN — implement `templates.py`

Create `src/ai_steward_wiki/templates.py`:

```python
# FILE: src/ai_steward_wiki/templates.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Slug-validated markdown template loader for user-facing strings.
#   SCOPE: render_template(path, required_slugs, **vars), TemplateError.
#   DEPENDS: pathlib, re
#   LINKS: D-030, D-032, D-041, M-AUTH-ONBOARDING, M-TG-HANDLERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   render_template - read template, validate slug set matches required_slugs, format
#   TemplateError - raised on slug mismatch
# END_MODULE_MAP

from __future__ import annotations
import re
from pathlib import Path

__all__ = ["TemplateError", "render_template"]

_SLUG_RE = re.compile(r"<!--\s*slug:([a-z][a-z0-9-]*)\s*-->")


class TemplateError(ValueError):
    """Raised when template slugs do not match the required set."""


# START_CONTRACT: render_template
#   PURPOSE: Load markdown template, validate slug-разметка matches required_slugs, format.
#   INPUTS: { path: Path - template file, required_slugs: frozenset[str], **format_vars: str }
#   OUTPUTS: { str - rendered text }
#   SIDE_EFFECTS: file read only
#   LINKS: M-AUTH-ONBOARDING (back-compat), M-TG-HANDLERS (consumer)
# END_CONTRACT: render_template
def render_template(
    path: Path,
    *,
    required_slugs: frozenset[str],
    **format_vars: str,
) -> str:
    text = path.read_text(encoding="utf-8")  # may raise FileNotFoundError
    found = frozenset(m.group(1) for m in _SLUG_RE.finditer(text))
    missing = required_slugs - found
    extra = found - required_slugs
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={sorted(missing)}")
        if extra:
            parts.append(f"extra={sorted(extra)}")
        raise TemplateError(f"slug mismatch in {path}: {'; '.join(parts)}")
    return text.format(**format_vars)
```

Run tests → expect GREEN.

### A.3 REFACTOR — `format_intro_message` becomes adapter

Edit `src/ai_steward_wiki/auth/onboarding.py`:

```python
INTRO_REQUIRED_SLUGS: frozenset[str] = frozenset(REQUIRED_SLUGS)  # convert existing tuple/list

def format_intro_message(
    template_path: Path,
    *,
    bot_name: str = "ai-steward-wiki",
    locale: str = "ru",
) -> str:
    if locale != "ru":
        raise OnboardingTemplateError(f"unsupported locale {locale!r} (D-032: ru-only MVP)")
    from ai_steward_wiki.templates import TemplateError, render_template
    try:
        return render_template(template_path, required_slugs=INTRO_REQUIRED_SLUGS, bot_name=bot_name)
    except FileNotFoundError as exc:
        raise OnboardingTemplateError(f"cannot read template {template_path}: {exc}") from exc
    except TemplateError as exc:
        raise OnboardingTemplateError(str(exc)) from exc
```

Run `uv run pytest tests/unit/auth/` → existing onboarding tests must still pass (regression check).

Add golden-output test `test_format_intro_message_backcompat` to `tests/unit/test_templates.py` reading the real `templates/onboarding-intro.ru.md` and asserting it renders (≥ 100 chars, contains substring «WIKI-ассистент»).

### A.4 Commit

```
test(M-TEMPLATES): RED tests for slug-validated render_template
feat(M-TEMPLATES): slug-validated template loader at package root
refactor(M-AUTH-ONBOARDING): format_intro_message → adapter over M-TEMPLATES
```
(Squash to single commit if all green.)

---

## Phase B — Create three new RU templates

**Goal:** Add `templates/start-known.ru.md`, `templates/help.ru.md`, `templates/manual.ru.md` with slug-разметка per design.

### B.1 `templates/start-known.ru.md`

Slugs: `greeting`, `how-to-start`, `commands-hint`, `pointers`.

Content draft (RU):

```markdown
<!-- slug:greeting -->
Привет! Я — {bot_name}, твой персональный WIKI-ассистент. Доступ открыт, можно начинать.

<!-- slug:how-to-start -->
Просто пиши обычным языком — текстом, голосом или фото. Примеры:
1. «Сегодня давление 120/80» — сохраню в твою Health-WIKI.
2. «Напомни завтра в 9 утра позвонить врачу» — поставлю напоминание.
3. «Давай заведём вики для путешествий» — создам новую WIKI с твоего подтверждения.

<!-- slug:commands-hint -->
Все служебные команды видны в меню Telegram слева от поля ввода (значок ≡).

<!-- slug:pointers -->
Подробнее о возможностях — /help. Расширенные сценарии и примеры — /manual.
```

### B.2 `templates/help.ru.md`

Slugs: `intro`, `wiki-explainer`, `scenarios`, `commands`, `next-steps`.

```markdown
<!-- slug:intro -->
{bot_name} — это персональный WIKI-ассистент. Я помогаю вести изолированную базу знаний по доменам (здоровье, финансы, учёба и т.д.) и работаю на естественном языке.

<!-- slug:wiki-explainer -->
WIKI — это твоя персональная AI-библиотека знаний по теме (здоровье, финансы, путешествия и т.д.). Каждая WIKI знает свои правила: например, `Health-WIKI` не диагностирует, `Investment-WIKI` не даёт инвест-советов. Ты не управляешь ими напрямую — просто скажи мне «давай заведём вики для X» или «удали Y-WIKI», я сам всё сделаю и спрошу подтверждение.

<!-- slug:scenarios -->
Что я умею:
1. Сохранять заметки текстом, голосом и фото — я сам разложу по нужным разделам.
2. Создавать и удалять персональные WIKI по доменам — командуй обычным языком.
3. Ставить напоминания на естественном языке («напомни завтра в 9 …»).
4. Делать ежедневные сводки (digest) с твоей статистикой и WIKI-выжимками.
5. Расшифровывать голосовые и распознавать фото/документы.

<!-- slug:commands -->
Доступные команды:
1. /start — приветствие.
2. /help — это сообщение.
3. /manual — расширенные сценарии и примеры.
4. /digest_now — сделать сводку сейчас (нужен одобренный доступ и настроенный дайджест).
5. /expand <раздел> — развернуть один раздел сводки (today | meds | trackers | wiki).
6. /digest_sections — включить/выключить разделы дайджеста.

<!-- slug:next-steps -->
Если не уверен с чего начать — просто напиши, что ты хочешь сделать. Я разберусь и переспрошу, если нужно.
```

### B.3 `templates/manual.ru.md`

Slugs: `intro`, `scenario-note`, `scenario-wiki`, `scenario-reminder`, `scenario-digest`, `scenario-expand-toggle`, `voice-photo`, `privacy-note`.

```markdown
<!-- slug:intro -->
{bot_name} — расширенный гид по сценариям. Здесь — пошаговые примеры.

<!-- slug:scenario-note -->
Сохранить заметку. Просто напиши то, что хочешь запомнить:
- «Сегодня вес 78.4 кг» — попадёт в Health-WIKI трекеры.
- «Купил акции X на 50000» — попадёт в Investment-WIKI.
Я сам определю домен и переспрошу, если непонятно.

<!-- slug:scenario-wiki -->
Создать WIKI для новой темы:
1. Напиши: «давай заведём вики для путешествий».
2. Я задам 1–3 уточняющих вопроса (цель, есть ли уже похожая WIKI, тип данных).
3. После твоего «да» создам структуру и подтвержу.
Удаление: «удали Travel-WIKI» — переношу в `_trash/` на 30 дней, можно восстановить.

<!-- slug:scenario-reminder -->
Поставить напоминание естественным языком:
- «Напомни завтра в 9 позвонить врачу».
- «Каждый понедельник в 8 утра напомни принять витамины».
Я пойму время даже без явной даты, переспрошу при неоднозначности.

<!-- slug:scenario-digest -->
Настроить ежедневную сводку:
1. Напиши: «делай сводку каждый день в 9».
2. Я подтвержу расписание и включу дайджест.
3. Каждое утро ты будешь получать резюме: задачи на сегодня, лекарства, трекеры, выжимка из WIKI.

<!-- slug:scenario-expand-toggle -->
Управление сводкой служебными командами:
1. /digest_now — запустить сводку прямо сейчас (вне расписания).
2. /expand meds — развернуть раздел «лекарства» подробнее. Допустимые разделы: `today`, `meds`, `trackers`, `wiki`.
3. /digest_sections — кнопками включить/выключить разделы (например, отключить «trackers», если нет трекеров).

<!-- slug:voice-photo -->
Голос и фото:
- Голосовое сообщение — расшифрую через faster-whisper.
- Фото документа/чека/таблицы — распознаю через OCR.
- Аудиофайл или видео-кружок — тоже принимаю.
Дальше обрабатываю как обычный текст.

<!-- slug:privacy-note -->
Приватность: твои данные хранятся изолированно — каждая WIKI принадлежит только тебе, другие пользователи их не видят. Логи бота не содержат личных данных.
```

### B.4 Verify templates render

Add to `tests/unit/test_templates.py`:

```python
def test_real_templates_render() -> None:
    from ai_steward_wiki.templates import render_template
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "templates"
    cases = [
        ("onboarding-intro.ru.md", frozenset({"greeting","purpose","capabilities","privacy","next-steps","contact"})),
        ("start-known.ru.md", frozenset({"greeting","how-to-start","commands-hint","pointers"})),
        ("help.ru.md", frozenset({"intro","wiki-explainer","scenarios","commands","next-steps"})),
        ("manual.ru.md", frozenset({"intro","scenario-note","scenario-wiki","scenario-reminder","scenario-digest","scenario-expand-toggle","voice-photo","privacy-note"})),
    ]
    for name, slugs in cases:
        out = render_template(root / name, required_slugs=slugs, bot_name="ai-steward-wiki")
        assert len(out) > 100, name
```

Run → expect GREEN.

### B.5 Commit

```
feat(M-TG-HANDLERS): RU templates for /start, /help, /manual (D-032, D-041 verbatim)
```

---

## Phase C — Middleware exemption + three handlers

### C.1 Middleware: generalise `/start` exemption

Edit `src/ai_steward_wiki/tg/middleware_auth.py`:

1. Replace `_START_COMMAND_PREFIX = "/start"` with:
   ```python
   _PUBLIC_COMMAND_PREFIXES: frozenset[str] = frozenset({"/start", "/help", "/manual"})
   ```
2. Rename `_is_start_command` → `_is_public_command`; update prefix-match to use `in _PUBLIC_COMMAND_PREFIXES`.
3. Update bypass log event: `auth.deny.bypass_public` (with `command=head`).
4. Bump `LAST_CHANGE` in `START_CHANGE_SUMMARY` to v0.0.3.

**RED** — extend `tests/unit/tg/test_middleware_auth.py`:
- /help from unknown id → passes (handler called).
- /manual from unknown id → passes.
- /digest_now from unknown id → blocked (deny path).
- /start from unknown id → still passes (regression).

**GREEN** — apply the edits above.

### C.2 Handlers — `/start`, `/help`, `/manual`

**RED** — create `tests/unit/tg/test_start_help_manual_handlers.py`:

```python
# Pseudocode (real tests use FakeMessage + FakeBot mocks like existing handler tests).
async def test_start_known() -> None: ...    # allowlist hit → answers with greeting marker
async def test_start_unknown() -> None: ...  # pending_repo.upsert called, intro substring
async def test_help_any() -> None: ...       # answer contains D-041 marker
async def test_manual_any() -> None: ...     # answer contains scenario markers
```

**GREEN** — edit `src/ai_steward_wiki/tg/handlers.py`:

1. Add module-level constants:
   ```python
   _TEMPLATES_DIR: Path = Path(__file__).resolve().parents[2] / "templates"
   _START_KNOWN_SLUGS = frozenset({"greeting","how-to-start","commands-hint","pointers"})
   _HELP_SLUGS = frozenset({"intro","wiki-explainer","scenarios","commands","next-steps"})
   _MANUAL_SLUGS = frozenset({"intro","scenario-note","scenario-wiki","scenario-reminder","scenario-digest","scenario-expand-toggle","voice-photo","privacy-note"})
   _BOT_NAME = "ai-steward-wiki"
   ```
2. Add handlers (inside `build_router`):

```python
@router.message(Command("start"))
async def _on_start(message: Message, **data: Any) -> None:
    # START_BLOCK_HANDLER_START
    if message.from_user is None or message.chat is None:
        _log.debug("tg.handlers.start.skip_missing_fields")
        return
    owner = message.from_user.id
    is_pending = bool(data.get("is_pending", False))
    if not is_pending:
        text = render_template(
            _TEMPLATES_DIR / "start-known.ru.md",
            required_slugs=_START_KNOWN_SLUGS,
            bot_name=_BOT_NAME,
        )
        _log.info("tg.command.start.known", owner_telegram_id=owner)
        await message.answer(text)
        return
    # unknown id — onboarding flow
    try:
        await pipeline.on_start_unknown(owner=owner, username=message.from_user.username)
    except Exception as exc:
        _log.warning("tg.command.start.unknown.failed", owner_telegram_id=owner, error_class=type(exc).__name__)
        await message.answer(_GENERIC_ERR_RU)
        return
    text = format_intro_message(_TEMPLATES_DIR / "onboarding-intro.ru.md", bot_name=_BOT_NAME)
    _log.info("tg.command.start.unknown", owner_telegram_id=owner)
    await message.answer(text)
    # END_BLOCK_HANDLER_START

@router.message(Command("help"))
async def _on_help(message: Message, **data: Any) -> None:
    # START_BLOCK_HANDLER_HELP
    if message.from_user is None: return
    text = render_template(_TEMPLATES_DIR / "help.ru.md", required_slugs=_HELP_SLUGS, bot_name=_BOT_NAME)
    _log.info("tg.command.help", owner_telegram_id=message.from_user.id, is_allowed=not data.get("is_pending"))
    await message.answer(text)
    # END_BLOCK_HANDLER_HELP

@router.message(Command("manual"))
async def _on_manual(message: Message, **data: Any) -> None:
    # START_BLOCK_HANDLER_MANUAL
    if message.from_user is None: return
    text = render_template(_TEMPLATES_DIR / "manual.ru.md", required_slugs=_MANUAL_SLUGS, bot_name=_BOT_NAME)
    _log.info("tg.command.manual", owner_telegram_id=message.from_user.id, is_allowed=not data.get("is_pending"))
    await message.answer(text)
    # END_BLOCK_HANDLER_MANUAL
```

3. Add `pipeline.on_start_unknown(...)` method or a thin direct call to `PendingUserRepo.upsert` — investigate which exists. **If `pipeline` lacks the method, call `start_unknown_user` directly through an injected repo dependency.** Verify in Step 11 by reading `tg/pipeline.py`.

4. Update `M-TG-HANDLERS` MODULE_MAP at file top with new exports/handlers and bump CHANGE_SUMMARY.

**REFACTOR**: extract `_render(path, slugs)` helper inside handlers.py to dedupe the 3-arg call.

### C.3 Commit

```
feat(M-TG-MIDDLEWARE-AUTH): generalise /start exemption to /help, /manual
feat(M-TG-HANDLERS): /start, /help, /manual handlers via M-TEMPLATES
```

---

## Phase D — Register bot commands (native TG menu)

### D.1 RED — `tests/unit/tg/test_register_bot_commands.py`

```python
import pytest
from unittest.mock import AsyncMock
from aiogram.types import BotCommand
from ai_steward_wiki.tg.bot import register_bot_commands

@pytest.mark.asyncio
async def test_register_bot_commands_calls_set_my_commands() -> None:
    bot = AsyncMock()
    await register_bot_commands(bot)
    assert bot.set_my_commands.await_count == 1
    args, kwargs = bot.set_my_commands.call_args
    cmds = args[0] if args else kwargs["commands"]
    assert {c.command for c in cmds} == {"start","help","manual","digest_now","expand","digest_sections"}
    for c in cmds:
        assert isinstance(c, BotCommand) and c.description
```

### D.2 GREEN — add helper to `tg/bot.py`

```python
async def register_bot_commands(bot: "Bot") -> None:
    """Publish the bot's command list so Telegram clients show the native ≡ menu."""
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="Знакомство и приветствие"),
        BotCommand(command="help", description="Что умеет бот и список команд"),
        BotCommand(command="manual", description="Расширенные сценарии и примеры"),
        BotCommand(command="digest_now", description="Сделать сводку сейчас"),
        BotCommand(command="expand", description="Развернуть раздел сводки"),
        BotCommand(command="digest_sections", description="Настроить разделы сводки"),
    ]
    _log.info("runtime.bot.commands.registered", n_commands=len(commands))
    await bot.set_my_commands(commands)
```

(Use module-level `_log = structlog.get_logger("tg.bot")` if not already present.)

Update MODULE_MAP + CHANGE_SUMMARY of `tg/bot.py`.

### D.3 Wire from `__main__.py`

After `bot = build_bot(...)` and `dp = build_dispatcher(...)`, before `dp.start_polling(bot)`:

```python
await register_bot_commands(bot)
```

Add import: `from ai_steward_wiki.tg.bot import register_bot_commands` (or import alongside existing `build_bot`).

### D.4 Commit

```
feat(M-TG-BOT,M-RUNTIME): register_bot_commands publishes native TG menu
```

---

## Phase E — Verify

1. `uv run pytest tests/unit -x` → all green.
2. `make lint` → ruff + format + mypy + grace lint clean.
3. `make total-test` (if available locally) — coverage ≥ 80% on touched modules.
4. `grace-refresh` — sync knowledge-graph.xml and verification-plan.xml.
5. Manual smoke (local bot token, optional): send `/start`, `/help`, `/manual` — eyeball rendering, check structlog events in journald.

---

## Self-review checklist (against Discovery FR/NFR)

- [ ] FR-1 /start branched — Phase C handler covers both branches.
- [ ] FR-2 /help — Phase C handler renders `help.ru.md`.
- [ ] FR-3 /manual — Phase C handler renders `manual.ru.md`.
- [ ] FR-4 loader extracted — Phase A.
- [ ] FR-5 set_my_commands — Phase D.
- [ ] FR-6 middleware exemption — Phase C.1.
- [ ] NFR-1 RU-only — all templates ru.
- [ ] NFR-2 SSoT — wording lives in `templates/`.
- [ ] NFR-3 TDD — RED tests in A.1, B.4, C.1, C.2, D.1.
- [ ] NFR-4 structlog — events listed in design.
- [ ] NFR-5 D-041 verbatim — lifted from `D-041-no-direct-wiki-commands.md:184–186`.
- [ ] NFR-6 idempotent /start — pending upsert preserves existing behaviour.
- [ ] No placeholders.
- [ ] No new deps.
- [ ] Plan fits in one context window (estimated ~30% of Opus 4.7 1M effective).
