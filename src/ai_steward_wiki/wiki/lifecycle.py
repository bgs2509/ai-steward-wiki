# FILE: src/ai_steward_wiki/wiki/lifecycle.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: WikiLifecycleManager — owner-scoped create / lookup / soft-delete /
#            restore with hard cap + Levenshtein <=2 anti-spam + atomic FS ops.
#   SCOPE: WikiLifecycleManager, AntiSpamCapError, NearDuplicateMatch, TrashedWiki.
#            INV-7 SSoT: this module is the *only* place allowed to mutate
#            wiki directories via os.replace.
#   DEPENDS: ai_steward_wiki.wiki.name, structlog, pydantic
#   LINKS: M-WIKI-LIFECYCLE, D-041, D-008, tech-spec §5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiLifecycleManager - create_wiki/lookup/list_active/list_trashed/soft_delete/restore
#   TrashedWiki - frozen Pydantic record of a soft-deleted wiki
#   NearDuplicateMatch - frozen Pydantic (existing_primary, distance)
#   AntiSpamCapError - hard cap reached
#   WikiNotFoundError - lookup target absent
#   TrashRetentionExpiredError - restore beyond retention window
#   levenshtein - exposed for unit tests
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 8: anti-spam cap + Levenshtein + soft-delete + restore
# END_CHANGE_SUMMARY

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

from ai_steward_wiki.wiki.name import WikiName, normalize_wiki_name

__all__ = [
    "AntiSpamCapError",
    "NearDuplicateMatch",
    "TrashRetentionExpiredError",
    "TrashedWiki",
    "WikiLifecycleManager",
    "WikiNotFoundError",
    "levenshtein",
]

_log = structlog.get_logger(__name__)

_TRASH_DIR = "_trash"
_TRASH_TS_FMT = "%Y%m%dT%H%M%SZ"


class AntiSpamCapError(RuntimeError):
    """Owner has reached the hard cap on active WIKIs."""


class WikiNotFoundError(LookupError):
    """Target wiki not found for the owner."""


class TrashRetentionExpiredError(RuntimeError):
    """Restore attempted after the retention window."""


