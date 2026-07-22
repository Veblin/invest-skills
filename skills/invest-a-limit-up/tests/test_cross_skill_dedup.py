"""Batch D smoke: shared invest_path shims + dates helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SKILLS = Path(__file__).resolve().parents[2]
_SKILL_LIBS = {
    "limit-up": _SKILLS / "invest-a-limit-up" / "scripts" / "lib",
    "gap-scan": _SKILLS / "invest-a-gap-scan" / "scripts" / "lib",
    "journal": _SKILLS / "invest-a-journal" / "scripts" / "lib",
}


@pytest.fixture(params=list(_SKILL_LIBS))
def skill_key(request: pytest.FixtureRequest) -> str:
    return request.param


def test_invest_path_shim_imports_nums(skill_key: str) -> None:
    """X-02: each skill shim resolves invest-a-stock and imports lib.nums."""
    skill_lib = _SKILL_LIBS[skill_key]
    path = skill_lib / "_invest_path.py"
    mod_name = f"_invest_path_shim_{skill_key.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    scripts = mod.ensure_invest_a_scripts_on_path()
    assert scripts.name == "scripts"
    assert scripts.parent.name == "invest-a-stock"
    assert scripts.is_dir()

    from lib.nums import safe_float

    assert safe_float("2.5") == 2.5
    assert safe_float(float("inf")) is None


def test_yyyymmdd_to_iso() -> None:
    """L-03: shared dates helper (also covered by skills/lib/tests/test_dates.py)."""
    from dates import yyyymmdd_to_iso

    assert yyyymmdd_to_iso("20260713") == "2026-07-13"
    assert yyyymmdd_to_iso("abc") == "abc"
    assert yyyymmdd_to_iso("abcdefgh") == "abcdefgh"  # len 8 but non-digit
    assert yyyymmdd_to_iso(" 20260101 ") == "2026-01-01"
