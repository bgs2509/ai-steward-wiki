from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    root = tmp_path / "wikis"
    root.mkdir()
    return root


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    (d / "health.md").write_text("# health template\n", encoding="utf-8")
    (d / "_default.md").write_text("# default\n", encoding="utf-8")
    return d