class TrashedWiki(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: str
    trashed_path: Path
    deleted_at: str  # ISO 8601 UTC


class NearDuplicateMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    existing_primary: str
    distance: int


def levenshtein(a: str, b: str) -> int:
    """Classic two-row DP edit-distance. Pure Python; O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i, ca in enumerate(a, start=1):
        curr[0] = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len(b)]


def _slug_from_primary(primary: str) -> str:
    body = primary[: -len("-WIKI")] if primary.endswith("-WIKI") else primary
    return body.lower()


class WikiLifecycleManager:
    """Owner-scoped wiki directory lifecycle.

    Layout: ``<wiki_root>/<owner>/<Name>-WIKI/`` for active, and
    ``<wiki_root>/<owner>/_trash/<UTC-ts>_<Name>-WIKI/`` for soft-deleted.
    """

    def __init__(
        self,
        wiki_root: Path,
        *,
        max_per_user: int = 20,
        retention_days: int = 30,
        levenshtein_threshold: int = 2,
    ) -> None:
        self._root = wiki_root
        self._max_per_user = max_per_user
        self._retention = timedelta(days=retention_days)
        self._lev_threshold = levenshtein_threshold

    # ---------- public API ----------

    def list_active(self, owner: int) -> list[WikiName]:
        owner_dir = self._owner_dir(owner)
        if not owner_dir.exists():
            return []
        out: list[WikiName] = []
        for entry in sorted(owner_dir.iterdir()):
            if entry.name == _TRASH_DIR or not entry.is_dir():
                continue
            if not entry.name.endswith("-WIKI"):
                continue
            try:
                out.append(normalize_wiki_name(entry.name[: -len("-WIKI")]))
            except Exception:
                continue
        return out

    def list_trashed(self, owner: int) -> list[TrashedWiki]:
        trash = self._owner_dir(owner) / _TRASH_DIR
        if not trash.exists():
            return []
        out: list[TrashedWiki] = []
        for entry in sorted(trash.iterdir()):
            if not entry.is_dir() or "_" not in entry.name:
                continue
            ts_part, _, name_part = entry.name.partition("_")
            try:
                dt = datetime.strptime(ts_part, _TRASH_TS_FMT).replace(tzinfo=UTC)
            except ValueError:
                continue
            out.append(
                TrashedWiki(
                    primary=name_part,
                    trashed_path=entry,
                    deleted_at=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
        return out

    def lookup(self, owner: int, name_or_hyphenated: str) -> WikiName | None:
        try:
            target = normalize_wiki_name(name_or_hyphenated)
        except Exception:
            return None
        for active in self.list_active(owner):
            if active.primary == target.primary:
                return active
        return None

    def create_wiki(
        self,
        owner: int,
        raw_name: str,
        template_id: str,
    ) -> WikiName:
        candidate = normalize_wiki_name(raw_name)
        existing = self.lookup(owner, candidate.primary)
        if existing is not None:
            return existing

        active = self.list_active(owner)
        if len(active) >= self._max_per_user:
            raise AntiSpamCapError(
                f"owner {owner} already has {len(active)} active WIKIs "
                f"(cap={self._max_per_user})"
            )

        # Levenshtein near-duplicate scan against active + trashed slugs.
        candidates: list[tuple[str, str]] = [(w.primary, w.slug) for w in active]
        for tw in self.list_trashed(owner):
            try:
                normalised = normalize_wiki_name(tw.primary[: -len("-WIKI")])
            except Exception:
                continue
            candidates.append((tw.primary, normalised.slug))

        for primary, slug in candidates:
            dist = levenshtein(candidate.slug, slug)
            if dist <= self._lev_threshold and dist > 0:
                _log.info(
                    "wiki.lifecycle.near_duplicate",
                    owner=owner,
                    candidate=candidate.primary,
                    existing=primary,
                    distance=dist,
                )
                # Return existing iff active; otherwise let caller retry with
                # a more distinct name (trashed restore is a different op).
                for w in active:
                    if w.primary == primary:
                        return w

        owner_dir = self._owner_dir(owner)
        owner_dir.mkdir(parents=True, exist_ok=True)
        wiki_dir = owner_dir / candidate.primary
        wiki_dir.mkdir()
        (wiki_dir / "CLAUDE.md").write_text(
            f"---\nschema_version: 2\ntemplate_id: {template_id}\n"
            f"last_migrated_at: {datetime.now(tz=UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            "template_sha256: \n---\n",
            encoding="utf-8",
        )
        _log.info(
            "wiki.lifecycle.created",
            owner=owner,
            primary=candidate.primary,
            template_id=template_id,
        )
        return candidate

    def soft_delete(self, owner: int, primary: str) -> TrashedWiki:
        wiki_dir = self._owner_dir(owner) / primary
        if not wiki_dir.exists():
            raise WikiNotFoundError(f"{primary} not found for owner {owner}")
        trash = self._owner_dir(owner) / _TRASH_DIR
        trash.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=UTC)
        ts = now.strftime(_TRASH_TS_FMT)
        target = trash / f"{ts}_{primary}"
        os.replace(wiki_dir, target)
        record = TrashedWiki(
            primary=primary,
            trashed_path=target,
            deleted_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        _log.info(
            "wiki.lifecycle.soft_deleted",
            owner=owner,
            primary=primary,
            trashed_path=str(target),
        )
        return record

    def restore(
        self,
        owner: int,
        trashed: TrashedWiki,
        *,
        now_utc: datetime | None = None,
    ) -> WikiName:
        moment = now_utc if now_utc is not None else datetime.now(tz=UTC)
        deleted_at = datetime.strptime(trashed.deleted_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if moment - deleted_at > self._retention:
            raise TrashRetentionExpiredError(
                f"trash retention {self._retention.days}d expired for {trashed.primary}"
            )
        target = self._owner_dir(owner) / trashed.primary
        if target.exists():
            raise FileExistsError(f"{target} already exists; name collision on restore")
        os.replace(trashed.trashed_path, target)
        _log.info(
            "wiki.lifecycle.restored",
            owner=owner,
            primary=trashed.primary,
            restored_path=str(target),
        )
        return normalize_wiki_name(trashed.primary[: -len("-WIKI")])

    # ---------- internals ----------

    def _owner_dir(self, owner: int) -> Path:
        return self._root / str(owner)
