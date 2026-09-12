"""sync_version.py — 版本收敛工具测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import sync_version as _sync


def _write_fixture_tree(root: Path, version: str) -> None:
    """Minimal fixture: pyproject.toml + all SKILL.md targets + json.in templates.

    Mirrors the current SKILL_TARGETS / JSON_TEMPLATES layout (incl. invest-a-pulse
    and the .agents/plugins/marketplace.json output dir) so sync/bump preflight
    and output generation pass.
    """
    # ⚠️ 技能列表**从 SKILL_TARGETS 派生**，不逐个硬编码——否则新增技能时
    # 夹具缺文件会让 preflight 假红（2026-09-12 新增 discover-scan 时实测踩坑）。
    for _t in _sync.SKILL_TARGETS:
        _p = root / _t.rel_path
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(f'---\nname: {_t.label}\nversion: "{version}"\n---\n', encoding="utf-8")
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "test"\nversion = "{version}"\n',
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

    # README release badge（sync_version 现覆盖 README.md）
    (root / "README.md").write_text(
        "# test\n\n[![Release]"
        f"(https://img.shields.io/github/v/release/Veblin/invest-skills?label=v{version})]\n",
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


class TestRegistrationParity:
    """三个注册面（SKILL_TARGETS / marketplace 清单 / skills.yaml）须两两一致。

    docs/architecture.md 声称「skills.yaml（10 个用户 skill）→ sync_version.py 同步
    生成三处 marketplace 清单」，但 *.json.in 模板里的插件列表是硬编码的：sync 只
    替换 {{ VERSION }}，新增技能不会进清单，也无测试断言三者一致——实测 marketplace
    仍停在 6 个插件，invest-hk-stock 与 3 个新技能无法经 /plugin marketplace 安装。
    """

    @staticmethod
    def _plugin_names(rel: str) -> set[str]:
        data = json.loads((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        return {p["name"] for p in data["plugins"]}

    @staticmethod
    def _skill_dirs() -> set[str]:
        return {p.parent.name for p in (_REPO_ROOT / "skills").glob("*/SKILL.md")}

    def test_skill_targets_cover_all_skill_dirs(self):
        have = {Path(t.rel_path).parent.name for t in _sync.SKILL_TARGETS}
        assert have == self._skill_dirs(), f"SKILL_TARGETS 未覆盖: {self._skill_dirs() - have}"

    def test_marketplace_lists_every_skill_target(self):
        expected = {t.label for t in _sync.SKILL_TARGETS}
        for rel in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
            names = self._plugin_names(rel)
            assert names == expected, (
                f"{rel} 插件集与 SKILL_TARGETS 不一致："
                f"缺 {sorted(expected - names)}，多 {sorted(names - expected)}"
            )

    def test_agents_marketplace_byte_identical_to_claude_plugin(self):
        """.agents 副本与 .claude-plugin 共用同一模板（sync_version docstring 承诺）。"""
        a = (_REPO_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        b = (_REPO_ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        assert a == b

    def test_skills_yaml_matches_skill_dirs(self):
        """skills.yaml（antfu/skills-cli 清单）条目须与 skills/ 一一对应。"""
        data = yaml.safe_load((_REPO_ROOT / "skills.yaml").read_text(encoding="utf-8"))
        names = {s["name"] for s in data["skills"]}
        assert names == self._skill_dirs(), (
            f"skills.yaml 未覆盖: {self._skill_dirs() - names}；多余: {names - self._skill_dirs()}"
        )
