"""港股池数据层 — 离线单测（T11-5 / HK-4）。

本轮重点：`annualized_roe` 的**口径判据与三态剔除**。
⚠️ 实测（2026-09-13，12 只 × 9 期 = 108 行）该接口返回**年报行**（`DATE_TYPE_CODE=001`），
`ROE_AVG` 即**年度 ROE**——**不得按 `report_date` 日历后缀判中报**：6 月财年公司
（00016/00017/00083/00659）的年报正是 `06-30`，×2 会把真实年度 ROE 翻倍。
判据用报告期类型 → 期长 → 不年化；NaN/Inf 不剔除则分别造成闸门恒 False / 恒放行。
"""
from __future__ import annotations

import math

import sources_hk


def _row(**over):
    r = {"report_date": "2026-06-30", "roe": 8.0, "net_profit": 1.0e9}
    r.update(over)
    return r


def test_annualized_roe_interim_doubled():
    """中报（`DATE_TYPE_CODE=002`，期长约半年）→ ×2 年化。"""
    assert sources_hk.annualized_roe(
        _row(report_date="2026-06-30", period_start="2026-01-01", report_type="002",
             roe=8.0)) == 16.0


def test_annualized_roe_annual_report_unchanged():
    """年报（001，期长约 12 个月）已是年度口径 → 原值。"""
    assert sources_hk.annualized_roe(
        _row(report_date="2026-12-31", period_start="2026-01-01", report_type="001",
             roe=8.0)) == 8.0


def test_annualized_roe_june_fiscal_year_end_not_doubled():
    """⚠️ **6 月财年公司的年报 report_date 正是 `06-30`** → 不得按日历后缀当年化 ×2。

    实测（2026-09-13，本 skill 真机）：00016/00017/00083/00659 的 report_date 序列**只有
    `06-30`**（新鸿基地产等 6 月财年），且 108 行全为 `DATE_TYPE_CODE=001`（年报）。
    按后缀 ×2 会把真实年度 ROE（新地 3.42%）翻成 6.85%，从而**误过 8% 闸门**或被误计入。
    """
    row = _row(report_date="2026-06-30", period_start="2025-07-01", report_type="001",
               roe=3.423266)
    assert sources_hk.annualized_roe(row) == 3.423266


def test_annualized_roe_march_fiscal_year_end_not_doubled():
    """3 月财年（如 09988）同理：年报 report_date = `03-31`，不得按其推断中报。"""
    row = _row(report_date="2026-03-31", period_start="2025-04-01", report_type="001",
               roe=9.911678)
    assert sources_hk.annualized_roe(row) == 9.911678


def test_annualized_roe_unknown_period_not_annualized():
    """类型与期长都拿不到 → **不年化**（宁可保守，也不按日历猜口径）。"""
    assert sources_hk.annualized_roe(
        _row(report_date="2026-06-30", period_start="", report_type="", roe=8.0)) == 8.0


def test_annualized_roe_infers_from_period_span_without_type_code():
    """类型码缺失时用**报告期长度**推断（期长是最直接的判据，不依赖码表语义）。"""
    half = _row(report_date="2026-06-30", period_start="2026-01-01", report_type="",
                roe=8.0)
    full = _row(report_date="2026-06-30", period_start="2025-07-01", report_type="",
                roe=8.0)
    assert sources_hk.annualized_roe(half) == 16.0
    assert sources_hk.annualized_roe(full) == 8.0


def test_annualized_roe_rejects_none_nan_and_inf():
    """三态：None / 非数值 / NaN / ±Inf → None。

    ⚠️ Inf 必须剔除——`inf < 8.0` 为 False，会**直接通过质量闸门**（年化后仍是 inf）。
    """
    for bad in (None, "abc", float("nan"), float("inf"), float("-inf")):
        assert sources_hk.annualized_roe(_row(roe=bad)) is None, bad


def test_annualized_roe_missing_report_date_not_doubled():
    """报告期缺失/格式异常 → 不猜口径，按原值（宁可不年化，也不无依据放大）。"""
    for bad in (None, "", "2026-06"):
        assert sources_hk.annualized_roe(_row(report_date=bad, roe=8.0)) == 8.0


def test_annualized_roe_non_dict_returns_none():
    assert sources_hk.annualized_roe(None) is None
    assert sources_hk.annualized_roe(["x"]) is None


