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


def shared_tool_relpath(module_file: str, name: str) -> str:
    """共享工具的**可执行相对路径**（供打印给用户/agent 的命令行）。

    单体仓库 → ``skills/lib/<name>.py``（相对仓库根）；
    分发包 → ``scripts/lib/<name>.py``（相对包根）——builder 把共享 lib 落在
    ``scripts/lib/`` 下，包内**没有** ``skills/`` 目录（2026-09-11 对实建包
    ``find -name report_qc.py`` 证实）。

    返回相对路径而非绝对路径：执行者的 cwd 就是这两种根之一。
    """
    return (f"skills/lib/{name}.py" if _repo_root(module_file) is not None
            else f"scripts/lib/{name}.py")


def skill_relpath(module_file: str, rel: str) -> str:
    """**本技能内**文件（references/ 等）的布局相对路径——供打印给用户/agent。

    单体仓库 → ``skills/<skill>/<rel>``；分发包 → ``<rel>``（包根本身就是技能根）。
    与 ``shared_tool_relpath`` 同族：写死仓内路径在分发包里指向不存在的目录。
    """
    if _repo_root(module_file) is not None:
        return f"skills/{skill_root(module_file).name}/{rel}"
    return rel
