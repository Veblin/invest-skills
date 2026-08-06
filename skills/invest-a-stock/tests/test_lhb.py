"""R12g-A 龙虎榜/涨停池采集 + 均线系统表渲染测试（全 mock，零活体网络）。

覆盖验收点：
- _render_ma_system 输出含 MA5/10 值与排列标签（均线系统表 grep 断言）
- fetch_lhb_detail mock 东财抛错 → 新浪回退
- fetch_zt_pool 解析（含非交易日回溯）
- 触发→采集→渲染端到端（情绪/梯队/席位/证伪四段；席位缺失降级行）
- 未触发 → 无连板段输出且零 lhb/zt_pool 网络调用断言
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pandas as pd

from lib.lhb import attach_limit_streak_dims, should_trigger_lhb


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _kline(n: int = 30, limit_ups_last: int = 0, pct_normal: float = 1.0) -> list[dict]:
    """构造 kline 序列（日期严格递增）；limit_ups_last >0 → 尾部 N 日 +10% 涨停。"""
    from datetime import date, timedelta
    start = date(2026, 1, 1)
    rows = []
    close = 10.0
    for i in range(n):
        is_limit = i >= n - limit_ups_last
        pct = 10.0 if is_limit else pct_normal
        close = round(close * (1 + pct / 100), 2)
        rows.append({
            "trade_date": (start + timedelta(days=i)).isoformat(),
            "open": round(close * 0.99, 2),
            "high": round(close * 1.01, 2),
            "low": round(close * 0.98, 2),
            "close": close,
            "vol": 1_000_000,
            "change_pct": pct,
        })
    return rows


def _collection(kline_rows: list[dict], extra_dims: list[dict] | None = None) -> dict:
    coll = {
        "symbol": "603773",
        "fetched_at": "2026-08-06T12:00:00+00:00",
        "dimensions": [
            {"dimension": "kline", "display": "行情", "data": kline_rows,
             "status": "available", "_meta": {"source": "test"}},
        ],
        "summary": {"available": 1, "total": 1, "degraded": 0},
    }
    if extra_dims:
        coll["dimensions"].extend(extra_dims)
    return coll


def _fake_lhb(has_seats: bool = False) -> dict:
    payload = {
        "records": [
            {"代码": "603773", "名称": "沃格光电", "上榜日": "2026-08-05",
             "龙虎榜净买额": 123456, "上榜原因": "日涨幅偏离值达到7%的前5只证券"},
        ],
        "source": "em",
    }
    if has_seats:
        payload["seats"] = {
            "2026-08-05": {
                "买入": [{"交易营业部名称": "机构专用", "买入金额": 1.0e8,
                          "卖出金额": 0.0, "净额": 1.0e8}],
                "卖出": [{"交易营业部名称": "东方财富拉萨营业部", "买入金额": 0.0,
                          "卖出金额": 5.0e7, "净额": -5.0e7}],
            }
        }
    else:
        payload["seats"] = {}
    return payload


def _fake_zt() -> dict:
    return {"date": "2026-08-05", "total": 103, "max_board": 6,
            "board_dist": {1: 78, 2: 15, 3: 6, 4: 3, 6: 1}}


# ---------------------------------------------------------------------------
# ① 均线系统表
# ---------------------------------------------------------------------------

class TestRenderMaSystem:
    def test_renders_ma_values_and_alignment(self):
        from lib.render_markdown._base import _render_ma_system

        coll = _collection(_kline(60))  # 线性上行 → 多头排列
        lines = _render_ma_system(coll)
        joined = "\n".join(lines)
        assert "**[均线系统表（R12g）]**" in joined
        assert "MA5=" in joined and "MA10=" in joined and "MA20=" in joined and "MA60=" in joined
        assert "现价 " in joined
        assert "多头排列" in joined or "排列" in joined
        assert "[来源: kline derived" in joined

    def test_insufficient_kline_renders_nothing(self):
        from lib.render_markdown._base import _render_ma_system

        assert _render_ma_system(_collection(_kline(3))) == []


# ---------------------------------------------------------------------------
# ② 东财失败 → 新浪回退
# ---------------------------------------------------------------------------

class TestFetchLhbDetail:
    def test_em_failure_falls_back_to_sina(self):
        sina_payload = {"records": [{"代码": "603773", "名称": "沃格光电"}],
                        "source": "sina", "seats": {}}
        with (
            patch("lib.lhb._fetch_lhb_em", return_value=None) as mock_em,
            patch("lib.lhb._fetch_lhb_sina", return_value=sina_payload) as mock_sina,
        ):
            from lib.lhb import fetch_lhb_detail
            result = fetch_lhb_detail("603773")
        mock_em.assert_called_once()
        mock_sina.assert_called_once()
        assert result is not None
        assert result["source"] == "sina"

    def test_both_fail_returns_none(self):
        with (
            patch("lib.lhb._fetch_lhb_em", return_value=None),
            patch("lib.lhb._fetch_lhb_sina", return_value=None),
        ):
            from lib.lhb import fetch_lhb_detail
            assert fetch_lhb_detail("603773") is None


# ---------------------------------------------------------------------------
# ③ 涨停池解析
# ---------------------------------------------------------------------------

class TestFetchZtPool:
    def test_parses_pool_and_board_dist(self, monkeypatch):
        import akshare as ak
        fake_df = pd.DataFrame([
            {"代码": "600001", "名称": "A", "连板数": 1},
            {"代码": "600002", "名称": "B", "连板数": 2},
            {"代码": "600003", "名称": "C", "连板数": 2},
            {"代码": "600004", "名称": "D", "连板数": 5},
        ])
        monkeypatch.setattr(ak, "stock_zt_pool_em", lambda date: fake_df)
        from lib.lhb import fetch_zt_pool
        result = fetch_zt_pool()
        assert result is not None
        assert result["total"] == 4
        assert result["max_board"] == 5
        assert result["board_dist"] == {1: 1, 2: 2, 5: 1}

    def test_backtracks_on_empty_days(self, monkeypatch):
        import akshare as ak
        empty = pd.DataFrame()
        good = pd.DataFrame([{"代码": "600001", "名称": "A", "连板数": 3}])
        calls = {"n": 0}

        def fake(date):
            calls["n"] += 1
            return good if calls["n"] >= 2 else empty

        monkeypatch.setattr(ak, "stock_zt_pool_em", fake)
        from lib.lhb import fetch_zt_pool
        result = fetch_zt_pool()
        assert result is not None
        assert result["max_board"] == 3


# ---------------------------------------------------------------------------
# ④ 触发→采集→渲染端到端
# ---------------------------------------------------------------------------

class TestLimitStreakEndToEnd:
    def test_triggered_collect_and_render(self):
        with (
            patch("lib.lhb.fetch_lhb_detail", return_value=_fake_lhb(has_seats=True)),
            patch("lib.lhb.fetch_zt_pool", return_value=_fake_zt()),
        ):
            coll = _collection(_kline(30, limit_ups_last=3))
            assert attach_limit_streak_dims(coll, "603773") is True

        from lib.render_markdown._base import _render_limit_streak_structure
        joined = "\n".join(_render_limit_streak_structure(coll))
        assert "**[连板结构（R12g）]**" in joined
        assert "情绪周期: 涨停 103 家" in joined
        assert "梯队: 最高连板 6 板" in joined
        assert "龙虎榜席位: 买入榜 机构专用" in joined
        assert "证伪条件" in joined
        # 筹码/题材纯度 → 不可得 + attempted sources
        assert "不可得 + attempted sources" in joined

    def test_seats_missing_shows_degradation_line(self):
        with (
            patch("lib.lhb.fetch_lhb_detail", return_value=_fake_lhb(has_seats=False)),
            patch("lib.lhb.fetch_zt_pool", return_value=_fake_zt()),
        ):
            coll = _collection(_kline(30, limit_ups_last=3))
            assert attach_limit_streak_dims(coll, "603773") is True

        from lib.render_markdown._base import _render_limit_streak_structure
        joined = "\n".join(_render_limit_streak_structure(coll))
        assert "未上榜或席位不可得" in joined
        assert "资金流三日结构替代" in joined

    def test_trigger_gate_on_kline_missing(self):
        """kline 缺失 → 不触发。"""
        coll = {"symbol": "603773", "dimensions": [], "summary": {}}
        with (
            patch("lib.lhb.fetch_lhb_detail") as mock_lhb,
            patch("lib.lhb.fetch_zt_pool") as mock_zt,
        ):
            assert attach_limit_streak_dims(coll, "603773") is False
            mock_lhb.assert_not_called()
            mock_zt.assert_not_called()

    def test_should_trigger_logic(self):
        assert should_trigger_lhb(_kline(30, limit_ups_last=3), "603773") is True
        assert should_trigger_lhb(_kline(30, limit_ups_last=1), "603773") is False
        assert should_trigger_lhb([], "603773") is False


# ---------------------------------------------------------------------------
# ⑤ 未触发 → 零网络调用
# ---------------------------------------------------------------------------

class TestNoTriggerNoNetwork:
    def test_no_trigger_no_calls_and_no_segment(self):
        with (
            patch("lib.lhb.fetch_lhb_detail") as mock_lhb,
            patch("lib.lhb.fetch_zt_pool") as mock_zt,
        ):
            coll = _collection(_kline(30))  # 无涨停
            assert attach_limit_streak_dims(coll, "603773") is False
            mock_lhb.assert_not_called()
            mock_zt.assert_not_called()

        from lib.render_markdown._base import _render_limit_streak_structure
        assert _render_limit_streak_structure(coll) == []
