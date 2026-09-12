"""skill_paths 布局自适应测试（R1 审查 F3：单体仓库 vs skillhub 包内运行）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skill_paths import (  # noqa: E402
    default_out_dir,
    default_reference,
    is_installed_in_repo,
    shared_tool_relpath,
    skill_relpath,
    skill_root,
)


def _layout(tmp_path: Path, kind: str) -> Path:
    if kind == "repo":
        repo = tmp_path / "repo"
        (repo / "skills").mkdir(parents=True)
        (repo / "pyproject.toml").write_text("", encoding="utf-8")
        f = repo / "skills" / "S" / "scripts" / "x.py"
    else:  # skillhub 包：<pkg>/scripts/x.py（scripts 拍平一层）
        f = tmp_path / "extract" / "S" / "scripts" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text("", encoding="utf-8")
    return f


def test_repo_layout(tmp_path):
    f = _layout(tmp_path, "repo")
    assert is_installed_in_repo(str(f)) is True
    assert Path(default_out_dir(str(f), "n")) == tmp_path / "repo" / "reports" / "n"
    assert (Path(default_reference(str(f), "references/a.yaml"))
            == tmp_path / "repo" / "skills" / "S" / "references" / "a.yaml")
    assert skill_root(str(f)) == tmp_path / "repo" / "skills" / "S"


def test_package_layout(tmp_path):
    f = _layout(tmp_path, "pkg")
    assert is_installed_in_repo(str(f)) is False
    assert Path(default_out_dir(str(f), "n")) == tmp_path / "extract" / "S" / "reports" / "n"
    assert (Path(default_reference(str(f), "references/a.yaml"))
            == tmp_path / "extract" / "S" / "references" / "a.yaml")
    assert skill_root(str(f)) == tmp_path / "extract" / "S"


def test_shared_tool_relpath_per_layout(tmp_path):
    """打印给用户/agent 执行的共享工具命令须**两种布局都可跑**。

    回归（R2 review P2）：复盘纪要尾部打印仓内路径 `skills/lib/report_qc.py`，
    而分发包里没有 `skills/` 目录（工具在 `<pkg>/scripts/lib/`，builder 实测），
    agent 照自己 CLI 的指示执行必然失败 →「机器层准出（必跑）」在分发形态从未运行。
    """
    assert (shared_tool_relpath(str(_layout(tmp_path / "r", "repo")), "report_qc")
            == "skills/lib/report_qc.py")
    assert (shared_tool_relpath(str(_layout(tmp_path / "p", "pkg")), "report_qc")
            == "scripts/lib/report_qc.py")


def test_skill_relpath_per_layout(tmp_path):
    """技能内文件（references/ 等）的打印路径同样须布局自适应。"""
    assert (skill_relpath(str(_layout(tmp_path / "r", "repo")),
                          "references/report-template.md")
            == "skills/S/references/report-template.md")
    assert (skill_relpath(str(_layout(tmp_path / "p", "pkg")),
                          "references/report-template.md")
            == "references/report-template.md")
