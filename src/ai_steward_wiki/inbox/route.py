# FILE: src/ai_steward_wiki/inbox/route.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Resolve/create the target <Domain>-WIKI from a RouterDecision, stage the
#            raw payload into it, pick the Stage-1b domain overlay, and build the
#            librarian ingest prompt (aisw-zd9, Inbox-WIKI Phase-B).
#   SCOPE: RouteTarget/RouteRejection/RouteOutcome/StagedRaw dataclasses;
#          resolve_target_wiki, render_target_raw, stage_raw_into_wiki,
#          pick_domain_overlay, build_ingest_prompt.
#   DEPENDS: ai_steward_wiki.wiki.lifecycle (WikiLifecycleManager, AntiSpamCapError),
#            ai_steward_wiki.wiki.name (WikiName, WikiNameError),
#            ai_steward_wiki.inbox.staging (promote_path_to_raw),
#            ai_steward_wiki.inbox.router (RouterDecision, RouterIntent),
#            ai_steward_wiki.logging_setup
#   LINKS: D-004, smart-inbox-routing, M-INBOX-ROUTE, aisw-zd9
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   RouteTarget - resolved target WIKI (wiki_name, wiki_dir, created)
#   RouteRejection - cap / bad-name rejection with a ru hint
#   RouteOutcome - RouteTarget | RouteRejection
#   StagedRaw - what stage_raw_into_wiki wrote (sidecar_rel, media_rel, media_abs)
#   resolve_target_wiki - lookup-or-create the target <Domain>-WIKI for the owner
#   render_target_raw - (filename, content) for <wiki>/raw/<ts>_<source>.<ext>
#   stage_raw_into_wiki - write the raw sidecar + promote media binaries into <wiki>/raw/
#   pick_domain_overlay - prompts/domain-<slug>.md if it exists else domain-default.md
#   build_ingest_prompt - the ru Stage-1b ingest instruction referencing the raw paths
#   RouteAction - a staged route+ingest action awaiting user confirmation (Phase-C)
#   route_action_to_payload - serialise a RouteAction to a JSON-able dict for draft_json
#   route_action_from_payload - inverse: reconstruct a typed RouteAction from the dict
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-e45 (Phase-C): RouteAction + route_action_to/from_payload (confirm-loop draft round-trip)
#   PREVIOUS:    v0.0.1 - initial route+ingest helpers (aisw-zd9, Inbox-WIKI Phase-B)
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.inbox.staging import promote_path_to_raw
from ai_steward_wiki.logging_setup import get_logger
from ai_steward_wiki.wiki.lifecycle import AntiSpamCapError, WikiLifecycleManager
from ai_steward_wiki.wiki.name import WikiName, WikiNameError

__all__ = [
    "RouteAction",
    "RouteOutcome",
    "RouteRejection",
    "RouteTarget",
    "StagedRaw",
    "build_ingest_prompt",
    "pick_domain_overlay",
    "render_target_raw",
    "resolve_target_wiki",
    "route_action_from_payload",
    "route_action_to_payload",
    "stage_raw_into_wiki",
]

_log = get_logger(__name__)

_RU_CAP_HINT = "Достигнут лимит вики — удали ненужную и попробуй снова."
_RU_BAD_NAME_HINT = "Не смог разобрать имя вики — переформулируй."  # noqa: RUF001

_RawSource = Literal["text", "voice", "document", "photo"]


@dataclass(frozen=True, slots=True)
class RouteTarget:
    wiki_name: WikiName
    wiki_dir: Path
    created: bool


@dataclass(frozen=True, slots=True)
class RouteRejection:
    reason: Literal["cap", "bad_name"]
    hint: str


RouteOutcome = RouteTarget | RouteRejection


@dataclass(frozen=True, slots=True)
class StagedRaw:
    sidecar_rel: str  # "raw/<ts>_<source>.md" relative to the wiki dir
    media_rel: list[str]  # ["raw/media/<ISO8601>_<sha8>.<ext>", ...] relative
    media_abs: list[Path]  # absolute paths of promoted media (for run_wiki_session media_paths)


