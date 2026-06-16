from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_steward_wiki.wiki.migration import (
    MANAGED_END,
    MANAGED_START,
    USER_END,
    USER_START,
    migrate_v1_to_v2,
    parse_frontmatter,
)


def _write_v1(path: Path, body: str) -> None:
    path.write_text(
        "---\n"
        "schema_version: 1\n"
        "template_id: health\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\n"
        "template_sha256: old\n"
        "---\n" + body,
        encoding="utf-8",
    )


def test_migrate_preserves_user_zone(tmp_path: Path) -> None:
    p = tmp_path / "CLAUDE.md"
    body = (
        f"{MANAGED_START}\nold managed\n{MANAGED_END}\n\n"
        f"{USER_START}\nuser custom rules\n{USER_END}\n"
    )
    _write_v1(p, body)
    applied = migrate_v1_to_v2(
        p,
        template_managed="new managed",
        template_sha256="abc",
        now_utc=datetime(2026, 5, 10, 12, tzinfo=UTC),
    )
    assert applied is True
    text = p.read_text(encoding="utf-8")
    assert "schema_version: 2" in text
    assert "new managed" in text
    assert "user custom rules" in text
    assert "old managed" not in text


def test_migrate_idempotent_on_v2(tmp_path: Path) -> None:
    p = tmp_path / "CLAUDE.md"
    p.write_text(
        "---\n"
        "schema_version: 2\n"
        "template_id: health\n"
        "last_migrated_at: 2026-05-10T00:00:00Z\n"
        "template_sha256: x\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    snapshot = p.read_text(encoding="utf-8")
    applied = migrate_v1_to_v2(p, template_managed="m", template_sha256="x")
    assert applied is False
    assert p.read_text(encoding="utf-8") == snapshot


def test_migrate_no_markers_preserves_body(tmp_path: Path) -> None:
    p = tmp_path / "CLAUDE.md"
    _write_v1(p, "legacy free-form rules\nline 2\n")
    migrate_v1_to_v2(p, template_managed="m", template_sha256="x")
    text = p.read_text(encoding="utf-8")
    assert "legacy free-form rules" in text
    assert "schema_version: 2" in text


def test_atomicity_no_tmp_left(tmp_path: Path) -> None:
    p = tmp_path / "CLAUDE.md"
    _write_v1(p, "body\n")
    migrate_v1_to_v2(p, template_managed="m", template_sha256="x")
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_parse_frontmatter_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "CLAUDE.md"
    _write_v1(p, "x")
    fm, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    assert fm.schema_version == 1
    assert fm.template_id == "health"
    assert body.startswith("x")


# --- aisw-db6: repair_managed_zone (re-render managed zone on already-v2 CLAUDE.md) ---


def _write_v2(path: Path, *, managed: str, user: str, sha: str = "old") -> None:
    path.write_text(
        "---\nschema_version: 2\ntemplate_id: medical\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\n"
        f"template_sha256: {sha}\n---\n"
        f"{MANAGED_START}\n{managed}\n{MANAGED_END}\n\n"
        f"{USER_START}\n{user}\n{USER_END}\n",
        encoding="utf-8",
    )


def test_repair_fills_empty_managed_zone(tmp_path: Path) -> None:
    """A v2 CLAUDE.md that is frontmatter-only (no body) gets its managed zone filled."""
    from ai_steward_wiki.wiki.migration import repair_managed_zone

    p = tmp_path / "CLAUDE.md"
    p.write_text(
        "---\nschema_version: 2\ntemplate_id: medical\n"
        "last_migrated_at: 2026-01-01T00:00:00Z\ntemplate_sha256: \n---\n",
        encoding="utf-8",
    )
    applied = repair_managed_zone(
        p,
        template_managed="# Medical\n## Data layout\nmetrics/ -> CSV",
        template_sha256="newsha",
        now_utc=datetime(2026, 6, 16, 9, tzinfo=UTC),
    )
    assert applied is True
    text = p.read_text(encoding="utf-8")
    assert "## Data layout" in text
    assert MANAGED_START in text
    assert MANAGED_END in text
    fm, _ = parse_frontmatter(text)
    assert fm.template_sha256 == "newsha"


def test_repair_preserves_user_zone(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.migration import repair_managed_zone

    p = tmp_path / "CLAUDE.md"
    _write_v2(p, managed="stale managed", user="МОИ правила юзера", sha="old")
    applied = repair_managed_zone(
        p,
        template_managed="fresh managed body",
        template_sha256="freshsha",
        now_utc=datetime(2026, 6, 16, 9, tzinfo=UTC),
    )
    assert applied is True
    text = p.read_text(encoding="utf-8")
    assert "fresh managed body" in text
    assert "stale managed" not in text
    assert "МОИ правила юзера" in text  # user zone preserved verbatim


def test_repair_idempotent_when_sha_matches(tmp_path: Path) -> None:
    from ai_steward_wiki.wiki.migration import repair_managed_zone

    p = tmp_path / "CLAUDE.md"
    _write_v2(p, managed="body v1", user="u", sha="samesha")
    applied = repair_managed_zone(
        p,
        template_managed="body v1",
        template_sha256="samesha",
        now_utc=datetime(2026, 6, 16, 9, tzinfo=UTC),
    )
    assert applied is False  # already up to date -> noop
