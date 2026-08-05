"""Tests for skills/lib/version.py — pyproject [project].version 解析（无网络）。

basename 用 test_lib_version 避免与 invest-a-stock/tests/test_version.py 撞名
（两目录均无 __init__.py，pytest 收集同 basename 报 import file mismatch）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_LIB = Path(__file__).resolve().parents[1]
if str(_SKILLS_LIB) not in sys.path:
    sys.path.insert(0, str(_SKILLS_LIB))

from version import get_package_version  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestGetPackageVersion:
    def test_repo_version_matches_pyproject(self):
        """默认调用（stock 语义）解析到仓库根 pyproject.toml 的 [project].version。"""
        v = get_package_version()
        text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # 与 pyproject.toml 字面对照（动态比对，避免版本号硬编码）
        assert f'version = "{v}"' in text

    def test_default_on_empty_dir(self, tmp_path: Path):
        assert get_package_version(default="fallback", _start_dir=tmp_path) == "fallback"

    def test_stop_at_first_semantics(self, tmp_path: Path):
        """stop_at_first=True 停在第一个 pyproject（无 version → default）；
        False 继续向上找到 version。"""
        root = tmp_path
        sub = root / "a"
        sub.mkdir()
        (sub / "pyproject.toml").write_text("[tool.poetry]\nname = \"x\"\n", encoding="utf-8")
        (root / "pyproject.toml").write_text(
            "[project]\nversion = \"9.9.9\"\n", encoding="utf-8")

        # gap 语义：停在第一个 pyproject（无 version 字段）→ default
        assert get_package_version(default="0.0.0", stop_at_first=True,
                                   _start_dir=sub) == "0.0.0"
        # stock 语义：继续向上找到 version
        assert get_package_version(_start_dir=sub) == "9.9.9"

    def test_version_from_first_pyproject_with_version(self, tmp_path: Path):
        root = tmp_path
        sub = root / "a"
        sub.mkdir()
        (sub / "pyproject.toml").write_text("[project]\nversion = \"1.2.3\"\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\nversion = \"9.9.9\"\n", encoding="utf-8")
        assert get_package_version(stop_at_first=True, _start_dir=sub) == "1.2.3"
        assert get_package_version(_start_dir=sub) == "1.2.3"