@dataclass(frozen=True, slots=True)
class RouteAction:
    """A staged route+ingest action awaiting user confirmation (Phase-C, aisw-e45).

    Persisted as JSON in pending_confirms.draft_json; replayed by the pipeline's
    on_confirm_callback to drive Librarian.ingest after the user taps Подтвердить.
    """

    decision: RouterDecision
    user_text: str
    source: _RawSource
    media_paths: list[str]  # POSIX path strings (re-hydrated to Path on replay)
    correlation_id: str


def route_action_to_payload(
    decision: RouterDecision,
    *,
    user_text: str,
    source: _RawSource,
    media_paths: list[Path] | None,
    correlation_id: str,
) -> dict[str, object]:
    """Serialise a route action to a plain JSON-able dict for draft_json."""
    return {
        "decision": decision.model_dump(mode="json"),
        "user_text": user_text,
        "source": source,
        "media_paths": [Path(p).as_posix() for p in (media_paths or [])],
        "correlation_id": correlation_id,
    }


def route_action_from_payload(payload: dict[str, object]) -> RouteAction:
    """Inverse of route_action_to_payload — reconstruct a typed RouteAction."""
    raw_decision = payload.get("decision")
    if not isinstance(raw_decision, dict):
        raise ValueError("route action payload missing 'decision' object")
    decision = RouterDecision(**raw_decision)
    raw_media = payload.get("media_paths") or []
    media = [str(p) for p in raw_media] if isinstance(raw_media, list) else []
    source = payload.get("source")
    if source not in ("text", "voice", "document", "photo"):
        raise ValueError(f"route action payload bad source: {source!r}")
    return RouteAction(
        decision=decision,
        user_text=str(payload.get("user_text", "")),
        source=cast(_RawSource, source),
        media_paths=media,
        correlation_id=str(payload.get("correlation_id", "")),
    )


# START_CONTRACT: resolve_target_wiki
#   PURPOSE: Turn a RouterDecision (ROUTE | CREATE_WIKI) into a concrete target WIKI dir
#            for the owner, creating it if needed; reject on cap / bad name.
#   INPUTS: { decision: RouterDecision, lifecycle: WikiLifecycleManager, owner: int,
#             wiki_root: Path, default_template_id: str, on_route_missing: Callable | None }
#   OUTPUTS: { RouteOutcome - RouteTarget on success, RouteRejection on cap/bad_name }
#   SIDE_EFFECTS: may create a <wiki_root>/<owner>/<Name>-WIKI/ dir + minimal CLAUDE.md
#                 (via WikiLifecycleManager.create_wiki); emits inbox.route.* logs.
#   LINKS: D-004, M-WIKI-LIFECYCLE, M-INBOX-ROUTE
# END_CONTRACT: resolve_target_wiki
def resolve_target_wiki(
    decision: RouterDecision,
    *,
    lifecycle: WikiLifecycleManager,
    owner: int,
    wiki_root: Path,
    default_template_id: str = "_default",
    on_route_missing: Callable[[], None] | None = None,
) -> RouteOutcome:
    # START_BLOCK_RESOLVE_TARGET
    raw_name = decision.target_wiki or ""
    if decision.intent is RouterIntent.ROUTE:
        existing = lifecycle.lookup(owner, raw_name)
        if existing is not None:
            return RouteTarget(existing, wiki_root / str(owner) / existing.primary, created=False)
        if on_route_missing is not None:
            on_route_missing()
        # fall through to create — the Router asserted it belongs there.
    existed_before = lifecycle.lookup(owner, raw_name) is not None
    try:
        name = lifecycle.create_wiki(owner, raw_name, default_template_id)
    except AntiSpamCapError:
        return RouteRejection(reason="cap", hint=_RU_CAP_HINT)
    except WikiNameError:
        return RouteRejection(reason="bad_name", hint=_RU_BAD_NAME_HINT)
    return RouteTarget(name, wiki_root / str(owner) / name.primary, created=not existed_before)
    # END_BLOCK_RESOLVE_TARGET


