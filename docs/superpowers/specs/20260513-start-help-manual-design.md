---
feature: start-help-manual
bd_id: aisw-s5i
status: design
date: 2026-05-13
depends_on_discovery: 20260513-start-help-manual-discovery.md
technology:
  stack:
    - name: aiogram
      version: "3.15"
      use: "Router commands, BotCommand, set_my_commands"
      verified_in_codebase: true
    - name: structlog
      version: "(existing)"
      use: "Per-handler event logs"
      verified_in_codebase: true
  modules_touched:
    - id: M-TG-HANDLERS
      path: src/ai_steward_wiki/tg/handlers.py
      change: add three new Command handlers; import shared template loader
    - id: M-TG-BOT
      path: src/ai_steward_wiki/tg/bot.py
      change: add register_bot_commands(bot) helper
    - id: M-TG-MIDDLEWARE-AUTH
      path: src/ai_steward_wiki/tg/middleware_auth.py
      change: generalise _START_COMMAND_PREFIX → _PUBLIC_COMMAND_PREFIXES frozenset
    - id: M-TEMPLATES
      path: src/ai_steward_wiki/templates.py
      change: NEW — package-level slug-validated template loader
    - id: M-AUTH-ONBOARDING
      path: src/ai_steward_wiki/auth/onboarding.py
      change: format_intro_message becomes thin adapter over M-TEMPLATES
    - id: M-RUNTIME
      path: src/ai_steward_wiki/__main__.py
      change: call register_bot_commands(bot) after build_dispatcher
  new_files:
    - templates/start-known.ru.md
    - templates/help.ru.md
    - templates/manual.ru.md
    - tests/unit/test_templates.py
    - tests/unit/tg/test_start_help_manual_handlers.py
    - tests/unit/tg/test_register_bot_commands.py
  api_verifications_needed:
    - aiogram.types.BotCommand signature (Context7 — first contact in session)
    - bot.set_my_commands kwargs (Context7)
---

# Design — `/start`, `/help`, `/manual` (V2 from /best-approach)

## Solution Summary

Template-first architecture. Three Markdown templates own the wording.
A package-level slug-validated loader (`src/ai_steward_wiki/templates.py`)
renders any template given an allowlist of slugs. Three new thin handlers
in `tg/handlers.py` call the loader. A new `register_bot_commands(bot)`
helper in `tg/bot.py` is called once at startup. Middleware's existing
`/start` exemption is generalised to a small set including `/help` and
`/manual`.

## Architecture

### Module map (new + changed)

```
ai_steward_wiki/
├── templates.py                       # NEW: render_template(path, allowed_slugs, **vars)
├── auth/
│   └── onboarding.py                  # CHANGED: format_intro_message → thin adapter
├── tg/
│   ├── bot.py                         # CHANGED: + register_bot_commands(bot)
│   ├── handlers.py                    # CHANGED: + /start, /help, /manual handlers
│   └── middleware_auth.py             # CHANGED: _PUBLIC_COMMAND_PREFIXES frozenset
└── __main__.py                        # CHANGED: await register_bot_commands(bot)
```

### Data / Flow

1. **Loader** — pure function:
   ```python
   def render_template(
       path: pathlib.Path,
       *,
       required_slugs: frozenset[str],
       **format_vars: str,
   ) -> str:
       """Read markdown, validate slug set matches required_slugs exactly,
       join blocks with double newline, format with format_vars.

       Raises:
         FileNotFoundError - if path missing
         TemplateSlugMismatch - if slugs ≠ required_slugs
         KeyError - via str.format on missing var
       """
   ```
2. **`format_intro_message`** keeps the same signature for back-compat;
   internally delegates to `render_template(..., required_slugs=INTRO_SLUGS)`.
3. **`/start` handler**:
   ```python
   if allowlist.is_allowed(tg_id):
       text = render_template(START_KNOWN_PATH, required_slugs=START_KNOWN_SLUGS, bot_name=bot_name)
   else:
       row = await pending_repo.upsert(...)        # existing
       text = format_intro_message(bot_name=bot_name)  # existing wrapper
   await message.answer(text)
   ```
4. **`/help`, `/manual` handlers** — flat: load template, answer.
5. **`register_bot_commands`**:
   ```python
   async def register_bot_commands(bot: Bot) -> None:
       await bot.set_my_commands([
           BotCommand(command="start", description="Знакомство и приветствие"),
           BotCommand(command="help", description="Что умеет бот и список команд"),
           BotCommand(command="manual", description="Расширенные сценарии и примеры"),
           BotCommand(command="digest_now", description="Сделать сводку сейчас"),
           BotCommand(command="expand", description="Развернуть раздел сводки"),
           BotCommand(command="digest_sections", description="Настроить разделы сводки"),
       ])
   ```
6. **Middleware**:
   ```python
   _PUBLIC_COMMAND_PREFIXES: frozenset[str] = frozenset({"/start", "/help", "/manual"})

   def _is_public_command(text: str | None) -> bool:
       if not text:
           return False
       head = text.split(maxsplit=1)[0].split("@", 1)[0]  # strip @botname
       return head in _PUBLIC_COMMAND_PREFIXES
   ```

### Template structure (slug-разметка, same as `onboarding-intro.ru.md`)

`templates/start-known.ru.md` slugs:
- `greeting` — короткое приветствие + что это
- `how-to-start` — три практических примера NL-ввода (заметка / напоминание / WIKI)
- `commands-hint` — короткая фраза «команды: /help, /manual, /digest_now, …»
- `pointers` — «подробнее — /help; расширенные сценарии — /manual»

