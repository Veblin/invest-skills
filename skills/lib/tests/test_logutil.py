"""`skills/lib/logutil.py`（canonical 开发日志）的行为测试。

加载方式刻意用 **显式路径**（`spec_from_file_location`）而非 `import logutil`：
`skills/lib/tests/` 无 conftest，prepend 模式不会把 `skills/lib` 放进 sys.path；
且显式加载每次得到**全新的模块对象**，`_setup_done` 幂等标志天然重置。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import itertools
import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[3]
_LOGUTIL = _SKILLS_LIB / "logutil.py"

_ids = itertools.count()


def _load_module() -> object:
    """按显式路径加载 canonical logutil（每次全新实例）。"""
    spec = importlib.util.spec_from_file_location(f"logutil_ut_{next(_ids)}", _LOGUTIL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """setup_logging 改的是**全局 root logger**：不快照恢复会污染同会话其它测试。"""
    root = logging.root
    before = list(root.handlers)
    level = root.level
    yield
    for h in list(root.handlers):
        if h not in before:
            h.close()
            root.removeHandler(h)
    root.setLevel(level)


def _added(root_before: list, ) -> list:
    return [h for h in logging.root.handlers if h not in root_before]


# ---------------------------------------------------------------- release 分支

def test_release_is_noop(monkeypatch):
    """默认（无 INVEST_DEV）必须零副作用：不碰 root，不建目录。"""
    monkeypatch.delenv("INVEST_DEV", raising=False)
    mod = _load_module()
    before, level = list(logging.root.handlers), logging.root.level

    assert mod.setup_logging() is False
    assert list(logging.root.handlers) == before, "release 分支不得挂 handler"
    assert logging.root.level == level, "release 分支不得改 root level"


def test_dev_false_is_noop(monkeypatch):
    monkeypatch.setenv("INVEST_DEV", "1")
    mod = _load_module()
    before = list(logging.root.handlers)

    assert mod.setup_logging(dev=False) is False
    assert list(logging.root.handlers) == before


# ---------------------------------------------------------------- 真身：落盘

def test_canonical_dev_writes_log_with_skill_tag(monkeypatch, tmp_path):
    """真身 + dev → 落盘到指定目录，文件名含 skill，行内含 skill 标签。"""
    mod = _load_module()
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    before = list(logging.root.handlers)

    assert mod.setup_logging(dev=True, skill="invest-a-gap-scan", log_dir=tmp_path) is True

    files = sorted(p.name for p in tmp_path.glob("invest_*.log"))
    assert files, "真身 + dev 应落盘"
    assert files[0].startswith("invest_invest-a-gap-scan_")
    assert files[0].endswith(".log")

    logging.getLogger("probe").info("扫描开始")
    for h in _added(before):
        h.flush()
    assert "[invest-a-gap-scan]" in stream.getvalue(), "stderr 行须含 skill 标签"
    assert "[invest-a-gap-scan]" in (tmp_path / files[0]).read_text(encoding="utf-8")


def test_repo_logs_dir_is_repo_logs():
    """真身加载 → `<repo>/logs`（只断言解析，不落盘）。"""
    mod = _load_module()
    assert mod.repo_logs_dir() == _ROOT / "logs"


# ---------------------------------------------------------------- 非真身：永不落盘

def _logs_snapshot() -> tuple[bool, float | None, int]:
    """`<repo>/logs` 的「前/后」对照基线。

    **不能断言该目录不存在**——真身 + dev 跑过一次就会留下它，那样测试只在干净的
    CI checkout 上绿、在开发者机上红。判据应是「本次调用没有改变它」。
    """
    d = _ROOT / "logs"
    if not d.exists():
        return (False, None, 0)
    return (True, d.stat().st_mtime_ns, len(list(d.glob("*.log"))))


def test_workbuddy_package_layout_never_writes(tmp_path, monkeypatch):
    """WorkBuddy 包布局（`<pkg>/skills/lib/logutil.py` + `<pkg>/pyproject.toml`）→ 永不落盘。

    这个形状**恰好满足**恒等比较（包根下真有 `skills/lib/logutil.py`），因此是真判据的
    试金石。旧测试传的是仓内 `<pkg>/scripts/lib/`——那条路径永不可能通过恒等比较，
    测试于是空转：把判据改回「只比路径」也照样绿。这里先断言**前置条件成立**
    （形状确实满足恒等比较、包根无 `.git`），前置不成立时测试自己会红。
    """
    pkg = tmp_path / "wb_pkg"
    copy = pkg / "skills" / "lib" / "logutil.py"
    copy.parent.mkdir(parents=True)
    copy.write_text(_LOGUTIL.read_text(encoding="utf-8"), encoding="utf-8")
    (pkg / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    assert (pkg / "skills" / "lib" / "logutil.py").resolve() == copy.resolve(), "前置：形状满足恒等比较"
    assert not (pkg / ".git").exists(), "前置：分发副本没有 .git"

    spec = importlib.util.spec_from_file_location(f"logutil_wb_{next(_ids)}", copy)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = list(logging.root.handlers)
    assert mod.setup_logging(dev=True, skill="invest-a-stock") is True

    assert mod.repo_logs_dir() is None
    assert not (pkg / "logs").exists(), "不得在用户安装目录建 logs/"
    assert list(pkg.rglob("*.log")) == [], "不得在用户安装目录写日志"
    assert len(_added(before)) == 1, "仅 console handler"


def test_packaged_copy_never_writes(monkeypatch, tmp_path):
    """分包布局（仓内 `<pkg>/scripts/lib/`）→ 仅 stderr，零落盘、零建目录。"""
    mod = _load_module()
    fake = _ROOT / "skills" / "invest-a-gap-scan" / "scripts" / "lib" / "logutil.py"
    monkeypatch.setattr(mod, "__file__", str(fake))
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    before = list(logging.root.handlers)
    logs_before = _logs_snapshot()

    assert mod.setup_logging(dev=True, skill="invest-a-gap-scan") is True

    assert mod.repo_logs_dir() is None
    assert _logs_snapshot() == logs_before, "分包副本不得改动仓根 logs/"
    assert len(_added(before)) == 1, "仅 console handler"


def test_foreign_layout_never_writes(monkeypatch, tmp_path):
    """仓外路径（无 pyproject.toml 祖先）→ 同样永不落盘。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "scripts" / "lib" / "logutil.py"))
    before = list(logging.root.handlers)

    assert mod.setup_logging(dev=True, skill="x") is True
    assert mod.repo_logs_dir() is None
    assert len(_added(before)) == 1
    assert list(tmp_path.rglob("*.log")) == []


