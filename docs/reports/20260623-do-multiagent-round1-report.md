# do-multiagent Round-1 — completion report (2026-06-23)

## Scope

Parallel execution of a pre-triaged bead queue via `do-multiagent`: 4 worktree
sessions (disjoint write-scope), each running `do-feature --auto-approve`, with a
controller-owned serialised merge queue and independent re-verification (Trust=0%)
before every merge.

Origin: investigation of a production incident on `vpn-gpu-1` (`aisw-bot.service`,
journald `corr_id tg-1961`) — the message «повтори последний ответ» made the
stateless Inbox-WIKI router return a valid `reject` block but leak its internal
role into the `notes` field, which `pipeline.py` echoed to the user verbatim.

## Beads delivered (6, merged to local master)

1. **aisw-kml** — Wire D-033 `chat_log`: `ChatLogWriter` (write_in/write_out/
   read_recent_window), write-time denylist redaction (`sk-ant-`/`Bearer `/
   `password=`), last-20/24h window folded into `build_router_input` + Stage-0.
2. **aisw-0ym** — Active-WIKI sticky pointer: `UserActiveWiki` + alembic sessions
   `0004`; `ActiveWikiPointer.set_active`/`get_active` (24h TTL); cold
   CLARIFY/REJECT follow-ups default-route into the last-active WIKI.
3. **aisw-o3m** — Runner-parity argv hardening on the cron-user CLI path
   (`consumer.py`): `--setting-sources ""`, `--disable-slash-commands`,
   `--permission-mode dontAsk`, `--disallowedTools WebFetch`; positional
   invocation model kept (no stream-json switch); aisw-0j4 `--` guard intact.
4. **aisw-358** — `pypdf` 5.1.0 → 6.14.1 (pip-audit: 30 pypdf CVEs → 0); kept
   `==` pin; removed unused `project_mapping` (vulture).
5. **aisw-3lx** — Test isolation: maintenance-jobstore test points DBs at
   `tmp_path`/env-url (root cause: alembic env.py clobbers `sqlalchemy.url` with
   `AISW_*_DB_URL_SYNC`); also a pypdf-6.x-surfaced PDF fixture fix.
6. **aisw-rui** — Fixed YAML frontmatter in 17(+3) excluded specs; regenerated
   `requirements.xml`/`technology.xml` (now 39/39 discovery + 38/38 design parse).

## Beads closed by triage (5, no code)

- **aisw-9gz** (systemd unit) — done: service is systemd-managed + reboot-safe;
  dedicated slice deferred by ADR-010.
- **aisw-qxq** (`AISW_ENV=vps` /opt switch) — superseded by ADR-009/010 (prod
  runs from `~bgs`).
- **aisw-cig** (PriorityJobQueue consumer) — superseded: `CronConsumer` drain
  loop + `cron_user` producer already exist (aisw-02v/aisw-0j4).
- **aisw-at7** (superseded-config-dir banner) — won't-fix (dated history).
- **aisw-0d3** (clarify-loop + L2 false-skip) — mitigated by aisw-aca Phase 1.
- **aisw-aca** (EPIC create-named-WIKI) — resolved: full child defect chain
  closed (2z6/t6w/rz3/dm2/t45/zpn/378), Phase-1 commit `80417c8`, create+ingest
  path green-tested.

## Verification (controller, Trust=0%, on merged master)

- `make lint` exit 0 (ruff + ruff-format + mypy `--strict`, 95→97 src files).
- `grace lint --failOn errors` — 0 issues.
- `make inv-lint` — 14/14.
- alembic sessions `0003 → 0004` applies cleanly.
- `uv run pytest tests/unit` exit 0 (1037 passed); retention purge + GDPR delete
  still green; new-file coverage 97–98%.
- Merge queue: 4 branches, serialised `--no-ff`, 0 conflicts (disjoint scopes),
  worktrees + branches removed after each merge.

## Documentation synced (this change)

- `knowledge-graph.xml` — `M-STORAGE-AUDIT` (+ChatLogWriter/ChatTurn/redact),
  `M-STORAGE-SESSIONS` (+UserActiveWiki/ActiveWikiPointer), 2 new CrossLinks
  (`M-TG-PIPELINE-CLASSIFIER` → audit/sessions).
- `verification-plan.xml` — new tests added to V-M-STORAGE-AUDIT/-SESSIONS/
  -TG-PIPELINE-CLASSIFIER; o3m hardening noted in V-M-SCHEDULER-CONSUMER.
- `requirements.xml`/`technology.xml` — regenerated under aisw-rui.

## Deferred (open beads)

- **Round 2** (deferred by user): `aisw-90t` (voice/photo aggregation),
  `aisw-9sn` (name-override on confirm card) — both touch `tg/pipeline.py` +
  sessions, so they run after the memory cluster.

## Git state

All work on **local `master`** (10 commits over `b614abf`); **not pushed** (project
policy). Deploy when ready: `git push`, then on `vpn-gpu-1`
`git pull --ff-only` in `/home/bgs/works/ai-steward-wiki` +
`sudo systemctl restart aisw-bot.service`.
