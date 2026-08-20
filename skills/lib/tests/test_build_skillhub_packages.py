"""build_skillhub_packages.py 单测 — v0.2.7 A5.1 五个缺口（skillhub 镜像构建）。

覆盖（每缺口 ≥1 条）:
  (a) shim 补丁 parent 链按实际层数减一（4→3 / 5→4），其余 import 保留
  (b) etf 包合并 stock scripts/lib（按相对路径去重，etf 自身优先）
  (c) etf.py 注入 _SCRIPT_PARENT
  (d) SKILL.md 正文五类替换（路径 / references / CLAUDE.md 引用 / 运行目录 / Step 0）
  (e) 文件数 >MAX_FILES 时非零退出

另含: 真实构建产物断言 + dry-run 两包 ≤200（显式 --out 到 tmp_path，不污染
../invest-skills-skillhub）；主仓库源文件不被回写。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import build_skillhub_packages as b  # noqa: E402

STOCK_SHIM = '''"""Shim — re-exports shared skills/lib/invest_path (Batch D / X-02)."""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from invest_path import ensure_invest_a_scripts_on_path, ensure_shared_lib_on_path

ensure_skills_lib_on_path = ensure_shared_lib_on_path

__all__ = ["ensure_invest_a_scripts_on_path", "ensure_skills_lib_on_path"]
'''

PATTERN_SHIM = '''"""Shim — re-exports shared skills/lib/invest_path（仿 invest-a-stock shim）。

