---
feature: start-help-manual
bd_id: aisw-s5i
status: discovery
date: 2026-05-13
requirements:
  fr:
    - id: FR-1
      title: /start handler — branched by user state
      body: >
        Bot MUST register a `/start` command handler. For an unknown
        `telegram_id` (not in allowlist) it invokes the existing
        `auth.onboarding.start_unknown_user` + `format_intro_message`
        pipeline (D-030). For an approved (allowlisted) user it renders
        a new template `templates/start-known.ru.md` containing a short
        greeting, brief how-to-start, and pointers to `/help` and `/manual`.
    - id: FR-2
      title: /help handler — capabilities + command list
      body: >
        `/help` MUST render `templates/help.ru.md` for any user (approved
        or pending). Content includes the D-041 mandatory WIKI-explainer
        paragraph verbatim, a short list of main scenarios (note, reminder,
        digest, voice/photo), and a cheat-sheet of all six commands.
    - id: FR-3
      title: /manual handler — extended scenarios
      body: >
        `/manual` MUST render `templates/manual.ru.md` for any user. Content
        expands on `/help` with worked scenarios (creating a WIKI by NL,
        setting a recurring digest, expanding a digest section, toggling
        sections).
    - id: FR-4
      title: Template loader — slug-validated, reusable
      body: >
        A slug-validated template loader (already exists as
        `auth.onboarding._render_intro_template` /
        `format_intro_message`) MUST be extracted or extended so the three
        new templates and the existing `onboarding-intro.ru.md` share one
        code path. Slugs in templates are validated against a per-template
        allowlist; missing/extra slugs raise at load time.
    - id: FR-5
      title: Native TG command menu
      body: >
        At bot startup the runtime MUST call `bot.set_my_commands([...])`
        with all six commands (`/start`, `/help`, `/manual`, `/digest_now`,
        `/expand`, `/digest_sections`) and Russian descriptions, so the
        Telegram client shows them in the `≡` menu.
    - id: FR-6
      title: Auth middleware pass-through
      body: >
        `tg/middleware_auth.py` MUST allow `/help` and `/manual` from any
        `telegram_id` (same exemption family as `/start`), so unknown
        users can read about the bot before requesting access. The
        middleware MUST NOT, however, allow `/digest_*` or `/expand` for
        non-allowlisted ids.
  nfr:
    - id: NFR-1
      title: RU-only
      body: All user-facing strings in Russian (D-032 MVP). No i18n catalog.
    - id: NFR-2
      title: SSoT
      body: >
        Command text lives in `templates/*.md`, not in code. Code only
        renders. Tests assert that templates load with required slugs;
        text correctness is reviewed via PR diff.
    - id: NFR-3
      title: TDD
      body: >
        Each handler and loader change goes through RED → GREEN → REFACTOR.
        Unit tests cover loader (happy + malformed), each handler branch
        (known / unknown for /start; flat for /help, /manual), and
        middleware exemption logic.
    - id: NFR-4
      title: Observability
      body: >
        structlog events per existing convention — `tg.command.start`,
        `tg.command.help`, `tg.command.manual` with `correlation_id`,
        `owner_telegram_id`, `state` (known|pending|unknown). No PII in
        logs.
    - id: NFR-5
      title: D-041 text compliance
      body: >
        `/help` template MUST contain the exact WIKI-explainer paragraph
        defined in D-041 (load-bearing — referenced by other specs).
    - id: NFR-6
      title: Idempotent /start for unknown
      body: >
        `/start` from unknown id repeated → pending_users row refreshed,
        not duplicated (already guaranteed by `start_unknown_user`).
  constraints:
    - id: C-1
      body: aiogram 3.x router pattern, asyncio, no blocking I/O in handlers.
    - id: C-2
      body: Template path is `templates/` at repo root (project convention).
    - id: C-3
      body: No new dependencies — use stdlib + existing aiogram.
  risks:
    - id: R-1
      body: >
        `set_my_commands` is cached by Telegram clients; description
        changes propagate with delay. Acceptable — content lives in
        templates, native menu only shows command names.
      mitigation: Document delay in CLAUDE.md (one-line note).
    - id: R-2
      body: >
        Adding /help and /manual for unknown ids could be abused for
        reconnaissance / amplification.
      mitigation: >
        Rate-limit via existing middleware if present; otherwise accept
        in MVP — content is public-by-design.
    - id: R-3
      body: >
        `auth.onboarding.format_intro_message` is slug-tight (exact match
        required). Extracting a generic loader risks regression in the
        existing intro flow.
      mitigation: >
        Keep existing function as adapter calling the new generic loader.
        Re-run existing onboarding tests as part of Step 11.
    - id: R-4
      body: >
        D-041 mandatory paragraph might be quoted with minor drift if
        copied by hand.
      mitigation: >
        Plan step lifts the exact text from `docs/Spec-WIKI/decisions/D-041-no-direct-wiki-commands.md`
        via Read tool and pastes verbatim into the template.
  dependencies:
    - aiogram 3.15 (existing)
    - auth.onboarding.PendingUserRepo (existing, D-030)
    - auth.allowlist (existing, D-031/D-042)
    - tg.middleware_auth (existing, modified)
    - templates/onboarding-intro.ru.md (existing reference)
  scope:
    in:
      - Three new templates (start-known, help, manual) — ru.
      - Three new handlers in tg/handlers.py.
      - Shared slug-validated template loader (extract/reuse).
      - bot.set_my_commands at startup.
      - Middleware exemption for /help, /manual.
      - Unit tests for loader, handlers, middleware.
    out:
      - English / multi-language support (D-032 defers).
      - Self-signup full flow (ENABLE_SELF_SIGNUP=false stays).
      - /admin namespace (D-028, out of MVP).
      - Read-only WIKI commands /wiki_list, /wiki_show (D-041, separate feature).
      - Rate-limiting middleware overhaul.
    later:
      - i18n catalog when EN support lands.
      - Dynamic command list (admin sees more commands).
  use_cases:
    - id: UC-1
      actor: unknown user
      flow: sends `/start` → middleware pass → handler routes to onboarding.start_unknown_user → bot replies with formatted intro template, instructing to wait for admin.
    - id: UC-2
      actor: approved user, first contact
      flow: sends `/start` → middleware pass → handler renders start-known.ru.md → user sees greeting + pointer to /help and /manual.
    - id: UC-3
      actor: any user
      flow: sends `/help` → middleware pass (new exemption) → handler renders help.ru.md → user sees D-041 WIKI-explainer + cheat-sheet of 6 commands.
    - id: UC-4
      actor: any user
      flow: sends `/manual` → middleware pass (new exemption) → handler renders manual.ru.md → user sees worked scenarios.
    - id: UC-5
      actor: telegram client (UI)
      flow: on bot startup `set_my_commands` populates native `≡` menu with 6 commands and RU descriptions; user discovers commands without typing.
