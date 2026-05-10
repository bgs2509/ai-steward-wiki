---
feature: auth-users-allowlist
bd_id: aisw-hnl
epic: aisw-fm0
chunk: 3
module_id: M-AUTH-USERS
status: stable
date: 2026-05-10
fr:
  - FR-1: Load users.toml from disk; parse TOML; validate via Pydantic v2 schema
  - FR-2: In-memory allowlist cache keyed by telegram_id (canonical, D-042)
  - FR-3: Hot-reload via SIGHUP (primary)
  - FR-4: Hot-reload via watchdog filesystem observer (fallback, 500ms debounce)
  - FR-5: Validate-before-swap — invalid file keeps current cache, emits admin-alert
  - FR-6: Sync sessions.db.users on every successful reload (upsert by telegram_id; disable removed)
  - FR-7: Public API — is_allowed(telegram_id) -> bool, get_user(telegram_id) -> UserRecord | None
  - FR-8: Admin-alert hook injectable (callable) for parse/validation failures
nfr:
  - NFR-1: Reload latency <200ms p95 for typical (≤100 users) file
  - NFR-2: Watchdog debounce 500ms (D-031)
  - NFR-3: Atomic swap (single assignment of frozen mapping)
  - NFR-4: structlog events on every load attempt with correlation_id
constraints:
  - users.toml uses telegram_id as canonical identity (D-042); telegram_username NOT identity
  - sessions.db.users is sync target only — never authoritative (D-031)
  - No FK from jobs.db / audit.db to users (INV-10)
risks:
  - parse-error mid-flight: mitigated by validate-before-swap + keep prior cache
  - watchdog double-fire: mitigated by 500ms debounce with per-path timer reset
  - SIGHUP races watchdog: both feed same reload coroutine, idempotent on no-change (sha256 guard)
scope_in:
  - users_toml.py — schema + loader
  - allowlist.py — cache + sync + public API
  - sighup.py — SIGHUP handler + watchdog observer wiring
scope_out:
  - /admin elevate flow (chunk 12)
  - pending_users onboarding (chunk 12)
  - actual TG admin-alert delivery (callback stub here; real wiring in chunk 10/12)
---

# Discovery: M-AUTH-USERS (Chunk 3)

Identity allowlist with hot-reload. Implements D-031, D-042, D-030 §"Allowlist".

## Sources
- spec §9 «Allowlist» (lines 550-558)
- D-031 — SIGHUP primary + watchdog 500ms debounce; validate-before-swap
- D-042 — users.toml unified SSoT; canonical key telegram_id

## Open Questions
None — D-031/D-042 closed.
