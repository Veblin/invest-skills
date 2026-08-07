"""v0.2.4：index_pe_history 指数 PE 历史快照（persist / 查询 / 分位 / 触发点）。

测试隔离：本地 isolated_store fixture（etf conftest 无此 fixture）；信封
用 monkeypatch _bridge_envelope 预种，不依赖 data_bridge 实现细节。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_ETF_ROOT = Path(__file__).resolve().parent.parent
_ETF_LIB = _ETF_ROOT / "scripts" / "lib"
_ETF_PY = _ETF_ROOT / "scripts" / "etf.py"
_STOCK_SCRIPTS = _ETF_ROOT.parent / "invest-a-stock" / "scripts"
# 全量 pytest 运行时 journal conftest 会把 journal scripts/lib 插到 sys.path 前，
# 遮蔽 canonical lib.*（解析到 invest-a-stock）——此处把 invest-a-stock 提到最前
if str(_STOCK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_STOCK_SCRIPTS))


def _ensure_canonical_etf_data():
    """确保 sys.modules["etf_data"] 是 canonical（invest-a-etf）而非 journal shim。

    全量跑时 journal scripts/lib 的 etf_data shim 可能先被 import（缺
    compute_history_stats 等 canonical 符号）。仅当当前模块非 canonical 时覆盖。
    """
    name = "etf_data"
    mod = sys.modules.get(name)
    if mod is not None:
        cur = Path(getattr(mod, "__file__", "") or "")
        if cur.resolve().parent == _ETF_LIB.resolve():
            return mod
    spec = importlib.util.spec_from_file_location(name, _ETF_LIB / "etf_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """临时 SQLite 隔离（仿 invest-a-stock conftest.isolated_store）。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


def _envelope_rows(n: int = 25, start_pe: float = 10.0) -> list[dict]:
    """构造 csindex 信封 rows（指数代码为整型，验证 str() 规范化）。"""
    rows = []
    for i in range(n):
        rows.append({
            "日期": f"202601{i + 1:02d}",
            "指数代码": 300,
            "指数中文简称": "沪深300",
            "市盈率1": start_pe + i,
            "市盈率2": None,
            "股息率1": 2.0,
            "股息率2": None,
        })
    return rows


def _load_etf_module():
    """按 path 加载 etf.py（避免 scripts/ 上 sys.path 遮蔽 lib）。"""
    name = "etf_cli_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ETF_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPersistIndexPe:
    def test_persist_writes_envelope_rows(self, isolated_store, monkeypatch):
        import index_pe_snapshot

        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "ok", "index_pe": 15.0,
                                          "rows": _envelope_rows(3)})
        result = index_pe_snapshot.persist_index_pe_from_cache(["000300"])
        assert result["rows_saved"] == 3
        assert result["error"] is None

        rows = index_pe_snapshot.get_index_pe_history("000300")
        assert len(rows) == 3
        # 整型指数代码已 str() 规范化 + 字段映射正确
        assert rows[0]["index_code"] == "300"
        assert rows[0]["pe"] is not None
        assert rows[0]["date"]

    def test_persist_idempotent(self, isolated_store, monkeypatch):
        import index_pe_snapshot

        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "ok", "index_pe": 15.0,
                                          "rows": _envelope_rows(3)})
        index_pe_snapshot.persist_index_pe_from_cache(["000300"])
        index_pe_snapshot.persist_index_pe_from_cache(["000300"])
        assert len(index_pe_snapshot.get_index_pe_history("000300")) == 3

    def test_persist_missing_envelope_skips(self, isolated_store, monkeypatch):
        import index_pe_snapshot

        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "missing"})
        result = index_pe_snapshot.persist_index_pe_from_cache(["000300"])
        assert result["rows_saved"] == 0
        assert result["ok_envelopes"] == 0  # 空结果显式化（D5）
        assert index_pe_snapshot.get_index_pe_history("000300") == []

    def test_persist_defaults_all_csindex_codes(self, isolated_store, monkeypatch):
        import index_pe_snapshot

        from etf_data import CSINDEX_MAP
        codes = set(CSINDEX_MAP.values())
        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "ok", "index_pe": 15.0,
                                          "rows": _envelope_rows(1)})
        result = index_pe_snapshot.persist_index_pe_from_cache()
        assert result["index_codes"] == len(codes)
        assert result["rows_saved"] == len(codes)

    def test_persist_poison_code_fallback(self, isolated_store, monkeypatch):
        """review #7：指数代码为 None/NaN 时回退 idx_code，不落 'None'/'nan' 毒键。"""
        import index_pe_snapshot

        rows = _envelope_rows(3)
        rows[1]["指数代码"] = None
        rows[2]["指数代码"] = float("nan")
        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "ok", "index_pe": 15.0, "rows": rows})
        result = index_pe_snapshot.persist_index_pe_from_cache(["000300"])
        assert result["rows_saved"] == 3

        hist = index_pe_snapshot.get_index_pe_history("000300")
        assert len(hist) == 3
        codes = {r["index_code"] for r in hist}
        assert codes == {"300"}
        assert "nan" not in codes and "None" not in codes


