"""Shim smoke: journal etf_data re-exports invest-a-etf canonical module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"

from invest_path import invest_a_etf_lib_dir  # noqa: E402  # skills/lib via conftest


def _load_journal_shim():
    """Load journal shim by file path (avoid collision with canonical etf_data)."""
    for key in ("journal_etf_shim", "invest_a_etf_etf_data"):
        sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location("journal_etf_shim", _LIB / "etf_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shim_reexports_same_map_objects():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.ETF_HEDGE_MAP is canon.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP is canon.CSINDEX_MAP


def test_shim_reexports_maps_content():
    shim = _load_journal_shim()
    assert "510300" in shim.ETF_HEDGE_MAP
    assert shim.CSINDEX_MAP.get("563300") == "932000"


def test_shim_callables_are_canonical():
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    assert shim.query_etf_data is canon.query_etf_data
    assert shim.query_etf_quote is canon.query_etf_quote
    assert shim.query_etf_kline is canon.query_etf_kline
    assert shim.prefetch_etf_spot is canon.prefetch_etf_spot
    assert shim.rollup_etf_quality_status is canon.rollup_etf_quality_status


def test_shim_fully_forwards_exported_names():
    """shim 完整转发 canonical 导出（review 第三轮 #1 防护）。

    pytest 会话中 journal lib 可能遮蔽 canonical etf_data（conftest 顺序
    依赖）——任一 `from etf_data import X` 都必须可用，含测试辅助
    clear_etf_spot_cache 与 fetch_* 系列。此前删除 re-export 导致
    `pytest etf目录 journal目录`（etf 在前）collection 失败（实证复现）。
    """
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    for name in (
        "clear_etf_spot_cache",
        "fetch_etf_spot_rows",
        "fetch_etf_index_pe",
        "fetch_etf_nav",
        "fetch_etf_index_daily",
        "fetch_etf_adj_factor",
        "fetch_etf_share_history",
        "fetch_etf_industry_alloc",
        "fetch_etf_category_sina",
    ):
        assert getattr(shim, name) is getattr(canon, name), name
        assert name in shim.__all__, name


def _consumed_etf_data_names() -> set[str]:
    """仓库内全部 `from etf_data import X` 的消费名（AST，非文本匹配）。"""
    import ast

    root = Path(__file__).resolve().parents[2]  # skills/
    names: set[str] = set()
    for p in root.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "etf_data":
                names.update(a.name for a in node.names)
    return names


def test_shim_forwards_whole_consumed_surface():
    """R15：shim 必须完整匹配消费面（含私有名与常量），且为 canonical 同一对象。

    手列清单会随消费增长漂移（本轮 42 个名字缺失）；此测试从源码扫描推导
    应有集合——新增 `from etf_data import X` 但 shim 未转发即失败。
    不用模块级 __getattr__ 惰性转发：那会让 monkeypatch 只改到 shim 命名空间、
    canonical 消费方看不见（D13 mock 不生效）。
    """
    shim = _load_journal_shim()
    canon = sys.modules["invest_a_etf_etf_data"]
    consumed = _consumed_etf_data_names()
    assert len(consumed) >= 40, f"消费面扫描异常（仅 {len(consumed)} 名）"
    missing = sorted(n for n in consumed if not hasattr(shim, n))
    assert not missing, f"shim 未转发消费名: {missing}"
    for n in sorted(consumed):
        if not hasattr(canon, n):
            continue  # canonical 无此名（消费方本就该报错）——不属 shim 职责
        assert getattr(shim, n) is getattr(canon, n), f"{n} 非 canonical 同一对象"


def _journal_first_env() -> dict:
    """journal lib 在 PYTHONPATH 最前的子进程环境（_skills_lib 由 shim 自解析）。"""
    import os

    skills = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([
        str(skills / "invest-a-journal" / "scripts" / "lib"),
        str(skills / "invest-a-etf" / "scripts" / "lib"),
        str(skills / "lib"),
    ])
    return env


def test_journal_first_sys_path_layout_imports_surface():
    """R15/F1：journal lib 在前时 `import etf_data` 必须解析到 **canonical 本体**
    （模块身份透明），且消费名全部可用。

    子进程隔离（避免本进程 sys.modules 已缓存 canonical 造成假通过）。
    """
    import subprocess

    code = (
        "import etf_data, sys\n"
        "canon = sys.modules['invest_a_etf_etf_data']\n"
        "print(etf_data is canon)\n"
        "from etf_data import (TrackingCaliberError, DIMENSION_STATES, "
        "ETF_FEE_SCHEDULE, TRADING_DAYS_PER_YEAR, _align_legs_by_date, "
        "_build_holdings_clusters, HOLDINGS_CLUSTER_MAP, build_data_status, "
        "compute_tracking_metrics)\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=_journal_first_env(), timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == "True"
    assert proc.stdout.splitlines()[-1] == "OK"


def test_journal_first_patch_reaches_canonical_no_network(tmp_path):
    """F1 反例：预加载 shim 后 `patch("etf_data._bridge_get")` 必须替换 canonical
    查找点（D13）——旧实现只改 shim 命名空间，canonical 走真实取数（联网）。

    子进程同时**禁网**（socket.connect / create_connection 直接抛错）：
    mock 未生效即失败，不会静默走网络。
    """
    import subprocess

    code = r"""
