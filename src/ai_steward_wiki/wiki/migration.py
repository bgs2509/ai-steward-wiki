# FILE: src/ai_steward_wiki/wiki/migration.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: CLAUDE.md frontmatter parser + managed/user-zone HTML markers +
#            linear v1 -> v2 migration that preserves user-zone verbatim.
#   SCOPE: Frontmatter model, parse/render helpers, migrate_v1_to_v2.
#   DEPENDS: pydantic, structlog
#   LINKS: M-WIKI-LIFECYCLE, D-039, tech-spec §5
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Frontmatter - frozen Pydantic schema_version/template_id/last_migrated_at/template_sha256
#   FrontmatterError - parse failure
#   MANAGED_START - HTML marker (start of managed zone)
#   MANAGED_END - HTML marker (end of managed zone)
#   USER_START - HTML marker (start of user zone)
#   USER_END - HTML marker (end of user zone)
#   parse_frontmatter - extract Frontmatter + body from text
#   render_frontmatter - serialise Frontmatter to YAML-like block
#   extract_user_zone - find user zone in body (or whole body if no markers)
#   render_v2 - build full v2 CLAUDE.md from fm + managed + user content
#   migrate_v1_to_v2 - atomic, idempotent linear migration on disk
#   load_template - read templates/<id>.md and return (managed_text, sha256)
#   TemplateNotFoundError - raised by load_template when template_id is unknown
#   repair_managed_zone - re-render managed zone on an already-v2 CLAUDE.md, preserve user zone (aisw-db6)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-db6: repair_managed_zone — re-render the managed
#                zone on already-v2 CLAUDE.md files whose schema body is empty/stale
#                (migrate_v1_to_v2 no-ops on v2). Preserves user zone, idempotent on
#                matching sha. Backfills the systemic empty-schema defect.
#   PREVIOUS:    v0.0.2 - chunk 15: load_template helper + sha256 computation
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

__all__ = [
    "MANAGED_END",
    "MANAGED_START",
    "USER_END",
    "USER_START",
    "Frontmatter",
    "FrontmatterError",
    "TemplateNotFoundError",
    "extract_user_zone",
    "load_template",
    "migrate_v1_to_v2",
    "parse_frontmatter",
    "render_frontmatter",
    "render_v2",
    "repair_managed_zone",
]

_log = structlog.get_logger(__name__)

MANAGED_START = "<!-- managed:start -->"
MANAGED_END = "<!-- managed:end -->"
USER_START = "<!-- user:start -->"
USER_END = "<!-- user:end -->"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class FrontmatterError(ValueError):
    """Raised when a CLAUDE.md frontmatter block cannot be parsed."""


class TemplateNotFoundError(FileNotFoundError):
    """Raised when load_template cannot locate templates/<id>.md."""


def load_template(template_id: str, templates_dir: Path) -> tuple[str, str]:
    """Return (managed_text, sha256_hex) for templates/<template_id>.md."""
    path = templates_dir / f"{template_id}.md"
    if not path.is_file():
        raise TemplateNotFoundError(f"template not found: {path}")
    managed = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(managed.encode("utf-8")).hexdigest()
    return managed, digest


class Frontmatter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    template_id: str
    last_migrated_at: str
    template_sha256: str


