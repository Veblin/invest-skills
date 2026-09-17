"""agent_facts 提取器测试（v0.3.1，SOP-DEEP P0 修复）——纯离线。

**回归锚点（本文件存在的理由）**：2026-09-17 实测 300750 确认，``financials`` 维度
是**降序**（首行 = 最新 20260630），而旧 prompt 的 ``data[-1]`` / ``data[-5:]``
因此取到 **2022Q3–2023Q1** 的财报——落后 13 期 / 3.2 年。Agent 却被要求写
「近 8 期趋势」。本组测试用**降序 fixture** 锁死「必须先归一化行序」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_facts as af  # noqa: E402


# ── fixture：刻意做成**降序**（与实测 financials 同向）──────────────────────

def _fin(period: str, roe: float, gp: float, nm: float, rev: float, np_: float,
         ocf: float) -> dict:
    return {"end_date": period, "roe": roe, "grossprofit_margin": gp,
            "netprofit_margin": nm, "revenue": rev, "net_profit": np_,
            "n_cashflow_act": ocf, "debt_to_assets": 60.0,
            "equity_multiplier": 2.9, "assets_turn": 0.26, "cap_ex": 1.0e9}


DESC_FIN = [
    _fin("20260630", 12.08, 23.93, 16.98, 2769.17e8, 432.84e8, 602.17e8),
    _fin("20260331", 5.97, 24.82, 17.61, 1291.31e8, 207.38e8, 336.81e8),
    _fin("20251231", 24.72, 26.27, 18.12, 4237.02e8, 722.01e8, 1332.20e8),
    _fin("20250930", 17.48, 25.31, 18.47, 2830.72e8, 490.34e8, 806.60e8),
    _fin("20250630", 11.25, 25.02, 18.09, 1788.86e8, 304.85e8, 586.87e8),
    _fin("20250331", 5.49, 24.41, 17.55, 847.05e8, 139.63e8, 328.68e8),
    _fin("20241231", 22.83, 24.44, 14.92, 3620.13e8, 507.45e8, 969.90e8),
    _fin("20240930", 16.57, 28.19, 14.95, 2590.45e8, 360.01e8, 674.44e8),
    _fin("20240630", 11.61, 26.53, 14.92, 1760.0e8, 300.0e8, 447.09e8),
]


def _coll(**kw) -> dict:
    base = {
        "dimensions": [
            {"dimension": "financials", "data": DESC_FIN},
            {"dimension": "basic_info", "data": {"name": "宁德时代", "industry": "电气设备"}},
        ],
        "events": [],
    }
    base.update(kw)
    return base


# ── 行序归一化 ───────────────────────────────────────────────────────────

class TestRowOrder:

    def test_rows_asc_on_descending_input(self):
        got = [af.date_key(r) for r in af.rows_asc(DESC_FIN)]
        assert got[0] == "20240630" and got[-1] == "20260630"

    def test_rows_asc_does_not_mutate_input(self):
        """D7：不得原地排序调用方持有的列表。"""
        before = [af.date_key(r) for r in DESC_FIN]
        af.rows_asc(DESC_FIN)
        assert [af.date_key(r) for r in DESC_FIN] == before

    def test_rows_asc_handles_empty_and_junk(self):
        assert af.rows_asc(None) == []
        assert af.rows_asc([]) == []
        assert af.rows_asc([None, "x", {"end_date": "20240101"}]) == [{"end_date": "20240101"}]

    def test_dedupe_counts_dropped(self):
        rows = [{"end_date": "20240101", "v": 1}, {"end_date": "20240101", "v": 2}]
        kept, dropped = af.dedupe_by_date(rows)
        assert len(kept) == 1 and dropped == 1 and kept[0]["v"] == 1


class TestRegressionLatestPeriodIsUsed:
    """核心回归：Agent B 拿到的趋势窗口必须**以最新期结尾**。"""

    def test_trend_window_ends_at_latest_not_oldest(self):
        f = af.build_financial(_coll())
        labels = {it["label"]: it for it in f.items}
        window = labels["趋势窗口报告期"]["value"]
        assert window.endswith("20260630"), f"趋势窗口未以最新期结尾: {window}"
        assert window.startswith("20240930"), f"窗口起点异常: {window}"

    def test_old_buggy_slice_would_have_taken_2022(self):
        """把旧切片钉在 fixture 上，证明缺陷真实存在（防止「本来就没错」的误判）。"""
        old_slice = [r["end_date"] for r in DESC_FIN[-3:]]
        assert old_slice == ["20241231", "20240930", "20240630"]
        assert max(old_slice) < "20260630", "旧切片确实取不到最新期"

    def test_latest_period_field_is_newest(self):
        f = af.build_financial(_coll())
        labels = {it["label"]: it for it in f.items}
        assert labels["ROE(%) 近 8 期序列"]["value"][-1] == pytest.approx(12.08)


# ── 聚合正确性 ───────────────────────────────────────────────────────────

class TestAggregation:

    def test_percentile_and_median_use_full_series(self):
        val = [{"trade_date": f"2025{i:04d}", "pe_ttm": float(i)} for i in range(1, 101)]
        val.append({"trade_date": "20260916", "pe_ttm": 50.0})
        coll = _coll()
        coll["dimensions"].append({"dimension": "valuation", "data": val})
        f = af.build_financial(coll)
        labels = {it["label"]: it for it in f.items}
        assert labels["估值序列样本数"]["value"] == 101
        # 分位口径 = count(≤ v) / n —— 含 v 自身（1..50 共 50 个 + v 本身 = 51）
        assert labels["PE(TTM) 历史分位(%)"]["value"] == pytest.approx(51 / 101 * 100, rel=1e-6)
        # 1..100 再追加一个 50.0 → 101 个样本，中位数落在第 51 位 = 50.0
        assert labels["PE(TTM) 中位数"]["value"] == pytest.approx(50.0)

    def test_pct_and_diff_none_safe(self):
        assert af.pct(None, 1) is None and af.pct(1, 0) is None and af.pct(2, 1) == 100.0
        assert af.diff(None, 1) is None and af.diff(3, 1) == 2.0

    def test_num_treats_zero_as_legit(self):
        """D1：0 是合法值，不得被 falsy 兜底吞掉。"""
        assert af.num(0) == 0.0 and af.num("0") == 0.0
        assert af.num(None) is None and af.num(float("nan")) is None

    def test_same_period_yoy_uses_matching_month_end(self):
        f = af.build_financial(_coll())
        labels = {it["label"]: it for it in f.items}
        assert labels["同报告期基准期"]["value"] == "20250630"
        assert labels["营收 同报告期同比(%)"]["value"] == pytest.approx(
            (2769.17e8 / 1788.86e8 - 1) * 100, rel=1e-6)

    def test_same_period_yoy_refuses_two_year_old_comparator(self):
        """缺 2025H1 时不得把 2024H1 冒充「上年同期」。"""
        rows = [r for r in DESC_FIN if r["end_date"] != "20250630"]
        f = af.build_financial(_coll(dimensions=[
            {"dimension": "financials", "data": rows},
            {"dimension": "basic_info", "data": {"name": "宁德时代", "industry": "电气设备"}},
        ]))
        labels = {it["label"]: it for it in f.items}
        assert "同报告期基准期" not in labels
        assert labels["同报告期同比"]["value"] is None
        assert "20250630" in labels["同报告期同比"]["source"]

    def test_mixed_cumulative_window_emits_warning(self):
        """窗口跨 Q1/H1/Q3/年报 → 累计窗口长度不同，必须显式警告（否则读出假趋势）。"""
        f = af.build_financial(_coll())
        labels = {it["label"]: it for it in f.items}
        assert any("口径警告" in k for k in labels), list(labels)

    def test_all_facts_carry_a_source_label(self):
        for role in ("business", "financial", "industry", "risk"):
            for it in af._BUILDERS[role](_coll()).items:
                assert it["source"].startswith("[来源:") or it["source"].startswith("[不可得:")
                assert it["text"].strip()


# ── 不可得显式化（不留给 Agent 补）──────────────────────────────────────────

class TestExplicitUnavailable:

    def test_industry_index_marked_unavailable(self):
        labels = {it["label"]: it for it in af.build_industry(_coll()).items}
        assert "不可得" in labels["申万行业指数 20/60 日涨跌幅"]["text"]

    def test_insider_signal_marked_unavailable(self):
        labels = {it["label"]: it for it in af.build_risk(_coll()).items}
        assert "不可得" in labels["内部人交易信号（近 12 月增减持方向）"]["text"]

    def test_profit_pool_marked_unavailable(self):
        labels = {it["label"]: it for it in af.build_industry(_coll()).items}
        assert "不可得" in labels["产业链利润池分布（上游/中游/下游利润占比）"]["text"]

    def test_missing_dimensions_do_not_crash(self):
        """空 collection：不抛异常，且**每个事实都带来源或不可得标注**。

        （不要求 value 全为 None——如「事件总数 0」是合法计数，不是缺失。）
        """
        empty = {"dimensions": [], "events": []}
        for role in ("business", "financial", "industry", "risk"):
            items = af._BUILDERS[role](empty).items
            assert items, role
            for it in items:
                assert (it["source"].startswith("[来源:")
                        or it["source"].startswith("[不可得:")), (role, it)

    def test_event_type_counts_match_full_list(self):
        ev = [{"date": "2026-09-01", "type": "buyback"}] * 3 + \
             [{"date": "2026-08-01", "type": "other"}] * 2
        labels = {it["label"]: it for it in af.build_risk(_coll(events=ev)).items}
        assert labels["事件总数"]["value"] == 5
        assert labels["事件类型计数 · buyback"]["value"] == 3
        assert labels["事件类型计数 · other"]["value"] == 2


# ── Fact 容器契约 ────────────────────────────────────────────────────────

class TestFactContract:

    def test_add_without_source_raises(self):
        f = af.Facts()
        with pytest.raises(ValueError):
            f.add("无来源", 1.0)

    def test_ids_are_sequential_and_unique(self):
        f = af.Facts()
        f.add("a", 1.0, formula="1.0")
        f.unavailable("b", "无数据")
        ids = [it["id"] for it in f.items]
        assert ids == ["F1", "F2"] and len(set(ids)) == 2


# ── CLI ─────────────────────────────────────────────────────────────────

class TestCli:

    def test_main_writes_json(self, tmp_path, capsys):
        p = tmp_path / "coll.json"
        p.write_text(json.dumps(_coll(), ensure_ascii=False), encoding="utf-8")
        assert af.main([str(p), "--for", "financial", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["role"] == "financial" and payload["facts"]

    def test_main_bad_path_returns_2(self, tmp_path, capsys):
        assert af.main([str(tmp_path / "nope.json"), "--for", "risk"]) == 2

    @pytest.mark.parametrize("role", ["business", "financial", "industry", "risk"])
    def test_every_role_runs(self, tmp_path, capsys, role):
        p = tmp_path / "coll.json"
        p.write_text(json.dumps(_coll(), ensure_ascii=False), encoding="utf-8")
        assert af.main([str(p), "--for", role]) == 0
        assert "禁止自行计算" in capsys.readouterr().out