import socket
def _no_net(*a, **k):
    raise AssertionError("真实网络访问（mock 未生效）")
socket.socket.connect = _no_net
socket.create_connection = _no_net

import sys
import etf_data
from unittest.mock import patch

# mock 标记：唯一哨兵字符串，mock 未生效时结果里绝不出现
NAV_MARK = "MOCK-NAV-4a1e"
SHARE_MARK = "MOCK-SHARE-3f9c"
calls = []

def fake_bridge(getter, *a, **k):
    calls.append(getter)
    if getter == "get_etf_share_history":
        return {"status": "missing", "fund_share": [], "fund_daily": [],
                "note": SHARE_MARK}
    if getter == "get_etf_nav":
        return {"status": "ok", "source": NAV_MARK,
                "rows": [{"date": f"2026-09-{d:02d}", "nav": 1.0 + d / 1000,
                          "change_pct": 0.1} for d in range(1, 10)]}
    return None

with patch("etf_data._bridge_get", fake_bridge):
    ena = etf_data.query_etf_est_net_assets("510300")
    kline = etf_data.query_etf_kline("510300", days=5)
    tracking = etf_data.compute_etf_tracking("510300")

assert calls and calls[0] == "get_etf_share_history", calls
assert SHARE_MARK in (ena.get("note") or ""), ena
assert any(NAV_MARK in s for s in kline["attempted_sources"]), kline
assert any(NAV_MARK in s for s in tracking["attempted_sources"]), tracking
assert any(g == "get_etf_nav" for g in calls), calls
print("PATCH-OK", len(calls))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=_journal_first_env(), timeout=300)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "PATCH-OK" in proc.stdout


def test_journal_first_pytest_run_tracking_status_identity(tmp_path):
    """F1 复验场景固化：journal-first 预加载 + 跑 tracking/status/identity/v026
    四个 ETF 测试文件，必须全过且无真实取数（禁网）。

    这是独立复检实际失败的命令（13 项失败、真实联网）的最小固化版；
    只做 collection 不算通过——这里执行完整用例。
    """
    import subprocess

    skills = Path(__file__).resolve().parents[2]
    targets = [
        str(skills / "invest-a-etf/tests/test_etf_tracking.py"),
        str(skills / "invest-a-etf/tests/test_etf_data_status.py"),
        str(skills / "invest-a-etf/tests/test_etf_identity.py"),
        str(skills / "invest-a-etf/tests/test_v026_p1_fixes.py"),
    ]
    code = r"""
import socket
def _no_net(*a, **k):
    raise AssertionError("真实网络访问（本会话禁止取数）")
socket.socket.connect = _no_net
socket.create_connection = _no_net

import sys, pytest, etf_data
assert etf_data is sys.modules["invest_a_etf_etf_data"]
sys.exit(pytest.main(sys.argv[1:] + ["-q", "-p", "no:cacheprovider",
                                     "--no-header", "-x"]))
"""
    proc = subprocess.run([sys.executable, "-c", code, *targets],
                          capture_output=True, text=True,
                          env=_journal_first_env(), timeout=900)
    assert proc.returncode == 0, f"stdout={proc.stdout[-3000:]}\nstderr={proc.stderr[-2000:]}"




