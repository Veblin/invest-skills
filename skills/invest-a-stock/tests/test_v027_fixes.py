"""v0.2.7 code-review 遗留缺陷回归测试（batch-review-v0.2.7-2026-08-23.md）。

覆盖：P1-1 季度营收同比改单季口径、P1-2 DCF β 降级日志去 traceback、
P2-6 store kline 升序落库（追加维护，对齐 test_v026_p0_fixes.py 惯例）。
"""
from __future__ import annotations

import pytest

from lib.render_risk import (
    _revenue_single_q_yoy_from_rows,
    _section_left_right_probability,
    _single_quarter_revenue,
)
from lib.render_utils import _index_dims
from lib.render_markdown._v2 import _header_v2
from lib.render_markdown._v3 import _section_4_header_mda, _section_dynamic_drivers
from lib.render_html import render_html

# 600176 复现值（batch-review P1-1）：20260630 累计 111.59 / 20260331 累计
# 52.82 → Q2 单季 58.77；正确值 Q2 单季同比 +26.9%（原实现误报 +111.3%）。
_Q2_ROWS = [
    {"end_date": "20250331", "revenue": 40.0},
    {"end_date": "20250630", "revenue": 86.31},   # 去年 Q2 单季 = 46.31
    {"end_date": "20260331", "revenue": 52.82},
    {"end_date": "20260630", "revenue": 111.59},  # 当期 Q2 单季 = 58.77
]

_MS_EMPTY = {"erp": {}, "sw_index": {}, "moneyflow": {}, "northbound": {}}


# --- P1-1 单季营收同比 ---

def test_single_q_yoy_q2_matches_batch_review():
    """Q2 四行构造 → 单季同比 ≈ +26.9%（batch-review 正确值口径）。"""
    yoy = _revenue_single_q_yoy_from_rows(_Q2_ROWS)
    assert yoy is not None
    assert yoy == pytest.approx(26.9, abs=0.1)


def test_single_q_yoy_missing_rows_returns_none():
    """缺去年同期行 → None（不误报）。"""
    rows = _Q2_ROWS[:2]  # 仅 2026 两期
    assert _revenue_single_q_yoy_from_rows(rows) is None


def test_single_q_yoy_q1_two_rows_only():
    """Q1 只需当期 + 去年同期两行。"""
    rows = [
        {"end_date": "20250331", "revenue": 30.0},
        {"end_date": "20260331", "revenue": 40.0},
    ]
    yoy = _revenue_single_q_yoy_from_rows(rows)
    assert yoy == pytest.approx(33.33, abs=0.1)


def test_single_q_yoy_non_quarter_end_skipped():
    """非季度末报告期（如 20260228）→ None。"""
    rows = [{"end_date": "20260228", "revenue": 10.0}] + _Q2_ROWS
    yoy = _revenue_single_q_yoy_from_rows(rows)
    # 最新报告期为 20260630（升序后最末），不受 20260228 影响
    assert yoy == pytest.approx(26.9, abs=0.1)
    assert _single_quarter_revenue(_Q2_ROWS, "20260228") is None


def _render_left_right(fin_rows: list[dict]) -> str:
    dims = {"financials": {"data": fin_rows}}
    # 资金流命中「主力资金/北向近10日净流入」，与财务信号凑满 2/3 组合
    ms = {"erp": {}, "sw_index": {}, "moneyflow": {"net_sum_10d": 123.0}, "northbound": {}}
    return _section_left_right_probability({}, "600176", dims, ms)


def test_single_q_yoy_below_threshold_not_appended():
    """+80% 单季同比不超 100% 阈值 → 信号不出现（原实现 +111.3% 误触发）。"""
    rows = [
        {"end_date": "20250331", "revenue": 90.0},
        {"end_date": "20250630", "revenue": 145.6},  # 去年 Q2 单季 55.6
        {"end_date": "20260331", "revenue": 100.0},
        {"end_date": "20260630", "revenue": 200.0},  # 当期 Q2 单季 100 → +79.9%
    ]
    out = _render_left_right(rows)
    assert "季度营收同比" not in out
    assert "111.3" not in out


def test_single_q_yoy_over_threshold_renders():
    """单季同比 >100% → 按原格式渲染。"""
    rows = [
        {"end_date": "20250331", "revenue": 90.0},
        {"end_date": "20250630", "revenue": 130.0},  # 去年 Q2 单季 40
        {"end_date": "20260331", "revenue": 100.0},
        {"end_date": "20260630", "revenue": 200.0},  # 当期 Q2 单季 100 → +150.0%
    ]
    out = _render_left_right(rows)
    assert "季度营收同比 +150.0%（>100%）" in out


def test_single_q_yoy_missing_rows_render_silent():
    """数据不足时渲染侧不报错、不出现信号。"""
    out = _render_left_right(_Q2_ROWS[:2])
    assert "季度营收同比" not in out


# --- P1-2 DCF β 降级日志去 traceback ---

