"""Tests for inbox/route.py — target-WIKI resolution + raw staging + ingest prompt (aisw-zd9)."""

from __future__ import annotations

from pathlib import Path

from ai_steward_wiki.inbox.route import (
    RouteRejection,
    RouteTarget,
    StagedRaw,
    build_ingest_prompt,
    pick_domain_overlay,
    resolve_target_wiki,
    stage_raw_into_wiki,
)
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.wiki.lifecycle import WikiLifecycleManager


def _decision(intent: RouterIntent, target: str | None) -> RouterDecision:
    return RouterDecision(
        intent=intent, target_wiki=target, notes="n", raw="```router\n...\n```", parsed_ok=True
    )


def _mgr(tmp_path: Path, *, cap: int = 20) -> tuple[WikiLifecycleManager, Path]:
    root = tmp_path / "wikis"
    root.mkdir()
    return WikiLifecycleManager(root, max_per_user=cap), root


# ---------- resolve_target_wiki ----------


def test_create_wiki_makes_new_dir(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path)
    out = resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    assert isinstance(out, RouteTarget)
    assert out.created is True
    assert out.wiki_name.primary == "Travel-WIKI"
    assert out.wiki_dir == root / "42" / "Travel-WIKI"
    assert out.wiki_dir.is_dir()


def test_create_wiki_idempotent_second_call_not_created(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path)
    resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    out = resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    assert isinstance(out, RouteTarget)
    assert out.created is False


def test_route_existing_not_created(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path)
    resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "Travel-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    out = resolve_target_wiki(
        _decision(RouterIntent.ROUTE, "Travel-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    assert isinstance(out, RouteTarget)
    assert out.created is False


def test_route_missing_target_is_created_and_callback_fires(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path)
    calls: list[int] = []
    out = resolve_target_wiki(
        _decision(RouterIntent.ROUTE, "Garden-WIKI"),
        lifecycle=mgr,
        owner=42,
        wiki_root=root,
        on_route_missing=lambda: calls.append(1),
    )
    assert isinstance(out, RouteTarget)
    assert out.created is True
    assert out.wiki_name.primary == "Garden-WIKI"
    assert calls == [1]


def test_cap_reached_returns_rejection(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path, cap=1)
    resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "A-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    out = resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "B-WIKI"), lifecycle=mgr, owner=42, wiki_root=root
    )
    assert isinstance(out, RouteRejection)
    assert out.reason == "cap"
    assert out.hint


def test_bad_name_returns_rejection(tmp_path: Path) -> None:
    mgr, root = _mgr(tmp_path)
    out = resolve_target_wiki(
        _decision(RouterIntent.CREATE_WIKI, "!!!"), lifecycle=mgr, owner=42, wiki_root=root
    )
    assert isinstance(out, RouteRejection)
    assert out.reason == "bad_name"


# ---------- stage_raw_into_wiki / render ----------


def test_stage_text_writes_plain_body(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "Travel-WIKI"
    wiki_dir.mkdir()
    staged = stage_raw_into_wiki(wiki_dir, source="text", user_text="вот билет", media_paths=None)
    assert isinstance(staged, StagedRaw)
    assert staged.sidecar_rel.startswith("raw/")
    assert staged.sidecar_rel.endswith("_text.md")
    assert (wiki_dir / staged.sidecar_rel).read_text(encoding="utf-8") == "вот билет\n"
    assert staged.media_rel == []
    assert staged.media_abs == []


def test_stage_voice_writes_sidecar_and_promotes_media(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "Health-WIKI"
    wiki_dir.mkdir()
    staged_src = tmp_path / "staging" / "x.ogg"
    staged_src.parent.mkdir()
    staged_src.write_bytes(b"\x00\x01\x02")
    staged = stage_raw_into_wiki(
        wiki_dir, source="voice", user_text="запиши встречу", media_paths=[staged_src]
    )
    assert staged.sidecar_rel.endswith("_voice.md")
    content = (wiki_dir / staged.sidecar_rel).read_text(encoding="utf-8")
    assert content.startswith("---\nsource: voice\n")
    assert "запиши встречу" in content
    assert len(staged.media_rel) == 1
    assert staged.media_rel[0].startswith("raw/media/")
    assert len(staged.media_abs) == 1
    assert staged.media_abs[0].is_absolute()
    assert staged.media_abs[0].exists()
    assert not staged_src.exists()  # moved


def test_stage_missing_media_is_skipped(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "X-WIKI"
    wiki_dir.mkdir()
    staged = stage_raw_into_wiki(
        wiki_dir, source="photo", user_text="фото", media_paths=[tmp_path / "gone.jpg"]
    )
    assert staged.media_rel == []
    assert (wiki_dir / staged.sidecar_rel).exists()


# ---------- pick_domain_overlay ----------


def test_pick_domain_overlay_known(tmp_path: Path) -> None:
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "domain-health.md").write_text("x", encoding="utf-8")
    (pdir / "domain-default.md").write_text("x", encoding="utf-8")
    assert pick_domain_overlay(pdir, "health") == pdir / "domain-health.md"


def test_pick_domain_overlay_falls_back(tmp_path: Path) -> None:
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "domain-default.md").write_text("x", encoding="utf-8")
    assert pick_domain_overlay(pdir, "garden") == pdir / "domain-default.md"


# ---------- build_ingest_prompt ----------


def test_build_ingest_prompt_references_raw_and_text(tmp_path: Path) -> None:
    staged = StagedRaw(sidecar_rel="raw/20260512T000000Z_text.md", media_rel=[], media_abs=[])
    prompt = build_ingest_prompt("вот авиабилет SVO→IST", staged)
    assert "raw/20260512T000000Z_text.md" in prompt
    assert "вот авиабилет" in prompt


def test_build_ingest_prompt_mentions_media(tmp_path: Path) -> None:
    staged = StagedRaw(
        sidecar_rel="raw/ts_photo.md",
        media_rel=["raw/media/iso_ab.jpg"],
        media_abs=[Path("/abs/raw/media/iso_ab.jpg")],
    )
    prompt = build_ingest_prompt("чек", staged)
    assert "raw/media/iso_ab.jpg" in prompt