def parse_frontmatter(text: str) -> tuple[Frontmatter, str]:
    """Parse strict-subset YAML frontmatter; return (Frontmatter, body)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise FrontmatterError("missing or malformed frontmatter block")
    block, body = match.group(1), match.group(2)
    data: dict[str, str | int] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise FrontmatterError(f"invalid frontmatter line: {line!r}")
        key, _, raw_val = line.partition(":")
        key = key.strip()
        val: str | int = raw_val.strip()
        if key == "schema_version":
            try:
                val = int(val)
            except ValueError as exc:
                raise FrontmatterError(f"schema_version not int: {raw_val!r}") from exc
        data[key] = val
    try:
        return Frontmatter(**data), body
    except Exception as exc:
        raise FrontmatterError(str(exc)) from exc


def render_frontmatter(fm: Frontmatter) -> str:
    return (
        "---\n"
        f"schema_version: {fm.schema_version}\n"
        f"template_id: {fm.template_id}\n"
        f"last_migrated_at: {fm.last_migrated_at}\n"
        f"template_sha256: {fm.template_sha256}\n"
        "---\n"
    )


def extract_user_zone(body: str) -> str | None:
    """Return content between USER_START / USER_END markers, or None if absent."""
    start = body.find(USER_START)
    end = body.find(USER_END)
    if start == -1 or end == -1 or end < start:
        return None
    return body[start + len(USER_START) : end].strip("\n")


def render_v2(*, fm: Frontmatter, managed: str, user: str) -> str:
    """Assemble a full v2 CLAUDE.md from frontmatter + managed + user content."""
    return (
        render_frontmatter(fm)
        + "\n"
        + MANAGED_START
        + "\n"
        + managed.strip("\n")
        + "\n"
        + MANAGED_END
        + "\n\n"
        + USER_START
        + "\n"
        + user.strip("\n")
        + "\n"
        + USER_END
        + "\n"
    )


def _utc_now_iso(now_utc: datetime | None) -> str:
    moment = now_utc if now_utc is not None else datetime.now(tz=UTC)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate_v1_to_v2(
    path: Path,
    *,
    template_managed: str,
    template_sha256: str,
    template_id: str | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Linear v1 -> v2 migration. Idempotent. Atomic tmp + os.replace.

    Returns True if migration was applied, False if already v2.
    """
    text = path.read_text(encoding="utf-8")
    try:
        fm, body = parse_frontmatter(text)
    except FrontmatterError:
        # Treat as v1 without frontmatter: take whole text as user zone.
        fm = Frontmatter(
            schema_version=1,
            template_id=template_id or "_default",
            last_migrated_at=_utc_now_iso(now_utc),
            template_sha256="",
        )
        body = text

    if fm.schema_version == 2:
        _log.info("wiki.lifecycle.migration.noop", path=str(path))
        return False

    user_zone = extract_user_zone(body)
    if user_zone is None:
        # No explicit markers in v1 — preserve full body verbatim.
        user_zone = body.strip("\n")

    new_fm = Frontmatter(
        schema_version=2,
        template_id=template_id or fm.template_id,
        last_migrated_at=_utc_now_iso(now_utc),
        template_sha256=template_sha256,
    )
    rendered = render_v2(fm=new_fm, managed=template_managed, user=user_zone)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)
    _log.info(
        "wiki.lifecycle.migration.applied",
        path=str(path),
        from_version=fm.schema_version,
        to_version=2,
    )
    return True


def repair_managed_zone(
    path: Path,
    *,
    template_managed: str,
    template_sha256: str,
    template_id: str | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Re-render the managed zone of an already-v2 CLAUDE.md from its template.

    Unlike migrate_v1_to_v2 (which no-ops on v2), this repairs v2 files whose
    managed zone is empty or stale — the systemic aisw-db6 defect where
    create_wiki wrote a frontmatter-only CLAUDE.md, leaving the model without a
    Data layout schema. The user zone is preserved verbatim. Idempotent: returns
    False when the managed zone already matches the template (same sha AND markers
    present); True when a rewrite was applied. Atomic tmp + os.replace.
    """
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    has_managed = MANAGED_START in body and MANAGED_END in body
    if fm.template_sha256 == template_sha256 and has_managed:
        _log.info("wiki.lifecycle.repair.noop", path=str(path))
        return False

    user_zone = extract_user_zone(body)
    if user_zone is None:
        user_zone = ""

    new_fm = Frontmatter(
        schema_version=2,
        template_id=template_id or fm.template_id,
        last_migrated_at=_utc_now_iso(now_utc),
        template_sha256=template_sha256,
    )
    rendered = render_v2(fm=new_fm, managed=template_managed, user=user_zone)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)
    _log.info(
        "wiki.lifecycle.repair.applied",
        path=str(path),
        template_id=new_fm.template_id,
        template_sha256=template_sha256,
    )
    return True
