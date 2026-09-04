"""v0.2.7 E2/E3 宏观扩展测试（12 个 FRED series + ACMTP10 + 序列消费）。

网络隔离：所有 fetcher 在定义模块（lib.macro）命名空间 mock（D13），
断言带独特标记（DGS30=5.28、T10Y2Y=-0.32 倒挂、JP 2.67 创 20 年新高等），
mock 失效时断言必挂，不会碰巧通过。缓存 patch 为 tmp 目录（autouse），
防止 mock 采集结果写入真实缓存。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from lib import env, macro


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _macro_cache_tmp(tmp_path, monkeypatch):
    """缓存隔离：模块级缓存 patch 为 tmp 目录（每测试独立）。

    背景：collect 路径的缓存键含 FRED key 哈希（_fred_cache_symbol），
    测试 mock key（"a"*32）与真实 key 天然隔离；此处再 patch 缓存实例，
    双保险避免 mock 采集结果污染 ~/.local/share/investment/cache。
    """
    from cache import DataCache

    monkeypatch.setattr(macro, "_macro_cache", DataCache(cache_dir=tmp_path / "cache"))


def _mock_config():
    return {"FRED_API_KEY": "a" * 32}


def _fred_daily_mock_values() -> dict[str, tuple[float, list[tuple[str, float]]]]:
    """12 个 series 的 mock 值。

    D13: 数值刻意偏离 2026-08 真实行情（如真实 DGS30≈5.28、JP≈2.67），
    mock 失效时真实源成功也会因断言不匹配而挂，不会碰巧通过。
    """
    return {
        "VIXCLS": (18.5, [("2026-08-18", 18.5)]),
        "DGS10": (4.12, [("2026-08-18", 4.12)]),
        "DGS30": (6.02, [("2026-08-18", 6.02)]),
        "DFII10": (2.61, [("2026-08-18", 2.61)]),
        "T10Y2Y": (-0.32, [("2026-08-19", -0.32)]),
        "T5YIE": (2.77, [("2026-08-19", 2.77)]),
        "DTWEXBGS": (96.4, [("2026-08-14", 96.4)]),
        "DCOILBRENTEU": (71.4, [("2026-08-18", 71.4)]),
        "DEXCHUS": (7.31, [("2026-08-14", 7.31)]),
        "IRLTLT01GBM156N": (4.91, [("2026-06-01", 4.91)]),
        "IRLTLT01DEM156N": (2.61, [("2026-06-01", 2.61)]),
        "IRLTLT01FRM156N": (3.21, [("2026-06-01", 3.21)]),
        "IRLTLT01JPM156N": (2.83, [("2026-06-01", 2.83)]),
    }


def _patch_collect_globals(monkeypatch, fred_values=None, acm=(0.87, "2026-08-18")):
    """collect_macro_context 的公共 mock：中国指标跳过 + SOX/ACM + FRED。"""
    monkeypatch.setattr(env, "get_config", lambda: _mock_config())
    monkeypatch.setattr(env, "is_fred_available", lambda cfg: True)
    monkeypatch.setattr(env, "is_akshare_available", lambda: False)
    monkeypatch.setattr(macro, "_fetch_sox_via_yahoo", lambda: 6850.0)
    monkeypatch.setattr(macro, "_fetch_acm_term_premia_cached", lambda: acm)
    values = _fred_daily_mock_values() if fred_values is None else fred_values

    def fake_fred(series_id, config, lookback_days=90):
        if series_id in values:
            v, s = values[series_id]
            return v, s
        return None, []

    monkeypatch.setattr(macro, "_fetch_fred_series", fake_fred)


_ALL_NEW_SERIES = [
    ("dgs10", "DGS10"), ("dgs30", "DGS30"), ("dfii10", "DFII10"),
    ("t10y2y", "T10Y2Y"), ("t5yie", "T5YIE"), ("dtwexbgs", "DTWEXBGS"),
    ("dcoilbrenteu", "DCOILBRENTEU"), ("dexchus", "DEXCHUS"),
    ("sovereign_gb10y", "IRLTLT01GBM156N"), ("sovereign_de10y", "IRLTLT01DEM156N"),
    ("sovereign_fr10y", "IRLTLT01FRM156N"), ("sovereign_jp10y", "IRLTLT01JPM156N"),
]


# ---------------------------------------------------------------------------
# E2: collect 三态（成功 / 失败独立降级 / 无 FRED key 降级）
# ---------------------------------------------------------------------------

class TestCollectE2:
    def test_all_series_success(self, monkeypatch):
        """12 个 FRED series + ACMTP10 全部成功，值/来源/截至日期/滞后标注齐全。"""
        _patch_collect_globals(monkeypatch)
        ctx = macro.collect_macro_context("600176")
        inds = ctx["indicators"]
        assert set(ctx["failed_indicators"]) == {"PMI", "CPI", "PPI", "LPR"}  # 仅 akshare 跳过
        assert ctx["status"] == "ok"

        d30 = inds["dgs30"]
        assert d30["value"] == 6.02
        assert d30["signal"] == "高位"
        assert d30["source"] == "FRED.DGS30"
        assert d30["as_of"] == "2026-08-18"
        assert "滞后约 1 个交易日" in d30["lag_note"]

        assert inds["t10y2y"]["value"] == -0.32
        assert inds["t10y2y"]["signal"] == "倒挂"
        assert inds["dfii10"]["signal"] == "高实际利率"
        assert inds["dexchus"]["signal"] == "人民币偏弱"  # 7.31 > 7.3
        assert inds["dtwexbgs"]["signal"] == ""
        assert inds["sox"]["value"] == 6850.0

        jp = inds["sovereign_jp10y"]
        assert jp["value"] == 2.83
        assert jp["as_of"] == "2026-06"
        assert jp["frequency"] == "monthly"
        # 滞后注记按月频数据截止月（2026-06）与真实当前月差动态生成——
        # 断言按当前日期计算期望（写死月数会随日期漂移过期，2026-09 实测
        # 2.5→3.5 的教训）。
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        _now = _dt.now(_ZI("Asia/Shanghai"))
        _months = (_now.year - 2026) * 12 + (_now.month - 6)
        assert f"滞后约 {_months + 0.5:g} 个月" in jp["lag_note"]
        assert jp["source"] == "FRED.IRLTLT01JPM156N"

        acm = inds["acm_tp10"]
        assert acm["value"] == 0.87
        assert acm["as_of"] == "2026-08-18"
        assert acm["frequency"] == "weekly"
        assert "周更新" in acm["lag_note"]

        for key, _series_id in _ALL_NEW_SERIES:
            assert inds[key] is not None, f"{key} 应成功采集"

    @pytest.mark.parametrize("ind_key,series_id", _ALL_NEW_SERIES)
    def test_series_failure_isolated(self, monkeypatch, ind_key, series_id):
        """单个 series 失败 → 该键 None + 记 failures，不阻塞其余 11 个。"""
        values = _fred_daily_mock_values()

        def fake_fred(sid, config, lookback_days=90):
            if sid == series_id:
                raise RuntimeError(f"boom {sid}")
            if sid in values:
                v, s = values[sid]
                return v, s
            return None, []

        _patch_collect_globals(monkeypatch)
        monkeypatch.setattr(macro, "_fetch_fred_series", fake_fred)
        ctx = macro.collect_macro_context("600176")
        inds = ctx["indicators"]
        assert inds[ind_key] is None
        label = macro._FRED_DAILY_SPECS.get(ind_key) or macro._FRED_SOVEREIGN_SPECS.get(ind_key)
        assert label["label"] in ctx["failed_indicators"]
        for other_key, _sid in _ALL_NEW_SERIES:
            if other_key != ind_key:
                assert inds[other_key] is not None, f"{other_key} 不应被 {series_id} 失败阻塞"

    def test_acm_failure_isolated(self, monkeypatch):
        """ACMTP10 失败 → 记 failures，FRED 12 个不受影响。"""
        _patch_collect_globals(monkeypatch, acm=(None, None))
        ctx = macro.collect_macro_context("600176")
        assert ctx["indicators"]["acm_tp10"] is None
        assert "ACMTP10" in ctx["failed_indicators"]
        assert ctx["indicators"]["dgs30"]["value"] == 6.02
        assert ctx["indicators"]["sovereign_jp10y"]["value"] == 2.83

    def test_no_fred_key_akshare_fallback(self, monkeypatch):
        """无 FRED key → dgs10/dgs30/t10y2y 走 akshare，标注来源差异方向；
        DFII10/T5YIE/主权债等无等价免费源 → None + failures。"""
        monkeypatch.setattr(env, "get_config", lambda: {})
        monkeypatch.setattr(env, "is_fred_available", lambda cfg: False)
        monkeypatch.setattr(env, "is_akshare_available", lambda: False)
        monkeypatch.setattr(macro, "_fetch_sox_via_yahoo", lambda: 6850.0)
        monkeypatch.setattr(
            macro, "_fetch_us_curve_akshare",
            lambda: {"dgs10": 4.65, "dgs30": 5.19, "t10y2y": 0.46},
        )
        monkeypatch.setattr(macro, "_fetch_acm_term_premia_cached", lambda: (0.87, "2026-08-18"))
        ctx = macro.collect_macro_context("600176")
        inds = ctx["indicators"]

        d10 = inds["dgs10"]
        assert d10["value"] == 4.65
        assert d10["source"] == "akshare.bond_zh_us_rate"
        assert "较 FRED 新约 1 个交易日" in d10["lag_note"]  # 来源差异方向标注
        assert inds["dgs30"]["value"] == 5.19
        assert inds["dgs30"]["signal"] == "高位"
        assert inds["t10y2y"]["value"] == 0.46
        assert inds["t10y2y"]["signal"] == "平坦"

        for key in ("dfii10", "t5yie", "dtwexbgs", "dcoilbrenteu", "dexchus"):
            assert inds[key] is None, f"{key} 无降级源应 None"
            assert key.upper() in ctx["failed_indicators"]
        assert inds["sovereign_gb10y"] is None
        assert "GB10Y" in ctx["failed_indicators"]
        assert inds["acm_tp10"]["value"] == 0.87  # ACM 无需 FRED key，独立可用
        assert ctx["status"] == "ok"


# ---------------------------------------------------------------------------
# akshare 降级源（bond_zh_us_rate）解析
# ---------------------------------------------------------------------------

class TestUsCurveAkshare:
    def test_parses_latest_complete_row(self, monkeypatch):
        """表为升序（实测）：从尾行向上找含 US 10Y 的最近完整行（跳过 NaN 行）；
        T10Y2Y 由 Python 计算（浮点差用 approx 断言）。"""
        import pandas as pd

        df = pd.DataFrame({
            "日期": ["2026-08-18", "2026-08-19", "2026-08-20"],
            "美国国债收益率2年": [4.15, 4.19, None],
            "美国国债收益率10年": [4.63, 4.65, None],
            "美国国债收益率30年": [5.17, 5.19, None],
            "美国国债收益率10年-2年": [0.48, 0.46, None],
        })
        monkeypatch.setattr("akshare.bond_zh_us_rate", lambda: df)
        out = macro._fetch_us_curve_akshare()
        assert out["dgs10"] == 4.65
        assert out["dgs30"] == 5.19
        assert out["t10y2y"] == pytest.approx(0.46)

    def test_empty_table_returns_empty(self, monkeypatch):
        import pandas as pd

        monkeypatch.setattr("akshare.bond_zh_us_rate", lambda: pd.DataFrame())
        assert macro._fetch_us_curve_akshare() == {}


# ---------------------------------------------------------------------------
# ACMTP10（NY Fed）：xls 解析 / csv 降级 / 双失败
# ---------------------------------------------------------------------------

_ACM_CSV_TEXT = (
    "RunDates,TERMYld,ACMFITYld,GSWYld\n"
    "30-Jun-2026,0.512275505289223,4.47713321495679,4.49095998278098\n"
    "31-Jul-2026,0.837552069026338,4.82299845150996,4.83483964203813\n"
)


class _FakeResp:
    def __init__(self, content=None, text=None, status=200):
        self.content = content
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")


class _FakeSession:
    """记录调用顺序；xls URL → xls_resp，其余 → csv_resp。"""

    def __init__(self, xls_resp, csv_resp):
        self._xls = xls_resp
        self._csv = csv_resp
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self._xls if "ACMTermPremium.xls" in url else self._csv


class TestACMTP10:
    def test_parse_csv_success(self):
        v, as_of = macro._acm_parse_csv(_ACM_CSV_TEXT)
        assert v == pytest.approx(0.837552069026338)
        assert as_of == "2026-07-31"

    def test_parse_csv_garbage(self):
        assert macro._acm_parse_csv("not,a,csv\n") == (None, None)

    def test_parse_xls_garbage_bytes(self):
        assert macro._acm_parse_xls(b"this is not an xls file") == (None, None)

    def test_xls_fail_then_csv_fallback(self):
        """主路径（xls）失败 → csv 降级路径生效，两 URL 按序请求。"""
        sess = _FakeSession(
            _FakeResp(content=b"garbage"),
            _FakeResp(text=_ACM_CSV_TEXT),
        )
        v, as_of = macro._fetch_acm_term_premia(_session=sess)
        assert v == pytest.approx(0.837552069026338)
        assert as_of == "2026-07-31"
        assert len(sess.calls) == 2
        assert "ACMTermPremium.xls" in sess.calls[0]
        assert "acmPlot_data.csv" in sess.calls[1]

    def test_xls_http_error_then_csv(self):
        sess = _FakeSession(
            _FakeResp(status=404),
            _FakeResp(text=_ACM_CSV_TEXT),
        )
        v, as_of = macro._fetch_acm_term_premia(_session=sess)
        assert v == pytest.approx(0.837552069026338)

    def test_both_paths_fail(self):
        sess = _FakeSession(_FakeResp(content=b"garbage"), _FakeResp(status=500))
        assert macro._fetch_acm_term_premia(_session=sess) == (None, None)


# ---------------------------------------------------------------------------
# 缓存包裹（DataCache）
# ---------------------------------------------------------------------------

class TestFetchFredSeriesCached:
    def test_second_call_hits_cache(self, monkeypatch):
        calls: list[str] = []

        def fake_fetch(series_id, config, lookback_days=90):
            calls.append(series_id)
            return 5.28, [("2026-08-18", 5.28)]

        monkeypatch.setattr(macro, "_fetch_fred_series", fake_fetch)
        cfg = _mock_config()
        v1, s1 = macro._fetch_fred_series_cached("DGS30", cfg, 90, 3600)
        v2, s2 = macro._fetch_fred_series_cached("DGS30", cfg, 90, 3600)
        assert v1 == v2 == 5.28
        assert s1 == s2 == [("2026-08-18", 5.28)]
        assert calls == ["DGS30"]  # D13: 计数验证第二次确实命中缓存

    def test_empty_result_not_cached(self, monkeypatch):
        """D6: 空结果不缓存 → 两次都回源。"""
        calls: list[str] = []

        def fake_fetch(series_id, config, lookback_days=90):
            calls.append(series_id)
            return None, []

        monkeypatch.setattr(macro, "_fetch_fred_series", fake_fetch)
        cfg = _mock_config()
        assert macro._fetch_fred_series_cached("DGS30", cfg, 90, 3600) == (None, [])
        assert macro._fetch_fred_series_cached("DGS30", cfg, 90, 3600) == (None, [])
        assert calls == ["DGS30", "DGS30"]


# ---------------------------------------------------------------------------
# macro_signal_label：新指标进标签、格式不破坏、月频不进标签
# ---------------------------------------------------------------------------

class TestLabelE2:
    def test_new_global_indicators_in_label(self):
        label = macro.macro_signal_label({
            "indicators": {
                "pmi": {"value": 50.2},
                "vix": {"value": 18.5, "signal": "正常"},
                "sox": {"value": 6850.0},
                "dgs10": {"value": 4.65, "signal": "高位"},
                "dgs30": {"value": 5.28, "signal": "高位"},
                "dfii10": {"value": 2.05, "signal": "高实际利率"},
                "t10y2y": {"value": -0.32, "signal": "倒挂"},
                "t5yie": {"value": 2.43, "signal": "正常"},
                "dtwexbgs": {"value": 96.4, "signal": ""},
                "dcoilbrenteu": {"value": 85.2, "signal": "中性"},
                "dexchus": {"value": 7.12, "signal": "正常"},
                "acm_tp10": {"value": 0.87, "signal": "正常"},
            },
        })
        # 既有格式前缀不破坏
        assert "PMI 50.2" in label
        assert "VIX 18.5 正常" in label
        assert "SOX 6,850" in label
        assert "|" in label
        # 新指标 + 非基线信号
        assert "美10Y 4.65% 高位" in label
        assert "美30Y 5.28% 高位" in label
        assert "实际利率 2.05% 高实际利率" in label
        assert "期限利差 -0.32% 倒挂" in label
        assert "5Y盈亏 2.43%" in label
        assert "美元指数 96.4" in label
        assert "布油 85.2" in label
        assert "USDCNY 7.12" in label
        assert "ACM10Y 0.87" in label
        # 基线信号不显示（避免标签冗长）
        assert "5Y盈亏 2.43% 正常" not in label
        assert "布油 85.2 中性" not in label
        assert "USDCNY 7.12 正常" not in label
        assert "ACM10Y 0.87 正常" not in label

    def test_monthly_sovereign_not_in_label(self):
        """英德法日 10Y 月频（滞后 2.5 个月）不进一行标签，属 E3 C6 块。"""
        label = macro.macro_signal_label({
            "indicators": {
                "sovereign_gb10y": {"value": 4.8},
                "sovereign_jp10y": {"value": 2.67},
            },
        })
        assert label == "宏观数据不可得"

    def test_negative_value_zero_ok(self):
        """T10Y2Y = 0.0 时仍显示（D1: 0.0 合法值不被 or 吞掉）。"""
        label = macro.macro_signal_label({
            "indicators": {
                "t10y2y": {"value": 0.0, "signal": "平坦"},
            },
        })
        assert "期限利差 0.00%" in label


# ---------------------------------------------------------------------------
# E3: 序列消费（VIX / PMI / DGS30 / C6 主权债）
# ---------------------------------------------------------------------------

def _hist_rows(days: int, vix_start: float = 10.0, vix_step: float = 2.0,
               start: date = date(2026, 7, 1), pmi: float | None = None) -> list[dict]:
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        rows.append({
            "date": d.strftime("%Y%m%d"),
            "vix": vix_start + vix_step * i,
            "pmi": pmi,
        })
    return rows


class TestVix20d:
    def test_change_ok(self):
        rows = _hist_rows(21)
        out = macro._vix_20d_change(rows)
        assert out["status"] == "ok"
        assert out["change"] == 40.0
        assert out["earlier"] == 10.0
        assert out["latest"] == 50.0
        assert out["coverage"] == "20 个采集日（2026-07-01 → 2026-07-21）"

    def test_zero_value_not_swallowed(self):
        """D1: vix=0.0 是合法值，参与计算不被跳过。"""
        rows = [{"date": "20260701", "vix": 0.0}]
        for i in range(20):
            d = date(2026, 7, 2) + timedelta(days=i)
            rows.append({"date": d.strftime("%Y%m%d"), "vix": float(i + 1)})
        out = macro._vix_20d_change(rows)
        assert out["status"] == "ok"
        assert out["earlier"] == 0.0
        assert out["change"] == 20.0

    def test_none_rows_skipped(self):
        rows = []
        for i in range(25):
            d = date(2026, 7, 1) + timedelta(days=i)
            rows.append({"date": d.strftime("%Y%m%d"), "vix": None if i % 2 else 10.0 + i})
        out = macro._vix_20d_change(rows)
        assert out["status"] == "insufficient"
        assert out["note"] == "样本不足 21 期（当前 13 期）"

    def test_insufficient_fails_loud(self):
        rows = [{"date": "20260701", "vix": 12.0}, {"date": "20260702", "vix": 12.5}]
        out = macro._vix_20d_change(rows)
        assert out["status"] == "insufficient"
        assert out["note"] == "样本不足 21 期（当前 2 期）"


class TestPmiDirection:
    def test_consecutive_contraction(self):
        rows = [
            {"date": f"2026{m:02d}01", "pmi": p}
            for m, p in [(1, 50.1), (2, 50.5), (3, 50.2), (4, 49.8), (5, 49.5), (6, 49.9), (7, 49.6)]
        ]
        out = macro._pmi_direction(rows)
        assert out == {
            "status": "ok",
            "direction": "收缩",
            "consecutive_months": 4,
            "as_of": "2026-07",
            "latest_value": 49.6,
            "coverage": "2026-04 → 2026-07（4 个月）",
        }

    def test_same_month_latest_wins(self):
        """同月多行快照 → 取最新值（YYYYMMDD 月份提取正确）。"""
        rows = [
            {"date": "20260705", "pmi": 49.6},
            {"date": "20260720", "pmi": 50.3},
            {"date": "20260601", "pmi": 49.8},
        ]
        out = macro._pmi_direction(rows)
        assert out["direction"] == "扩张"
        assert out["consecutive_months"] == 1
        assert out["as_of"] == "2026-07"
        assert out["coverage"] == "2026-07 → 2026-07（1 个月）"

    def test_insufficient(self):
        out = macro._pmi_direction([{"date": "20260701", "pmi": 50.1}])
        assert out["status"] == "insufficient"
        assert out["note"] == "样本不足 2 期（当前 1 期）"
        assert macro._pmi_direction([{"date": "20260701"}])["note"] == "样本不足（无 PMI 历史）"


def _dgs30_series(n: int = 300, head_max: int = 10) -> list[tuple[str, float]]:
    """250+ 观测：前 head_max 个 5.0（全序列高点），其余 4.0（当前值）。"""
    return [
        ((date(2001, 8, 27) + timedelta(days=i)).strftime("%Y-%m-%d"),
         5.0 if i < head_max else 4.0)
        for i in range(n)
    ]


class TestDgs30:
    def test_percentile_high_distance(self, monkeypatch):
        series = _dgs30_series()

        def fake(sid, config, lookback_days=90):
            return 4.0, series

        monkeypatch.setattr(macro, "_fetch_fred_series", fake)
        out = macro._dgs30_analysis(_mock_config())
        assert out["status"] == "ok"
        assert out["current"] == 4.0
        assert out["high"] == 5.0
        assert out["high_date"] == "2001-09-05"  # 末个 5.0（reversed 扫描）
        assert out["percentile"] == 96.7  # 290/300 ≤ 4.0 → round(96.667, 1)
        assert out["distance_from_high_pct"] == -20.0  # 收益率口径（C10）
        assert out["n_obs"] == 300
        assert out["coverage"].startswith("2001-08-27 →")

    def test_insufficient_fails_loud(self, monkeypatch):
        series = [(f"2026-{(i % 12) + 1:02d}-01", 4.0) for i in range(10)]
        monkeypatch.setattr(macro, "_fetch_fred_series", lambda *a, **k: (4.0, series))
        out = macro._dgs30_analysis(_mock_config())
        assert out["status"] == "insufficient"
        assert out["note"] == "样本不足 250 期（当前 10 期）"

    def test_no_series(self, monkeypatch):
        monkeypatch.setattr(macro, "_fetch_fred_series", lambda *a, **k: (None, []))
        out = macro._dgs30_analysis(_mock_config())
        assert out["note"] == "样本不足（DGS30 序列不可得）"


def _sovereign_series(months: int = 250, flat: float | None = None,
                      peak: float | None = None, peak_at: int | None = None,
                      current: float | None = None) -> list[tuple[str, float]]:
    """月频序列：默认 3.0；peak_at 位置置 peak；末值可覆盖（current）。

    250 个月 = 2000-01 .. 2020-10；20 年窗口 = 后 241 个观测（2000-09 起）。"""
    series: list[tuple[str, float]] = []
    for i in range(months):
        y = 2000 + i // 12
        m = (i % 12) + 1
        d = date(y, m, 1).strftime("%Y-%m-%d")
        v = flat if flat is not None else 3.0
        if peak is not None and i == peak_at:
            v = peak
        series.append((d, v))
    if current is not None:
        series[-1] = (series[-1][0], current)
    return series


class TestSovereignC6:
    def test_verdict_matches_c6_anchors(self, monkeypatch):
        """GB 峰值 2007-05（20 年窗口内）→ 非新高；JP 当前=窗口最高 → 创 20 年新高；
        DE 峰值在窗口外（2000-09 前）→ 当前高于窗口最高 → 仍计 20 年新高。"""
        mocks = {
            "IRLTLT01GBM156N": _sovereign_series(peak=5.4321, peak_at=88, current=4.796),
            "IRLTLT01DEM156N": _sovereign_series(peak=6.0, peak_at=8, current=5.5),
            "IRLTLT01FRM156N": _sovereign_series(peak=4.73, peak_at=100, current=3.68),
            "IRLTLT01JPM156N": _sovereign_series(flat=2.67),
        }

        def fake(sid, config, lookback_days=90):
            s = mocks[sid]
            return s[-1][1], s

        monkeypatch.setattr(macro, "_fetch_fred_series", fake)
        out = macro._sovereign_analysis(_mock_config())
        assert out["status"] == "ok"
        assert out["verdict"] == "4 项中 2 项创 20 年新高"
        assert out["new_high_count"] == 2
        assert out["as_of"] == "2020-10"
        assert "滞后约" in out["lag_note"]
        assert out["insufficient"] == []

        gb = out["countries"]["gb"]
        assert gb["current"] == 4.796
        assert gb["high_20y"] == 5.4321
        assert gb["high_20y_date"] == "2007-05-01"
        assert gb["high"] == 5.4321
        assert gb["high_date"] == "2007-05-01"
        assert gb["new_20y_high"] is False

        de = out["countries"]["de"]
        assert de["new_20y_high"] is True  # 旧峰值在 20 年窗口外
        assert de["high"] == 6.0
        assert de["high_date"] == "2000-09-01"

        jp = out["countries"]["jp"]
        assert jp["new_20y_high"] is True
        assert jp["high"] == 2.67
        assert jp["high_date"] == "2020-10-01"

    def test_all_insufficient_fails_loud(self, monkeypatch):
        short = _sovereign_series(months=100)
        monkeypatch.setattr(macro, "_fetch_fred_series", lambda sid, config, lookback_days=90: (short[-1][1], short))
        out = macro._sovereign_analysis(_mock_config())
        assert out["status"] == "all_insufficient"
        assert "样本不足" in out["verdict"]
        assert out["countries"]["gb"]["note"] == "样本不足 241 期（当前 100 期）"
        assert set(out["insufficient"]) == {"GB", "DE", "FR", "JP"}

    def test_partial_annotated(self, monkeypatch):
        mocks = {
            "IRLTLT01GBM156N": _sovereign_series(months=100),
            "IRLTLT01DEM156N": _sovereign_series(months=100),
            "IRLTLT01FRM156N": _sovereign_series(months=100),
            "IRLTLT01JPM156N": _sovereign_series(flat=2.67),
        }

        def fake(sid, config, lookback_days=90):
            s = mocks[sid]
            return s[-1][1], s

        monkeypatch.setattr(macro, "_fetch_fred_series", fake)
        out = macro._sovereign_analysis(_mock_config())
        assert out["status"] == "partial"
        assert out["verdict"] == "4 项中 1 项创 20 年新高（3 项样本不足）"


class TestMacroTrendAnalysis:
    def test_consumes_history_and_fred(self, monkeypatch):
        """历史行注入 + FRED 序列 mock → 四个子项聚合结果与手工期望一致。"""
        # VIX: 21 个采集日（2026-07-01..07-21，15.0 + 0.1i → 20 日变化 +2.0）
        # PMI: 2026-03..07 月度（03/04 扩张，05/06/07 收缩 → 连续 3 个月收缩）
        rows = [
            {
                "date": (date(2026, 7, 1) + timedelta(days=i)).strftime("%Y%m%d"),
                "vix": 15.0 + 0.1 * i,
                "pmi": None,
            }
            for i in range(21)
        ]
        for m, p in [(3, 50.0), (4, 50.2), (5, 49.0), (6, 49.3), (7, 49.1)]:
            rows.append({"date": f"2026{m:02d}15", "vix": None, "pmi": p})
        mocks = _fred_daily_mock_values()
        mocks["DGS30"] = (4.0, _dgs30_series())
        for sid in ("IRLTLT01GBM156N", "IRLTLT01DEM156N", "IRLTLT01FRM156N"):
            mocks[sid] = (3.0, _sovereign_series(peak=4.5, peak_at=88, current=3.0))
        mocks["IRLTLT01JPM156N"] = (2.67, _sovereign_series(flat=2.67))

        def fake(sid, config, lookback_days=90):
            if sid in mocks:
                v, s = mocks[sid]
                return v, s
            return None, []

        monkeypatch.setattr(macro, "_fetch_fred_series", fake)
        trends = macro.macro_trend_analysis(history=rows, config=_mock_config())

        assert trends["vix_20d"]["status"] == "ok"
        assert trends["vix_20d"]["change"] == 2.0  # 17.0 - 15.0

        pmi = trends["pmi_direction"]
        assert pmi["status"] == "ok"
        assert pmi["direction"] == "收缩"
        assert pmi["consecutive_months"] == 3  # 2026-05/06/07
        assert pmi["as_of"] == "2026-07"
        assert pmi["coverage"] == "2026-05 → 2026-07（3 个月）"

        assert trends["dgs30"]["status"] == "ok"
        assert trends["dgs30"]["high"] == 5.0

        sov = trends["sovereign"]
        assert sov["verdict"] == "4 项中 1 项创 20 年新高"
        assert sov["countries"]["jp"]["new_20y_high"] is True

    def test_loads_store_when_history_none(self, monkeypatch):
        """history=None → 懒加载 store.load_macro_history（读路径首个生产消费方）。"""
        monkeypatch.setattr(macro, "_fetch_fred_series", lambda *a, **k: (None, []))
        monkeypatch.setattr(
            "lib.store.load_macro_history",
            lambda days=365: [{"date": "20260701", "vix": 15.0}],
        )
        trends = macro.macro_trend_analysis(config=_mock_config())
        assert trends["vix_20d"]["note"] == "样本不足 21 期（当前 1 期）"


class TestFormatMacroTrends:
    def test_lines_contain_verdict_and_staleness(self):
        trends = {
            "vix_20d": {"status": "insufficient", "note": "样本不足 21 期（当前 5 期）"},
            "pmi_direction": {
                "status": "ok", "direction": "扩张", "consecutive_months": 2,
                "as_of": "2026-07", "coverage": "2026-06 → 2026-07（2 个月）",
            },
            "dgs30": {
                "status": "ok", "current": 5.28, "percentile": 94.2,
                "distance_from_high_pct": -10.81, "high": 5.92,
                "high_date": "2002-04-01", "coverage": "2001-08-27 → 2026-08-18（6245 个观测）",
            },
            "sovereign": {
                "status": "ok", "verdict": "4 项中 1 项创 20 年新高",
                "lag_note": "月频，截至 2026-06，滞后约 2.5 个月",
                "countries": {
                    "jp": {
                        "status": "ok", "name": "日本", "current": 2.67,
                        "high_20y": 2.67, "high_20y_date": "2026-06-01",
                        "high": 2.67, "high_date": "2026-06-01",
                        "new_20y_high": True, "lag_note": "月频，截至 2026-06，滞后约 2.5 个月",
                    },
                },
            },
        }
        lines = macro.format_macro_trends(trends)
        text = "\n".join(lines)
        assert "样本不足 21 期" in text
        assert "PMI 连续 2 个月扩张" in text
        assert "DGS30 5.28%" in text and "收益率口径" in text
        assert "4 项中 1 项创 20 年新高" in text
        assert "滞后约 2.5 个月" in text
        assert "创 20 年新高" in text
