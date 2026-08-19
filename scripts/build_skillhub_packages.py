#!/usr/bin/env python3
"""构建 SkillHub 分发包 — 从主仓库生成 skillhub.cn 兼容的精简 skill 包。

用法:
  uv run python scripts/build_skillhub_packages.py                # 输出到 ../invest-skills-skillhub/skills
  uv run python scripts/build_skillhub_packages.py --out /tmp/skillhub-skill
  uv run python scripts/build_skillhub_packages.py --dry-run      # 只打印将生成的包与文件数

输出（每个 skill 一个目录，自包含）:
  <out>/<skill-name>/
    SKILL.md              # 双格式 frontmatter（标准 name/description + skillhub slug/displayName/version/summary/license）
    requirements.txt      # 运行时依赖（从 pyproject.toml [project].dependencies 生成）
    scripts/              # 引擎（裁剪 tests/__pycache__）
    references/           # 参考文档
    lib/                  # 合并 skills/lib（消除跨包依赖）

设计背景: host-docs/v0.2.7/skillhub-publishing.md（200 文件/包限制 → 按 skill 独立发布；
skillhub.cn frontmatter 用 slug/displayName/version/summary/license；白名单不含 .toml → requirements.txt）。
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
    new_text = f"{m.group(1)}{chr(10).join(lines)}{m.group(3)}"
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
    total = len(own_files) + len(lib_files) + 1  # + requirements.txt

    print(f"\n=== {skill_name} ===")
    print(f"  自身文件: {len(own_files)} + lib: {len(lib_files)} + requirements.txt = {total} (≤200: {'✅' if total <= 200 else '❌ 超限'})")
    if dry_run:
        return total

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    # 1. 复制自身（裁剪 tests/__pycache__/非白名单）
    for rel in own_files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)

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
    print(f"  ✅ 已生成 {dst.relative_to(out_dir.parent if out_dir.parent != ROOT else ROOT)}")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 SkillHub 分发包")
    ap.add_argument("--out", type=Path, default=ROOT.parent / "invest-skills-skillhub" / "skills",
                    help="输出目录（默认 ../invest-skills-skillhub/skills）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    ap.add_argument("--skills", nargs="*", default=None, help="只构建指定 skill（默认全部）")
    args = ap.parse_args()

    version = project_version()
    print(f"主仓库版本: {version}")
    print(f"输出目录: {args.out.resolve()}")

    skills = args.skills or PUBLISH_SKILLS
    grand_total = 0
    for name in skills:
        if name not in PUBLISH_SKILLS:
            print(f"⚠️ 跳过未知 skill: {name}")
            continue
        grand_total += build_one(name, version, args.out, args.dry_run)

    print(f"\n共 {len(skills)} 个包，文件总数 {grand_total}（按包独立计算，均 ≤200 即可发布）")
    if not args.dry_run:
        print("下一步: 进入分发仓库 → 逐个包 `skillhub publish <dir> --dry-run` 预检 → 提交 → tag → CI 发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