def _dcf_kline(n: int = 120) -> list[dict]:
    """构造 n 个连续交易日的升序 kline。"""
    return [
        {"trade_date": f"2025{1000 + i:04d}", "close": 10.0 + i * 0.1}
        for i in range(1, n + 1)
    ]


def test_dcf_beta_degradation_logs_single_line(monkeypatch, caplog):
    """东财不可达 → 降级 β=1.0，降级日志单行无 traceback（exc_info=None）。"""
    from lib import collector
    from lib.render_dcf import _dcf_compute_beta

    def boom(*args, **kwargs):
        raise ConnectionError("proxy unreachable: push2.eastmoney.com")

    monkeypatch.setattr(collector, "_akshare_hs300_dated_closes", boom)

    with caplog.at_level("WARNING"):
        result = _dcf_compute_beta(_dcf_kline())

    assert result["is_default"] is True
    assert result["beta"] == 1.0
    warnings = [r for r in caplog.records if r.levelno >= 30]
    assert any("沪深300" in r.getMessage() for r in warnings)
    # 降级日志不得携带完整 traceback（唯一 exc_info=True 已移除）
    assert all(r.exc_info is None for r in warnings)


# --- P2-2 采集时间 UTC → 北京时间渲染 ---

def test_header_v2_beijing_time():
    """报告头采集时间转北京时间并标注（对齐 ETF 报告规范）。"""
    from test_v013_phase4 import _phase4_collection

    c = _phase4_collection("600176", "2026-08-22T17:10:41+00:00")
    out = _header_v2(c, "600176")
    assert "采集时间: 2026-08-23 01:10 (北京时间)" in out
    assert "T17:10:41" not in out


def test_html_header_beijing_time():
    from test_v013_phase4 import _phase4_collection

    c = _phase4_collection("600176", "2026-08-22T17:10:41+00:00")
    out = render_html(c, "600176")
    assert "2026-08-23 01:10 (北京时间)" in out
    assert "T17:10:41" not in out


def test_dynamic_drivers_collection_date_beijing():
    """模块 2 动态驱动的采集日期取北京日期（确定性，无挂钟依赖）。"""
    from test_v013_phase4 import _phase4_collection

    c = _phase4_collection("600176", "2026-08-01T00:00:00Z")
    out = _section_dynamic_drivers(c, "600176", _index_dims(c), {})
    assert "（采集: 2026-08-01）" in out


def test_mda_generated_at_beijing_date():
    """MDA 生成时间（analysis_templates UTC 产出）渲染侧转北京日期。"""
    c = {"_meta": {"analysis_cards": {"mda_narrative": {
        "generated_at": "2026-08-22T17:10:41+00:00",
        "revenue_growth_yoy": None,
    }}}}
    out = "\n".join(_section_4_header_mda(c))
    assert "生成时间: 2026-08-23" in out


# --- P2-3 store kline 升序落库 ---

_DESC_KLINE = [
    {"trade_date": "20260821", "close": 20.0},
    {"trade_date": "20260720", "close": 19.0},
    {"trade_date": "20250721", "close": 10.0},  # Tushare 降序（最新在前）
]


def _kline_env_off(monkeypatch):
    """禁用 kline 非 tushare 源，仅保留首选源 tushare.daily。"""
    import lib.collector._orchestrate as orch

    monkeypatch.setattr(orch.env, "is_tushare_available", lambda cfg: True)
    monkeypatch.setattr(orch.env, "baostock_kline_enabled", lambda: False)
    monkeypatch.setattr(orch.env, "tickflow_kline_enabled", lambda: False)
    monkeypatch.setattr(orch.env, "is_akshare_available", lambda: False)


def test_collect_kline_sorts_desc_source(monkeypatch):
    """Tushare 降序来源 → collect_kline 输出升序（data[-1]=最新）。"""
    import lib.collector._kline_cache as kc
    from lib.collector import _orchestrate as orch

    _kline_env_off(monkeypatch)
    monkeypatch.setattr(kc, "load_or_fetch", lambda *a, **k: _DESC_KLINE)

    result = orch.collect_kline("600176")
    dates = [r["trade_date"] for r in result["data"]]
    assert dates == sorted(dates)
    assert dates[-1] == "20260821"  # data[-1] = 最新


def test_collect_kline_cache_hit_also_sorted(monkeypatch):
    """缓存命中（同日重复采集）同样经 postprocess 归一为升序。"""
    import lib.collector._kline_cache as kc
    from lib.collector import _orchestrate as orch

    _kline_env_off(monkeypatch)
    # 缓存命中语义：load_or_fetch 直接返回降序数据，不调用 fetch
    monkeypatch.setattr(kc, "load_or_fetch", lambda *a, **k: _DESC_KLINE)

    result = orch.collect_kline("600176")
    dates = [r["trade_date"] for r in result["data"]]
    assert dates == sorted(dates)
    assert dates[-1] == "20260821"
