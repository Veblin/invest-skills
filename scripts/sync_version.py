#!/usr/bin/env python3
"""Single version sync tool for invest skills.

Canonical source: pyproject.toml [project].version

Commands:
  bump 0.3.0   Write pyproject.toml → sync all SKILL.md targets → generate JSON manifests
               from templates → rewrite README release badge
  sync          Read pyproject.toml → sync all SKILL.md targets → generate JSON manifests
               → rewrite README release badge
  check         Verify pyproject.toml / SKILL.md / JSON / README badge are all
               consistent (exit 1 if drift)

JSON manifest outputs (.claude-plugin/plugin.json, .claude-plugin/marketplace.json,
.agents/plugins/marketplace.json, gemini-extension.json) are all generated from
their *.json.in templates — the .agents copy shares the .claude-plugin marketplace
template so both marketplace listings stay byte-identical.

README.md release badge (label=vX.Y.Z) is rewritten in place by regex — the only
arbitrary-string version surface not covered by frontmatter/placeholder templates.

Usage:
  uv run python scripts/sync_version.py bump 0.3.0
  uv run python scripts/sync_version.py sync
  uv run python scripts/sync_version.py check
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_PLACEHOLDER = "{{ VERSION }}"
VERSION_PLACEHOLDER_TYPO = "{{VERSION}}"  # no spaces — reject so typo cannot silently pass
# README 徽章 label（唯一版本面：label=v0.2.4 式 URL 参数，sync_version 原本不覆盖）
README_BADGE_RE = re.compile(r"label=v[0-9]+\.[0-9]+\.[0-9]+")

JSON_TEMPLATES: tuple[tuple[str, str], ...] = (
    (".claude-plugin/plugin.json.in", ".claude-plugin/plugin.json"),
    (".claude-plugin/marketplace.json.in", ".claude-plugin/marketplace.json"),
    (".claude-plugin/marketplace.json.in", ".agents/plugins/marketplace.json"),
    ("gemini-extension.json.in", "gemini-extension.json"),
)


@dataclass(frozen=True)
class SkillTarget:
    rel_path: str
    label: str


SKILL_TARGETS: tuple[SkillTarget, ...] = (
    SkillTarget("skills/invest-a-stock/SKILL.md", "invest:a-stock"),
    SkillTarget("skills/invest-a-gap-scan/SKILL.md", "invest:a-gap-scan"),
    SkillTarget("skills/invest-a-journal/SKILL.md", "invest:a-journal"),
    SkillTarget("skills/invest-a-etf/SKILL.md", "invest:a-etf"),
    SkillTarget("skills/invest-a-pulse/SKILL.md", "invest:a-pulse"),
)


# ── pyproject.toml ──────────────────────────────────────────


_PYPROJECT_VERSION_RE = re.compile(r"^version\s*=\s*[\"']([^\"']+)[\"']")

def read_pyproject_version(path: Path) -> str:
    in_project = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[project]":
            in_project = True
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project:
            m = _PYPROJECT_VERSION_RE.match(line)
            if m:
                return m.group(1)
    raise ValueError(f"no [project].version in {path}")


def write_pyproject_version(path: Path, version: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_project = False
    updated = False
    out: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            out.append(raw)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            out.append(raw)
            continue
        if in_project and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key == "version":
                prefix = raw.split("=", 1)[0]
                out.append(f'{prefix}= "{version}"\n')
                updated = True
                continue
        out.append(raw)
    if not updated:
        raise ValueError(f"could not update version in {path}")
    path.write_text("".join(out), encoding="utf-8")


# ── SKILL.md ────────────────────────────────────────────────


def read_skill_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"no YAML frontmatter in {path}")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"unclosed YAML frontmatter in {path}")
    for raw in text[3:end].splitlines():
        if raw.strip().startswith("version:"):
            value = raw.split(":", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise ValueError(f"no version: in frontmatter ({path})")


def write_skill_version(path: Path, version: str) -> bool:
    """Return True if file was modified.

    只重写 version: 行，其余字节（含空行）原样保留——保证幂等：
    重复 sync/bump 不产生任何 diff（旧实现重建整个 frontmatter，
    每次运行新增一个空行，SKILL.md 头部已积累 9 个空行）。
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"no YAML frontmatter in {path}")
    if text.find("\n---", 3) == -1:
        raise ValueError(f"unclosed YAML frontmatter in {path}")
    lines = text.splitlines(keepends=True)
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("version:"):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f'{indent}version: "{version}"\n'
            updated = True
    if not updated:
        raise ValueError(f"no version: in frontmatter ({path})")
    new_text = "".join(lines)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ── README badge ────────────────────────────────────────────