def test_lens_table_caliber_matches_implementation():
    """可用性表与实现同口径（契约不实检查：表称「年度 ROE」则代码不得对年报 ×2）。"""
    row = [r for r in sources_hk.lens_availability() if r["lens"].startswith("质量门 ROE")]
    assert row, "透镜表须含 ROE 质量门"
    assert "年化" in row[0]["status"]
    # 年报（001）原值；中报（002）才 ×2 —— 表里声明什么，代码就得做什么
    annual = _row(report_date="2026-06-30", period_start="2025-07-01", report_type="001",
                  roe=8.0)
    interim = _row(report_date="2026-06-30", period_start="2026-01-01", report_type="002",
                   roe=8.0)
    assert math.isclose(sources_hk.annualized_roe(annual), 8.0)
    assert math.isclose(sources_hk.annualized_roe(interim), 16.0)


def test_universe_uses_the_process_singleton_client(monkeypatch):
    """`hk_basic` 取数须走 `sources.client()` 单例，不得另建实例。

    ⚠️ `TushareClient()` 每次新建 = 实例级限流器清零（`_call_timestamps`/`_daily_calls`）
    → 全速突发；这正是 R4 评审实测的「连发空返回、单发正常」根因。
    """
    import sys
    import types

    import sources

    calls = []

    class _FakeClient:
        def query(self, api, **kw):
            calls.append(api)
            import pandas as pd
            return pd.DataFrame([
                {"ts_code": "00700.HK", "name": "腾讯控股", "market": "主板",
                 "curr_type": "HKD", "list_status": "L"},
            ])

    monkeypatch.setattr(sources, "client", lambda: _FakeClient(), raising=False)
    # 若实现里出现直接实例化，则该假模块会被导入并记录到
    fake_mod = types.ModuleType("lib.tushare_client")

    class _BoomClient:
        def __init__(self):
            raise AssertionError("不得直接实例化 TushareClient——须用 sources.client() 单例")

    fake_mod.TushareClient = _BoomClient
    monkeypatch.setitem(sys.modules, "lib.tushare_client", fake_mod)
    # 缓存层也走假模块（`from cache import DataCache` 在函数内解析）
    cache_mod = types.ModuleType("cache")
    cache_mod.DataCache = _NullCache
    monkeypatch.setitem(sys.modules, "cache", cache_mod)

    rows = sources_hk.fetch_hk_universe()
    assert rows and rows[0]["symbol"] == "00700"
    assert calls == ["hk_basic"]


class _NullCache:
    def get(self, *a, **k):
        return None

    def set(self, *a, **k):
        return None


# ── 实测调用计数（轮末评审修复 2026-09-13）──────────────────────────────────

def test_financials_call_counter_and_empty_returns(monkeypatch):
    """逐只财务调用须 +1；空返回另计（与「标的真的无数据」在计数上分开看）。"""
    import types

    class _FakeFin:
        @staticmethod
        def fetch_financials(sym):
            return [] if sym == "X" else [{"roe": 1.0}]

    monkeypatch.setattr(sources_hk, "_load_hk_module", lambda name: _FakeFin)
    sources_hk.reset_warnings()
    assert sources_hk.fetch_hk_financials("X") == []
    assert sources_hk.fetch_hk_financials("Y") == [{"roe": 1.0}]
    assert sources_hk.CALL_COUNT["hk_financials"] == 2
    assert sources_hk.EMPTY_RETURN_COUNT == 1


def test_quotebatch_counts_each_chunk(monkeypatch):
    """r_hk 是**分批**调用——计数须按批，不是按只。"""
    import types

    class _FakeQuote:
        @staticmethod
        def parse_tencent_hk(seg):
            raise ValueError("解析失败 → 不产出数据，但调用本身已计")

    monkeypatch.setattr(sources_hk, "_load_hk_module", lambda name: _FakeQuote)
    sources_hk.reset_warnings()
    syms = [f"{i:05d}" for i in range(250)]           # 250 只 / BATCH_SIZE 100 → 3 批
    sources_hk.fetch_hk_quote_batch(syms)
    assert sources_hk.CALL_COUNT["r_hk"] == 3


def test_reset_warnings_zeroes_counters():
    sources_hk.CALL_COUNT["hk_basic"] = 5
    sources_hk.EMPTY_RETURN_COUNT = 9
    sources_hk.reset_warnings()
    assert sources_hk.CALL_COUNT["hk_basic"] == 0
    assert sources_hk.EMPTY_RETURN_COUNT == 0
