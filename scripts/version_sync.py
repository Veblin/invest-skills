#!/usr/bin/env python3
"""Centralized invest-A product version sync (canonical: pyproject.toml)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_JSON_VERSION_LINE_RE = re.compile(
    r'^(\s*"version"\s*:\s*")[0-9]+\.[0-9]+\.[0-9]+(")'
)
_JSON_VERSION_VALUE_RE = re.compile(
    r'("version"\s*:\s*")[0-9]+\.[0-9]+\.[0-9]+(")'
)
_MARKETPLACE_PLUGIN_NAME = "invest-A"


@dataclass(frozen=True)
class VersionTarget:
    rel_path: str
    label: str
    canonical: bool = False


TARGETS: tuple[VersionTarget, ...] = (
    VersionTarget("pyproject.toml", "pyproject.toml", canonical=True),
    VersionTarget("skills/invest-A/SKILL.md", "SKILL.md"),
    VersionTarget(".claude-plugin/plugin.json", "plugin.json"),
    VersionTarget(".claude-plugin/marketplace.json", "marketplace.json"),
    VersionTarget("gemini-extension.json", "gemini-extension.json"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def validate_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ValueError(f"invalid version format: {version!r} (expected X.Y.Z)")


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
        if in_project and line.startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
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
        if in_project and stripped.startswith("version") and "=" in stripped:
            prefix = raw.split("=", 1)[0]
            out.append(f'{prefix}= "{version}"\n')
            updated = True
            continue
        out.append(raw)
    if not updated:
        raise ValueError(f"could not update version in {path}")
    path.write_text("".join(out), encoding="utf-8")


def read_skill_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"no YAML frontmatter in {path}")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"unclosed YAML frontmatter in {path}")
    frontmatter = text[3:end]
    for raw in frontmatter.splitlines():
        if raw.strip().startswith("version:"):
            value = raw.split(":", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise ValueError(f"no version: in SKILL frontmatter ({path})")


def write_skill_version(path: Path, version: str) -> None:
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
        raise ValueError(f"no version: in SKILL frontmatter ({path})")
    path.write_text("---\n" + "\n".join(new_lines) + rest, encoding="utf-8")


def _marketplace_plugin_version(data: dict) -> str:
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("marketplace.json missing plugins array")
    for plugin in plugins:
        if isinstance(plugin, dict) and plugin.get("name") == _MARKETPLACE_PLUGIN_NAME:
            version = plugin.get("version")
            if isinstance(version, str) and version:
                return version
    raise ValueError(
        f'marketplace.json has no plugin named "{_MARKETPLACE_PLUGIN_NAME}"'
    )


def read_json_version(path: Path, rel_path: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if rel_path == ".claude-plugin/marketplace.json":
        return _marketplace_plugin_version(data)
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"invalid top-level version in {path}")
    return version


def _patch_json_version_lines(text: str, version: str, *, marketplace: bool) -> str:
    lines = text.splitlines(keepends=True)
    in_target_plugin = False
    updated = False
    out: list[str] = []
    plugin_name_re = re.compile(
        rf'"name"\s*:\s*"{re.escape(_MARKETPLACE_PLUGIN_NAME)}"'
    )
    for line in lines:
        if marketplace and plugin_name_re.search(line):
            in_target_plugin = True
            if _JSON_VERSION_VALUE_RE.search(line):
                line = _JSON_VERSION_VALUE_RE.sub(rf"\g<1>{version}\2", line, count=1)
                updated = True
                in_target_plugin = False
                out.append(line)
                continue
        elif marketplace and in_target_plugin and re.search(r'"name"\s*:', line):
            in_target_plugin = False
        if _JSON_VERSION_LINE_RE.match(line.rstrip("\n")):
            if not marketplace or in_target_plugin:
                line = _JSON_VERSION_LINE_RE.sub(rf"\g<1>{version}\2", line, count=1)
                updated = True
                if marketplace:
                    in_target_plugin = False
        out.append(line)
    if not updated:
        scope = "marketplace plugin" if marketplace else "top-level"
        raise ValueError(f"could not update {scope} version in file")
    return "".join(out)


def write_json_version(path: Path, rel_path: str, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    marketplace = rel_path == ".claude-plugin/marketplace.json"
    patched = _patch_json_version_lines(text, version, marketplace=marketplace)
    if patched != text:
        path.write_text(patched, encoding="utf-8")


def read_target_version(root: Path, target: VersionTarget) -> str:
    path = root / target.rel_path
    if not path.exists():
        raise FileNotFoundError(path)
    if target.rel_path == "pyproject.toml":
        return read_pyproject_version(path)
    if target.rel_path.endswith("SKILL.md"):
        return read_skill_version(path)
    if target.rel_path.endswith(".json"):
        return read_json_version(path, target.rel_path)
    raise ValueError(f"unsupported target: {target.rel_path}")


def write_target_version(root: Path, target: VersionTarget, version: str) -> None:
    path = root / target.rel_path
    if target.rel_path == "pyproject.toml":
        write_pyproject_version(path, version)
        return
    if target.rel_path.endswith("SKILL.md"):
        write_skill_version(path, version)
        return
    if target.rel_path.endswith(".json"):
        write_json_version(path, target.rel_path, version)
        return
    raise ValueError(f"unsupported target: {target.rel_path}")


def read_canonical_version(root: Path | None = None) -> str:
    root = root or repo_root()
    canonical = next(t for t in TARGETS if t.canonical)
    return read_target_version(root, canonical)


def cmd_show(root: Path) -> int:
    print(read_canonical_version(root))
    return 0


def cmd_check(root: Path) -> int:
    canonical_target = next(t for t in TARGETS if t.canonical)
    try:
        canonical = read_target_version(root, canonical_target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"❌ cannot read canonical version from {canonical_target.rel_path}: {exc}", file=sys.stderr)
        return 1

    mismatches: list[str] = []
    report: list[str] = []
    for target in TARGETS:
        try:
            found = read_target_version(root, target)
        except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
            print(f"❌ cannot read {target.rel_path}: {exc}", file=sys.stderr)
            return 1
        suffix = " (canonical)" if target.canonical else f" (expected {canonical})"
        report.append(f"{target.label}={found}{suffix}")
        if found != canonical:
            mismatches.append(f"  {target.rel_path}: {found} != {canonical}")

    if mismatches:
        print("⚠️ invest-A version mismatch:")
        for line in report:
            print(f"  {line}")
        for line in mismatches:
            print(line)
        print(f"❌ fix: bash scripts/bump-version.sh {canonical}")
        return 1

    print(
        "✅ invest-A versions consistent: "
        + ", ".join(f"{t.label}={canonical}" for t in TARGETS)
    )
    return 0


def cmd_bump(root: Path, version: str) -> int:
    validate_version(version)
    backups: list[tuple[Path, str]] = []
    written: list[Path] = []
    try:
        for target in TARGETS:
            path = root / target.rel_path
            if not path.exists():
                raise FileNotFoundError(path)
            backups.append((path, path.read_text(encoding="utf-8")))
            write_target_version(root, target, version)
            written.append(path)
            print(f"  ✅ {target.label}")
    except (OSError, ValueError, FileNotFoundError) as exc:
        for path, content in reversed(backups):
            if path in written:
                path.write_text(content, encoding="utf-8")
        print(f"❌ bump failed, rolled back: {exc}", file=sys.stderr)
        return 1

    if len(written) == len(TARGETS):
        print(f"\n✅ all {len(written)} version files updated to v{version}")
        return 0
    print(f"⚠️ partial update ({len(written)}/{len(TARGETS)})", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="invest-A version sync utilities")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: parent of scripts/)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="print canonical version from pyproject.toml")
    sub.add_parser("check", help="verify all version targets match canonical")
    bump_p = sub.add_parser("bump", help="write version to all targets")
    bump_p.add_argument("version", help="new version X.Y.Z")

    args = parser.parse_args(argv)
    root = (args.root or repo_root()).resolve()

    if args.command == "show":
        return cmd_show(root)
    if args.command == "check":
        return cmd_check(root)
    if args.command == "bump":
        try:
            validate_version(args.version)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        return cmd_bump(root, args.version)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