def read_readme_badge(path: Path) -> str:
    """Extract version from README release badge URL (label=vX.Y.Z)."""
    text = path.read_text(encoding="utf-8")
    m = README_BADGE_RE.search(text)
    if not m:
        raise ValueError(f"no release badge (label=vX.Y.Z) in {path}")
    return m.group(0)[len("label=v"):]  # 徽章形如 label=v0.2.5 → 返回 0.2.5


def write_readme_badge(path: Path, version: str) -> bool:
    """Rewrite README release badge label (label=vX.Y.Z). Return True if modified."""
    text = path.read_text(encoding="utf-8")
    new_text = README_BADGE_RE.sub(f"label=v{version}", text, count=1)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ── JSON manifests ──────────────────────────────────────────


def _validate_template_placeholder(content: str, tmpl_rel: str) -> None:
    """Require ``{{ VERSION }}``; reject typo ``{{VERSION}}`` (no spaces)."""
    if VERSION_PLACEHOLDER_TYPO in content:
        raise ValueError(
            f"template {tmpl_rel} has typo {VERSION_PLACEHOLDER_TYPO!r}; "
            f"use {VERSION_PLACEHOLDER!r}"
        )
    if VERSION_PLACEHOLDER not in content:
        raise ValueError(f"template {tmpl_rel} missing {VERSION_PLACEHOLDER!r}")


def generate_json_manifests(root: Path, version: str) -> list[str]:
    """Return list of changed file labels."""
    written: list[str] = []
    for tmpl_rel, out_rel in JSON_TEMPLATES:
        tmpl_path = root / tmpl_rel
        out_path = root / out_rel
        if not tmpl_path.is_file():
            raise FileNotFoundError(f"template not found: {tmpl_path}")
        content = tmpl_path.read_text(encoding="utf-8")
        _validate_template_placeholder(content, tmpl_rel)
        generated = content.replace(VERSION_PLACEHOLDER, version)
        if out_path.is_file() and out_path.read_text(encoding="utf-8") == generated:
            continue
        out_path.write_text(generated, encoding="utf-8")
        written.append(out_rel)
    return written


def check_json_manifests(root: Path, version: str) -> list[str]:
    """Return list of drift descriptions (empty = clean)."""
    drifts: list[str] = []
    for tmpl_rel, out_rel in JSON_TEMPLATES:
        tmpl_path = root / tmpl_rel
        out_path = root / out_rel
        if not tmpl_path.is_file():
            drifts.append(f"  {tmpl_rel}: template missing")
            continue
        content = tmpl_path.read_text(encoding="utf-8")
        try:
            _validate_template_placeholder(content, tmpl_rel)
        except ValueError as exc:
            drifts.append(f"  {exc}")
            continue
        if not out_path.is_file():
            drifts.append(f"  {out_rel}: missing (run sync_version.py sync)")
            continue
        expected = content.replace(VERSION_PLACEHOLDER, version)
        if out_path.read_text(encoding="utf-8") != expected:
            drifts.append(f"  {out_rel}: drift detected")
    return drifts


# ── Commands ────────────────────────────────────────────────


def _do_sync(root: Path, version: str) -> int:
    """Core sync: write SKILL.md + generate JSONs. Returns change count."""
    changed = 0
    for t in SKILL_TARGETS:
        if write_skill_version(root / t.rel_path, version):
            print(f"  ✅ {t.label} SKILL.md → {version}")
            changed += 1
        else:
            print(f"  ⚪ {t.label} SKILL.md (unchanged)")
    written = generate_json_manifests(root, version)
    for p in written:
        print(f"  ✅ {p}")
        changed += 1
    if not written:
        print("  ⚪ JSON manifests (unchanged)")
    if write_readme_badge(root / "README.md", version):
        print(f"  ✅ README.md badge → v{version}")
        changed += 1
    else:
        print("  ⚪ README.md badge (unchanged)")
    return changed


def _derived_paths(root: Path) -> list[Path]:
    """All paths that bump/sync may rewrite (for atomic rollback)."""
    paths = [root / "pyproject.toml"]
    paths.extend(root / t.rel_path for t in SKILL_TARGETS)
    paths.extend(root / out_rel for _, out_rel in JSON_TEMPLATES)
    paths.append(root / "README.md")
    return paths


def _preflight_derived(root: Path) -> list[str]:
    """Return list of missing required paths (empty = ok)."""
    missing: list[str] = []
    for t in SKILL_TARGETS:
        if not (root / t.rel_path).is_file():
            missing.append(t.rel_path)
    for tmpl_rel, _out_rel in JSON_TEMPLATES:
        if not (root / tmpl_rel).is_file():
            missing.append(tmpl_rel)
    if not (root / "README.md").is_file():
        missing.append("README.md")
    return missing


