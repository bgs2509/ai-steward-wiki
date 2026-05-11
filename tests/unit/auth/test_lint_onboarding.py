"""scripts/lint_onboarding.py — slug presence / order / uniqueness / non-empty."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINT_PATH = REPO_ROOT / "scripts" / "lint_onboarding.py"


def _load():
    spec = importlib.util.spec_from_file_location("lint_onboarding", LINT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _valid_body() -> str:
    return (
        "<!-- slug:greeting -->\nПривет.\n"
        "<!-- slug:purpose -->\nЦель.\n"
        "<!-- slug:capabilities -->\nУмения.\n"
        "<!-- slug:privacy -->\nПриватность.\n"
        "<!-- slug:next-steps -->\nДальше.\n"
        "<!-- slug:contact -->\nКонтакт.\n"
    )


def test_happy_path_exit_zero(tmp_path: Path) -> None:
    mod = _load()
    p = tmp_path / "intro.md"
    p.write_text(_valid_body(), encoding="utf-8")
    assert mod.main(["--template", str(p)]) == 0


def test_missing_slug_exit_nonzero(tmp_path: Path, capsys) -> None:
    mod = _load()
    p = tmp_path / "intro.md"
    body = _valid_body().replace("<!-- slug:privacy -->\nПриватность.\n", "")
    p.write_text(body, encoding="utf-8")
    code = mod.main(["--template", str(p)])
    assert code == 1
    err = capsys.readouterr().err
    assert "privacy" in err


def test_duplicate_slug_exit_nonzero(tmp_path: Path, capsys) -> None:
    mod = _load()
    p = tmp_path / "intro.md"
    body = _valid_body() + "<!-- slug:greeting -->\nещё.\n"
    p.write_text(body, encoding="utf-8")
    code = mod.main(["--template", str(p)])
    assert code == 1
    assert "duplicate" in capsys.readouterr().err


def test_empty_section_exit_nonzero(tmp_path: Path, capsys) -> None:
    mod = _load()
    p = tmp_path / "intro.md"
    body = (
        "<!-- slug:greeting -->\n"
        "<!-- slug:purpose -->\nЦель.\n"
        "<!-- slug:capabilities -->\nx.\n"
        "<!-- slug:privacy -->\nx.\n"
        "<!-- slug:next-steps -->\nx.\n"
        "<!-- slug:contact -->\nx.\n"
    )
    p.write_text(body, encoding="utf-8")
    code = mod.main(["--template", str(p)])
    assert code == 1
    assert "empty section" in capsys.readouterr().err


def test_real_template_passes() -> None:
    mod = _load()
    real = REPO_ROOT / "templates" / "onboarding-intro.ru.md"
    assert mod.main(["--template", str(real)]) == 0
