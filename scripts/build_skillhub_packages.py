#!/usr/bin/env python3
"""构建 SkillHub 分发包 — 从主仓库生成 skillhub.cn 兼容的精简 skill 包。

用法:
  uv run python scripts/build_skillhub_packages.py                # 输出到 ../invest-skills-skillhub/skills
  uv run python scripts/build_skillhub_packages.py --out /tmp/skillhub-skill
  uv run python scripts/build_skillhub_packages.py --dry-run      # 只打印将生成的包与文件数

输出（每个 skill 一个目录，自包含）:
  <out>/<skill-name>/
    SKILL.md              # 双格式 frontmatter（标准 name/description + skillhub slug/displayName/version/summary/license）
                          # + 正文适配（路径改写 / Step 0 / CLAUDE.md 引用替换 — 仅作用于包内副本）
    requirements.txt      # 运行时依赖（从 pyproject.toml [project].dependencies 生成）
    scripts/<entry>.py    # 引擎（包副本导入改写；journal 包内为 inline 数据管道形态）
    scripts/lib/          # 依赖闭包合并后的单一 lib 包（pulse 例外：lib/ 落包根）
    references/ events/   # 各 skill 自有文档/数据
    lib/references/       # 共享 skills/lib/references 文档（位置不变）

v0.2.7 修订（替代旧版的全量复制 + shim 转发层）:
  * 闭包裁剪: 从引擎入口 BFS 解析 import（ast + import_module 字符串 + 跨 skill
    加载器 load_gap_scan_module / load_invest_a_etf_module + SKILL.md 内联 python），
    只复制闭包内模块。etf 从全量合并 stock lib(74) 降为闭包子集；测试与
    死模块（futures_data 等）不进包
  * 单一 lib: 共享 skills/lib 真实现直接落入包内 lib，替代 23 个转发 shim。
    主仓库 shim 层只为仓库布局服务（引擎 scripts/ + 共享 skills/lib 分离），
    包内 sys.path[0]=scripts 已可解析 lib 包 → 无双重文件
  * 跨 skill 闭包并入: journal→stock、pulse→journal+stock、pattern→gap-scan，
    修 v0.2.7 三个包 standalone 依赖不闭合（缺 store/market_microstructure/gap 模块）
  * 包内副本确定性改写: 裸导入 → lib.X（引擎 / pulse 内联）/ .X（lib 内，
    journal 包除外——inline 形态保持裸导入）; 生成包内 _invest_path.py
    （journal 双份: scripts/ 与 scripts/lib/，分别支撑引擎与 inline 上下文）;
    version.py 改为读 SKILL.md frontmatter（包内唯一权威版本）

设计背景: host-docs/v0.2.7/skillhub-publishing.md + skillhub-mirror-publish-design.md §4-§11
（200 文件/包限制 → 按 skill 独立发布；skillhub.cn frontmatter 用 slug/displayName/version/summary/license；
白名单不含 .toml → requirements.txt；文件数 >200 时退出码非零）。
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
LIB_DIR = ROOT / "skills" / "lib"
PYPROJECT = ROOT / "pyproject.toml"

# skillhub 单包文件数上限（用户实践；超限 → build_one 标记 + main 非零退出）
MAX_FILES = 200

# 发布的 skill（invest-a-limit-up 已废弃，无 SKILL.md，不发布）
PUBLISH_SKILLS = [
    "invest-a-stock",
    "invest-a-etf",
    "invest-a-journal",
    "invest-a-pulse",
    "invest-a-gap-scan",
    "invest-a-pattern-scan",
]

# 包内排除的路径（tests/__pycache__ 等）
EXCLUDE_DIRS = {"tests", "__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".DS_Store"}

# skillhub 文件类型白名单（.toml/.lock 不在白名单 → 用 requirements.txt）
ALLOWED_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".js", ".cjs", ".mjs", ".ts",
                    ".py", ".sh", ".png", ".jpg", ".svg"}

# 各 skill 的 skillhub 展示名/简介（summary 复用 description 或单独定制）
SKILL_META: dict[str, dict[str, str]] = {
    "invest-a-stock": {"displayName": "invest:a-stock 个股投研"},
    "invest-a-etf": {"displayName": "invest:a-etf ETF 研究"},
    "invest-a-journal": {"displayName": "invest:a-journal 交易日志"},
    "invest-a-pulse": {"displayName": "invest:a-pulse 市场情绪"},
    "invest-a-gap-scan": {"displayName": "invest:a-gap-scan 缺口扫描"},
    "invest-a-pattern-scan": {"displayName": "invest:a-pattern-scan 形态扫描"},
}

# 各 skill 的 CLI 入口脚本（SKILL.md 正文「见 CLAUDE.md」/「子命令全清单」改写目标；
# 无独立 CLI 的 skill（journal/pulse）跳过该类替换）
ENTRY_SCRIPTS: dict[str, str | None] = {
    "invest-a-stock": "invest.py",
    "invest-a-etf": "etf.py",
    "invest-a-journal": None,
    "invest-a-pulse": None,
    "invest-a-gap-scan": "scan.py",
    "invest-a-pattern-scan": "scan.py",
}

# ─────────────────────────────────────────────────────────────
# 包布局与闭包源配置
# ─────────────────────────────────────────────────────────────

# 引擎运行形态:
#   "script" — uv run python scripts/<entry>.py（sys.path[0]=scripts，lib 包即 scripts/lib）
#   "inline" — cd scripts/lib && python -c（journal 数据管道，lib 内保持裸导入）
#   "pulse"  — 无 scripts 目录，SKILL.md 内联 python 从包根运行，lib 落 <pkg>/lib/
LAYOUT: dict[str, str] = {
    "invest-a-stock": "script",
    "invest-a-etf": "script",
    "invest-a-journal": "inline",
    "invest-a-pulse": "pulse",
    "invest-a-gap-scan": "script",
    "invest-a-pattern-scan": "script",
}

# 跨 skill lib 合并源（闭包解析优先级: 共享 skills/lib > cross 列表序 > 包自身 scripts/lib）
# 主仓库里 gap/pattern 引擎经 ensure_invest_a_scripts_on_path 引导 stock 的 scripts/lib
# （lib.env / lib.proxy / lib.tushare_client 等），包内无此引导 → 一并闭包并入。
CROSS_LIBS: dict[str, list[str]] = {
    "invest-a-etf": ["invest-a-stock"],
    # journal 的 etf_data.py 是转发 shim（canonical 在 invest-a-etf，经
    # load_invest_a_etf_module 加载）→ etf 源须在 self 之前，canonical 胜出
    "invest-a-journal": ["invest-a-etf", "invest-a-stock"],
    "invest-a-pulse": ["invest-a-journal", "invest-a-stock"],
    "invest-a-gap-scan": ["invest-a-stock"],
    "invest-a-pattern-scan": ["invest-a-gap-scan", "invest-a-stock"],
}

# SKILL.md 正文中的跨 skill 路径改写（包内副本）
CROSS_PATH_REWRITES: dict[str, list[tuple[str, str]]] = {
    "invest-a-pulse": [
        ("skills/invest-a-journal/scripts/lib/market_microstructure.py",
         "lib/market_microstructure.py"),
    ],
    "invest-a-journal": [
        ("skills/invest-a-journal/scripts/lib", "scripts/lib"),
    ],
}


def project_version() -> str:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def runtime_dependencies() -> list[str]:
    with PYPROJECT.open("rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    return [d.strip() for d in deps if d.strip()]


def _collect_files(src: Path) -> list[Path]:
    """收集 src 下符合白名单、未被排除的文件（相对路径）。"""
    files = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.suffix in EXCLUDE_SUFFIXES or p.suffix not in ALLOWED_SUFFIXES:
            continue
        files.append(rel)
    return files


def _collect_own(src: Path) -> list[Path]:
    """包「自身文件」：SKILL.md / references / events / 引擎脚本，排除 scripts/lib
    （由闭包复制）与 scripts/__init__.py（包内运行不需要）。"""
    files = []
    for rel in _collect_files(src):
        if rel.parts[:2] == ("scripts", "lib"):
            continue
        if rel == Path("scripts/__init__.py"):
            continue
        files.append(rel)
    return files


# ─────────────────────────────────────────────────────────────
# 模块闭包解析（ast + 字符串兜底）
# ─────────────────────────────────────────────────────────────

def _py_module_map(root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """(点分模块名 → 文件, 包节点名 → __init__.py)。

    包节点名: "" = lib 根 __init__，"collector" = 子包 __init__。相对 lib 根。
    """
    modules: dict[str, Path] = {}
    pkg_inits: dict[str, Path] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name == "__init__.py":
            pkg_inits[".".join(rel.parts[:-1])] = p
        else:
            modules[".".join(rel.with_suffix("").parts)] = p
    return modules, pkg_inits


def _parse_imports(text: str) -> list[tuple[str, int]]:
    """ast 解析全部 import 语句 → [(目标模块名, 相对层级)]。

    相对层级 0 = 绝对（lib.X 前缀由调用方剥离）；>0 = from ..X。语句形如
    `from M import A` 时同时产出 (M, 0)、(A, 0)、(M.A, 0)，由解析器逐个尝试
    （M.A 覆盖 `from lib.collector import _kline_cache` 的子模块形式）。
    """
    out: list[tuple[str, int]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, 0))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module is None:
                    for a in node.names:
                        out.append((a.name, node.level))
                else:
                    out.append((node.module, node.level))
            else:
                if node.module:
                    out.append((node.module, 0))
                    for a in node.names:
                        out.append((a.name, 0))
                        out.append((f"{node.module}.{a.name}", 0))
                else:
                    for a in node.names:
                        out.append((a.name, 0))
    return out


_MD_IMPORT_RE = re.compile(
    r"\bfrom\s+([A-Za-z_][\w.]*)\s+import\b|\bimport\s+([A-Za-z_][\w.]*)\b"
)


def _md_import_names(text: str) -> list[str]:
    """从 SKILL.md 内联 python 提取 import 目标名（去重保序）。"""
    names: list[str] = []
    for m in _MD_IMPORT_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if name and name not in names:
            names.append(name)
    return names


# 字符串兜底: import_module("lib.X") / "lib.X" 字面量 / 跨 skill 加载器
_STRING_IMPORT_RE = re.compile(
    r"import_module\(\s*[\"'](?:lib\.)?([A-Za-z_][\w.]*)[\"']\s*\)"
    r"|[\"']lib\.([A-Za-z_][\w.]*)[\"']"
)
_LOAD_GAP_SCAN_RE = re.compile(r"load_gap_scan_module\(\s*[\"']([A-Za-z_]\w*)[\"']\s*\)")
_LOAD_INVEST_A_ETF_RE = re.compile(r"load_invest_a_etf_module\s*\(")


def _dot_name(own_name: str, level: int, mod: str) -> str:
    """相对导入解析后的包内点分名（闭包去重键）。"""
    parts = own_name.split(".") if own_name else []
    base = parts[: max(0, len(parts) - level)]
    return ".".join(base + [mod]) if mod else ".".join(base)


class _Closure:
    """一次构建的模块宇宙 + 闭包结果。

    解析优先级（canonical 真实现优先于 shim）: 共享 skills/lib > cross > 包自身。
    """

    def __init__(self, skill_name: str):
        self.skill_name = skill_name
        self.layout = LAYOUT[skill_name]
        self.sources: list[tuple[str, Path]] = [("shared", LIB_DIR)]
        for other in CROSS_LIBS.get(skill_name, []):
            self.sources.append((f"cross:{other}",
                                 SKILLS_DIR / other / "scripts" / "lib"))
        self.sources.append(("self", SKILLS_DIR / skill_name / "scripts" / "lib"))

        self._by_source: dict[str, tuple[dict[str, Path], dict[str, Path]]] = {}
        self.module_map: dict[str, tuple[str, Path]] = {}   # 名 → (source_key, 文件)
        for key, root in self.sources:
            if not root.is_dir():
                continue
            m = _py_module_map(root)
            self._by_source[key] = m
            for name, f in {**m[0], **m[1]}.items():
                if name not in self.module_map:
                    self.module_map[name] = (key, f)
        self.included: dict[str, tuple[str, Path]] = {}     # 闭包（含包节点与 __init__）
        self._assets: set[Path] = set()                     # 引用 assets/ 的模块所在源目录

    # ---- 解析 ----
    def resolve(self, name: str) -> tuple[str, Path] | None:
        """绝对名解析（已剥 lib. 前缀）。"""
        return self.module_map.get(name)

    def resolve_relative(self, own_name: str, level: int, mod: str,
                         source_key: str) -> tuple[str, Path] | None:
        """相对导入解析。

        universe（优先级 shared > cross > self）优先 — 同名冲突时 canonical 真实现
        覆盖 shim（如 stock 自身 lib/db_util.py 是转发 shim，store.py 的相对导入
        必须落到共享真实现，否则自循环）；文件自身 source 仅兜底同包子模块。
        """
        parts = own_name.split(".") if own_name else []
        base = parts[: max(0, len(parts) - level)]
        cand = ".".join(base + ([mod] if mod else []))
        if cand in self.module_map:
            return self.module_map[cand]
        m, pi = self._by_source[source_key]
        if cand in m:
            return (source_key, m[cand])
        if cand in pi:
            return (source_key, pi[cand])
        return None

    def _lib_root_init(self) -> tuple[str, Path]:
        """lib 根 __init__.py：优先共享（pattern 无自身 __init__），否则自身。"""
        for key, root in self.sources:
            if not root.is_dir():
                continue
            if (root / "__init__.py").is_file():
                return key, root / "__init__.py"
        raise SystemExit(f"{self.skill_name}: 找不到 lib 根 __init__.py")

    # ---- 闭包 BFS ----
    def _need_invest_path(self) -> None:
        """包内 _invest_path 为生成模块；其内部引用真 invest_path（共享 canonical）。"""
        r = self.resolve("invest_path")
        if r:
            self._enqueue("invest_path", r)

    def _enqueue(self, name: str, resolved: tuple[str, Path]) -> None:
        if name in self.included:
            return
        self.included[name] = resolved
        self._scan_node(name, resolved)

    def _scan_node(self, name: str, resolved: tuple[str, Path]) -> None:
        key, f = resolved
        # 包节点（__init__.py）的相对 level 1 指向自身包；以 ".__init__" 后缀使
        # _dot_name / resolve_relative 的截断公式统一（"collector.__init__"[:-1]="collector"）。
        own = name + ".__init__" if f.name == "__init__.py" else name
        text = f.read_text(encoding="utf-8")
        for target, level in _parse_imports(text):
            self._follow(own, key, target, level)
        for t in _STRING_IMPORT_RE.findall(text):
            for g in t:
                if g:
                    self._follow(name, key, g, 0)
        for t in _LOAD_GAP_SCAN_RE.findall(text):
            self._follow(name, key, t, 0)
        if _LOAD_INVEST_A_ETF_RE.search(text):
            self._follow(name, key, "etf_data", 0)
        if "assets/" in text:
            self._assets.add(f.parent)

    def _follow(self, own_name: str, own_source: str, target: str, level: int) -> None:
        """解析一条 import 引用并入队。

        level > 0 即相对导入（ast 的 `from . import X` 产出的 target 为 "X" 不带点，
        须以 level 判别）；target 以 "." 开头是 `from ..X` 类既有形式，兼容处理。
        """
        if level > 0 or target.startswith("."):
            mod = target.lstrip(".")
            if mod == "_invest_path":
                self._need_invest_path()
                return
            r = self.resolve_relative(own_name, level, mod, own_source)
            if r is None:
                return
            self._enqueue(_dot_name(own_name, level, mod), r)
            return
        if target.startswith("lib."):
            target = target[len("lib."):]
        if target == "lib":
            return
        if target == "_invest_path":
            self._need_invest_path()
            return
        r = self.resolve(target)
        if r is None:
            return
        self._enqueue(target, r)

    def compute(self, entry_files: list[Path], md_text: str = "") -> None:
        """从入口（引擎 py + SKILL.md 内联 import 名）BFS 求闭包。

        引擎文件自身由 build_one 复制（不入 included），此处只跟踪其 lib 依赖。
        """
        seeds: list[tuple[str, int]] = []
        for f in entry_files:
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            seeds.extend(_parse_imports(text))
            # 引擎文件的字符串兜底（import_module / 跨 skill 加载器调用点）
            for t in _STRING_IMPORT_RE.findall(text):
                for g in t:
                    if g:
                        seeds.append((g, 0))
            for t in _LOAD_GAP_SCAN_RE.findall(text):
                seeds.append((t, 0))
            if _LOAD_INVEST_A_ETF_RE.search(text):
                seeds.append(("etf_data", 0))
        for name in _md_import_names(md_text):
            seeds.append((name, 0))

        for t, _lv in seeds:
            target = t[len("lib."):] if t.startswith("lib.") else t
            if target == "lib" or target.startswith("."):
                continue
            if target == "_invest_path":
                self._need_invest_path()
                continue
            r = self.resolve(target)
            if r:
                self._enqueue(target, r)

    # ---- 复制计划 ----
    def plan(self) -> list[tuple[Path, Path, str]]:
        """[(包内 lib 相对路径, 源文件, 改写 kind)]。

        kind: lib_root（深 0）/ lib_sub（深 ≥1）/ none（journal 保持裸导入不改写）。
        """
        plan: list[tuple[Path, Path, str]] = []
        src_by_key = dict(self.sources)
        # lib 根 __init__（+1 文件）
        k, f = self._lib_root_init()
        plan.append((Path("__init__.py"), f, "lib_root"))
        for name in sorted(self.included):
            key, f = self.included[name]
            rel = f.relative_to(src_by_key[key])
            depth = len(rel.parts) - 1
            if self.layout == "inline":
                kind = "none"
            else:
                kind = "lib_root" if depth == 0 else "lib_sub"
            plan.append((rel, f, kind))
        return plan

    def rewrite_names(self) -> set[str]:
        """包内可解析的单段模块名（改写白名单）+ 两个路径引导模块。"""
        names = {n for n in self.included if "." not in n}
        names.add("invest_path")
        names.add("_invest_path")
        return names

    def asset_files(self) -> list[tuple[Path, Path]]:
        """[(assets 相对路径, 源文件)] — 引用 assets/ 的闭包模块所在源目录。"""
        out: list[tuple[Path, Path]] = []
        for src_root in sorted(self._assets):
            for rel in _collect_files(src_root):
                if rel.parts[:1] == ("assets",):
                    out.append((rel, src_root / rel))
        return out


# ─────────────────────────────────────────────────────────────
# 包内副本改写
# ─────────────────────────────────────────────────────────────

_FROM_RE = re.compile(r"^(\s*)from\s+([A-Za-z_][\w.]*)\s+import\b(.*)$")
_IMPORT_RE = re.compile(r"^(\s*)import\s+([A-Za-z_][\w.]*)\b(.*)$")


def _rewrite_mod(mod: str, kind: str, names: set[str]) -> str | None:
    """单个 import 目标的改写；返回 None 表示保留原样。"""
    if mod.startswith("."):
        return None
    if mod == "lib" or mod.startswith("lib."):
        return None
    if mod in names:
        prefix = "lib." if kind in ("engine", "lib_sub", "pulse_md") else "."
        return prefix + mod
    return None


def _rewrite_line(line: str, kind: str, names: set[str]) -> str:
    m = _FROM_RE.match(line)
    if m:
        ind, mod, rest = m.groups()
        nm = _rewrite_mod(mod, kind, names)
        if nm:
            return f"{ind}from {nm} import{rest}"
        return line
    m = _IMPORT_RE.match(line)
    if m:
        ind, mod, rest = m.groups()
        if mod in names and mod not in ("lib",):
            return f"{ind}import lib.{mod}{rest}"
        return line
    return line


def _rewrite_imports(text: str, kind: str, names: set[str]) -> str:
    """包副本导入改写（确定性，仅改写 names 白名单内的裸导入）。

    kind:
      engine   — scripts/*.py: 裸 from X → from lib.X
      lib_root — lib/*.py 深 0: 裸 from X → from .X（journal 包不应用）
      lib_sub  — lib/sub/*.py: 裸 from X → from lib.X
      pulse_md — SKILL.md 内联 python: 裸 from X → from lib.X（非锚定匹配）
    """
    if kind == "pulse_md":
        def _sub_from(m: re.Match) -> str:
            nm = _rewrite_mod(m.group(1), "pulse_md", names)
            return f"from {nm} import" if nm else m.group(0)
        text = re.sub(r"\bfrom\s+([A-Za-z_][\w.]*)\s+import\b", _sub_from, text)

        def _sub_import(m: re.Match) -> str:
            mod = m.group(1)
            if mod in names and mod != "lib":
                return f"import lib.{mod}"
            return m.group(0)
        text = re.sub(r"\bimport\s+([A-Za-z_][\w.]*)\b", _sub_import, text)
        return text
    return "\n".join(_rewrite_line(l, kind, names) for l in text.splitlines())


# 包内生成: _invest_path.py（替代主仓库 shim；engine/inline 双上下文引导）
def _generated_invest_path(lib_depth: int) -> str:
    """生成包内 _invest_path.py。

    本模块在两种上下文被裸导入（inline: pwd=scripts/lib；引擎: sys.path[0]=scripts），
    __package__ 为空 → 内部必须用裸导入（相对导入不可用）。自身先把 lib 目录
    （裸 `invest_path` 解析）与包根/scripts（`lib.X` 包名解析）加入 sys.path。
    lib_depth: lib 根到包根的父链层数（scripts/lib 深 2；pulse lib/ 深 1）。
    """
    parents = "".join(".parent" for _ in range(lib_depth))
    return f'''"""包内路径 shim（由 build_skillhub_packages.py 生成）。

主仓库的 shim 把共享 skills/lib 加入 sys.path；包内全部模块单份落在本 lib 包，
本模块可能在顶层上下文被裸导入（__package__ 为空）→ 内部用裸导入。
把 lib 目录与包根/scripts 加入 sys.path，使裸 `invest_path` 与 `lib.X` 均可解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

_scripts = Path(__file__).resolve().parent{parents}
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_shared_lib_on_path,
    load_invest_a_etf_module,
)

ensure_skills_lib_on_path = ensure_shared_lib_on_path

__all__ = [
    "ensure_invest_a_scripts_on_path",
    "ensure_skills_lib_on_path",
    "ensure_shared_lib_on_path",
    "load_invest_a_etf_module",
]
'''


# 包内生成: scripts/_invest_path.py（仅 inline 布局 — journal 引擎上下文引导）
_GENERATED_SCRIPTS_INVEST_PATH = '''"""包内引导 shim（由 build_skillhub_packages.py 生成）— scripts 目录上下文。

journal 数据管道在 inline（cd scripts/lib && python -c）与引擎 CLI 两种上下文
运行；inline 由 scripts/lib/_invest_path.py 引导，本文件覆盖引擎上下文
（sys.path[0]=scripts）：把 scripts/lib 加入 sys.path，使 lib 模块的裸导入可解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent / "lib"
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

from lib._invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_shared_lib_on_path,
    load_invest_a_etf_module,
)

ensure_skills_lib_on_path = ensure_shared_lib_on_path

__all__ = [
    "ensure_invest_a_scripts_on_path",
    "ensure_skills_lib_on_path",
    "ensure_shared_lib_on_path",
    "load_invest_a_etf_module",
]
'''


# 包内生成: version.py（读 SKILL.md frontmatter；主仓库 canonical 为 pyproject.toml）
_PACKAGE_VERSION_PY = '''"""Package version from SKILL.md frontmatter（skillhub 包内改写，由构建脚本生成）。

主仓库 canonical 为 pyproject.toml [project].version（经 version.py 读取）；
包内无 pyproject，SKILL.md frontmatter 的 version 字段（构建时注入，与主仓库
一致）为包内唯一权威版本。
"""
from __future__ import annotations

from pathlib import Path


def get_package_version(default: str = "unknown", *,
                        stop_at_first: bool = False,
                        _start_dir: Path | None = None) -> str:
    """Read version from the package SKILL.md frontmatter (walk-up)."""
    try:
        root = _start_dir or Path(__file__).resolve().parent
        for parent in [root, *root.parents]:
            md = parent / "SKILL.md"
            if not md.exists():
                continue
            for raw in md.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default
'''


# 包内生成: invest_path.py（整体生成，替代主仓库跨 skill 路径引导）
_PACKAGE_INVEST_PATH_PY = '''"""Package-local path bootstrap（由 build_skillhub_packages.py 生成）。

主仓库的 skills/lib/invest_path.py 负责跨 skill 路径引导（resolve 到
invest-a-stock / invest-a-etf / invest-a-gap-scan 各自的 scripts/lib）；
包内所有模块单份落在本 lib 包，sys.path[0] 已覆盖，目录函数直接指向包内
scripts/lib。load_*_module 用包导入（import_module("lib.X")），与包内其它
引用共享同一模块实例（主仓库按文件路径加载会产生双实例，包内无此问题）。
"""
from __future__ import annotations

import importlib as _il
import sys
from pathlib import Path

__all__ = [
    "invest_a_scripts_dir",
    "invest_a_etf_lib_dir",
    "ensure_invest_a_scripts_on_path",
    "ensure_shared_lib_on_path",
    "load_gap_scan_module",
    "load_invest_a_etf_module",
]


def invest_a_scripts_dir() -> Path:
    """包内 scripts 目录（sys.path[0]，lib 包所在父目录）。"""
    return Path(__file__).resolve().parent.parent


def invest_a_etf_lib_dir() -> Path:
    """包内 lib 目录（etf_data 等模块所在）。"""
    return Path(__file__).resolve().parent


def gap_scan_lib_dir() -> Path:
    """包内 lib 目录（gap-scan canonical 模块合并后所在）。"""
    return Path(__file__).resolve().parent


def ensure_invest_a_scripts_on_path() -> Path:
    """包内 scripts 已在 sys.path[0]（脚本运行时），幂等补插。"""
    scripts = invest_a_scripts_dir()
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    return scripts


def ensure_shared_lib_on_path() -> Path:
    """包内 lib 目录（相对导入消费方无需路径引导），幂等补插。"""
    d = Path(__file__).resolve().parent
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)
    return d


_GAP_SCAN_KLINE_SOURCE_NAME = "gap_scan_kline_source"


def load_gap_scan_module(module_file: str = "kline_source"):
    """包内加载 gap-scan canonical 模块（import_module("lib.X")，共享同一实例）。"""
    mod_name = f"{_GAP_SCAN_KLINE_SOURCE_NAME}_{module_file}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    mod = _il.import_module(f"lib.{module_file}")
    sys.modules[mod_name] = mod
    return mod


_INVEST_A_ETF_MODULE_NAME = "invest_a_etf_etf_data"


def load_invest_a_etf_module():
    """包内加载 etf_data（import_module("lib.etf_data")，共享同一实例）。"""
    mod = sys.modules.get(_INVEST_A_ETF_MODULE_NAME)
    if mod is not None:
        return mod
    mod = _il.import_module("lib.etf_data")
    sys.modules[_INVEST_A_ETF_MODULE_NAME] = mod
    return mod
'''


# ─────────────────────────────────────────────────────────────
# SKILL.md frontmatter 注入与正文适配
# ─────────────────────────────────────────────────────────────

def _read_frontmatter(skill_md: Path) -> tuple[dict[str, str], str]:
    """读取 SKILL.md frontmatter（简单解析 name/description）。返回 (字段 dict, 原始文件文本)。"""
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip().strip('"')
    return fields, text


def _inject_skillhub_frontmatter(skill_md: Path, slug: str, version: str) -> None:
    """在 SKILL.md frontmatter 注入 skillhub.cn 字段（slug/displayName/version/summary/license）。

    保留标准字段（name/description/user-invocable/metadata），追加 skillhub 字段；
    若字段已存在则更新。frontmatter 为开放 YAML，各平台只读自己认识的字段。
    """
    fields, text = _read_frontmatter(skill_md)
    display = SKILL_META[slug]["displayName"]
    summary = fields.get("description", display)
    # 去掉触发词尾巴（skillhub 无触发词概念，displayName/summary 保持简洁）
    summary = re.split(r"触发词[:：]", summary)[0].strip()

    additions = {
        "slug": slug,
        "displayName": display,
        "version": f'"{version}"',
        "summary": f'"{summary}"',
        "license": "MIT",
    }
    m = re.match(r"^(---\n)(.*?)(\n---\n)", text, re.S)
    if not m:
        raise SystemExit(f"{skill_md}: 缺少 frontmatter，无法注入 skillhub 字段")
    body = m.group(2)
    lines = body.splitlines()
    existing = {ln.split(":", 1)[0].strip() for ln in lines if ":" in ln and not ln.startswith((" ", "\t"))}
    new_lines = []
    for k, v in additions.items():
        if k in existing:
            for ln in lines:
                if ln.split(":", 1)[0].strip() == k:
                    new_lines.append(f"{k}: {v}")
                else:
                    new_lines.append(ln)
            lines = new_lines
            new_lines = []
        else:
            lines.append(f"{k}: {v}")
    new_text = f"{m.group(1)}{chr(10).join(lines)}{m.group(3)}{text[m.end():]}"
    if new_text != text:
        skill_md.write_text(new_text, encoding="utf-8")


# ---- SKILL.md 正文适配（五类确定性替换 + 跨 skill 路径 + pulse 内联改写）----
_STEP0_BLOCK = (
    "\n> **Step 0（首次使用）**：初始化虚拟环境并安装依赖（仅一次）：\n"
    ">\n"
    "> ```bash\n"
    "> uv venv && uv pip install -r requirements.txt\n"
    "> ```\n"
    ">\n"
    "> 之后引擎命令不变：`uv run python` 自动发现包根 `.venv`。\n"
)


def _rewrite_skill_md(text: str, skill_name: str,
                      names: set[str] | None = None) -> str:
    """SKILL.md 正文适配 — 五类确定性替换 + 包内新增。

    1. skills/<name>/scripts/ → scripts/（CLI 命令行路径）
    2. ../../../skills/lib/references/ → lib/references/（含链接文本的
       skills/lib/references/ → lib/references/）
    3. 「见 CLAUDE.md「X」」 → 「见 scripts/<entry>.py --help」（无 CLI 的 skill 跳过）
    4. 「> 运行目录：code/ …」行 → 「运行目录：skill 包根 …」（含第 3 类合并改写）
    5. 「CLI 命令 / 运行」节开头插入 Step 0（uv venv && uv pip install -r requirements.txt）
    6. 跨 skill 路径改写（CROSS_PATH_REWRITES）
    7. pulse 专属: cd 路径收口到包根 + 内联 python 裸导入 → lib.X
    """
    entry = ENTRY_SCRIPTS.get(skill_name)
    text = text.replace(f"skills/{skill_name}/scripts/", "scripts/")
    text = text.replace("../../../skills/lib/references/", "lib/references/")
    text = text.replace("skills/lib/references/", "lib/references/")
    if entry:
        text = re.sub(r"见 CLAUDE\.md「[^」]*」", f"见 scripts/{entry} --help", text)
        text = re.sub(
            r"^> 运行目录：.*$",
            f"> 运行目录：skill 包根（与 scripts/ 同级）。必须用 `uv run python`"
            f"（uv 自动发现包根 .venv）。子命令全清单见 scripts/{entry} --help。",
            text,
            flags=re.M,
        )
    text, _ = re.subn(
        r"(^#{1,6}\s*(?:CLI|运行)\b[^\n]*\n)",
        rf"\1{_STEP0_BLOCK}",
        text,
        count=1,
        flags=re.M,
    )
    for old, new in CROSS_PATH_REWRITES.get(skill_name, []):
        text = text.replace(old, new)
    if skill_name == "invest-a-pulse" and names:
        text = _rewrite_pulse_md(text, names)
    return text


def _rewrite_pulse_md(text: str, names: set[str]) -> str:
    """pulse 包专属：inline 采集命令收口到包根（模块单份落 <pkg>/lib/）。"""
    text = text.replace(
        'cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-journal/scripts/lib"',
        'cd "${INVEST_SKILLS_ROOT:-.}"',
    )
    text = text.replace(
        'cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-stock/scripts/lib"',
        'cd "${INVEST_SKILLS_ROOT:-.}"',
    )
    return _rewrite_imports(text, "pulse_md", names)


def _rewrite_skill_md_file(skill_md: Path, skill_name: str,
                           names: set[str] | None = None) -> None:
    """对包内 SKILL.md 副本执行正文适配（主仓库源文件不受影响）。"""
    text = skill_md.read_text(encoding="utf-8")
    new_text = _rewrite_skill_md(text, skill_name, names)
    if new_text != text:
        skill_md.write_text(new_text, encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 包构建
# ─────────────────────────────────────────────────────────────

_N_SHARED_REFS = len(_collect_files(LIB_DIR / "references"))  # 动态计数（含 backtest_prereg 子目录）

def build_one(skill_name: str, version: str, out_dir: Path, dry_run: bool) -> int:
    src = SKILLS_DIR / skill_name
    if not (src / "SKILL.md").is_file():
        print(f"⚠️ 跳过 {skill_name}: 无 SKILL.md")
        return 0
    dst = out_dir / skill_name
    layout = LAYOUT[skill_name]

    # 1. 闭包（入口 = 引擎 py；inline/pulse 另含 SKILL.md 内联 import 名）
    closure = _Closure(skill_name)
    entry_files = sorted((src / "scripts").glob("*.py")) if (src / "scripts").is_dir() else []
    entry_files = [p for p in entry_files if p.name != "__init__.py"]
    md_text = ""
    if layout in ("inline", "pulse"):
        md_text = (src / "SKILL.md").read_text(encoding="utf-8")
    closure.compute(entry_files, md_text)

    # 2. 计数
    own = _collect_own(src)
    lib_plan = closure.plan()
    assets = closure.asset_files()
    n_generated = 2 if layout == "inline" else 1   # 生成 _invest_path.py（journal + scripts/ 引导）
    n_lib = len(lib_plan) + len(assets)
    total = len(own) + n_lib + _N_SHARED_REFS + 1 + n_generated  # + requirements.txt

    ok = total <= MAX_FILES
    print(f"\n=== {skill_name} ===")
    print(f"  自身文件: {len(own)} + lib 闭包: {n_lib} + references: {_N_SHARED_REFS}"
          f" + requirements/引导: {1 + n_generated} = {total} (≤{MAX_FILES}: {'✅' if ok else '❌ 超限'})")
    if dry_run:
        return total

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    lib_root = dst / "scripts" / "lib" if layout != "pulse" else dst / "lib"
    lib_root.mkdir(parents=True, exist_ok=True)

    names = closure.rewrite_names()

    # 3. 复制自身（引擎脚本做 engine 改写）
    for rel in own:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (src / rel).read_text(encoding="utf-8")
        if rel.parts[:1] == ("scripts",) and rel.suffix == ".py" and len(rel.parts) == 2:
            text = _rewrite_imports(text, "engine", names)
        target.write_text(text, encoding="utf-8")

    # 4. lib 闭包复制（含改写 / version / invest_path 替换）
    for rel, src_path, kind in lib_plan:
        target = lib_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.name == "version.py":
            target.write_text(_PACKAGE_VERSION_PY, encoding="utf-8")
        elif rel.name == "invest_path.py":
            target.write_text(_PACKAGE_INVEST_PATH_PY, encoding="utf-8")
        else:
            text = src_path.read_text(encoding="utf-8")
            if kind != "none":
                text = _rewrite_imports(text, kind, names)
            target.write_text(text, encoding="utf-8")

    # 5. assets（闭包内模块引用 assets/ 时）
    for rel, src_path in assets:
        target = lib_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, target)

    # 6. 生成 _invest_path.py（lib 包内；journal 额外 scripts/ 引导）
    lib_depth = 2 if layout != "pulse" else 1
    (lib_root / "_invest_path.py").write_text(
        _generated_invest_path(lib_depth), encoding="utf-8")
    if layout == "inline":
        scripts_dir = dst / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "_invest_path.py").write_text(
            _GENERATED_SCRIPTS_INVEST_PATH, encoding="utf-8")

    # 7. 共享 references 文档 → <dst>/lib/references/
    refs_src = LIB_DIR / "references"
    for rel in _collect_files(refs_src):
        target = dst / "lib" / "references" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(refs_src / rel, target)

    # 8. requirements.txt
    (dst / "requirements.txt").write_text(
        "# 运行时依赖（由主仓库 pyproject.toml [project].dependencies 生成）\n"
        + "\n".join(runtime_dependencies()) + "\n",
        encoding="utf-8",
    )

    # 9. 注入 skillhub frontmatter + SKILL.md 正文适配（pulse 需闭包名做内联改写）
    _inject_skillhub_frontmatter(dst / "SKILL.md", skill_name, version)
    _rewrite_skill_md_file(dst / "SKILL.md", skill_name, names)

    print(f"  ✅ 已生成 {dst.relative_to(out_dir.parent if out_dir.parent != ROOT else ROOT)}")
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 SkillHub 分发包")
    ap.add_argument("--out", type=Path, default=ROOT.parent / "invest-skills-skillhub" / "skills",
                    help="输出目录（默认 ../invest-skills-skillhub/skills）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    ap.add_argument("--skills", nargs="*", default=None, help="只构建指定 skill（默认全部）")
    args = ap.parse_args(argv)

    version = project_version()
    print(f"主仓库版本: {version}")
    print(f"输出目录: {args.out.resolve()}")

    skills = args.skills or PUBLISH_SKILLS
    grand_total = 0
    results: list[tuple[str, int]] = []
    for name in skills:
        if name not in PUBLISH_SKILLS:
            print(f"⚠️ 跳过未知 skill: {name}")
            continue
        total = build_one(name, version, args.out, args.dry_run)
        results.append((name, total))
        grand_total += total

    failed = [(name, total) for name, total in results if total > MAX_FILES]
    print(f"\n共 {len(results)} 个包，文件总数 {grand_total}（按包独立计算，均 ≤{MAX_FILES} 即可发布）")
    if failed:
        for name, total in failed:
            print(f"❌ {name}: {total} 个文件 > {MAX_FILES} 上限，构建失败")
        return 1
    if not args.dry_run:
        print("下一步: 进入分发仓库 → 逐个包 `skillhub publish <dir> --dry-run` 预检 → 提交 → tag → CI 发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