def cmd_bump(root: Path, version: str) -> int:
    if not VERSION_RE.match(version):
        print(f"❌ invalid version: {version!r} (expected X.Y.Z)", file=sys.stderr)
        return 1

    missing = _preflight_derived(root)
    if missing:
        print("❌ bump preflight failed — missing required files:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1

    if not (root / "pyproject.toml").is_file():
        print("❌ bump preflight failed — pyproject.toml missing", file=sys.stderr)
        return 1

    # Backup every file bump may touch so a mid-sync failure restores the tree
    backups: dict[Path, str | None] = {}
    for path in _derived_paths(root):
        backups[path] = path.read_text(encoding="utf-8") if path.is_file() else None

    try:
        write_pyproject_version(root / "pyproject.toml", version)
        print(f"  ✅ pyproject.toml → {version}")
        _do_sync(root, version)
        print(f"\n✅ version {version} synced across all files")
        return 0
    except Exception as exc:
        for path, content in backups.items():
            if content is None:
                if path.is_file():
                    path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
        print(f"❌ bump failed: {exc}", file=sys.stderr)
        print("  ↻ all derived files restored", file=sys.stderr)
        return 1


def cmd_sync(root: Path) -> int:
    missing = _preflight_derived(root)
    if missing:
        print("❌ sync preflight failed — missing required files:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1

    try:
        version = read_pyproject_version(root / "pyproject.toml")
    except (OSError, ValueError) as exc:
        print(f"❌ cannot read canonical version from pyproject.toml: {exc}",
              file=sys.stderr)
        return 1

    # Backup derived files sync may rewrite (not pyproject.toml) for rollback
    backups: dict[Path, str | None] = {}
    for path in _derived_paths(root):
        if path == root / "pyproject.toml":
            continue
        backups[path] = path.read_text(encoding="utf-8") if path.is_file() else None

    try:
        changed = _do_sync(root, version)
        if changed:
            print(f"\n✅ {changed} file(s) synced from pyproject.toml ({version})")
        else:
            print(f"\n✅ all files up-to-date with pyproject.toml ({version})")
        return 0
    except Exception as exc:
        for path, content in backups.items():
            if content is None:
                if path.is_file():
                    path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
        print(f"❌ sync failed: {exc}", file=sys.stderr)
        print("  ↻ all derived files restored", file=sys.stderr)
        return 1


def cmd_check(root: Path) -> int:
    errors = 0

    try:
        canonical = read_pyproject_version(root / "pyproject.toml")
    except (OSError, ValueError) as exc:
        print(f"❌ cannot read canonical version from pyproject.toml: {exc}",
              file=sys.stderr)
        return 1

    # 1. Check SKILL.md
    for t in SKILL_TARGETS:
        path = root / t.rel_path
        # Detect cached copies (installed via /plugin marketplace)
        resolved = str(path.resolve())
        if "/.claude/plugins/cache/" in resolved:
            print(f"⛔ {t.label} loaded from cache: {resolved}", file=sys.stderr)
            print("   Re-install via /plugin marketplace.", file=sys.stderr)
            errors += 1
            continue
        try:
            found = read_skill_version(path)
        except (OSError, ValueError) as exc:
            print(f"❌ {t.label}: {exc}", file=sys.stderr)
            errors += 1
            continue
        if found != canonical:
            print(f"❌ {t.label}: {found} ≠ pyproject.toml ({canonical})", file=sys.stderr)
            errors += 1

    # 2. Check JSON manifests
    drifts = check_json_manifests(root, canonical)
    if drifts:
        print("❌ JSON manifest drift:", file=sys.stderr)
        for d in drifts:
            print(d, file=sys.stderr)
        errors += len(drifts)

    # 3. Check README release badge
    try:
        badge_ver = read_readme_badge(root / "README.md")
    except (OSError, ValueError) as exc:
        print(f"❌ README badge: {exc}", file=sys.stderr)
        errors += 1
    else:
        if badge_ver != canonical:
            print(f"❌ README badge: v{badge_ver} ≠ pyproject.toml ({canonical})",
                  file=sys.stderr)
            errors += 1

    if errors:
        print(f"\n❌ {errors} drift(s) found. Fix: uv run python scripts/sync_version.py sync",
              file=sys.stderr)
        return 1

    print(f"✅ all files consistent with pyproject.toml (version {canonical})")
    return 0


# ── Main ────────────────────────────────────────────────────


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="invest skills version sync")
    parser.add_argument("--root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    bump_p = sub.add_parser("bump", help="write pyproject.toml + sync all derived files")
    bump_p.add_argument("version", help="new version X.Y.Z")

    sub.add_parser("sync", help="sync derived files from pyproject.toml")
    sub.add_parser("check", help="verify consistency (exit 1 if drift)")

    args = parser.parse_args(argv)
    root = (args.root or repo_root()).resolve()

    if args.command == "bump":
        return cmd_bump(root, args.version)
    if args.command == "sync":
        return cmd_sync(root)
    if args.command == "check":
        return cmd_check(root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
