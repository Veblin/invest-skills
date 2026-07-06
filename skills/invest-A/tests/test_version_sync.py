"""version_sync.py — 版本收敛工具测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import version_sync  # noqa: E402


def _write_fixture_tree(root: Path, version: str) -> None:
    (root / "skills" / "invest-A").mkdir(parents=True)
    (root / ".claude-plugin").mkdir(parents=True)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "test"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-A" / "SKILL.md").write_text(
        f'---\nname: invest-A\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "invest-A", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "invest-A", "version": version}]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (root / "gemini-extension.json").write_text(
        json.dumps({"name": "invest-skills", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )


class TestVersionSyncInRepo:
    def test_check_passes_in_repo(self):
        assert version_sync.cmd_check(_REPO_ROOT) == 0

    def test_show_matches_pyproject(self):
        shown = version_sync.read_canonical_version(_REPO_ROOT)
        from_pyproject = version_sync.read_pyproject_version(_REPO_ROOT / "pyproject.toml")
        assert shown == from_pyproject


class TestVersionSyncBump:
    def test_bump_updates_all_targets(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        assert version_sync.cmd_bump(tmp_path, "0.1.8") == 0
        assert version_sync.cmd_check(tmp_path) == 0
        assert version_sync.read_canonical_version(tmp_path) == "0.1.8"

    def test_check_fails_on_mismatch(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        plugin = tmp_path / ".claude-plugin" / "plugin.json"
        data = json.loads(plugin.read_text(encoding="utf-8"))
        data["version"] = "9.9.9"
        plugin.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        assert version_sync.cmd_check(tmp_path) == 1

    def test_bump_rolls_back_on_failure(self, tmp_path: Path, monkeypatch):
        _write_fixture_tree(tmp_path, "0.1.0")
        real_write = version_sync.write_target_version
        calls = {"n": 0}

        def flaky_write(root, target, ver):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise ValueError("simulated write failure")
            return real_write(root, target, ver)

        monkeypatch.setattr(version_sync, "write_target_version", flaky_write)
        assert version_sync.cmd_bump(tmp_path, "0.9.9") == 1
        assert version_sync.read_pyproject_version(tmp_path / "pyproject.toml") == "0.1.0"


class TestJsonVersionPreservesFormatting:
    def test_plugin_json_only_touches_version_line(self, tmp_path: Path):
        path = tmp_path / "plugin.json"
        original = (
            '{\n'
            '  "name": "invest-A",\n'
            '  "version": "0.1.0",\n'
            '  "description": "keep me"\n'
            "}\n"
        )
        path.write_text(original, encoding="utf-8")
        version_sync.write_json_version(path, ".claude-plugin/plugin.json", "0.1.8")
        assert path.read_text(encoding="utf-8") == original.replace("0.1.0", "0.1.8")


class TestMarketplaceNestedVersion:
    def test_read_by_plugin_name_not_index(self, tmp_path: Path):
        path = tmp_path / "marketplace.json"
        original = (
            '{\n'
            '  "plugins": [\n'
            '    {"name": "other-plugin", "version": "9.9.9"},\n'
            '    {"name": "invest-A", "version": "0.1.5"}\n'
            "  ]\n"
            "}\n"
        )
        path.write_text(original, encoding="utf-8")
        assert version_sync.read_json_version(path, ".claude-plugin/marketplace.json") == "0.1.5"
        version_sync.write_json_version(path, ".claude-plugin/marketplace.json", "0.1.9")
        assert path.read_text(encoding="utf-8") == original.replace("0.1.5", "0.1.9")

    def test_bump_updates_marketplace_nested_path(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.1")
        version_sync.cmd_bump(tmp_path, "0.2.0")
        marketplace = json.loads(
            (tmp_path / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        assert marketplace["plugins"][0]["version"] == "0.2.0"
