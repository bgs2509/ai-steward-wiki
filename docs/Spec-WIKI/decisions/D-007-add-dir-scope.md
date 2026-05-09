# D-007: `--add-dir` = только `<wiki>`, профиль через CLAUDE.md auto-walk

**Статус:** accepted
**Дата:** 2026-05-08
**Контекст:** [Q-C-22](../questions/Q-C-22-add-dir-scope.md), overview §7.3 / §7a / §3a п.6, [D-004](D-004-inbox-wiki-scope.md)

## Проблема

Какую область охватывает `--add-dir` при запуске Claude CLI: только текущая WIKI, или включая родительский `USERS/<NAME>/` (для tool-доступа к профилю и/или sibling-WIKI).

## Варианты

1. **A. `--add-dir <wiki>` only.** Tool-scope = WIKI. Профиль `USERS/<NAME>/CLAUDE.md` доступен в context автоматически через Claude CLI CLAUDE.md auto-walk (ancestor от cwd). Минимум привилегий.
2. **B. `--add-dir <wiki> + файл профиля`.** Технически невозможно — `--add-dir` принимает только директории.
3. **C. `--add-dir <wiki> + USERS/<NAME>/`.** Tool-access на родителя, ломает §3a п.6 (cross-domain), нарушает sibling-isolation.

## Выбор

**Вариант A.** Юзер подтвердил 2026-05-08.

Обоснование:
1. Соответствует overview §7.3 default-стенсу: «`--add-dir`: только сама `<wiki>` папка».
2. Принцип минимальных привилегий (OWASP): по умолчанию закрыто.
3. Профиль уже доступен в context через CLAUDE.md chain auto-walk (поведение Claude CLI: walk вверх от cwd собирает все CLAUDE.md). Дополнительный `--add-dir` для этого не нужен.
4. Сохраняет sibling-isolation §3a п.6: Claude в `Health-WIKI` не дотянется до `Recipes-WIKI` через свои tools.
5. Совместимо с D-004 (per-user Inbox-WIKI) и path-traversal §7.2.

## Применение по kinds (D-002)

1. **`reminder_job`** — Claude CLI не запускается (D-002). Не применимо.
2. **`wiki_job`** — `--add-dir <wiki>`. Одна целевая WIKI.
3. **`digest_job`** — несколько `--add-dir <wiki1> --add-dir <wiki2> ...`, поимённо. **Не** `--add-dir USERS/<NAME>/`.
4. **`tracker_survey` / `tracker_followup` / `boundary_message`** — обычно TG-only, без Claude CLI; если нужен Claude — `--add-dir` на конкретную целевую WIKI или Inbox-WIKI юзера.
5. **Router в Inbox-WIKI** (D-004) — `--add-dir <USERS/<NAME>/Inbox-WIKI>`. Sibling-WIKI юзера видны через Inbox-CLAUDE.md как list-of-domains, не через tool-access.

## Последствия

1. CLI builder сервиса (`profile.build_claude_cmd()` из overview line 264) собирает аргументы по правилу: `cwd=<wiki>`, `--add-dir <wiki>` (один или несколько для digest), `--permission-mode acceptEdits`. Запрещено добавлять `USERS/<NAME>/` или любых её родителей.
2. Профиль юзера (`USERS/<NAME>/CLAUDE.md`) — read-only через CLAUDE.md chain. Если нужен write в профиль — отдельный механизм (TG-команда `/profile_update`, не Claude tools).
3. Cross-domain ingest невозможен ботом по умолчанию. Допустимый ручной случай (§3a п.6 (a)): юзер явно запускает `/run` с указанием `--add-dir` на соседнюю WIKI как read-only — это **ручное действие**, оформляется отдельным UX (Q-C-24, Q-D-25).
4. Admin-сценарии (Q-C-24, Q-D-25) могут использовать более широкий `--add-dir` — это отдельный ADR, не блокируется D-007.
5. Q-C-22 закрывается этим решением.

## Запреты

1. CLI builder не должен принимать на вход `USERS/<NAME>/` или его родителей в качестве `--add-dir`.
2. digest-job composer не может «оптимизировать» N ситблингов до одного `--add-dir USERS/<NAME>/` — только поимённый список.
3. Не вводить «debug-режим, в котором --add-dir шире» без отдельного ADR (override D-007).

## Перенос в ADR

- [ ] перенести в `docs/adr/ADR-NNN-add-dir-scope.md` при финализации.
