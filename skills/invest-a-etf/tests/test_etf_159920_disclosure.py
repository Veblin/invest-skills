"""A5/A6：159920 原文登记（身份/费率/官方基准对比/结构补充）与跟踪币种闸门（离线）。

登记来源：基金 2026 年中期报告（物理 PDF 56 页）与管理人官网产品/费率页，
2026-10-10 实际读取；sha256/页码定位随条目冻结。本测试不联网：身份/费率/
结构为登记表查询，tracking 币种闸门在取数之前返回。
"""

from __future__ import annotations

import pytest

from etf_data import (
    ETF_BENCHMARK_PERFORMANCE,
    ETF_FEE_SCHEDULE,
    ETF_IDENTITY,
    compute_etf_tracking,
    query_etf_disclosed_structure,
    query_etf_fees,
    query_etf_identity,
    resolve_tracked_index,
)

_PDF_SHA = "c207dd9f5331be91269c5bfb23005064b45d43cea9e10b3c1e6aaf2925458210"


# ---------------------------------------------------------------------------
# 身份（A5）
# ---------------------------------------------------------------------------

def test_identity_verified_original_with_page_locators():
    ident = query_etf_identity("159920", category="cross_border")
    assert ident["verification"]["status"] == "verified_original"
    assert ident["fund_name"] == "恒生交易型开放式指数证券投资基金"
    assert ident["axes"] == {"asset_type": "stock", "channel": "cross_border",
                             "strategy": "broad_market"}
    assert ident["tracked_index"]["name"] == "恒生指数"
    assert ident["tracked_index"]["code"] == "HSI"
    assert ident["tracked_index"]["currency"] == "HKD"
    # 证据三件套齐（URL + sha256 + 物理页码定位），且非「未读即已核」
    for ev in ident["evidence"]:
        assert ev["url"] and ev["sha256"] and ev["locator"] and ev["read_at"]
        assert "物理 PDF 第" in ev["locator"] or "官网" in ev["locator"]
    assert any(ev["sha256"] == _PDF_SHA for ev in ident["evidence"])
    # 业绩比较基准 = 经人民币汇率调整的恒生指数收益率（原文 §2.2）
    assert "经人民币汇率调整" in ETF_IDENTITY["159920"]["tracked_index"]["note"]


def test_resolve_tracked_index_returns_benchmark_currency():
    res = resolve_tracked_index("159920")
    assert res["code"] == "HSI"
    assert res["currency"] == "HKD"
    assert "identity.tracked_index" in res["source"]


# ---------------------------------------------------------------------------
# 费率（A5）：版本已读 ≠ 报告日现行已核
# ---------------------------------------------------------------------------

def test_fees_registered_but_current_not_verified():
    fees = query_etf_fees("159920")
    assert fees["status"] == "ok"
    rates = {i["name"]: i["annual_rate_pct"] for i in fees["items"]}
    assert rates["管理费"] == 0.6 and rates["托管费"] == 0.15
    assert rates["基金运作综合费率（年化）"] == 0.76
    assert fees["current_rate_verified"] is False      # 2026-09 后调整公告未全量核对
    assert "报告日现行费率未核" in fees["note"] or "不签 True" in fees["note"]
    assert "rate_as_of" in str(ETF_FEE_SCHEDULE["159920"]["items"][0])
    src = ETF_FEE_SCHEDULE["159920"]["source"]
    # 主源 = 2026-07-20 更新招募说明书（报告日前最新法律文件），四方同值定位齐
    assert src["sha256"] == "a2e2b4855aaf118b35e4edeb26c1cccc72d4fa9e5c75894a16a57cb152337235"
    assert "物理 PDF 第 49 页" in src["locator"]
    assert "2026-05-29" in src["locator"]


# ---------------------------------------------------------------------------
# 官方基准对比（A6）：优先于两腿自算；②－④ 不是 TE
# ---------------------------------------------------------------------------

def test_official_benchmark_comparison_registered():
    reg = ETF_BENCHMARK_PERFORMANCE["159920"]
    six_m = [p for p in reg["periods"] if p["period"] == "过去六个月"][0]
    assert (six_m["fund_pct"], six_m["benchmark_pct"], six_m["diff_pct"]) == \
        (-13.29, -14.15, 0.86)
    assert reg["benchmark_text"] == "经人民币汇率调整的恒生指数收益率"
    assert reg["source"]["sha256"] == _PDF_SHA
    assert "物理 PDF 第 7 页" in reg["source"]["locator"]
    # ②－④ = 标准差之差，明示不是 TE
    assert "不是跟踪误差" in reg["note"]