---

# Discovery — `/start`, `/help`, `/manual`

## Intent

User-facing onboarding & help surface. Currently the bot has only three
slash commands and all of them are digest-utility (`/digest_now`,
`/expand`, `/digest_sections`). A first-time user who types `/start` gets
no greeting; a confused user has no `/help` to read; the project lacks a
public-facing usage manual. We add three commands to close that gap
without touching the conversational-NL primary UX (D-041).

## Real Goal

Make the bot discoverable and self-explanatory for both newcomers
(unknown ids → see pending instructions) and approved users
(see what to type, how to use NL for WIKI/reminders/digests). Keep
content as git-tracked templates so wording evolves via PR, not code
change.

## Pre-existing Infrastructure (verified)

1. `auth/onboarding.py` — `PendingUserRepo`, `start_unknown_user`,
   `format_intro_message`, slug-validated loader. Half of `/start` unknown
   path already implemented; **handler wiring missing**.
2. `tg/middleware_auth.py` already lets `/start` through for unknown ids
   (`_START_COMMAND_PREFIX = "/start"`, comment: "chunk 12").
3. `templates/onboarding-intro.ru.md` — slug-разметка
   (`<!-- slug:greeting -->`, etc.), targets pending users.
4. `tg/handlers.py` — existing pattern: Command handler + ru
   `_*_RU` constants + structlog.
5. No `set_my_commands` call anywhere in the codebase — needs adding to
   bot startup module (TBD which file in Step 6 GRACE ASK).

## Spec Alignment

1. **D-030** (onboarding) — covers unknown `/start` path. Implemented.
2. **D-041** (no-direct-wiki-commands) — mandates verbatim WIKI-explainer
   paragraph in `/start` and `/help`. Will live in templates.
3. **D-032** (multi-language) — `/start`, `/cancel`, `/help` listed as
   system strings, MVP ru-only.
4. **D-033** (chat-history) — `/start` acks must be logged (existing
   chat_log path handles this if writer is wired; verify in Step 6).

## Blind Spots

1. **`set_my_commands` is global per bot, not per user** — same six
   commands shown to admin and regular user. Admin-only commands are
   out of scope here.
2. **/manual is NOT in any spec** — net-new concept; we own its naming
   and content.
3. **Existing `format_intro_message` is tightly coupled** to the intro
   template's slug set (`greeting/purpose/capabilities/privacy/next-steps/contact`).
   Generic loader must be parameterised by allowed-slug list.
4. **/cancel** is referenced in D-032 but not implemented — explicit
   non-goal here, do not silently add.

## Open Questions for Brainstorming

1. Where exactly does `set_my_commands` belong — `runtime/bot.py`?
   `__main__.py`? (resolved by GRACE ASK in Step 6).
2. Should `/help` for unknown ids omit `/digest_*` from the cheat-sheet
   (since they cannot use them) or show all with a note? Recommend:
   show all + small note.
3. Should `/start` from a `pending` user (already in pending_users) show
   "your request is pending" instead of re-running `start_unknown_user`?
   `start_unknown_user` is documented idempotent → re-run is fine and
   refreshes TTL; user-facing text identical. No change needed.

## Best Practices Cross-Check

1. Telegram BotFather convention: every bot ships `/start` + `/help`
   minimum; `/menu` and `/about` are common third commands. `/manual`
   instead of `/about` is acceptable — clearer naming for this project.
2. aiogram 3 idiom: `bot.set_my_commands([BotCommand(...)])` in a startup
   hook (`on_startup` dispatcher event or directly after dispatcher init).
3. Template separation from code — standard for i18n-ready apps.
