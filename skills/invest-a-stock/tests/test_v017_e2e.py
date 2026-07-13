"""v0.1.7 Step 8 端到端冒烟（live 数据源）。

默认跳过；本地或发布前执行：

    INVEST_RUN_E2E=1 uv run pytest skills/invest-a-stock/tests/test_v017_e2e.py -v
"""
from __future__ import annotations

import os

import pytest

from lib.collector import _DEFAULT_DIMS, attach_phase2_extras, collect_all

pytestmark = pytest.mark.skipif(
    os.environ.get("INVEST_RUN_E2E") != "1",
    reason="live e2e: set INVEST_RUN_E2E=1",
)

# v0.1.7-implementation-plan.md Step 8.2
E2E_SYMBOLS = ("600176", "000858", "600519", "000001")


def _dim_map(collection: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dim in collection.get("dimensions", []):
        if isinstance(dim, dict) and dim.get("dimension"):
            out[str(dim["dimension"])] = dim
    return out


@pytest.mark.e2e
@pytest.mark.parametrize("symbol", E2E_SYMBOLS)
def test_collect_all_default_dims_no_regression(symbol: str):
    """四标的默认维度采集不报错，且含 v0.1.7 新增 holder_changes。"""
    result = collect_all(symbol, dims=list(_DEFAULT_DIMS))
    summary = result.get("summary") or {}
    assert summary.get("total", 0) >= len(_DEFAULT_DIMS)

    dims = _dim_map(result)
    assert "holder_changes" in dims
    assert "financials" in dims
    assert dims["holder_changes"].get("status") in ("available", "partial", "missing")

    fin = dims.get("financials") or {}
    if fin.get("status") == "available":
        assert fin.get("dcf_preprocess") is not None or fin.get("data")


@pytest.mark.e2e
@pytest.mark.parametrize("symbol", E2E_SYMBOLS)
def test_attach_phase2_extras_industry_pricing(symbol: str):
    """attach_phase2_extras 挂载 industry_pricing / price_shock。"""
    result = collect_all(symbol, dims=list(_DEFAULT_DIMS))
    attach_phase2_extras(result, symbol)

    assert "industry_pricing" in result
    ip = result["industry_pricing"]
    assert isinstance(ip, dict)
    assert ip.get("dimension") == "industry_pricing"

    shock = result.get("price_shock")
    assert isinstance(shock, dict)
    assert "has_shock" in shock


@pytest.mark.e2e
@pytest.mark.parametrize("symbol", E2E_SYMBOLS)
def test_report_v3_renders_v017_sections(symbol: str):
    """report 渲染含 v0.1.7 章节骨架（增减持 / 行业定价）。"""
    from lib.render import render_report_v3

    result = collect_all(symbol, dims=list(_DEFAULT_DIMS))
    attach_phase2_extras(result, symbol)
    md = render_report_v3(result, symbol, mode="full")

    assert len(md) > 800
    assert "风险" in md
    dims = _dim_map(result)
    hc = dims.get("holder_changes") or {}
    if hc.get("status") == "available" and hc.get("data"):
        assert "## 3d. 股东增减持动向" in md
    # industry_pricing 可能仅新闻源（无期货映射行业）
    assert "行业产品定价" in md or "原材料成本速览" in md or "涨价" in md