canonical 模块（gap-scan universe/kline_source）依赖 `ensure_invest_a_scripts_on_path`
与 `ensure_shared_lib_on_path`，两者一并 re-export 防止同名遮蔽。
"""
from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parent.parent.parent.parent.parent / "lib"
_s = str(_skills_lib)
if _s not in sys.path:
    sys.path.insert(0, _s)

from invest_path import (  # noqa: E402
    ensure_invest_a_scripts_on_path,
    ensure_shared_lib_on_path,
    load_gap_scan_module,
)

__all__ = [
    "ensure_invest_a_scripts_on_path",
    "ensure_shared_lib_on_path",
    "load_gap_scan_module",
]
'''


# ---- (a) shim 补丁 ----
def test_shim_patch_4_parents_reduced_to_3():
    out = b._patch_shim_parent(STOCK_SHIM)
    assert '.parent.parent.parent / "lib"' in out
    assert '.parent.parent.parent.parent / "lib"' not in out
    # 其余 import/结构原样保留
    assert "from invest_path import ensure_invest_a_scripts_on_path, ensure_shared_lib_on_path" in out
    assert "__all__ = [\"ensure_invest_a_scripts_on_path\", \"ensure_skills_lib_on_path\"]" in out


def test_shim_patch_5_parents_reduced_to_4_keeps_extra_import():
    out = b._patch_shim_parent(PATTERN_SHIM)
    assert '.parent.parent.parent.parent / "lib"' in out
    assert '.parent.parent.parent.parent.parent / "lib"' not in out
    # pattern-scan 额外 import 的 load_gap_scan_module 保留
    assert "load_gap_scan_module" in out
    assert "from invest_path import (  # noqa: E402" in out


def test_shim_patch_noop_without_chain():
    assert b._patch_shim_parent("_skills_lib = Path(__file__).parent / 'lib'\n") == \
        "_skills_lib = Path(__file__).parent / 'lib'\n"
    assert b._patch_shim_parent("# no _skills_lib here\n") == "# no _skills_lib here\n"


# ---- (b) etf 合并 stock scripts/lib ----
def test_etf_merged_lib_dedup_etf_wins():
    merged = b._etf_merged_lib_files()
    assert merged, "合并结果不应为空"
    rels = [rel for rel, _ in merged]
    assert len(rels) == len(set(rels)), "按相对路径去重后不应有重复"
    assert Path("etf_data.py") in rels          # etf 自身模块保留
    assert Path("nums.py") in rels              # stock 提供（lib.nums 依赖）
    assert Path("proxy.py") in rels             # stock 提供（lib.proxy 依赖）
    assert any(r.parts[:1] == ("collector",) for r in rels)   # stock 子包并入
    by_rel = dict(merged)
    # 冲突文件 etf 自身优先（__init__.py 与 _invest_path.py）
    assert by_rel[Path("_invest_path.py")] == \
        b.SKILLS_DIR / "invest-a-etf" / "scripts" / "lib" / "_invest_path.py"
    assert by_rel[Path("__init__.py")] == \
        b.SKILLS_DIR / "invest-a-etf" / "scripts" / "lib" / "__init__.py"


# ---- (b)+(c) etf 包真实构建 ----
def test_etf_package_build_merges_stock_lib_and_patches(tmp_path):
    total = b.build_one("invest-a-etf", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-etf"
    assert (dst / "scripts/lib/nums.py").is_file()          # stock lib 并入
    assert (dst / "scripts/lib/etf_data.py").is_file()      # etf 自身保留
    assert (dst / "scripts/lib/proxy.py").is_file()         # stock 提供
    assert (dst / "scripts/lib/collector").is_dir()         # stock 子包
    # shim 补丁生效（4→3；注意 3-parent 链是 4-parent 链的子串，必须同时断言
    # 「4 连 parent 不存在」才能区分补丁是否生效）
    shim = (dst / "scripts/lib/_invest_path.py").read_text(encoding="utf-8")
    assert '.parent.parent.parent / "lib"' in shim
    assert '.parent.parent.parent.parent / "lib"' not in shim
    # (c) etf.py 注入 _SCRIPT_PARENT
    etf_py = (dst / "scripts/etf.py").read_text(encoding="utf-8")
    assert "_SCRIPT_PARENT" in etf_py
    assert "_LIB_DIR = Path(__file__).resolve().parent / \"lib\"" in etf_py
    # 共享 lib 落地（etf 运行时依赖 codes/dates 在共享 lib）
    assert (dst / "lib/invest_path.py").is_file()
    assert (dst / "lib/codes.py").is_file()


# ---- (a)+(d) stock 包真实构建 ----
def test_stock_package_build_patches_shim_and_rewrites_skillmd(tmp_path):
    total = b.build_one("invest-a-stock", b.project_version(), tmp_path, dry_run=False)
    assert 0 < total <= b.MAX_FILES
    dst = tmp_path / "invest-a-stock"
    shim = (dst / "scripts/lib/_invest_path.py").read_text(encoding="utf-8")
    assert '.parent.parent.parent / "lib"' in shim
    assert '.parent.parent.parent.parent / "lib"' not in shim
    md = (dst / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-stock/scripts/" not in md           # (d)1 路径改写
    assert "../../../skills/lib/references/" not in md          # (d)2 references
    assert "lib/references/report-conventions.md" in md
    assert "Step 0" in md and "uv venv && uv pip install -r requirements.txt" in md  # (d)5
    assert "见 scripts/invest.py --help" in md                  # (d)3 CLAUDE.md 引用
    assert "见 CLAUDE.md" not in md
    assert "运行目录：skill 包根" in md                          # (d)4 运行目录


# ---- (d) 纯函数级断言 ----
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


# ---- (e) >200 守卫非零退出 ----
def test_overflow_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "MAX_FILES", 3)
    rc = b.main(["--dry-run", "--out", str(tmp_path), "--skills", "invest-a-pulse"])
    assert rc != 0


def test_within_limit_exits_zero(tmp_path):
    rc = b.main(["--dry-run", "--out", str(tmp_path), "--skills", "invest-a-stock", "invest-a-etf"])
    assert rc == 0


# ---- 验收标准：dry-run 两包 ≤200（文件数断言，显式 --out 避免污染兄弟仓库） ----
def test_dry_run_stock_etf_counts_within_200(tmp_path):
    stock_total = b.build_one("invest-a-stock", b.project_version(), tmp_path, dry_run=True)
    etf_total = b.build_one("invest-a-etf", b.project_version(), tmp_path, dry_run=True)
    assert 0 < stock_total <= b.MAX_FILES
    assert 0 < etf_total <= b.MAX_FILES
    # etf 合并 stock scripts/lib 后应显著大于「仅自身 + 共享 lib」的旧口径（54）
    assert etf_total > 100


# ---- 主仓库源文件不得被回写 ----
def test_source_files_untouched():
    md = (b.SKILLS_DIR / "invest-a-stock" / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/invest-a-stock/scripts/invest.py" in md
    assert "../../../skills/lib/references/" in md
    assert "Step 0" not in md
    shim = (b.SKILLS_DIR / "invest-a-stock" / "scripts" / "lib" / "_invest_path.py").read_text(encoding="utf-8")
    assert '.parent.parent.parent.parent / "lib"' in shim
    etf_py = (b.SKILLS_DIR / "invest-a-etf" / "scripts" / "etf.py").read_text(encoding="utf-8")
    assert "_SCRIPT_PARENT" not in etf_py
