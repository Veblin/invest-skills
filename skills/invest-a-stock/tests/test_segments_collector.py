"""A2 分部数据（``fina_mainbz``）采集的契约测试。

两条容易踩的坑，本文件逐条锁住：

1. **别名重复行**：``fina_mainbz`` 对同一分部返回两行别名、数值完全相同（实测
   300750 2026H1：按产品「电池材料及回收、矿产资源」/「电池材料及回收」）。
   不去重会让分部合计虚高——实测 3489.89 亿 vs 真实营收 2769.17 亿。
2. **NaN 占位行**：接口里还有「合计特别调整」这类 ``bz_sales`` 为 NaN 的行，
   不是分部，必须剔除。

真实数据回归（人工核对过，不入自动化以免依赖网络）：去重后按产品合计 2769.17 亿、
按地区合计 2769.17 亿，与引擎 ``financials.revenue.latest`` 完全一致。
"""
from __future__ import annotations

import pandas as pd
import pytest

from lib.collector import _orchestrate as orch
from lib.collector import _sources as src


def _mainbz_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _install(monkeypatch, frame_by_type: dict[str, pd.DataFrame]) -> None:
    class _TC:
        def query(self, api, **kw):
            assert api == "fina_mainbz"
            return frame_by_type.get(str(kw.get("type")))

    monkeypatch.setattr(src, "_require_tushare", lambda: (None, _TC()))


# ── 纯函数：别名去重 ────────────────────────────────────────────────────────


def test_dedupe_keeps_shorter_alias_name() -> None:
    rows = [
        {"bz_item": "电池材料及回收、矿产资源", "bz_sales": 1.881108e10, "bz_profit": 5.087196e9},
        {"bz_item": "电池材料及回收", "bz_sales": 1.881108e10, "bz_profit": 5.087196e9},
        {"bz_item": "储能电池系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
        {"bz_item": "储能系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
    ]
    kept = {row["bz_item"] for row in src._dedupe_mainbz_rows(rows)}
    assert kept == {"电池材料及回收", "储能系统"}


def test_dedupe_keeps_distinct_segments() -> None:
    rows = [
        {"bz_item": "动力电池系统", "bz_sales": 1.921249e11, "bz_profit": 3.963278e10},
        {"bz_item": "其他业务", "bz_sales": 1.271964e10, "bz_profit": 8.781601e9},
    ]
    assert len(src._dedupe_mainbz_rows(rows)) == 2


# ── fetcher：解析 / 去重 / 剔 NaN / 派生毛利率 ──────────────────────────────


def test_mainbz_parses_dedupes_and_skips_nan(monkeypatch) -> None:
    _install(monkeypatch, {
        "P": _mainbz_frame([
            {"end_date": "20260630", "bz_item": "动力电池系统", "bz_sales": 1.921249e11, "bz_profit": 3.963278e10},
            {"end_date": "20260630", "bz_item": "储能系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
            {"end_date": "20260630", "bz_item": "储能电池系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
            {"end_date": "20260630", "bz_item": "合计特别调整", "bz_sales": float("nan"), "bz_profit": float("nan")},
        ]),
        "D": _mainbz_frame([
            {"end_date": "20260630", "bz_item": "境外", "bz_sales": 8.712919e10, "bz_profit": 2.611083e10},
            {"end_date": "20260630", "bz_item": "国外", "bz_sales": 8.712919e10, "bz_profit": 2.611083e10},
        ]),
    })
    rows = src._q_tushare_mainbz("300750")
    assert rows is not None
    product = [r for r in rows if r["type"] == "product"]
    region = [r for r in rows if r["type"] == "region"]
    assert {r["item"] for r in product} == {"动力电池系统", "储能系统"}   # 别名与 NaN 行已剔除
    assert {r["item"] for r in region} == {"境外"}
    assert all(r["end_date"] == "20260630" for r in rows)

    storage = next(r for r in product if r["item"] == "储能系统")
    assert storage["margin_pct"] == pytest.approx(23.96, abs=0.01)      # 127.60/532.61
    assert storage["sales"] == pytest.approx(5.326097e10)


def test_mainbz_returns_none_when_all_sources_empty(monkeypatch) -> None:
    _install(monkeypatch, {"P": pd.DataFrame(), "D": pd.DataFrame()})
    assert src._q_tushare_mainbz("300750") is None


def test_mainbz_deduped_total_matches_revenue_scale(monkeypatch) -> None:
    """去重后的分部合计必须等于营收量级——不去重会虚高约 26%。"""
    _install(monkeypatch, {
        "P": _mainbz_frame([
            {"end_date": "20260630", "bz_item": "动力电池系统", "bz_sales": 1.921249e11, "bz_profit": 3.963278e10},
            {"end_date": "20260630", "bz_item": "储能系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
            {"end_date": "20260630", "bz_item": "储能电池系统", "bz_sales": 5.326097e10, "bz_profit": 1.276011e10},
            {"end_date": "20260630", "bz_item": "电池材料及回收", "bz_sales": 1.881108e10, "bz_profit": 5.087196e9},
            {"end_date": "20260630", "bz_item": "其他业务", "bz_sales": 1.271964e10, "bz_profit": 8.781601e9},
        ]),
        "D": pd.DataFrame(),
    })
    rows = src._q_tushare_mainbz("300750")
    total = sum(r["sales"] for r in rows if r["type"] == "product")
    assert total / 1e8 == pytest.approx(2769.17, abs=0.5)   # 引擎 2026H1 营收 2769.17 亿


# ── 维度注册与降级 ──────────────────────────────────────────────────────────


def test_segments_registered_as_dimension() -> None:
    assert orch.COLLECTORS["segments"][0] == "分部数据"
    assert orch.COLLECTORS["segments"][1] is orch.collect_segments


def test_collect_segments_degrades_without_tushare(monkeypatch) -> None:
    """Tushare 不可用 → 空任务集，不得抛异常。"""
    monkeypatch.setattr(orch.env, "is_tushare_available", lambda *a, **k: False)
    result = orch.collect_segments("300750")
    assert result["dimension"] == "segments"
    assert result["status"] in {"missing", "unavailable", "partial"}


def test_segments_in_deep_analysis_plan() -> None:
    from lib.planner import INTENT_PRESETS

    ids = {m.module_id for m in INTENT_PRESETS["deep_analysis"].modules}
    assert "segments" in ids
