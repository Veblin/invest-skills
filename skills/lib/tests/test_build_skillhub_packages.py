"""build_skillhub_packages.py 单测 — v0.2.7 修订（闭包裁剪 + 单一 lib 合并重构）。

覆盖:
  (a) 模块闭包解析（ast 相对导入 / from lib.X 子模块 / import_module 字符串 /
      跨 skill 加载器 load_gap_scan_module / SKILL.md 内联 import）
  (b) shim 优先级（共享真实现覆盖 stock shim；etf canonical 覆盖 journal shim）
  (c) 包内单一 lib：无 shim 双文件、裸导入改写（engine→lib.X / lib 内→.X）、
      journal inline 保持裸导入 + 双 _invest_path 引导
  (d) 生成物：version.py 读 SKILL.md frontmatter、invest_path.py 包内版
  (e) SKILL.md 正文五类替换 + 跨 skill 路径 + pulse 内联改写
  (f) 文件数 >MAX_FILES 时非零退出
另含: 真实构建产物断言 + dry-run 六包 ≤200（显式 --out 到 tmp_path，不污染
../invest-skills-skillhub）；主仓库源文件不被回写。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import build_skillhub_packages as b  # noqa: E402


# ---- (a) 闭包解析 ----
def test_parse_imports_from_import_submodule():
    imports = b._parse_imports("from lib.collector import _kline_cache\n")
    assert ("lib.collector", 0) in imports
    assert ("lib.collector._kline_cache", 0) in imports


def test_parse_imports_relative_forms():
    imports = b._parse_imports("from . import _base\nfrom .._invest_path import x\n")
    assert ("_base", 1) in imports
    assert ("_invest_path", 2) in imports


def test_closure_collector_subpackage():
    """collector 子包经 __init__ 相对导入完整进入闭包（曾漏 _base/_legacy/_sources/_orchestrate）。"""
    c = b._Closure("invest-a-stock")
    c.compute([b.SKILLS_DIR / "invest-a-stock" / "scripts" / "invest.py"])
    for name in ("collector", "collector._base", "collector._legacy",
                 "collector._sources", "collector._orchestrate"):
        assert name in c.included, f"闭包缺 {name}"


def test_closure_shim_priority_shared_wins():
    """同名冲突时共享真实现覆盖 stock shim（store.py 的相对 .db_util 不得落到 shim）。"""
    c = b._Closure("invest-a-stock")
    c.compute([b.SKILLS_DIR / "invest-a-stock" / "scripts" / "invest.py"])
    key, f = c.included["db_util"]
    assert key == "shared", f"db_util 应解析到共享真实现，实际 {key}"
    text = f.read_text(encoding="utf-8")
    assert "from .db_util import" not in text, "包内 db_util 不得是 stock shim（自循环）"


def test_closure_import_module_string():
    c = b._Closure("invest-a-stock")
    c.compute([b.SKILLS_DIR / "invest-a-stock" / "scripts" / "invest.py"])
    # render_markdown/_v2.py 经 import_module('lib.render_markdown._concise') 动态加载
    assert "render_markdown._concise" in c.included


def test_closure_load_gap_scan_module():
    """pattern 引擎调 load_gap_scan_module('universe') → gap-scan 的 universe 入闭包。"""
    c = b._Closure("invest-a-pattern-scan")
    c.compute([b.SKILLS_DIR / "invest-a-pattern-scan" / "scripts" / "scan.py"])
    assert "universe" in c.included
    assert "kline_source" in c.included
    # pattern 自身的 kline_source/universe 是转发 shim → 应解析到 gap-scan canonical
    key, f = c.included["kline_source"]
    assert key == "cross:invest-a-gap-scan", f"kline_source 应来自 gap-scan，实际 {key}"


def test_closure_journal_etf_data_canonical():
    """journal 的 etf_data 是转发 shim → invest-a-etf canonical 胜出（cross 优先）。"""
    c = b._Closure("invest-a-journal")
    c.compute([b.SKILLS_DIR / "invest-a-journal" / "scripts" / "journal.py"],
              (b.SKILLS_DIR / "invest-a-journal" / "SKILL.md").read_text(encoding="utf-8"))
    key, f = c.included["etf_data"]
    assert key == "cross:invest-a-etf", f"etf_data 应来自 invest-a-etf，实际 {key}"
    assert "Canonical owner" in f.read_text(encoding="utf-8")


def test_closure_md_inline_names():
    """journal/pulse 的 SKILL.md 内联 import 名进入闭包（数据管道运行面）。"""
    md = (b.SKILLS_DIR / "invest-a-journal" / "SKILL.md").read_text(encoding="utf-8")
    names = b._md_import_names(md)
    assert "query_data" in names and "market_microstructure" in names and "db" in names


# ---- (b) 包内副本改写 ----
def test_rewrite_engine_bare_imports():
    names = {"db", "dates", "etf_data", "_invest_path", "invest_path"}
    out = b._rewrite_imports(
        "from db import x\nfrom dates import y\nfrom _invest_path import z\n"
        "from lib.nums import n\nfrom typing import T\nimport akshare as ak\n",
        "engine", names)
    assert "from lib.db import x" in out
    assert "from lib.dates import y" in out
    assert "from lib._invest_path import z" in out
    assert "from lib.nums import n" in out          # 已是包内绝对 → 保留
    assert "from typing import T" in out            # stdlib → 保留
    assert "import akshare as ak" in out            # 三方 → 保留


def test_rewrite_lib_root_relative():
    names = {"db_util", "cache", "stats"}
    out = b._rewrite_imports(
        "from db_util import connect_db\n    from cache import default_cache\n"
        "from .schema import index_dimensions\nfrom lib.nums import safe_float\n",
        "lib_root", names)
    assert "from .db_util import connect_db" in out
    assert "    from .cache import default_cache" in out
    assert "from .schema import index_dimensions" in out   # 相对保留
    assert "from lib.nums import safe_float" in out        # 绝对保留


def test_rewrite_lib_sub_absolute():
    names = {"db_util"}
    out = b._rewrite_imports("from db_util import x\n", "lib_sub", names)
    assert "from lib.db_util import x" in out


def test_rewrite_pulse_md_inline():
    names = {"data_bridge", "market_microstructure", "_invest_path"}
    out = b._rewrite_imports(
        'import _invest_path; from data_bridge import get_microstructure; '
        'from market_microstructure import snapshot; import json',
        "pulse_md", names)
    assert "import lib._invest_path;" in out
    assert "from lib.data_bridge import get_microstructure" in out
    assert "from lib.market_microstructure import snapshot" in out
    assert "import json" in out                     # stdlib 保留


# ---- (c) 生成物 ----
def test_generated_invest_path_bare_import():
    """生成 _invest_path.py 用裸导入（inline 顶层加载场景 __package__ 为空）。"""
    out = b._generated_invest_path(2)
    assert "from invest_path import (" in out
    assert "from .invest_path import" not in out
    assert "ensure_skills_lib_on_path = ensure_shared_lib_on_path" in out


def test_generated_version_reads_skillmd(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: x\nversion: \"0.2.7\"\n---\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    mod_file = tmp_path / "lib" / "version.py"
    mod_file.write_text(b._PACKAGE_VERSION_PY, encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location("pkg_version_test", mod_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert mod.get_package_version() == "0.2.7"


# ---- (d) SKILL.md 改写 ----
def test_skill_md_rewrite_rules():
    # 1. CLI 路径
    text = 'cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-stock/scripts/invest.py report 600176\n'
    out = b._rewrite_skill_md(text, "invest-a-stock")
    assert "scripts/invest.py report 600176" in out
    assert "skills/invest-a-stock/scripts/" not in out
    # 2. references URL + 链接文本
    out = b._rewrite_skill_md("[report-conventions.md](../../../skills/lib/references/report-conventions.md)\n",
                              "invest-a-stock")
    assert "[report-conventions.md](lib/references/report-conventions.md)" in out
    out = b._rewrite_skill_md("[skills/lib/references/report-conventions.md](../../../skills/lib/references/report-conventions.md)\n",
                              "invest-a-stock")
    assert "[lib/references/report-conventions.md](lib/references/report-conventions.md)" in out
    # 3. 见 CLAUDE.md 引用
    out = b._rewrite_skill_md("子命令全清单见 CLAUDE.md「运行命令」。\n", "invest-a-stock")
    assert "子命令全清单见 scripts/invest.py --help。" in out
    # 4. 运行目录行（含第 3 类合并改写）
    text = "> 运行目录：`code/`。必须用 `uv run python`（所有引擎命令已统一带 `${INVEST_SKILLS_ROOT:-.}` cd 前缀）。子命令全清单见 CLAUDE.md「运行命令」。\n"
    out = b._rewrite_skill_md(text, "invest-a-stock")
    assert "运行目录：skill 包根" in out
    assert "见 scripts/invest.py --help" in out
    # 5. Step 0 插入（stock ## CLI 命令 / etf ### CLI / gap-scan ## 运行 均命中一次）
    text = "## CLI 命令\n\n```bash\nuv run python scripts/invest.py --help\n```\n"
    out = b._rewrite_skill_md(text, "invest-a-stock")
    assert "Step 0（首次使用）" in out
    assert out.index("## CLI 命令") < out.index("Step 0")
    out = b._rewrite_skill_md("### CLI\n\n```bash\nuv run python scripts/etf.py --help\n```\n", "invest-a-etf")
    assert "Step 0（首次使用）" in out
    out = b._rewrite_skill_md("## 运行\n\n```bash\nuv run python scripts/scan.py\n```\n", "invest-a-gap-scan")
    assert "Step 0（首次使用）" in out
    # 无 CLI/运行 节（journal 形态）不插入
    out = b._rewrite_skill_md("## 数据查询规范\n\n### 引擎脚本\n", "invest-a-journal")
    assert "Step 0" not in out
    # 无 CLI 的 skill 跳过 CLAUDE.md 引用替换
    out = b._rewrite_skill_md("见 CLAUDE.md「报告复检流程」\n", "invest-a-journal")
    assert "见 CLAUDE.md「报告复检流程」" in out
    # 6. 跨 skill 路径（journal → scripts/lib）
    out = b._rewrite_skill_md('cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-journal/scripts/lib" && \\\n',
                              "invest-a-journal")
    assert 'cd "${INVEST_SKILLS_ROOT:-.}/scripts/lib" && \\' in out


def test_rewrite_pulse_md_cd_paths_and_imports():
    text = ('cd "${INVEST_SKILLS_ROOT:-.}/skills/invest-a-journal/scripts/lib" && \\\n'
            'uv run python -c "from market_microstructure import snapshot; import json" 2>/dev/null\n')
    names = {"market_microstructure"}
    out = b._rewrite_pulse_md(text, names)
    assert 'cd "${INVEST_SKILLS_ROOT:-.}" && \\' in out
    assert "from lib.market_microstructure import snapshot" in out
    assert "skills/invest-a-journal" not in out


# ---- (e) 真实构建产物 ----
def test_stock_package_build_single_lib_and_rewrites(tmp_path):
    total = b.build_one("invest-a-stock", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-stock"
    # 单一 lib：无 shim 双文件（共享模块只出现一次，且为真实现）
    nums = (dst / "scripts/lib/nums.py").read_text(encoding="utf-8")
    assert "Shim" not in nums and "from nums import" not in nums
    assert not (dst / "lib/nums.py").exists(), "共享模块不得双份存在于 <pkg>/lib/"
    # collector 子包完整
    for sub in ("_base", "_legacy", "_sources", "_orchestrate"):
        assert (dst / "scripts/lib/collector" / f"{sub}.py").is_file()
    # 引擎改写: 裸导入已 lib. 化
    invest = (dst / "scripts/invest.py").read_text(encoding="utf-8")
    assert "from lib import collector, env, render" in invest
    # 生成物
    assert (dst / "scripts/lib/_invest_path.py").is_file()
    assert (dst / "scripts/lib/version.py").is_file()
    assert "SKILL.md frontmatter" in (dst / "scripts/lib/version.py").read_text(encoding="utf-8")
    # SKILL.md 改写
    md = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-stock/scripts/" not in md
    assert "Step 0" in md and "uv venv && uv pip install -r requirements.txt" in md
    assert "见 scripts/invest.py --help" in md
    # 共享 references 落包根 lib/references/
    assert (dst / "lib/references/report-conventions.md").is_file()


def test_etf_package_build_closure(tmp_path):
    total = b.build_one("invest-a-etf", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-etf"
    assert (dst / "scripts/lib/etf_data.py").is_file()          # etf 自身
    assert (dst / "scripts/lib/store.py").is_file()             # stock 闭包（etf 依赖）
    assert (dst / "scripts/lib/nums.py").is_file()              # 共享真实现（非 shim）
    # 死模块不进包（futures_data 无引用）
    assert not (dst / "scripts/lib/futures_data.py").exists()
    # 引擎无裸导入
    etf = (dst / "scripts/etf.py").read_text(encoding="utf-8")
    assert "from lib.etf_data import" in etf
    assert "_SCRIPT_PARENT" not in etf                          # 旧注入机制已移除
    # 单一 lib（无包根 lib/ 重复）
    assert not (dst / "lib/store.py").exists()


def test_journal_package_inline_layout(tmp_path):
    total = b.build_one("invest-a-journal", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-journal"
    # inline 形态: lib 模块保持裸导入（不 rewrite）+ 双 _invest_path 引导
    db = (dst / "scripts/lib/db.py").read_text(encoding="utf-8")
    assert "from db_util import connect_db" in db
    assert (dst / "scripts/lib/_invest_path.py").is_file()
    assert (dst / "scripts/_invest_path.py").is_file()          # engine 上下文引导
    # journal 的 etf_data → etf canonical
    assert "Canonical owner" in (dst / "scripts/lib/etf_data.py").read_text(encoding="utf-8")
    # SKILL.md cd 路径收口
    md = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-journal/scripts/lib" not in md


def test_pulse_package_lib_at_root(tmp_path):
    total = b.build_one("invest-a-pulse", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-pulse"
    # 模块落包根 lib/（无 scripts 目录）
    assert (dst / "lib/market_microstructure.py").is_file()     # journal 合入
    assert (dst / "lib/store.py").is_file()                     # stock 合入
    assert (dst / "lib/data_bridge.py").is_file()               # 共享
    assert not (dst / "scripts").exists()
    # SKILL.md 内联改写: cd 收口 + 裸导入 lib. 化
    md = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-journal" not in md
    assert "skills/invest-a-stock" not in md
    assert "from lib.market_microstructure import" in md
    assert "import lib._invest_path" in md


def test_pattern_package_gap_modules(tmp_path):
    total = b.build_one("invest-a-pattern-scan", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-pattern-scan"
    # gap canonical 合入（pattern 的 kline_source/universe 是转发 shim；gap_scanner
    # 仅 gap 自身 scan.py 使用，不进 pattern 闭包）
    kline = (dst / "scripts/lib/kline_source.py").read_text(encoding="utf-8")
    assert "Shim" not in kline
    assert not (dst / "scripts/lib/gap_scanner.py").exists()
    assert (dst / "scripts/lib/universe.py").is_file()
    # 包内 invest_path 提供 load_gap_scan_module
    ip = (dst / "scripts/lib/invest_path.py").read_text(encoding="utf-8")
    assert "import_module" in ip and "load_gap_scan_module" in ip


# ---- (e2) 闭包自闭合（发布包不得有悬空 lib.X）----
_GENERATED_MODULES = {"_invest_path", "invest_path"}


def _dangling_lib_imports(skill: str) -> set[tuple[str, str]]:
    """闭包文本中显式 `lib.X` 引用但 X 未入闭包 → {(来源, target)}。

    生成模块（_invest_path/invest_path）由 plan() 单独落包，不在 included 内，豁免。
    属性形式（from lib.X import func → target `lib.X.func`）以首段回溯判定归属。
    """
    c = b._Closure(skill)
    src = b.SKILLS_DIR / skill
    entries = [p for p in sorted((src / "scripts").glob("*.py")) if p.name != "__init__.py"]
    md = ""
    if c.layout in ("inline", "pulse"):
        md = (src / "SKILL.md").read_text(encoding="utf-8")
    c.compute(entries, md)
    texts = [(p.name, p.read_text(encoding="utf-8")) for p in entries]
    texts += [(n, f.read_text(encoding="utf-8")) for n, (_, f) in c.included.items()]
    if md:
        texts.append(("SKILL.md", md))
    out: set[tuple[str, str]] = set()
    for label, text in texts:
        for target, level in b._parse_imports(text):
            if level != 0 or not target.startswith("lib."):
                continue
            name = target[len("lib."):]
            head = name.split(".")[0]
            if name in c.included or head in c.included or head in _GENERATED_MODULES:
                continue
            out.add((label, target))
    return out


def test_published_packages_have_no_dangling_lib_imports():
    """每个发布包的闭包必须自闭合：显式 lib.X 引用不得被 _resolve 静默丢弃。

    R1 验收 H4/F3 的包内路径自适应只覆盖了路径解析，未覆盖 lib 闭包依赖——
    event-calendar 的 lib/proxy（unlock_source 东财直连）因 CROSS_LIBS 缺条目
    被丢弃，分发形态下池模式全部取数失败且错误归因到数据源，而仓库布局测试全绿。
    """
    for skill in b.PUBLISH_SKILLS:
        dangling = _dangling_lib_imports(skill)
        assert not dangling, f"{skill} 闭包悬空引用: {sorted(dangling)}"


def test_event_calendar_package_build_closure(tmp_path):
    """event-calendar 池模式的东财直连依赖（lib.proxy）与交易日历依赖必须进包。

    无 lib.proxy → 池模式每次取数 ModuleNotFoundError（误报为数据源不可得）；
    无 lib.tushare_client → trade_cal 恒走 except ImportError 估算分支（恒粗判）。
    """
    total = b.build_one("invest-a-event-calendar", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-event-calendar"
    assert (dst / "scripts/lib/proxy.py").is_file()
    assert (dst / "scripts/lib/tushare_client.py").is_file()
    assert (dst / "scripts/lib/unlock_source.py").is_file()
    # v3 宏观日程：数据层模块与两份策展表须一并进包（否则 --macro 在分发包内不可用）
    assert (dst / "scripts/macro_calendar.py").is_file()
    assert (dst / "references/fomc_meetings.yaml").is_file()
    assert (dst / "references/macro_sources.yaml").is_file()


def test_discover_package_bundles_dynamic_hk_modules(tmp_path):
    """单独分发的 discover-scan 也必须能运行其 advertised 的 --pool hk。"""
    total = b.build_one("invest-a-discover-scan", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-discover-scan"
    lib = dst / "scripts" / "lib"
    for name in ("hk_quote", "hk_financials", "hk_calendar", "hk_codes"):
        assert (lib / f"{name}.py").is_file(), f"动态 HK 依赖未随包: {name}"

    # CLI 实际把 scripts/lib 插入 sys.path 后以**顶层**导入 sources_hk；不得只测
    # ``from lib import`` 的包导入形态，否则 __package__ 非空会掩盖分发包故障。
    code = (
        f"import sys; sys.path.insert(0, r'{lib}')\n"
        "import sources_hk\n"
        "mod = sources_hk._load_hk_module('hk_quote')\n"
        "assert str(mod.__file__).endswith('/scripts/lib/hk_quote.py')\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"包内 HK loader 失败:\n{proc.stderr[-2000:]}"
    assert "OK" in proc.stdout


# ---- (e3) SKILL.md 强制工具（report_qc「机器层准出」）进包 ----
def _mandating_skills() -> list[str]:
    return sorted(p.parent.name for p in b.SKILLS_DIR.glob("*/SKILL.md")
                  if "skills/lib/report_qc.py" in p.read_text(encoding="utf-8"))


def test_mandated_tool_seeded_from_skill_md():
    """强制工具须由 SKILL.md 引用驱动入闭包（含动态加载目标 lint）。

    report_qc 经 importlib.import_module("_invest_lib.lint") 与 f-string 形式动态
    加载，ast 与正则均无法静态解析 → 闭包 BFS 抓不到 lint，须显式 seed。
    """
    mandating = _mandating_skills()
    assert mandating, "未发现任何强制 report_qc 的 SKILL.md"
    for skill in mandating:
        c = b._Closure(skill)
        c.add_mandated_tools(
            (b.SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
        assert "report_qc" in c.included, f"{skill}: report_qc 未入闭包"
        assert "lint" in c.included, f"{skill}: lint（动态加载目标）未入闭包"


def test_mandated_qc_tool_packaged_and_runnable(tmp_path):
    """「机器层准出（必跑）」命令须在包内真实可运行，而非指向不存在的仓库路径。

    回归：包内既无 report_qc 也无路径改写 → 分发用户执行 SKILL.md 强制的命令直接
    can't open file …/skills/lib/report_qc.py，闸门在大多数用户收到的形态下失效。
    """
    b.build_one("invest-a-event-calendar", b.project_version(), tmp_path, dry_run=False)
    dst = tmp_path / "invest-a-event-calendar"
    lib = dst / "scripts" / "lib"
    assert (lib / "report_qc.py").is_file(), "report_qc 未进包"
    assert (lib / "lint.py").is_file(), "lint（动态加载目标）未进包"
    assert (lib / "codes.py").is_file(), "codes（report_qc 依赖）未进包"
    assert (dst / "scripts/references/compliance_rules.yaml").is_file(), "规则数据未随包"

    md = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/lib/report_qc.py" not in md, "SKILL.md 路径未改写"
    assert "scripts/lib/report_qc.py" in md

    # 包内真实跑通动态加载链：report_qc → _load_lint_module → compliance_rules.yaml
    code = (
        f"import sys; sys.path.insert(0, r'{lib}')\n"
        "import report_qc\n"
        "rules = report_qc._load_lint_module().load_rules()\n"
        "print('OK', len(rules))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"包内动态加载失败:\n{proc.stderr[-2000:]}"
    assert "OK" in proc.stdout


# ---- (f) >200 守卫非零退出 + dry-run ----
def test_overflow_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "MAX_FILES", 3)
    rc = b.main(["--dry-run", "--out", str(tmp_path), "--skills", "invest-a-pulse"])
    assert rc != 0


def test_within_limit_exits_zero(tmp_path):
    rc = b.main(["--dry-run", "--out", str(tmp_path), "--skills", "invest-a-stock", "invest-a-etf"])
    assert rc == 0


def test_dry_run_all_packages_within_200(tmp_path):
    """六包全部 ≤200（闭包裁剪后各包文件数显著低于旧版全量复制）。"""
    for skill in b.PUBLISH_SKILLS:
        total = b.build_one(skill, b.project_version(), tmp_path, dry_run=True)
        assert 0 < total <= b.MAX_FILES, f"{skill}: {total} 超限"
    # etf 闭包裁剪: 远低于 v0.2.7 的 129。
    # 2026-09-03 实测 102（B3-R 后 render 共享面（html_charts/render_html）
    # 随闭包并入 etf 包，文件数较 v0.2.7 时代断言 <70 时上升）——上限放宽
    # 至 120 且仍显著低于旧版全量复制 129。
    etf_total = b.build_one("invest-a-etf", b.project_version(), tmp_path, dry_run=True)
    assert etf_total < 120


# ---- 主仓库源文件不得被回写 ----
def test_source_files_untouched():
    md = (b.SKILLS_DIR / "invest-a-stock" / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-stock/scripts/invest.py" in md
    assert "../../../skills/lib/references/" in md
    assert "Step 0" not in md
    etf_py = (b.SKILLS_DIR / "invest-a-etf" / "scripts" / "etf.py").read_text(encoding="utf-8")
    assert "from etf_data import" in etf_py                     # 源文件裸导入保留
    assert "_SCRIPT_PARENT" not in etf_py
