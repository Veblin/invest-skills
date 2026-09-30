"""价格基准标注回归：盘中价与「日线截至」必须同时可读，缺失时不得编造基准。

背景（2026-09-30 实测 600519）：同一次采集里 `quote` 维度的 `data.price` 是**盘中
实时价**，而估值分位与技术指标的日线序列截至**前一交易日**收盘 —— 两者时间基准
不同，报告却不加区分。渲染层已在既有「最新价」行内附加来源、取数时点与日线截止
日（`render_markdown/_v3.py::_section_snapshot` 的 `bar_note`）。

本文件锁定该行为的三条边界：
1. 盘中价行同时带出来源、取数时点、日线截止日三者；
2. 日线截止日取**最新**一行（`max`），不是首行；
3. 日线缺失／行内无 `trade_date` 时**不出现**「日线截至」——宁可少标，不得编造基准。
"""

from __future__ import annotations

import pytest


def _dims(*, kline, price=1248.92, price_source="tencent_finance",
          price_fetched_at="2026-09-30T05:31:54+00:00"):
    return {
        "quote": {
            "data": {"price": price, "change_pct": 1.08, "kline": kline},
            "_meta": {
                "source": "merged:tushare.daily+tencent_finance",
                "price_source": price_source,
                "price_fetched_at": price_fetched_at,
            },
        }
    }


def _render(dims):
    from lib.render_markdown._v3 import _section_snapshot

    return _section_snapshot({"dimensions": []}, "600519", dims)


def test_intraday_price_line_carries_source_time_and_bar_cutoff():
    # 独特标记日期（D13）：只可能来自本次 mock，不会与真实日期混淆
    out = _render(_dims(kline=[
        {"trade_date": "20260123", "close": 1235.58},
        {"trade_date": "20260120", "close": 1230.0},
    ]))
    assert "最新价" in out
    assert "tencent_finance" in out
    assert "日线截至 2026-01-23" in out
    assert "取数 2026-09-30" in out


def test_bar_cutoff_uses_latest_row_not_first():
    out = _render(_dims(kline=[
        {"trade_date": "20260120", "close": 1.0},
        {"trade_date": "20260227", "close": 2.0},
        {"trade_date": "20260225", "close": 3.0},
    ]))
    assert "日线截至 2026-02-27" in out
    assert "日线截至 2026-01-20" not in out


@pytest.mark.parametrize("quote_kline", ["missing", None, []])
def test_tencent_quote_uses_standalone_kline_cutoff(quote_kline):
    dims = _dims(kline=quote_kline)
    if quote_kline == "missing":
        dims["quote"]["data"].pop("kline")
    dims["quote"]["_meta"]["source"] = "tencent_finance"
    dims["kline"] = {
        "status": "available",
        "data": [
            {"trade_date": "20260120", "close": 1230.0},
            {"trade_date": "20260227", "close": 1235.58},
            {"trade_date": "20260225", "close": 1234.0},
        ],
        "_meta": {"source": "tushare.daily"},
    }

    price_line = next(line for line in _render(dims).splitlines() if "**最新价:**" in line)
    assert "1248.92" in price_line
    assert "tencent_finance" in price_line
    assert "取数 2026-09-30" in price_line
    assert "日线截至 2026-02-27" in price_line


def test_embedded_kline_takes_precedence_over_standalone_kline():
    dims = _dims(kline=[{"trade_date": "20260123", "close": 1235.58}])
    dims["kline"] = {"data": [{"trade_date": "20260227", "close": 1230.0}]}

    out = _render(dims)
    assert "日线截至 2026-01-23" in out
    assert "日线截至 2026-02-27" not in out


def test_missing_kline_omits_cutoff_instead_of_inventing_one():
    for kline in ([], None):
        out = _render(_dims(kline=kline))
        assert "日线截至" not in out
    # 缺日线时该行仍须可读（来源与取数时点保留）
    assert "tencent_finance" in _render(_dims(kline=[]))


def test_kline_rows_without_trade_date_do_not_fabricate_cutoff():
    out = _render(_dims(kline=[{"close": 1.0}, {"trade_date": None}]))
    assert "日线截至" not in out


def test_unsealed_price_source_is_labeled_not_blank():
    dims = _dims(kline=[{"trade_date": "20260123", "close": 1.0}])
    dims["quote"]["_meta"].pop("price_source")
    dims["quote"]["_meta"].pop("source")
    out = _render(dims)
    assert "来源未封存" in out
    assert "日线截至 2026-01-23" in out


# ── HTML 侧（工作流默认交付面；缺这段等于该缺口在主交付面上未修） ──────────────


def _collection_for_html():
    """最小 collection：只保留 HTML 行情卡片依赖的维度。"""
    return {
        "symbol": "600519",
        "fetched_at": "2026-09-30T05:31:54+00:00",
        "dimensions": [
            {
                "dimension": "quote",
                "display": "实时行情",
                "data": {
                    "price": 1248.92,
                    "change_pct": 1.08,
                    "turnover_rate": 0.15,
                    "kline": [{"trade_date": "20260123", "close": 1235.58}],
                },
                "status": "available",
                "_meta": {
                    "source": "merged:tushare.daily+tencent_finance",
                    "primary_source": "tushare.daily",
                    "price_source": "tencent_finance",
                    "price_fetched_at": "2026-09-30T05:31:54+00:00",
                },
            },
        ],
        "summary": {"available": 1, "total": 1, "degraded": 0, "missing": 0},
    }


def _latest_price_card(html: str) -> str:
    return html[html.index("最新价"): html.index("最新价") + 400]


def test_html_latest_price_card_carries_same_basis_labels_as_md():
    from lib import render_html as rh

    card = _latest_price_card(rh.render_html(_collection_for_html(), "600519"))
    assert "1248.92" in card
    assert "tencent_finance" in card
    assert "取数 2026-09-30" in card
    assert "日线截至 2026-01-23" in card


def test_html_latest_price_card_omits_cutoff_when_kline_missing():
    from lib import render_html as rh

    coll = _collection_for_html()
    coll["dimensions"][0]["data"].pop("kline")
    card = _latest_price_card(rh.render_html(coll, "600519"))
    assert "日线截至" not in card
    assert "tencent_finance" in card  # 缺日线时来源仍须可读
