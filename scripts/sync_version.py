#!/usr/bin/env python3
"""Single version sync tool for invest skills.

Canonical source: pyproject.toml [project].version

Commands:
  bump 0.3.0   Write pyproject.toml → sync SKILL.md × 2 → generate 3 JSON from templates
  sync          Read pyproject.toml → sync SKILL.md × 2 → generate 3 JSON
  check         Verify pyproject.toml / SKILL.md / JSON are all consistent (exit 1 if drift)

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

JSON_TEMPLATES: tuple[tuple[str, str], ...] = (
    (".claude-plugin/plugin.json.in", ".claude-plugin/plugin.json"),
    (".claude-plugin/marketplace.json.in", ".claude-plugin/marketplace.json"),
    ("gemini-extension.json.in", "gemini-extension.json"),
)


@dataclass(frozen=True)
class SkillTarget:
    rel_path: str
    label: str


SKILL_TARGETS: tuple[SkillTarget, ...] = (
    SkillTarget("skills/invest-a-stock/SKILL.md", "invest:a-stock"),
    SkillTarget("skills/invest-a-limit-up/SKILL.md", "invest:a-limit-up"),
    SkillTarget("skills/invest-a-gap-scan/SKILL.md", "invest:a-gap-scan"),
    SkillTarget("skills/invest-a-journal/SKILL.md", "invest:a-journal"),
    SkillTarget("skills/invest-a-etf/SKILL.md", "invest:a-etf"),
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
    """Return True if file was modified."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"no YAML frontmatter in {path}")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"unclosed YAML frontmatter in {path}")
    frontmatter = text[3:end]
    rest = text[end:]
    lines = frontmatter.splitlines()
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("version:"):
            indent = line[: len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}version: "{version}"')
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        raise ValueError(f"no version: in frontmatter ({path})")
    new_text = "---\n" + "\n".join(new_lines) + rest
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


# ── JSON manifests ──────────────────────────────────────────


def generate_json_manifests(root: Path, version: str) -> list[str]:
    """Return list of changed file labels."""
    written: list[str] = []
    for tmpl_rel, out_rel in JSON_TEMPLATES:
        tmpl_path = root / tmpl_rel
        out_path = root / out_rel
        if not tmpl_path.is_file():
            raise FileNotFoundError(f"template not found: {tmpl_path}")
        content = tmpl_path.read_text(encoding="utf-8")
        if VERSION_PLACEHOLDER not in content:
            raise ValueError(f"template {tmpl_rel} missing {VERSION_PLACEHOLDER!r}")
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
        if not out_path.is_file():
            drifts.append(f"  {out_rel}: missing (run sync_version.py sync)")
            continue
        expected = tmpl_path.read_text(encoding="utf-8").replace(
            VERSION_PLACEHOLDER, version
        )
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
    return changed


def _derived_paths(root: Path) -> list[Path]:
    """All paths that bump/sync may rewrite (for atomic rollback)."""
    paths = [root / "pyproject.toml"]
    paths.extend(root / t.rel_path for t in SKILL_TARGETS)
    paths.extend(root / out_rel for _, out_rel in JSON_TEMPLATES)
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

    changed = _do_sync(root, version)
    if changed:
        print(f"\n✅ {changed} file(s) synced from pyproject.toml ({version})")
    else:
        print(f"\n✅ all files up-to-date with pyproject.toml ({version})")
    return 0


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
