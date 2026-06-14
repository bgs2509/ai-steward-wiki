# ADR-008: Dev and life assistants run as separate bots on separate servers

- Status: Accepted
- Date: 2026-06-14
- Deciders: @bgs
- Related: ADR-009 (config dir), ADR-010 (isolation), ADR-001 (superseded)

## Context

The `ai-steward` family of services spans two fundamentally different kinds of
work:

- **dev** — software engineering: GRACE, `feature-workflow`, TDD, commit
  conventions, `MODULE_CONTRACT`, knowledge graphs. Governed by dev-type
  `CLAUDE.md` files full of process rituals.
- **life** — personal knowledge management: health, family, budget, study,
  hobby. Karpathy LLM Wiki style, conversational, no engineering rituals.

Running both kinds of work through **one bot on one host** mixes incompatible
instruction contexts:

1. **Instruction bleed via CLAUDE.md auto-discovery.** Claude Code walks parent
   directories from its working directory to find `CLAUDE.md`. A dev project's
   `CLAUDE.md` (GRACE / feature-workflow / commit rules) can be picked up while
   serving a life task, pushing the model to apply engineering rituals where
   they make no sense — and the reverse. Mixed, contradictory instructions are
   a known source of instruction-collision hallucinations.
2. **Shared identity.** One Telegram token = one bot identity for both audiences;
   a test/dev mishap can reach life users.
3. **Shared blast radius.** One host means a bug, resource spike, or compromise
   in dev tooling affects life data, and vice versa.

The runtime already has a concrete anti-bleed mechanism for (1): `neutral_cwd()`
runs the Claude CLI inside the config dir specifically so it does **not**
auto-discover any project `CLAUDE.md` (`claude_cli/common.py`), and the system
prompt is injected explicitly via `--system-prompt` (replaces the default). This
ADR lifts that principle from a code detail to a deployment rule.

## Decision

1. **Cross-cutting principle.** Any **dev**-type service and any **life**-type
   service are deployed as **separate Telegram bots** (separate tokens =
   separate identities) on **separate servers**. This is the standing default,
   not a per-case judgement.
2. **`ai-steward-wiki` is classified as exclusively a life service** (Karpathy
   LLM Wiki for Health / Family / Budget / etc.). It carries **no** dev
   workload. Its repository is a `dev`-type project only in the sense that the
   *code that runs the bot* is engineered with GRACE/feature-workflow; the
   *runtime assistant it exposes to users* is life-only.
3. **Instruction isolation is partially enforced at runtime** by `neutral_cwd()`
   (avoids **project-layer** `CLAUDE.md` auto-discovery) and explicit
   `--system-prompt` injection.
   **Correction (2026-06-14):** these do **not** fully isolate instructions. The
   **user-layer** memory file `~/.claude/CLAUDE.md` loads unconditionally on every
   CLI run regardless of `cwd`, `CLAUDE_CONFIG_DIR`, `--system-prompt`, or
   `--setting-sources ""` (verified, claude 2.1.175). Running the life bot under
   `bgs`, whose global `~/.claude/CLAUDE.md` is dev-oriented (GRACE /
   feature-workflow / beads), means those dev instructions **do** bleed into the
   life bot's runs. A real fix (e.g. `--bare` + API key, or a life-clean global
   `CLAUDE.md` for the run user) is tracked separately in `aisw-aqo`.

This ADR is authored from the `ai-steward-wiki` repository and records a
principle that also governs services **outside** this repo (notably
`ai-steward`). Those services are not managed here; the principle is recorded
for consistency and enforced per-service.

## Alternatives considered

1. **Single bot, single host, mode-switch by user/project.** Rejected:
   `CLAUDE.md` auto-discovery + a shared token produce instruction bleed and a
   shared blast radius. The mode-switch lives only in the prompt, which is
   exactly the layer most prone to injection/collision.
2. **Two bots (two tokens) but one shared server.** Separates identity but not
   blast radius, resource isolation, or filesystem-level instruction context.
   Acceptable as a *transition* state, rejected as the standing principle.

## Consequences

Positive:

- Clean separation of instruction contexts → fewer cross-domain hallucinations.
- Independent identity, blast radius, lifecycle, scaling, and auth per service.
- The in-service `env`-based token split (`local` test bot vs `prod` bot) stays
  as the *within-a-service* test/prod boundary — see ADR-009.

Negative / follow-ups:

- More infrastructure to operate (two hosts, two bots).
- "Separate server" is satisfied: `ai-steward-wiki` runs on its own dedicated
  VPS (`vpn-gpu-1`). It runs under the developer account `bgs`, **not** a
  dedicated service user — this is a deliberate choice (see ADR-010), not a gap
  to close.