`templates/help.ru.md` slugs:
- `intro` — одно предложение про назначение
- `wiki-explainer` — **load-bearing** D-041 verbatim paragraph
- `scenarios` — нумерованный список 5–7 основных сценариев
- `commands` — cheat-sheet всех 6 команд, у `/digest_*` пометка «нужен одобренный доступ»
- `next-steps` — «начни с короткой фразы — я разберусь»

`templates/manual.ru.md` slugs:
- `intro` — назначение этого документа
- `scenario-note` — детальный сценарий «сохрани заметку»
- `scenario-wiki` — детальный сценарий «заведи WIKI для X»
- `scenario-reminder` — детальный сценарий «напомни …»
- `scenario-digest` — детальный сценарий «настрой ежедневную сводку»
- `scenario-expand-toggle` — `/expand` и `/digest_sections` worked examples
- `voice-photo` — что можно слать голосом / фото
- `privacy-note` — короткое напоминание про приватность

### Constants (in `templates.py`)

```python
INTRO_SLUGS = frozenset({"greeting","purpose","capabilities","privacy","next-steps","contact"})
START_KNOWN_SLUGS = frozenset({"greeting","how-to-start","commands-hint","pointers"})
HELP_SLUGS = frozenset({"intro","wiki-explainer","scenarios","commands","next-steps"})
MANUAL_SLUGS = frozenset({"intro","scenario-note","scenario-wiki","scenario-reminder","scenario-digest","scenario-expand-toggle","voice-photo","privacy-note"})
```

## Log Anchors

1. `tg.command.start.known` — `owner_telegram_id`, `correlation_id`.
2. `tg.command.start.unknown` — `owner_telegram_id`, `correlation_id`, `pending_state`.
3. `tg.command.help` — `owner_telegram_id`, `correlation_id`, `is_allowed`.
4. `tg.command.manual` — `owner_telegram_id`, `correlation_id`, `is_allowed`.
5. `runtime.bot.commands.registered` — `n_commands`.
6. `templates.render.fail` — `path`, `error_class` (no slug values logged — PII-free).

## Verification Plan

### Unit tests

1. `tests/unit/test_templates.py`:
   1. `render_template_happy` — all 4 existing+new templates load + slug-match.
   2. `render_template_missing_file` raises `FileNotFoundError`.
   3. `render_template_extra_slug` raises `TemplateSlugMismatch`.
   4. `render_template_missing_slug` raises `TemplateSlugMismatch`.
   5. `render_template_missing_var` raises `KeyError`.
   6. `format_intro_message_backcompat` — golden text equals current output.
2. `tests/unit/tg/test_start_help_manual_handlers.py`:
   1. `/start` known → answer contains greeting marker + no pending_repo call.
   2. `/start` unknown → pending_repo.upsert called + answer contains intro marker.
   3. `/help` → answer contains D-041 marker substring + cheat-sheet of 6 cmds.
   4. `/manual` → answer contains all 8 scenario slugs.
   5. structlog events emitted with expected event names.
3. `tests/unit/tg/test_register_bot_commands.py`:
   1. Calls `bot.set_my_commands` once with 6 commands.
   2. All descriptions are non-empty RU strings.
4. `tests/unit/tg/test_middleware_auth.py` (existing — extend):
   1. `/help` and `/manual` from unknown id → passes through.
   2. `/digest_now` from unknown id → blocked (no behaviour change).

### Integration / regression

1. Existing onboarding tests still pass (back-compat for `format_intro_message`).
2. `make lint` + `make total-test` green.

## Code-quality cross-check (17 principles)

- **DRY** ✅ — one loader for 4 templates.
- **KISS** ✅ — handlers ≤20 lines each, no state machine.
- **YAGNI** ✅ — no i18n abstraction, no /admin command, no /cancel.
- **SoC** ✅ — content in md, parsing in templates.py, dispatch in handlers.py.
- **SSoT** ✅ — `templates/*.md` is the only place for command wording; D-041 paragraph lifted verbatim once.
- **Security** ✅ — no user input reaches template format vars (`bot_name` is server-side const).
- **Fail Fast** ✅ — slug mismatch raises at load (not at render time).
- **Explicit > Implicit** ✅ — `required_slugs` is a parameter, not inferred.
- **Composition** ✅ — `format_intro_message` becomes a 2-line wrapper.
- **Testability** ✅ — pure loader + Bot mock for handler tests.

No red flags (no `except: pass`, no god class, no magic numbers).

## Open Questions (resolved in design)

1. **Loader location**: package root `templates.py`. Rationale: not auth-specific anymore (`/help`, `/manual` are not auth).
2. **set_my_commands location**: helper in `tg/bot.py`, invoked from `__main__.py` after `build_dispatcher` and before `dp.start_polling`. Rationale: keeps `__main__.py` thin; helper is independently testable.
3. **/help for unknown**: shows all 6 commands; digest-trio gets parenthetical «(нужен одобренный доступ)». Rationale: discoverability > strict gating.
4. **/start from pending user**: same as unknown — `start_unknown_user` is idempotent. Rationale: simpler handler, identical UX.
5. **D-041 verbatim source**: exact paragraph at `docs/Spec-WIKI/decisions/D-041-no-direct-wiki-commands.md:183` lines 184–186. Read-and-paste at Step 11.
