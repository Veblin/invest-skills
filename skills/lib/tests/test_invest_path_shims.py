"""Tests for 4 个 skill 的 _invest_path.py 引导 shim — 逐字节一致 + 导出面。

Batch D / X-02：shim 形状必须保持统一，防止再次分叉（曾有 _skills_lib_path
26 行变体）。加载各 shim 验证 ensure_* 函数行为。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SKILLS = Path(__file__).resolve().parents[2]

_SKILL_NAMES = ("invest-a-etf", "invest-a-gap-scan", "invest-a-journal",
                "invest-a-stock")


def _shim_path(skill: str) -> Path:
    return _SKILLS / skill / "scripts" / "lib" / "_invest_path.py"


def test_all_four_shims_byte_identical() -> None:
    contents = {skill: _shim_path(skill).read_text(encoding="utf-8")
                for skill in _SKILL_NAMES}
    reference = contents[_SKILL_NAMES[0]]
    for skill in _SKILL_NAMES[1:]:
        assert contents[skill] == reference, f"{skill} shim 与 canonical 形状不一致"


def _load_shim(skill: str):
    spec = importlib.util.spec_from_file_location(
        f"_invest_path_{skill.replace('-', '_')}", _shim_path(skill))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_each_shim_exports_both_ensure_names() -> None:
    for skill in _SKILL_NAMES:
        mod = _load_shim(skill)
        # ensure_skills_lib_on_path 是 ensure_shared_lib_on_path 的别名
        assert mod.ensure_skills_lib_on_path is mod.ensure_shared_lib_on_path
        assert "ensure_skills_lib_on_path" in mod.__all__
        assert "ensure_invest_a_scripts_on_path" in mod.__all__


def test_shim_functions_resolve_canonical() -> None:
    mod = _load_shim("invest-a-stock")
    skills_lib = mod.ensure_skills_lib_on_path()
    assert skills_lib == (_SKILLS / "lib").resolve()
    scripts = mod.ensure_invest_a_scripts_on_path()
    assert scripts.name == "scripts"
    assert scripts.parent.name == "invest-a-stock"
    assert (scripts / "invest.py").is_file()
