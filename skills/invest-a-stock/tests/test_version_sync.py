"""sync_version.py — 版本收敛工具测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import sync_version as _sync


def _write_fixture_tree(root: Path, version: str) -> None:
    """Minimal fixture: pyproject.toml + SKILL.md targets + 3 .json.in templates."""
    (root / "skills" / "invest-a-stock").mkdir(parents=True)
    (root / "skills" / "invest-a-limit-up").mkdir(parents=True)
    (root / "skills" / "invest-a-gap-scan").mkdir(parents=True)
    (root / "skills" / "invest-a-journal").mkdir(parents=True)
    (root / "skills" / "invest-a-etf").mkdir(parents=True)
    (root / ".claude-plugin").mkdir(parents=True)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "test"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-a-stock" / "SKILL.md").write_text(
        f'---\nname: invest:a-stock\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-a-limit-up" / "SKILL.md").write_text(
        f'---\nname: invest:a-limit-up\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-a-gap-scan" / "SKILL.md").write_text(
        f'---\nname: invest:a-gap-scan\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-a-journal" / "SKILL.md").write_text(
        f'---\nname: invest:a-journal\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (root / "skills" / "invest-a-etf" / "SKILL.md").write_text(
        f'---\nname: invest:a-etf\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )

    # JSON templates
    (root / ".claude-plugin" / "plugin.json.in").write_text(
        '{\n  "name": "invest:a-stock",\n  "version": "{{ VERSION }}"\n}\n',
        encoding="utf-8",
    )
    (root / ".claude-plugin" / "marketplace.json.in").write_text(
        '{\n  "plugins": [{"name": "invest:a-stock", "version": "{{ VERSION }}"}]\n}\n',
        encoding="utf-8",
    )
    (root / "gemini-extension.json.in").write_text(
        '{\n  "name": "invest-skills",\n  "version": "{{ VERSION }}"\n}\n',
        encoding="utf-8",
    )


class TestSyncVersionCheck:
    def test_check_passes_in_repo(self):
        assert _sync.cmd_check(_REPO_ROOT) == 0

    def test_write_pyproject_ignores_version_scheme(self, tmp_path: Path):
        pp = tmp_path / "pyproject.toml"
        pp.write_text(
            '[project]\nname = "x"\nversion_scheme = "pep440"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        _sync.write_pyproject_version(pp, "0.2.0")
        text = pp.read_text(encoding="utf-8")
        assert 'version_scheme = "pep440"' in text
        assert 'version = "0.2.0"' in text

    def test_sync_is_idempotent(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        assert _sync.cmd_sync(tmp_path) == 0
        # Second sync should be no-op
        assert _sync.cmd_sync(tmp_path) == 0


class TestSyncVersionBump:
    def test_bump_updates_all_targets(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        assert _sync.cmd_bump(tmp_path, "0.1.8") == 0
        assert _sync.cmd_check(tmp_path) == 0

    def test_bump_invalid_version(self, tmp_path: Path):
        assert _sync.cmd_bump(tmp_path, "not.a.version") == 1

    def test_bump_rolls_back_on_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _write_fixture_tree(tmp_path, "0.1.0")
        _sync.cmd_sync(tmp_path)  # materialize JSON outputs at 0.1.0

        before = {
            p: (tmp_path / p).read_text(encoding="utf-8")
            for p in (
                "pyproject.toml",
                "skills/invest-a-stock/SKILL.md",
                "skills/invest-a-limit-up/SKILL.md",
                ".claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
                "gemini-extension.json",
            )
        }

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated sync failure")

        monkeypatch.setattr(_sync, "generate_json_manifests", _boom)
        assert _sync.cmd_bump(tmp_path, "0.9.9") == 1

        for rel, content in before.items():
            assert (tmp_path / rel).read_text(encoding="utf-8") == content, rel
        assert _sync.cmd_check(tmp_path) == 0

    def test_check_fails_on_skill_mismatch(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        skill = tmp_path / "skills" / "invest-a-stock" / "SKILL.md"
        skill.write_text(skill.read_text().replace("0.1.0", "9.9.9"))
        assert _sync.cmd_check(tmp_path) == 1

    def test_check_fails_on_json_drift(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        _sync.cmd_sync(tmp_path)
        plugin = tmp_path / ".claude-plugin" / "plugin.json"
        plugin.write_text(plugin.read_text().replace("0.1.0", "9.9.9"))
        assert _sync.cmd_check(tmp_path) == 1

    def test_check_graceful_on_corrupt_pyproject(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\n", encoding="utf-8")
        assert _sync.cmd_check(tmp_path) == 1

    def test_bump_preflight_missing_template(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        (tmp_path / "gemini-extension.json.in").unlink()
        before = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        assert _sync.cmd_bump(tmp_path, "0.9.9") == 1
        assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == before


class TestSyncCommand:
    def test_sync_updates_skill_when_pyproject_changed(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        # Change pyproject.toml manually (simulating manual edit workflow)
        pp = tmp_path / "pyproject.toml"
        pp.write_text(pp.read_text().replace("0.1.0", "0.5.0"))
        assert _sync.cmd_sync(tmp_path) == 0
        assert _sync.cmd_check(tmp_path) == 0

    def test_sync_preflight_missing_template(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        (tmp_path / "gemini-extension.json.in").unlink()
        assert _sync.cmd_sync(tmp_path) == 1

    def test_sync_graceful_on_corrupt_pyproject(self, tmp_path: Path):
        _write_fixture_tree(tmp_path, "0.1.0")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\n", encoding="utf-8")
        assert _sync.cmd_sync(tmp_path) == 1
