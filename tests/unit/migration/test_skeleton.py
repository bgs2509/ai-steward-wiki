"""Smoke import tests for the migration package (aisw-0a5 P1.1)."""

from __future__ import annotations


def test_modules_importable() -> None:
    """All 6 migration submodules import cleanly without runtime errors."""
    import importlib

    for name in (
        "ai_steward_wiki.migration",
        "ai_steward_wiki.migration.config",
        "ai_steward_wiki.migration.extract",
        "ai_steward_wiki.migration.transform",
        "ai_steward_wiki.migration.load",
        "ai_steward_wiki.migration.report",
        "ai_steward_wiki.migration.__main__",
    ):
        importlib.import_module(name)


def test_cli_parser_help() -> None:
    """argparse parser builds and --help renders (exit code irrelevant — we
    just want no ImportError / construction failure)."""
    from ai_steward_wiki.migration.__main__ import _build_parser

    p = _build_parser()
    assert p.prog == "ai_steward_wiki.migration"
    # default mode is dry-run
    ns = p.parse_args(["--snapshot-root", "/tmp/x"])
    assert ns.dry_run is True
    assert ns.execute is False
