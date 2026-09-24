"""开发模式日志接线回归（v0.3.1 批次一 S2 验收）。

纯 AST / 闭包断言，**不执行任何入口**（执行会触网）：

- 8 个入口的 `main()` 须调用 `setup_logging(skill="<目录名>")`，且 import 在函数体内
- gap-scan 不再有 `basicConfig` 常显 INFO（R3）
- 「有 `scripts/` 的 skill 都已接线」写成**完整性不变量**——pulse 豁免因此不必靠注释背书
- 构建器闭包须把共享层 `logutil` 打进每个包（防「入口改了、包内没有」的失步）
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import build_skillhub_packages as b  # noqa: E402

# 与 v0.3.1 批次一任务表一致：skill 目录名 → 主入口脚本
WIRED = {
    "invest-a-stock": "invest.py",
    "invest-a-etf": "etf.py",
    "invest-a-journal": "journal.py",
    "invest-a-gap-scan": "scan.py",
    "invest-a-pattern-scan": "scan.py",
    "invest-hk-stock": "hk.py",
    "invest-a-event-calendar": "unlock_calendar.py",
    "invest-a-discover-scan": "discover_scan.py",
}

# 结构豁免：无 scripts/ 目录 → 无日志面（R4 裁决）
EXEMPT_SKILLS = {"invest-a-pulse"}


def _src(skill: str) -> str:
    return (ROOT / "skills" / skill / "scripts" / WIRED[skill]).read_text(encoding="utf-8")


def _main_node(tree: ast.Module) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _calls(node: ast.AST, fname: str) -> list[ast.Call]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            if name == fname:
                out.append(n)
    return out


# ---------------------------------------------------------------- 接线本身

@pytest.mark.parametrize("skill", sorted(WIRED))
def test_entry_wires_setup_logging_with_skill(skill: str):
    tree = ast.parse(_src(skill))
    main = _main_node(tree)
    assert main is not None, f"{skill} 无 main()"

    imports = [n for n in ast.walk(main)
               if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == "logutil"]
    assert imports, f"{skill} 的 main() 未从 logutil 导入"
    assert any(a.name == "setup_logging" for n in imports for a in n.names)

    skills_arg = [kw.value.value for c in _calls(main, "setup_logging")
                  for kw in c.keywords
                  if kw.arg == "skill" and isinstance(kw.value, ast.Constant)]
    assert skill in skills_arg, f"{skill} 未传 skill={skill!r}，实际 {skills_arg}"


@pytest.mark.parametrize("skill", sorted(WIRED))
def test_logutil_import_is_inside_main_only(skill: str):
    """「不改模块级引导顺序」写成断言：logutil 不得出现在模块顶层 import。

    ⚠️ 这条断言把「不改顺序」实现成了「只能在函数内导入」，代价是**入口无法自给自足**：
    7/8 个入口的 `from logutil import …` 靠**另一个模块的导入副作用**才解析得到
    （如 `etf.py` 依赖 `etf_data.py` 调用 `ensure_skills_lib_on_path()`）。
    当前 8 个入口在仓库布局与包内布局均实测 exit 0，但一次 import 重排就可能使其失效，
    而 `test_builder_closure_includes_shared_logutil` 抓不到（它只断言包内有 logutil，
    不断言入口能解析到）。**若要根治，需允许模块级 import 并改为断言「引导语句先于它」**——
    属设计变更，未在本版做。
    """
    tree = ast.parse(_src(skill))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for n in top:
        if isinstance(n, ast.ImportFrom):
            assert n.module != "logutil", f"{skill} 在模块顶层 import logutil（破坏引导顺序）"
        else:
            assert all(a.name != "logutil" for a in n.names), f"{skill} 顶层 import logutil"


def test_gap_scan_has_no_basicconfig():
    """R3：gap-scan 是唯一曾无条件输出 INFO 的入口，须并入 INVEST_DEV 门控。"""
    assert "basicConfig" not in _src("invest-a-gap-scan")


# ---------------------------------------------------------------- 完整性不变量

def test_every_script_skill_is_wired_or_exempt():
    """新增任何带 scripts/ 的 skill 而未接线 → 本测试必红（豁免无需注释背书）。"""
    scripted = {p.name for p in (ROOT / "skills").iterdir()
                if p.is_dir() and (p / "scripts").is_dir()}
    assert scripted <= set(WIRED), f"未接线的入口 skill：{sorted(scripted - set(WIRED))}"
    # 豁免必须是**结构性**的：没有 scripts/ 才可豁免，否则豁免掩盖了遗漏
    for s in EXEMPT_SKILLS:
        assert not (ROOT / "skills" / s / "scripts").is_dir(), f"{s} 有 scripts/，不可豁免"


def test_pulse_is_exempt_by_structure():
    """pulse 豁免的三重结构依据（R4）。"""
    assert b.ENTRY_SCRIPTS["invest-a-pulse"] is None
    assert b.LAYOUT["invest-a-pulse"] == "pulse"
    assert not list((ROOT / "skills" / "invest-a-pulse").rglob("*.py")), "pulse 不应有脚本"


def test_builder_entry_scripts_are_all_wired():
    """构建器 `ENTRY_SCRIPTS` 里非 None 的入口都必须在接线表内（防两表失步）。"""
    for skill, entry in b.ENTRY_SCRIPTS.items():
        if entry is None:
            continue
        assert skill in WIRED, f"{skill} 有入口 {entry} 但未接线"
        assert WIRED[skill] == entry, f"{skill} 接线表与构建器不一致：{WIRED[skill]} != {entry}"


# ---------------------------------------------------------------- 分发面

@pytest.mark.parametrize("skill", sorted(WIRED))
def test_builder_closure_includes_shared_logutil(skill: str):
    """入口改了但包内没有 = 分发形态缺功能：闭包必须解析到共享层真身。"""
    c = b._Closure(skill)
    c.compute([b.SKILLS_DIR / skill / "scripts" / WIRED[skill]])
    assert "logutil" in c.included, f"{skill} 闭包缺 logutil"
    key, path = c.included["logutil"]
    assert key == "shared", f"{skill} 的 logutil 应来自 shared，实际 {key}"
    assert path == ROOT / "skills" / "lib" / "logutil.py"


def test_packaged_logutil_has_no_relative_import():
    """包内副本会被原样落盘（构建器不改写 `.` 开头的导入）——故源文件必须无相对导入。"""
    tree = ast.parse((ROOT / "skills" / "lib" / "logutil.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "包内副本会保留相对导入 → ImportError"
