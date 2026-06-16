#!/usr/bin/env python3
"""从 CHANGELOG.md 提取指定版本的 Release Notes（供 GitHub Actions 使用）。"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


def extract_changelog_section(changelog: str, version: str) -> str | None:
    """提取 ## v{version} ... 至下一 ## v 之间的内容（不含标题行）。"""
    ver = version.removeprefix("v").strip()
    if not ver:
        return None

    lines = changelog.splitlines()
    header = re.compile(rf"^##\s+v{re.escape(ver)}(\s|\(|$)")
    next_header = re.compile(r"^##\s+v\d")

    start: int | None = None
    for i, line in enumerate(lines):
        if header.match(line):
            start = i + 1
            break
    if start is None:
        return None

    body: list[str] = []
    for line in lines[start:]:
        if next_header.match(line):
            break
        body.append(line)

    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()

    if not body:
        return None
    return "\n".join(body)


def read_project_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version", "")
    if not version:
        raise ValueError(f"no project.version in {pyproject_path}")
    return str(version).strip()


def version_to_tag(version: str) -> str:
    v = version.strip()
    return v if v.startswith("v") else f"v{v}"


def changelog_has_section(version: str, changelog_path: Path) -> bool:
    text = changelog_path.read_text(encoding="utf-8")
    return extract_changelog_section(text, version_to_tag(version)) is not None


def build_release_notes(version: str, changelog_path: Path) -> str | None:
    tag = version_to_tag(version)
    text = changelog_path.read_text(encoding="utf-8")
    section = extract_changelog_section(text, tag)
    if not section:
        return None

    prev_tag = _previous_tag(tag)
    compare = f"**Full Changelog**: {prev_tag}...{tag}" if prev_tag else ""

    parts = [f"## {tag}", "", section.strip()]
    if compare:
        parts.extend(["", "---", "", compare])
    return "\n".join(parts) + "\n"


def _previous_tag(tag: str) -> str | None:
    """尽力从同目录 git 标签推断上一版本（本地/CI 有 git 时可用）。"""
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    tags = [t.strip() for t in out.splitlines() if t.strip()]
    if tag not in tags:
        tags = sorted({tag, *tags}, key=_version_key, reverse=True)
    try:
        idx = tags.index(tag)
    except ValueError:
        return None
    if idx + 1 >= len(tags):
        return None
    return tags[idx + 1]


def _version_key(tag: str) -> tuple:
    parts = tag.lstrip("v").split("-")[0].split(".")
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="从 CHANGELOG.md 提取 GitHub Release 正文")
    parser.add_argument(
        "version",
        nargs="?",
        help="版本号，如 v0.1.4 或 0.1.4（与 --from-pyproject 二选一）",
    )
    parser.add_argument(
        "--from-pyproject",
        action="store_true",
        help="从 pyproject.toml 读取 project.version",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="CHANGELOG 无对应章节时退出码 1（CI 用于跳过 Draft Release）",
    )
    parser.add_argument(
        "--print-tag",
        action="store_true",
        help="仅输出 tag 名（如 v0.1.4），供 workflow 使用",
    )
    args = parser.parse_args()

    root = _repo_root()
    changelog = root / "CHANGELOG.md"
    pyproject = root / "pyproject.toml"
    if not changelog.exists():
        print(f"error: {changelog} not found", file=sys.stderr)
        return 1

    if args.from_pyproject:
        if not pyproject.exists():
            print(f"error: {pyproject} not found", file=sys.stderr)
            return 1
        try:
            version = read_project_version(pyproject)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif args.version:
        version = args.version
    else:
        parser.print_help()
        return 2

    tag = version_to_tag(version)
    if args.print_tag:
        print(tag)
        return 0

    notes = build_release_notes(version, changelog)
    if notes is None:
        if args.strict:
            print(
                f"error: no CHANGELOG section for {tag}",
                file=sys.stderr,
            )
            return 1
        notes = (
            f"## {tag}\n\n"
            f"_未在 {changelog.name} 中找到 `## {tag}` 章节。"
            "请补充 CHANGELOG 后重新发布。_\n"
        )

    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