def render_target_raw(
    *, source: _RawSource, user_text: str, media_rel: list[str]
) -> tuple[str, str]:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{ts}_{source}.md"
    if source == "text":
        body = user_text if user_text.endswith("\n") else user_text + "\n"
        return filename, body
    lines = ["---", f"source: {source}", f"received_utc: {ts}"]
    if media_rel:
        lines.append("raw_media:")
        lines.extend(f"  - {p}" for p in media_rel)
    else:
        lines.append("raw_media: []")
    lines += ["---", "", "## Содержимое", "", user_text.rstrip("\n"), ""]
    return filename, "\n".join(lines)


# START_CONTRACT: stage_raw_into_wiki
#   PURPOSE: Materialise the user's raw payload inside the target WIKI before Stage-1b:
#            write a raw/<ts>_<source>.md entry and promote any media binary into raw/media/.
#   INPUTS: { wiki_dir: Path, source: _RawSource, user_text: str, media_paths: list[Path] | None }
#   OUTPUTS: { StagedRaw - sidecar_rel + media_rel (relative) + media_abs (absolute) }
#   SIDE_EFFECTS: mkdir <wiki>/raw[/media]; atomic-write the sidecar; os.replace each media
#                 (via inbox.staging.promote_path_to_raw); skips already-gone media (logged).
#   LINKS: D-022, M-INBOX (promote_path_to_raw), M-INBOX-ROUTE
# END_CONTRACT: stage_raw_into_wiki
def stage_raw_into_wiki(
    wiki_dir: Path,
    *,
    source: _RawSource,
    user_text: str,
    media_paths: list[Path] | None,
) -> StagedRaw:
    # START_BLOCK_STAGE_RAW
    media_abs: list[Path] = []
    media_rel: list[str] = []
    for src in media_paths or []:
        try:
            promoted = promote_path_to_raw(src, wiki_root=wiki_dir)
        except FileNotFoundError:
            _log.warning("inbox.route.media_missing", src=str(src), wiki_dir=str(wiki_dir))
            continue
        media_abs.append(promoted)
        rel = str(promoted.relative_to(wiki_dir))
        media_rel.append(rel)
        _log.info("inbox.route.raw_moved", src=str(src), dest=rel)
    filename, content = render_target_raw(source=source, user_text=user_text, media_rel=media_rel)
    raw_dir = wiki_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / filename
    tmp = raw_dir / f"{filename}.tmp"
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
    sidecar_rel = f"raw/{filename}"
    _log.info("inbox.route.raw_moved", src="text", dest=sidecar_rel)
    return StagedRaw(sidecar_rel=sidecar_rel, media_rel=media_rel, media_abs=media_abs)
    # END_BLOCK_STAGE_RAW


def pick_domain_overlay(prompts_dir: Path, slug: str) -> Path:
    candidate = prompts_dir / f"domain-{slug}.md"
    return candidate if candidate.exists() else prompts_dir / "domain-default.md"


def build_ingest_prompt(user_text: str, staged: StagedRaw) -> str:
    lines = [
        "Пользователь прислал материал для занесения в эту WIKI.",
        f"Текст обращения: {user_text}",
        f"Сырьё в этой WIKI: {staged.sidecar_rel}",
    ]
    if staged.media_rel:
        lines.append("Медиа-файлы: " + ", ".join(staged.media_rel))
        lines.append("Изображения и аудио открой инструментом Read.")
    lines.append(
        "Выполни ingest строго по схеме этой WIKI. Следуй секции «## Data layout» в "
        "CLAUDE.md: для каждого вида данных найди указанный там файл и допиши запись в "
        "его формате. Для CSV — добавь новую строку в УЖЕ существующий файл "  # noqa: RUF001
        "(`*.csv`); новый файл создавай только если подходящего ещё нет. "
        "Не создавай произвольные страницы или папки (например `pages/`) в обход "  # noqa: RUF001
        "«Data layout». Зафиксируй действие в `log.md` (append-only). Кратко ответь, "
        "что именно и в какой файл записал."
    )
    return "\n".join(lines)