def test_tracking_currency_gate_stops_td_te_before_fetch():
    """159920 基准为 HKD 恒指：两腿币种不一致 → limited，不确认 TD/TE、
    不输出跨币种价格偏离；官方披露对比随块返回；两腿未取数（skipped 留痕）。"""
    tr = compute_etf_tracking("159920")
    assert tr["status"] == "limited"
    assert tr["td_pct"] is None and tr["te_pct_annualized"] is None
    assert tr["price_benchmark_deviation"] is None
    assert "币种" in tr["note"] and "未发起" in str(tr["skipped_sources"])
    assert tr["caliber"]["currency"] == "CNY"
    assert tr["caliber"]["benchmark_currency"] == "HKD"
    oc = tr["official_benchmark_comparison"]
    assert oc and oc["source"]["sha256"] == _PDF_SHA
    assert any(p["period"] == "过去六个月" for p in oc["periods"])


def test_data_status_tracking_available_with_disclosure():
    """limited + 官方披露对比 → 维度五态为 available（有可用证据），
    producer_status 保留 limited（两腿自算受限）。"""
    from etf_data import build_data_status

    tr = compute_etf_tracking("159920")
    ds = build_data_status(identity=query_etf_identity("159920", category="cross_border"),
                           profile={}, quote={}, kline={}, share_history={},
                           tracking=tr, fees=query_etf_fees("159920"))
    dim = ds["dimensions"]["tracking"]
    assert dim["status"] == "available"
    assert dim["producer_status"] == "limited"
    assert "原文披露对比" in dim["note"]


# ---------------------------------------------------------------------------
# 结构补充（A5）：分母分开、静态披露标注
# ---------------------------------------------------------------------------

def test_disclosed_structure_denominators_and_static_note():
    st = query_etf_disclosed_structure("159920")
    assert st and st["as_of"] == "2026-06-30"
    assert st["asset_mix_pct_of_total_assets"]["equity"] == 91.81     # 分母=总资产
    assert st["equity_pct_of_nav"] == 93.05                           # 分母=资产净值
    assert st["industry_total_pct_of_nav"] == 93.05
    assert sum(i["pct"] for i in st["industry_gics_pct_of_nav"]) == pytest.approx(93.05, abs=0.01)
    assert st["equity_holdings_detail"]["n_holdings"] == 93           # 原文全量明细
    assert "第 39–48 页" in st["equity_holdings_detail"]["locator"]
    assert st["derivatives"]["index_futures_notional_cny"] == 629586500.71
    assert st["source"]["sha256"] == _PDF_SHA
    # 不把行业合计余额当现金；期末静态披露 ≠ 报告日现行
    assert "不是现金" in st["denominators_note"]
    assert "非报告日现行全核" in st["note"]
    assert "不等于报告日现行持仓" in st["equity_holdings_detail"]["note"]


def test_disclosed_structure_unregistered_returns_none():
    assert query_etf_disclosed_structure("510300") is None
    assert query_etf_disclosed_structure("999999") is None


def test_non_csindex_tracked_index_does_not_fetch_csindex_pe(monkeypatch):
    """A5 修正：身份登记的非 csindex 代码（HSI）不发起 csindex PE 取数，
    状态记 not_mapped（未接线，非源失败）——不得显示为「已映射但缺失」。"""
    from unittest.mock import patch

    import etf_data

    calls = {"n": 0}

    def _boom(idx_code):
        calls["n"] += 1
        raise AssertionError(f"不得对非 csindex 代码发起 PE 取数: {idx_code}")

    with patch("etf_data._fetch_csindex_pe", _boom), \
         patch("etf_data._lookup_etf_spot_row", return_value=(None, "skip")), \
         patch("etf_data._attach_industry_pe"), \
         patch("etf_data._attach_industry_allocation"), \
         patch("etf_data._attach_valuation_guide"):
        profile = etf_data.query_etf_data("159920")
    assert calls["n"] == 0
    assert profile["index_code"] == "HSI"
    assert profile["index_pe_status"] == "not_mapped"
    assert "不属于 csindex" in profile["index_pe_note"]
    assert "非源失败" in profile["index_pe_note"]
