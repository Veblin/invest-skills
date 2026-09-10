"""技能路径自适应（包内运行 vs 单体仓库）——R1 审查 F3。

skillhub / WorkBuddy 分发包会把 ``scripts/`` 拍平到包根下一层
（``<pkg>/scripts/x.py`` + ``<pkg>/references/`` + ``<pkg>/lib/``），
此时 ``Path(__file__).parents[3]``（单体仓库假设）指向包外 → 默认 map/输出
目录失效、CLI 首次运行即 FATAL。两种布局下技能根恒为 ``parents[1]``。
"""

from __future__ import annotations

import pathlib


def skill_root(module_file: str) -> pathlib.Path:
    """技能根目录（含 SKILL.md 与 references/）——两种布局下同为 parents[1]。"""
    return pathlib.Path(module_file).resolve().parents[1]


def is_installed_in_repo(module_file: str) -> bool:
    """是否运行于单体仓库（repo/skills/<skill>/scripts/… 且 repo 有 pyproject.toml）。"""
    return _repo_root(module_file) is not None


def _repo_root(module_file: str) -> pathlib.Path | None:
    p = pathlib.Path(module_file).resolve()
    try:
        repo = p.parents[3]
    except IndexError:
        return None
    if p.parents[2].name == "skills" and (repo / "pyproject.toml").exists():
        return repo
    return None


def default_out_dir(module_file: str, name: str) -> str:
    """默认输出目录：单体仓库 → <repo>/reports/<name>；包内运行 → <skill>/reports/<name>。"""
    repo = _repo_root(module_file)
    base = repo if repo is not None else skill_root(module_file)
    return str(base / "reports" / name)


def default_reference(module_file: str, rel: str) -> str:
    """技能内参考文件默认路径（<skill>/<rel>）——两种布局一致。"""
    return str(skill_root(module_file) / rel)
