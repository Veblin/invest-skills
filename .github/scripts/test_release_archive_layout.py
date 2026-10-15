"""release.yml「Create release archive」包布局回归（R4，2026-10-11 复查）。

现场：旧块 `cp -r skills/X dist/pkg/` 把源布局压扁到包根——包内
`.claude-plugin/plugin.json` 的 "skills": "./skills"、`.agents`/`.workbuddy`
技能链接（../../skills/<name>）与 README/docs 的 `skills/<name>/scripts/<cli>.py`
命令都按 skills/ 布局解析，平铺后技能链接悬空、发布版 CLI 无法从解压树启动；
且 tests/、reports/、__pycache__ 运行期产物随包发布（首轮清点 1423 个文件）。

本测试用**从 release.yml 实际切出的打包块**（与监督者 check_artifacts 同一切片
口径）在临时目录做一次最小构建：断言解压树保留 skills/<skill> 与共享
skills/lib、无悬空链接、plugin ./skills 解析、运行期产物零泄漏、退出入口
（invest-a-event-calendar）不复制、技能 CLI 能从解压树 `--help` 启动。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

PUBLIC_SKILLS = ["invest-a-stock", "invest-a-gap-scan", "invest-a-journal",
                 "invest-a-etf", "invest-a-pulse", "invest-a-pattern-scan",
                 "invest-hk-stock", "invest-a-discover-scan"]
_CLI_ENTRY = {"invest-a-stock": "invest.py", "invest-a-etf": "etf.py",
              "invest-a-gap-scan": "scan.py", "invest-a-pattern-scan": "scan.py",
              "invest-hk-stock": "hk.py", "invest-a-discover-scan": "discover_scan.py"}
_TAG = "v0.3.1"


def _archive_block_script() -> str:
    """切出「Create release archive」块并转成可执行脚本（同 check_artifacts 口径）。"""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("      - name: Create release archive")
    end = workflow.index("      - name:", start + 10)
    run = workflow[start:end].split("        run: |\n", 1)[1]
    return "\n".join(
        line[10:] if line.startswith("          ") else line
        for line in run.splitlines()).replace("${{ github.ref_name }}", _TAG)


def test_block_copies_public_skills_under_skills_layout():
    """结构与公开清单：skills/X → dist/pkg/skills/X（回归：不得再平铺到包根）。"""
    script = _archive_block_script()
    assert "rm -rf dist/pkg" in script      # 重跑前清残包（防旧产物混入 tar）
    assert "mkdir -p dist/pkg/skills" in script
    # 说明性清单不复制退出入口；公开集=8 skill + 共享 lib
    assert not re.search(r"^\s*skills/invest-a-event-calendar\b", script, re.M), \
        "退出入口（event-calendar）不得进入复制清单"

    misc_cp = script.split("cp -r", 1)[1].split("dist/pkg/", 1)[0]
    assert "skills/" not in misc_cp, "非技能杂项复制不得混入 skills/ 源"
    assert "skills/lib \\" in script
    for name in PUBLIC_SKILLS:
        assert f"skills/{name} \\" in script, name

    idx = script.index("cp -r", script.index("cp -r") + 1)
    m = re.search(r"^\s*(dist/pkg/\S*)\s*$", script[idx:], re.M)
    assert m and m.group(1) == "dist/pkg/skills/", \
        "技能复制目标必须是 dist/pkg/skills/（保留源布局）"


def test_block_cleans_runtime_artifacts():
    """运行期产物清除口径：tests/、reports/、缓存与编译产物。"""
    script = _archive_block_script()
    for token in ("__pycache__", "'tests'", "'reports'", ".pytest_cache",
                  "*.pyc", "*.pyo", "dist/pkg/.venv"):
        assert token in script, token


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("tar")),
                    reason="需要 bash 与 tar")
def test_minimal_build_yields_deployable_tree(tmp_path: Path):
    """最小真实构建：解压树 skills/lib 完整、无泄漏、CLI 可 --help 启动。"""
    script = _archive_block_script()
    for d in (".claude-plugin", "hooks", "docs", ".agents/skills",
              ".workbuddy/skills"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude-plugin/plugin.json").write_text(
        json.dumps({"skills": "./skills"}), encoding="utf-8")
    for name in ("skills.yaml", "gemini-extension.json", "pyproject.toml",
                 "README.md", "AGENTS.md", "CONFIGURATION.md", "CHANGELOG.md",
                 "CONTRIBUTORS.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
                 ".env.example", "LICENSE"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "skills/lib/references").mkdir(parents=True)
    (tmp_path / "skills/lib/references/report-conventions.md").write_text(
        "x", encoding="utf-8")
    (tmp_path / "skills/invest-a-event-calendar").mkdir(parents=True)
    (tmp_path / "skills/invest-a-event-calendar/SKILL.md").write_text(
        "x", encoding="utf-8")
    for name in PUBLIC_SKILLS:
        entry = _CLI_ENTRY.get(name, "cli.py")
        sdir = tmp_path / "skills" / name / "scripts"
        sdir.mkdir(parents=True)
        (sdir / entry).write_text("import sys\nprint('help-ok')\n", encoding="utf-8")
        for link_root in (".agents/skills", ".workbuddy/skills"):
            (tmp_path / link_root / name).symlink_to(f"../../skills/{name}")
    # 运行期产物诱饵：必须被清除，不得进入 tar
    decoy = tmp_path / "skills/invest-a-etf"
    (decoy / "tests").mkdir(parents=True)
    (decoy / "tests/test_x.py").write_text("x", encoding="utf-8")
    (decoy / "reports").mkdir(parents=True)
    (decoy / "reports/r.md").write_text("x", encoding="utf-8")
    (decoy / "scripts/__pycache__").mkdir(parents=True)
    (decoy / "scripts/__pycache__/x.pyc").write_text("x", encoding="utf-8")

    r = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    archive = tmp_path / f"dist/invest-skills-{_TAG}.tar.gz"
    assert archive.is_file()
    dest = tmp_path / "extract"
    dest.mkdir()
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")

    # 布局与解析链：plugin ./skills / 技能目录 / 共享 lib
    assert (dest / "skills/invest-a-stock/scripts/invest.py").is_file()
    assert (dest / "skills/lib/references/report-conventions.md").is_file()
    plugin = json.loads(
        (dest / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert (dest / plugin["skills"]).is_dir(), "plugin.json ./skills 必须解析"
    assert [p for p in dest.rglob("*") if p.is_symlink() and not p.exists()] == [], \
        "技能链接不得悬空"
    # 运行期泄漏与退出入口
    leaks = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()
             and any(part in ("reports", "tests", "__pycache__")
                     for part in p.relative_to(dest).parts)]
    assert leaks == []
    assert not any("invest-a-event-calendar" in str(p) for p in dest.rglob("*"))
    # 技能 CLI 能从解压树启动 --help
    rr = subprocess.run(
        [sys.executable, str(dest / "skills/invest-a-stock/scripts/invest.py"),
         "--help"], cwd=dest / "skills", capture_output=True, text=True, timeout=60)
    assert rr.returncode == 0 and "help-ok" in rr.stdout