def test_invest_a_etf_lib_dir_resolves():
    lib = invest_a_etf_lib_dir()
    assert lib.name == "lib"
    assert lib.parent.name == "scripts"
    assert (lib / "etf_data.py").is_file()


def test_data_bridge_etf_attr_resolves_under_hostile_sys_path(monkeypatch, tmp_path):
    """v0.2.4 缺陷修复：_import_etf_attr 显式定位 invest-a-etf canonical
    （invest_path.load_invest_a_etf_module），不再裸 ``import etf_data``。

    模拟 sys.path 变化：invest-a-etf lib 缺席 / 后置 / 有同名 decoy
    etf_data.py 抢占首位 —— 旧实现分别 ImportError→None 或解析到 decoy，
    新实现三种布局下都解析到 canonical 同一实例。
    """
    import data_bridge

    scripts = Path(__file__).resolve().parent.parent.parent
    stock_scripts = scripts / "invest-a-stock" / "scripts"
    skills_lib = scripts / "lib"
    journal_lib = scripts / "invest-a-journal" / "scripts" / "lib"

    decoy = tmp_path / "etf_data.py"
    decoy.write_text(
        "def fetch_etf_spot_rows():\n    raise AssertionError('decoy resolved')\n",
        encoding="utf-8",
    )

    configs = [
        # 1. invest-a-etf lib 完全不在 sys.path（invest-a-stock 上下文）
        [str(stock_scripts), str(skills_lib)],
        # 2. journal 上下文但 etf lib 缺席（旧实现依赖 journal shim 被
        #    先导入才不返回 None；未导入时 ImportError → None）
        [str(journal_lib), str(skills_lib), str(stock_scripts)],
        # 3. 敌意首位：同名 decoy etf_data.py 抢先 —— 旧裸 import 解析到 decoy
        [str(decoy.parent), str(stock_scripts), str(skills_lib)],
    ]
    for cfg in configs:
        monkeypatch.setattr(sys, "path", list(cfg))
        monkeypatch.delitem(sys.modules, "invest_a_etf_etf_data", raising=False)
        fetch = data_bridge._import_etf_attr("fetch_etf_spot_rows")
        assert callable(fetch), f"resolve failed under sys.path={cfg}"
        canon = sys.modules["invest_a_etf_etf_data"]
        assert fetch is canon.fetch_etf_spot_rows, (
            f"resolved wrong module under sys.path={cfg}"
        )


def test_get_etf_spot_rows_resolves_end_to_end_under_hostile_path(monkeypatch):
    """get_etf_* getter 全链路在敌意 sys.path 下解析成功（dummy fetch，无网络）。

    旧实现：etf lib 不在 sys.path → _import_etf_attr 返回 None →
    getter 静默返回 None；新实现 resolve 后正常走缓存维度包装。
    """
    import data_bridge

    scripts = Path(__file__).resolve().parent.parent.parent
    monkeypatch.setattr(
        sys, "path",
        [str(scripts / "invest-a-stock" / "scripts"), str(scripts / "lib")],
    )
    monkeypatch.delitem(sys.modules, "invest_a_etf_etf_data", raising=False)

    fetch = data_bridge._import_etf_attr("fetch_etf_spot_rows")
    assert callable(fetch)
    # 解析到 canonical 后把 fetch 换成 dummy，避免 getter 触发真实网络
    canon = sys.modules["invest_a_etf_etf_data"]
    monkeypatch.setattr(canon, "fetch_etf_spot_rows", lambda: [])
    assert data_bridge.get_etf_spot_rows(force=True) == []
