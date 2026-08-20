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
    scripts/              # 引擎（裁剪 tests/__pycache__；_invest_path.py shim parent 链减一；
                          #   etf 包额外合并 stock scripts/lib + etf.py 注入 _SCRIPT_PARENT）
    references/           # 参考文档
    lib/                  # 合并 skills/lib（消除跨包依赖）

设计背景: host-docs/v0.2.7/skillhub-publishing.md + skillhub-mirror-publish-design.md §4-§11
（200 文件/包限制 → 按 skill 独立发布；skillhub.cn frontmatter 用 slug/displayName/version/summary/license；
白名单不含 .toml → requirements.txt；shim 一行补丁 parent 链减一；etf 合并 stock lib + _SCRIPT_PARENT 注入；
SKILL.md 正文五类确定性替换；文件数 >200 时退出码非零）。
"""
from __future__ import annotations

import argparse
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


# ---- (a) shim 补丁：_invest_path.py parent 链按实际层数减一 ----
def _patch_shim_parent(text: str) -> str:
    """包内共享 lib 位置上移一层 → `_skills_lib` 行的 parent 链减一。

    主仓库 shim 位于 scripts/lib/ 下，4 层 parent 指向 skills/lib
    （pattern-scan 因历史形态为 5 层，且额外 import load_gap_scan_module）；
    包内共享 lib 落到 <pkg>/lib（3 层）→ 统一减一。只重写 _skills_lib 行的
    parent 链，其余 import/结构原样保留（pattern-scan 的 load_gap_scan_module
    等额外 import 不动）。
    """
    m = re.search(r"^_skills_lib\s*=.*$", text, re.M)
    if not m:
        return text
    line = m.group(0)
    n = line.count(".parent")
    if n < 2:
        return text
    return text[: m.start()] + line.replace(".parent" * n, ".parent" * (n - 1)) + text[m.end():]


def _is_shim_rel(rel: Path) -> bool:
    """需要 parent 链补丁的文件：scripts/lib/_invest_path.py。

    按文件名判定：自身复制场景 rel 相对 skill 根（scripts/lib/_invest_path.py），
    etf 合并场景 rel 相对 scripts/lib（裸文件名 _invest_path.py），两者统一。
    _patch_shim_parent 对无 parent 链的文件是无操作，故按文件名判定安全。
    """
    return rel.name == "_invest_path.py"


# ---- (b) etf 包专用：合并 stock 的 scripts/lib ----
def _etf_merged_lib_files() -> list[tuple[Path, Path]]:
    """etf 包合并 stock scripts/lib（按相对路径去重，etf 自身同名文件优先）。

    etf 引擎的 `from lib.nums/db_util/proxy/technical import ...` 在主仓库解析到
    invest-a-stock/scripts/lib（经 ensure_invest_a_scripts_on_path 兜底），包内无此
    兜底 → 必须并入。实测 stock scripts/lib 75 个白名单文件（含 collector/、
    industry/、render_markdown/ 子包与 assets/chart.umd.min.js），etf 自身 10 个；
    __init__.py 与 _invest_path.py 的冲突由去重规则天然处理（etf 版本优先）。
    返回 [(相对路径, 源文件), ...]。
    """
    stock_lib = SKILLS_DIR / "invest-a-stock" / "scripts" / "lib"
    etf_lib = SKILLS_DIR / "invest-a-etf" / "scripts" / "lib"
    merged: dict[Path, Path] = {}
    for rel in _collect_files(stock_lib):
        merged[rel] = stock_lib / rel
    for rel in _collect_files(etf_lib):  # etf 自身优先，覆盖 stock 同名项
        merged[rel] = etf_lib / rel
    return sorted(merged.items())


# ---- (c) etf.py 注入 _SCRIPT_PARENT ----
_SCRIPT_PARENT_BLOCK = (
    "# 包内补丁（skillhub 构建注入）：scripts/ 入 sys.path — `from lib import ...` 的 lib 包解析依赖它\n"
    "_SCRIPT_PARENT = Path(__file__).resolve().parent\n"
    "if str(_SCRIPT_PARENT) not in sys.path:\n"
    "    sys.path.insert(0, str(_SCRIPT_PARENT))\n"
)


def _inject_script_parent(text: str) -> str:
    """在 etf.py 的 _LIB_DIR 引导块后注入 _SCRIPT_PARENT（幂等）。

    主仓库 `from lib.nums import safe_float` 靠 ensure_invest_a_scripts_on_path
    把 invest-a-stock/scripts 放入 sys.path 才可解析；包内改为把 etf 自身
    scripts/ 放入 sys.path，`lib` 包（合并后的 scripts/lib）即可被解析。
    """
    if "_SCRIPT_PARENT" in text:
        return text
    m = re.search(
        r"^_LIB_DIR = Path\(__file__\)\.resolve\(\)\.parent / \"lib\"\n"
        r"if str\(_LIB_DIR\) not in sys\.path:\n"
        r"    sys\.path\.insert\(0, str\(_LIB_DIR\)\)\n",
        text,
        re.M,
    )
    if not m:
        return text
    return text[: m.end()] + "\n" + _SCRIPT_PARENT_BLOCK + text[m.end():]


# ---- (d) SKILL.md 正文适配（五类确定性替换，仅作用于包内副本）----
_STEP0_BLOCK = (
    "\n> **Step 0（首次使用）**：初始化虚拟环境并安装依赖（仅一次）：\n"
    ">\n"
    "> ```bash\n"
    "> uv venv && uv pip install -r requirements.txt\n"
    "> ```\n"
    ">\n"
    "> 之后引擎命令不变：`uv run python` 自动发现包根 `.venv`。\n"
)


def _rewrite_skill_md(text: str, skill_name: str) -> str:
    """SKILL.md 正文适配 — 设计文档 §5.2 五类确定性替换。

    1. skills/<name>/scripts/ → scripts/（CLI 命令行路径）
    2. ../../../skills/lib/references/ → lib/references/（含链接文本的
       skills/lib/references/ → lib/references/，如 stock L87、pattern-scan L15）
    3. 「见 CLAUDE.md「X」」 → 「见 scripts/<entry>.py --help」（无 CLI 的 skill 跳过）
    4. 「> 运行目录：code/ …」行 → 「运行目录：skill 包根 …」（含第 3 类合并改写）
    5. 「CLI 命令 / 运行」节开头插入 Step 0（uv venv && uv pip install -r requirements.txt；
       uv run 自动发现包根 .venv，引擎命令不变）
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
    return text