class TestIndexPePercentile:
    def test_percentile_bounds(self):
        import index_pe_snapshot

        rows = [{"pe": 10 + i} for i in range(25)]  # 10.0 ~ 34.0
        # 低于全部 → 低分位；含边界（<=）
        assert index_pe_snapshot.index_pe_percentile(rows, 10.0) == pytest.approx(4.0)
        assert index_pe_snapshot.index_pe_percentile(rows, 34.0) == pytest.approx(100.0)

    def test_percentile_too_short_returns_none(self):
        import index_pe_snapshot

        rows = [{"pe": 10.0} for _ in range(5)]
        assert index_pe_snapshot.index_pe_percentile(rows, 12.0) is None

    def test_percentile_current_none(self):
        import index_pe_snapshot

        rows = [{"pe": 10.0} for _ in range(25)]
        assert index_pe_snapshot.index_pe_percentile(rows, None) is None

    def test_percentile_valid_count_guard(self):
        """review #5：守卫计有效 PE 值——19 有效 + 5 个 pe=None 行 → None。"""
        import index_pe_snapshot

        rows = [{"pe": 10.0 + i} for i in range(19)] + [{"pe": None}] * 5
        assert index_pe_snapshot.index_pe_percentile(rows, 15.0) is None

    def test_percentile_nan_row_filtered(self):
        """review #5：NaN 历史行被 safe_float 滤掉，不污染分位分母。"""
        import index_pe_snapshot

        rows24 = [{"pe": 10.0 + i} for i in range(24)]
        rows_with_nan = rows24 + [{"pe": float("nan")}]
        assert index_pe_snapshot.index_pe_percentile(rows_with_nan, 15.0) == \
            index_pe_snapshot.index_pe_percentile(rows24, 15.0)
        assert index_pe_snapshot.index_pe_percentile(rows_with_nan, 15.0) is not None


class TestEtfDataIntegration:
    def test_profile_attaches_index_pe_pct(self, isolated_store, monkeypatch):
        """history ≥20 条 → _index_pe_percentile_from_db 返回分位；无 history → None。"""
        import index_pe_snapshot

        etf_data = _ensure_canonical_etf_data()
        _index_pe_percentile_from_db = etf_data._index_pe_percentile_from_db
        monkeypatch.setattr(index_pe_snapshot, "_bridge_envelope",
                            lambda code: {"status": "ok", "index_pe": 15.0,
                                          "rows": _envelope_rows(25)})
        index_pe_snapshot.persist_index_pe_from_cache(["000300"])

        pct = _index_pe_percentile_from_db("000300", 12.0)
        assert pct is not None and 0 <= pct <= 100
        # 无 history（未入库的指数代码）→ None
        assert _index_pe_percentile_from_db("999999", 12.0) is None
        # current_pe None → None
        assert _index_pe_percentile_from_db("000300", None) is None


class TestCollectWeeklyTrigger:
    def test_collect_weekly_invokes_index_pe_persist(self, monkeypatch):
        """cmd_collect_weekly 顺带全量写 index_pe_history。"""
        import industry_snapshot
        import index_pe_snapshot

        calls: dict = {}

        def fake_collect():
            return {"date": "20260807", "industries_saved": 31, "error": None}

        def fake_persist(*a, **k):
            calls["called"] = True
            return {"index_codes": 9, "rows_saved": 31, "error": None}

        monkeypatch.setattr(industry_snapshot, "collect_industry_weekly", fake_collect)
        monkeypatch.setattr(index_pe_snapshot, "persist_index_pe_from_cache", fake_persist)

        mod = _load_etf_module()
        assert mod.cmd_collect_weekly() == 0
        assert calls.get("called") is True

    def test_collect_weekly_empty_envelope_warns(self, monkeypatch, capsys):
        """review #8：0 行可写（无 ok 信封）→ 警告文案而非成功式打印。"""
        import industry_snapshot
        import index_pe_snapshot

        def fake_collect():
            return {"date": "20260807", "industries_saved": 31, "error": None}

        def fake_persist(*a, **k):
            return {"index_codes": 9, "rows_saved": 0, "ok_envelopes": 0, "error": None}

        monkeypatch.setattr(industry_snapshot, "collect_industry_weekly", fake_collect)
        monkeypatch.setattr(index_pe_snapshot, "persist_index_pe_from_cache", fake_persist)

        mod = _load_etf_module()
        assert mod.cmd_collect_weekly() == 0
        err = capsys.readouterr().err
        assert "无 ok 信封" in err or "0 行可写" in err
