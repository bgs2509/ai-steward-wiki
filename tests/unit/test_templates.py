# FILE: tests/unit/test_templates.py
"""Unit tests for ai_steward_wiki.templates — slug-validated md template loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_steward_wiki.templates import TemplateError, render_template


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_render_template_happy(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "t.md",
        "<!-- slug:a -->\nhello {bot_name}\n<!-- slug:b -->\nbye",
    )
    out = render_template(p, required_slugs=frozenset({"a", "b"}), bot_name="Aisw")
    assert "hello Aisw" in out
    assert "bye" in out


def test_render_template_extra_slug(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "t.md",
        "<!-- slug:a -->\nx\n<!-- slug:b -->\ny\n<!-- slug:c -->\nz",
    )
    with pytest.raises(TemplateError, match="extra"):
        render_template(p, required_slugs=frozenset({"a", "b"}))


def test_render_template_missing_slug(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\nx")
    with pytest.raises(TemplateError, match="missing"):
        render_template(p, required_slugs=frozenset({"a", "b"}))


def test_render_template_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_template(tmp_path / "nope.md", required_slugs=frozenset({"a"}))


def test_render_template_missing_var(tmp_path: Path) -> None:
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\n{missing}")
    with pytest.raises(KeyError):
        render_template(p, required_slugs=frozenset({"a"}))


def test_render_template_no_format_vars_passthrough(tmp_path: Path) -> None:
    # Templates without {placeholders} must render without any kwargs.
    p = _write(tmp_path, "t.md", "<!-- slug:a -->\nhello world")
    out = render_template(p, required_slugs=frozenset({"a"}))
    assert "hello world" in out


def test_real_templates_render() -> None:
    # All shipped user-facing templates must load with their expected slug sets.
    root = Path(__file__).resolve().parents[2] / "templates"
    cases: list[tuple[str, frozenset[str]]] = [
        (
            "onboarding-intro.ru.md",
            frozenset({"greeting", "purpose", "capabilities", "privacy", "next-steps", "contact"}),
        ),
        (
            "start-known.ru.md",
            frozenset({"greeting", "how-to-start", "commands-hint", "pointers"}),
        ),
        (
            "help.ru.md",
            frozenset(
                {"intro", "wiki-explainer", "lazy-domains", "scenarios", "commands", "next-steps"}
            ),
        ),
        (
            "manual.ru.md",
            frozenset(
                {
                    "intro",
                    "scenario-note",
                    "scenario-wiki",
                    "scenario-reminder",
                    "scenario-digest",
                    "scenario-expand-toggle",
                    "voice-photo",
                    "privacy-note",
                }
            ),
        ),
    ]
    for name, slugs in cases:
        out = render_template(root / name, required_slugs=slugs, bot_name="ai-steward-wiki")
        assert len(out) > 100, name


def test_help_template_contains_d041_paragraph() -> None:
    # D-041 mandatory WIKI-explainer must appear verbatim (NFR-5).
    root = Path(__file__).resolve().parents[2] / "templates"
    out = render_template(
        root / "help.ru.md",
        required_slugs=frozenset(
            {"intro", "wiki-explainer", "lazy-domains", "scenarios", "commands", "next-steps"}
        ),
        bot_name="ai-steward-wiki",
    )
    assert "WIKI — это твоя персональная AI-библиотека" in out
    assert "не управляешь ими напрямую" in out