def test_foreign_layout_ignores_explicit_log_dir(monkeypatch, tmp_path):
    """显式 `log_dir` **不豁免身份门**——非真身即使传入也不落盘（不变量零例外）。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "scripts" / "lib" / "logutil.py"))
    target = tmp_path / "explicit"

    assert mod.setup_logging(dev=True, skill="x", log_dir=target) is True
    assert not target.exists(), "非真身不得因显式 log_dir 而落盘"


# ---------------------------------------------------------------- skill 名转义

def test_unsafe_skill_name_is_escaped():
    """`%` 进格式串、路径分隔进文件名——两者都是调用方传入、必须转义。"""
    mod = _load_module()
    assert mod._safe_tag("invest-100%") == "invest-100%%"
    assert mod._safe_filename_part("../x") == ".._x"
    assert mod._safe_filename_part("invest-a-stock") == "invest-a-stock", "安全名不变形"


def test_percent_in_skill_name_does_not_drop_records(monkeypatch, tmp_path):
    """skill 名含 `%` 时记录仍须输出。

    未转义时 `logging.Formatter` 把 `%` 当字段占位符：每条记录抛 TypeError、
    打 "--- Logging error ---" 后**丢弃**——日志全空且每行一段栈。
    """
    mod = _load_module()
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "scripts" / "lib" / "logutil.py"))
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    assert mod.setup_logging(dev=True, skill="invest-100%") is True
    logging.getLogger("ut.percent").info("hello")

    out = stream.getvalue()
    assert "hello" in out, "记录被 Formatter 丢弃"
    assert "[invest-100%]" in out, "转义后应还原为单个 %"


# ---------------------------------------------------------------- 幂等 / 降级 / 参数

def test_idempotent(monkeypatch, tmp_path):
    mod = _load_module()
    before = list(logging.root.handlers)

    assert mod.setup_logging(dev=True, skill="a", log_dir=tmp_path) is True
    n = len(_added(before))
    assert mod.setup_logging(dev=True, skill="a", log_dir=tmp_path) is True
    assert len(_added(before)) == n, "重复调用不得重复挂 handler"


def test_oserror_degrades_to_stderr_only(monkeypatch, tmp_path):
    """落盘失败（此处：目标是既有文件）不致命，退化为仅 stderr。"""
    mod = _load_module()
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    before = list(logging.root.handlers)

    assert mod.setup_logging(dev=True, skill="a", log_dir=blocker) is True
    assert len(_added(before)) == 1, "落盘失败后应只剩 console handler"


def test_second_skill_in_same_process_is_reported_not_silently_mislabelled(
    monkeypatch, tmp_path, capsys
):
    """进程内第二次换 skill：不重配 handler，但**必须把限制说出来**。

    逐 skill 隔离按进程生效——handler 与日志文件名绑定首个调用方。此前静默返回 True，
    后续 skill 的行会带前一个 skill 的标识、写进前一个文件（错标且不可见）。
    """
    mod = _load_module()
    assert mod.setup_logging(dev=True, skill="invest-a-stock", log_dir=tmp_path) is True
    assert mod.setup_logging(dev=True, skill="invest-a-etf", log_dir=tmp_path) is True

    err = capsys.readouterr().err
    assert "invest-a-stock" in err and "invest-a-etf" in err, "换 skill 未提示"
    assert len(list(tmp_path.glob("invest_*.log"))) == 1, "仍只应有首个 skill 的文件"


def test_same_skill_twice_is_silent(monkeypatch, tmp_path, capsys):
    """同一 skill 重复调用是正常幂等，不该刷提示。"""
    mod = _load_module()
    mod.setup_logging(dev=True, skill="invest-a-stock", log_dir=tmp_path)
    mod.setup_logging(dev=True, skill="invest-a-stock", log_dir=tmp_path)
    assert "已在 skill=" not in capsys.readouterr().err


def test_rotation_params(monkeypatch, tmp_path):
    mod = _load_module()
    before = list(logging.root.handlers)

    mod.setup_logging(dev=True, skill="a", log_dir=tmp_path)
    fhs = [h for h in _added(before) if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(fhs) == 1
    assert fhs[0].maxBytes == 5 * 1024 * 1024
    assert fhs[0].backupCount == 7


# ---------------------------------------------------------------- 构建面防线

def test_canonical_is_stdlib_only_and_has_no_relative_import():
    """陷阱 B 防线：canonical 若带相对导入（如 `from . import env`），
    构建器 `_rewrite_mod` 对 `.` 开头不改写 → 包内副本 ImportError。"""
    tree = ast.parse(_LOGUTIL.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"canonical 不得有相对导入（level={node.level}）"
            assert node.module != "env", "canonical 不得依赖 env（包内不存在 env.py）"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in {
                    "logging", "os", "sys", "re", "datetime", "pathlib", "__future__",
                }, f"canonical 只应依赖 stdlib，发现 {alias.name}"