def _rewrite_skill_md_file(skill_md: Path, skill_name: str) -> None:
    """对包内 SKILL.md 副本执行正文适配（主仓库源文件不受影响）。"""
    text = skill_md.read_text(encoding="utf-8")
    new_text = _rewrite_skill_md(text, skill_name)
    if new_text != text:
        skill_md.write_text(new_text, encoding="utf-8")


def build_one(skill_name: str, version: str, out_dir: Path, dry_run: bool) -> int:
    src = SKILLS_DIR / skill_name
    if not (src / "SKILL.md").is_file():
        print(f"⚠️ 跳过 {skill_name}: 无 SKILL.md")
        return 0
    dst = out_dir / skill_name
    own_files = _collect_files(src)
    lib_files = _collect_files(LIB_DIR)
    if skill_name == "invest-a-etf":
        # etf 包专用：scripts/lib 换为 stock 合并版（etf 自身同名文件优先）；
        # 共享 skills/lib → <dst>/lib/ 的合并照常，故 lib 计数 = 共享 lib + 合并 lib
        merged_lib = _etf_merged_lib_files()
        own_files = [r for r in own_files if r.parts[:2] != ("scripts", "lib")]
        lib_count = len(lib_files) + len(merged_lib)
    else:
        merged_lib = []
        lib_count = len(lib_files)
    total = len(own_files) + lib_count + 1  # + requirements.txt

    ok = total <= MAX_FILES
    print(f"\n=== {skill_name} ===")
    print(f"  自身文件: {len(own_files)} + lib: {lib_count} + requirements.txt = {total} (≤{MAX_FILES}: {'✅' if ok else '❌ 超限'})")
    if dry_run:
        return total

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    # 1. 复制自身（_invest_path.py shim 打 parent 链补丁；etf 的 scripts/lib 由合并路径覆盖）
    for rel in own_files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if _is_shim_rel(rel):
            target.write_text(_patch_shim_parent((src / rel).read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(src / rel, target)

    # 1b. etf 专用：stock scripts/lib 合并进 scripts/lib（去重，etf 优先）
    for rel, s in merged_lib:
        target = dst / "scripts" / "lib" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if _is_shim_rel(rel):
            target.write_text(_patch_shim_parent(s.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(s, target)

    # 2. 合并 skills/lib → <dst>/lib/
    for rel in lib_files:
        target = dst / "lib" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LIB_DIR / rel, target)

    # 3. requirements.txt
    (dst / "requirements.txt").write_text(
        "# 运行时依赖（由主仓库 pyproject.toml [project].dependencies 生成）\n"
        + "\n".join(runtime_dependencies()) + "\n",
        encoding="utf-8",
    )

    # 4. 注入 skillhub frontmatter
    _inject_skillhub_frontmatter(dst / "SKILL.md", skill_name, version)

    # 5. SKILL.md 正文适配（路径改写 / Step 0 / CLAUDE.md 引用替换）
    _rewrite_skill_md_file(dst / "SKILL.md", skill_name)

    # 6. etf 专用：etf.py 注入 _SCRIPT_PARENT（`from lib import ...` 的 lib 包解析依赖）
    if skill_name == "invest-a-etf":
        etf_py = dst / "scripts" / "etf.py"
        etf_py.write_text(_inject_script_parent(etf_py.read_text(encoding="utf-8")), encoding="utf-8")

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
