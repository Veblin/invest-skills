"""技术指标模块单元测试。

测试覆盖:
  - SMA/MACD/RSI/KDJ/BOLL/ATR 与 synthetic kline 对比
  - 数据不足时返回 null / 标注不可得
  - MA250 需要 ≥250 根 K 线
  - 边界情况
"""

from __future__ import annotations

import math

import pytest


# ---- 辅助：生成 synthetic K 线 ----

def _make_kline(n: int, base: float = 100.0, step: float = 1.0,
                noise: float = 0.0, seed: int = 42) -> list[dict]:
    """生成简单线性 K 线（上升趋势）。"""
    import random
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        close = base + i * step + rng.uniform(-noise, noise)
        open_p = close - rng.uniform(-2, 2)
        high = max(open_p, close) + rng.uniform(0, 3)
        low = min(open_p, close) - rng.uniform(0, 3)
        vol = 1000000 + rng.uniform(-200000, 200000)
        rows.append({
            "trade_date": f"2026{1 + (i // 250):02d}{(i % 250) + 1:02d}",
            "open": round(open_p, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "vol": round(vol, 0),
        })
    return rows


class TestSMA:
    def test_sma_basic(self):
        """SMA 基本计算：5 日均线。"""
        from lib.technical import sma
        closes = [10.0, 12.0, 14.0, 16.0, 18.0]
        result = sma(closes, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(12.0)  # (10+12+14)/3
        assert result[3] == pytest.approx(14.0)  # (12+14+16)/3
        assert result[4] == pytest.approx(16.0)  # (14+16+18)/3

    def test_sma_insufficient_data(self):
        """数据不足 N 时前 N-1 位为 None。"""
        from lib.technical import sma
        closes = [10.0, 12.0]
        result = sma(closes, 5)
        assert all(v is None for v in result)

    def test_ma250_requires_250(self):
        """MA250 需要 ≥250 根 K 线。"""
        from lib.technical import compute
        rows = _make_kline(200)
        result = compute(rows)
        ma_avail = result["trend"]["ma_availability"]
        assert ma_avail["250"] is not None  # 应该为 "数据不足 250 日..."
        assert "不可得" in ma_avail["250"]


class TestEMA:
    def test_ema_basic(self):
        """EMA 基本计算。"""
        from lib.technical import _ema
        closes = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
        result = _ema(closes, 5)
        # 前 4 个为 None
        for i in range(4):
            assert result[i] is None
        # 第 5 个（index 4）= SMA5
        assert result[4] == pytest.approx(14.0)
        # 第 6 个 = EMA
        assert result[5] is not None
        assert result[5] > 14.0


class TestMACD:
    def test_macd_compute(self):
        """MACD 可对足够数据计算。"""
        from lib.technical import compute
        rows = _make_kline(100)
        result = compute(rows)
        macd = result["momentum"]["macd"]
        assert macd["available"] is True
        assert macd["dif"] is not None
        assert macd["dea"] is not None

    def test_macd_insufficient_data(self):
        """数据不足 26 天时 MACD 不可得。"""
        from lib.technical import compute
        rows = _make_kline(15)
        result = compute(rows)
        macd = result["momentum"]["macd"]
        assert macd["available"] is False


class TestRSI:
    def test_rsi_basic(self):
        """RSI 基本计算。"""
        from lib.technical import _rsi
        # 全涨序列应接近 100
        closes = list(range(100, 130))
        result = _rsi(closes, 14)
        valid = [v for v in result if v is not None]
        assert len(valid) > 0
        # 连续上涨 RSI 应偏高
        assert valid[-1] > 50

    def test_rsi_all_periods(self):
        """RSI(6), RSI(12), RSI(24) 均可计算。"""
        from lib.technical import compute
        rows = _make_kline(50)
        result = compute(rows)
        rsi = result["overbought_oversold"]["rsi"]
        assert rsi["6"]["available"]
        assert rsi["12"]["available"]
        assert rsi["24"]["available"]

    def test_rsi_insufficient(self):
        """数据不足时 RSI 标注不可得。"""
        from lib.technical import compute
        rows = _make_kline(5)
        result = compute(rows)
        rsi = result["overbought_oversold"]["rsi"]
        for p in ("6", "12", "24"):
            assert rsi[p]["available"] is False


class TestKDJ:
    def test_kdj_basic(self):
        """KDJ 基本计算。"""
        from lib.technical import compute
        rows = _make_kline(30)
        result = compute(rows)
        kdj = result["overbought_oversold"]["kdj"]
        assert kdj["available"] is True
        assert kdj["k"] is not None
        assert kdj["d"] is not None
        assert kdj["j"] is not None


class TestBOLL:
    def test_boll_basic(self):
        """BOLL 基本计算。"""
        from lib.technical import compute
        rows = _make_kline(30)
        result = compute(rows)
        boll = result["volatility"]["boll"]
        assert boll["available"] is True
        assert boll["mid"] is not None
        assert boll["upper"] is not None
        assert boll["lower"] is not None
        assert boll["upper"] > boll["mid"] > boll["lower"]


class TestATR:
    def test_atr_basic(self):
        """ATR 基本计算。"""
        from lib.technical import compute
        rows = _make_kline(30)
        result = compute(rows)
        atr = result["volatility"]["atr"]
        assert atr["available"] is True
        assert atr["value"] > 0


class TestVolume:
    def test_volume_ratio(self):
        """量比计算。"""
        from lib.technical import compute
        rows = _make_kline(30)
        result = compute(rows)
        vol = result["volume"]
        assert vol["latest_ratio"] is not None
        assert vol["latest_ratio"] > 0


class TestStructure:
    def test_n_day_extremes(self):
        """N 日极值。"""
        from lib.technical import compute
        rows = _make_kline(200)
        result = compute(rows)
        extremes = result["structure"]["extremes"]
        assert extremes[20]["available"]
        assert extremes[60]["available"]
        assert extremes[120]["available"]
        assert extremes[20]["max"] >= extremes[20]["min"]

    def test_drawdown(self):
        """回撤计算。"""
        from lib.technical import compute
        rows = _make_kline(100)
        result = compute(rows)
        dd = result["structure"]["drawdown_60d"]
        assert dd["available"] is True
        assert "drawdown_pct" in dd

    def test_drawdown_insufficient(self):
        """回撤数据不足。"""
        from lib.technical import compute
        rows = _make_kline(30)
        result = compute(rows)
        dd = result["structure"]["drawdown_60d"]
        assert dd["available"] is False


class TestEmptyInput:
    def test_empty_rows(self):
        """空输入返回 error 标记。"""
        from lib.technical import compute
        result = compute([])
        assert "error" in result


class TestKlineSortOrder:
    def test_descending_input_uses_latest_close(self):
        """Tushare 降序 K 线：compute 内部升序后 latest_close 为最新交易日。"""
        from lib.technical import compute

        rows = _make_kline(30)
        descending = list(reversed(rows))
        newest_close = rows[-1]["close"]
        oldest_close = rows[0]["close"]
        assert newest_close != oldest_close

        result = compute(descending)
        assert result["latest_close"] == pytest.approx(newest_close)
        assert result["last_date"] == rows[-1]["trade_date"]

    def test_sort_kline_asc_idempotent(self):
        from lib.technical import sort_kline_asc

        rows = _make_kline(10)
        asc = sort_kline_asc(rows)
        assert asc[0]["trade_date"] <= asc[-1]["trade_date"]
        assert sort_kline_asc(list(reversed(rows))) == asc

    def test_mixed_date_formats_sort_correctly(self):
        """混合格式（20260801 vs 2026-08-01）归一化后正确排序（review fix #15）。"""
        from lib.technical import sort_kline_asc

        rows = [
            {"trade_date": "2026-08-01", "close": 3.0},
            {"trade_date": "20260730", "close": 1.0},
            {"trade_date": "2026.07.31", "close": 2.0},
        ]
        asc = sort_kline_asc(rows)
        assert [r["close"] for r in asc] == [1.0, 2.0, 3.0]

    def test_empty_date_rows_sort_last(self):
        """无 trade_date 的快照行排最后，data[-1] 语义保持「最新」（review fix #15）。"""
        from lib.technical import sort_kline_asc

        rows = [
            {"trade_date": "20260801", "close": 2.0},
            {"close": 3.0},  # intraday 快照行，无日期
            {"trade_date": "20260731", "close": 1.0},
        ]
        asc = sort_kline_asc(rows)
        assert asc[-1].get("trade_date") is None
        assert asc[-1]["close"] == 3.0


class TestNoSignalWords:
    """合规：输出不含交易信号词汇。"""
    FORBIDDEN = ["金叉", "死叉", "买入", "卖出", "抄底", "追涨", "建仓", "目标价"]

    def test_no_forbidden_words_in_summary(self):
        from lib.technical import compute
        import json
        rows = _make_kline(300)
        result = compute(rows)
        # 序列化为 JSON 字符串检查
        text = json.dumps(result, ensure_ascii=False, default=str)
        for word in self.FORBIDDEN:
            assert word not in text, f"输出含禁止词: {word}"


class TestLimitPctTable:
    """R12e 涨跌停阈值表（跨 skill 唯一权威）：北交所 30%、ST 5%。

    修复前 _limit_pct_for_symbol 只认 30x/68x 为 20%（其余 10%），
    北交所 4/8/920 前缀的 20-25% 涨幅会被误计为涨停。
    """

    def _kline(self, closes):
        return [
            {"trade_date": f"2026080{i+1:02d}", "close": float(c)}
            for i, c in enumerate(closes)
        ]

    def test_limit_pct_table_values(self):
        from lib.technical import limit_pct_for_symbol

        assert limit_pct_for_symbol("600176") == 10.0
        assert limit_pct_for_symbol("300328") == 20.0
        assert limit_pct_for_symbol("301001") == 20.0
        assert limit_pct_for_symbol("688001") == 20.0
        assert limit_pct_for_symbol("430001") == 30.0
        assert limit_pct_for_symbol("832001") == 30.0
        assert limit_pct_for_symbol("920001") == 30.0
        # ST/*ST 需名称信息；无名称按板块阈值兜底
        assert limit_pct_for_symbol("600001", name="ST某某") == 5.0
        assert limit_pct_for_symbol("600001", name="*ST某某") == 5.0
        assert limit_pct_for_symbol("600001", name="正常公司") == 10.0

    def test_bse_20pct_not_limit_up_30pct_is(self):
        """北交所 30% 阈值：20-25% 涨幅不再被计为涨停（缺陷 1）。"""
        from lib.technical import detect_limit_streaks

        # 20% 连涨两天：北交所阈值 30% → 不算涨停
        st = detect_limit_streaks(self._kline([10.0, 12.0, 14.4]), symbol="430001")
        assert st["limit_threshold"] == 30.0
        assert st["recent_limit_ups"] == 0
        # 30% 连涨两天：算涨停
        st2 = detect_limit_streaks(self._kline([10.0, 13.0, 16.9]), symbol="920001")
        assert st2["limit_threshold"] == 30.0
        assert st2["recent_limit_ups"] == 2
        # 同一序列按主板 10% 阈值仍为涨停（对照，阈值表确实按代码前缀生效）
        st3 = detect_limit_streaks(self._kline([10.0, 12.0, 14.4]), symbol="600001")
        assert st3["limit_threshold"] == 10.0
        assert st3["recent_limit_ups"] == 2

    def test_st_name_5pct_threshold(self):
        """ST/*ST 5% 阈值：传入名称后 5% 涨停被识别（缺陷 1）。"""
        from lib.technical import detect_limit_streaks

        # 5% 连涨两天 + ST 名称 → 涨停（阈值 5%）
        st = detect_limit_streaks(
            self._kline([10.0, 10.5, 11.02]), symbol="600001", name="*ST某某",
        )
        assert st["limit_threshold"] == 5.0
        assert st["recent_limit_ups"] == 2
        # 同一序列无名称信息 → 按主板 10% 兜底，5% 不算涨停
        st2 = detect_limit_streaks(self._kline([10.0, 10.5, 11.02]), symbol="600001")
        assert st2["limit_threshold"] == 10.0
        assert st2["recent_limit_ups"] == 0
