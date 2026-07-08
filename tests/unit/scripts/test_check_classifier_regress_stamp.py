"""Unit tests for scripts/check_classifier_regress_stamp.py (full-audit 2026-07-08 fix).

Verifies the pre-commit guard blocks commits touching prompts/classifier.md unless
the stamp written by classifier_regress.py's write_stamp matches the staged content,
and that the CLASSIFIER_REGRESS_OK=1 escape hatch is honoured.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from check_classifier_regress_stamp import check_stamp, main


def _write_matching_stamp(prompt_path: Path, stamp_path: Path) -> None:
    digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    stamp_path.write_text(f"{digest}\n", encoding="utf-8")


def test_check_stamp_missing_returns_error(tmp_path: Path) -> None:
    prompt_path = tmp_path / "classifier.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    stamp_path = tmp_path / ".classifier_regress.stamp"

    error = check_stamp(prompt_path, stamp_path)

    assert error is not None
    assert "no regress stamp found" in error


def test_check_stamp_stale_returns_error(tmp_path: Path) -> None:
    prompt_path = tmp_path / "classifier.md"
    stamp_path = tmp_path / ".classifier_regress.stamp"
    prompt_path.write_text("prompt v1", encoding="utf-8")
    _write_matching_stamp(prompt_path, stamp_path)

    prompt_path.write_text("prompt v2", encoding="utf-8")
    error = check_stamp(prompt_path, stamp_path)

    assert error is not None
    assert "changed since the last passing" in error


def test_check_stamp_fresh_returns_none(tmp_path: Path) -> None:
    prompt_path = tmp_path / "classifier.md"
    stamp_path = tmp_path / ".classifier_regress.stamp"
    prompt_path.write_text("prompt", encoding="utf-8")
    _write_matching_stamp(prompt_path, stamp_path)

    assert check_stamp(prompt_path, stamp_path) is None


def test_main_ignores_unrelated_files(tmp_path: Path) -> None:
    other = tmp_path / "wiki.md"
    other.write_text("unrelated", encoding="utf-8")

    assert main([str(other)]) == 0


def test_main_blocks_on_missing_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = tmp_path / "classifier.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(
        "check_classifier_regress_stamp.STAMP_PATH", tmp_path / ".classifier_regress.stamp"
    )
    monkeypatch.delenv("CLASSIFIER_REGRESS_OK", raising=False)

    assert main([str(prompt_path)]) == 1


def test_main_passes_with_fresh_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = tmp_path / "classifier.md"
    stamp_path = tmp_path / ".classifier_regress.stamp"
    prompt_path.write_text("prompt", encoding="utf-8")
    _write_matching_stamp(prompt_path, stamp_path)
    monkeypatch.setattr("check_classifier_regress_stamp.STAMP_PATH", stamp_path)

    assert main([str(prompt_path)]) == 0


def test_main_honours_override_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = tmp_path / "classifier.md"
    monkeypatch.setattr(
        "check_classifier_regress_stamp.STAMP_PATH", tmp_path / ".classifier_regress.stamp"
    )
    prompt_path.write_text("prompt", encoding="utf-8")
    monkeypatch.setenv("CLASSIFIER_REGRESS_OK", "1")

    assert main([str(prompt_path)]) == 0
