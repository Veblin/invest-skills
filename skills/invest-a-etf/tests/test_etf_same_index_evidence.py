"""R3 B02/B04：同指数实现质量有限比较 + 持仓定向证据表（离线，独立复算）。

B02：比较前置核验（同指数/币种/期间/收益口径）——源或材料不满足时降级为
明确缺口，不排名、不用不同指数排序替代；费率只出自本次读取的官方材料。
B04：预算内证据行回填——来源三件套不齐的行不得按已取证处理；未取证行
保持 not_collected（未执行 ≠ 完成）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import etf_data  # noqa: E402
import etf_peers  # noqa: E402


def _nav_rows(pairs: list[tuple[str, float]]) -> list[dict]:
    return [{"date": d, "nav": v} for d, v in pairs]


_SUM_EVIDENCE = {
    "source_type": "基金产品资料概要（管理人/交易所原文）",
    "url": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-06-20/x.pdf",
    "as_of": "2024-06-20", "read_at": "2026-10-08",
    "sha256": "a" * 64,
    "locator": "费用表（物理 PDF 第 3 页）",
    "version": "产品资料概要更新（2024-06-20 编制）",
    "currency": "CNY",
}


def _patch_nav(monkeypatch, mapping: dict[str, dict]):
    def fake_kline(code, days=250):
        return mapping[code]

    monkeypatch.setattr(etf_peers, "query_etf_kline", fake_kline)
    monkeypatch.setattr(etf_peers, "_peer_name", lambda code: f"ETF{code}")
    monkeypatch.setattr(etf_peers, "query_etf_est_net_assets",
                        lambda code: {"status": "missing", "note": "no data"})


class TestSameIndexQualityComparison:
    @pytest.mark.parametrize("missing", ["all", "url", "sha256", "locator",
                                         "source_type", "as_of", "read_at"])
    def test_identity_without_provenance_is_not_comparable(self, monkeypatch, missing):
        _patch_nav(monkeypatch, {
            code: {"status": "available", "adj_applied": True,
                   "nav_history": _nav_rows([("2026-09-01", 1.0),
                                              ("2026-09-02", 1.1)])}
            for code in ("510300", "159919")})
        ev = {**_SUM_EVIDENCE, "index_code": "000300"}
        if missing == "all":
            ev = {"index_code": "000300", "currency": "CNY"}
        else:
            ev.pop(missing)
        out = etf_peers.same_index_quality_comparison(
            "510300", ["159919"], index_code="000300",
            evidence={"510300": ev, "159919": ev})
        assert out["status"] == "limited"
        assert out["common_window"] is None
        for row in out["rows"]:
            assert row["status"] == "not_comparable"
            assert row["window_return_pct"] is None
            assert row["index"]["declared_same_index"] is False
            assert any("证据缺少" in reason for reason in row["comparability_reasons"])

    def test_same_index_same_window_comparison_no_ranking(self, monkeypatch):
        dates = ["2026-09-01", "2026-09-02", "2026-09-03"]
        mapping = {
            "510300": {"status": "available", "adj_applied": True,
                       "source": "fund_open_fund_info_em",
                       "nav_history": _nav_rows(list(zip(dates, [1.0, 1.05, 1.10])))},
            "159919": {"status": "available", "adj_applied": True,
                       "source": "fund_open_fund_info_em",
                       "nav_history": _nav_rows(list(zip(dates, [2.0, 2.08, 2.15])))},
        }
        _patch_nav(monkeypatch, mapping)
        ev = {
            "510300": {**_SUM_EVIDENCE, "index_code": "000300",
                       "index_name": "沪深300", "fund_name": "华泰柏瑞沪深300ETF",
                       "fee_items": [{"name": "管理费", "annual_rate_pct": 0.5}]},
            "159919": {**_SUM_EVIDENCE, "index_code": "000300",
                       "index_name": "沪深300", "fund_name": "嘉实沪深300ETF",
                       "fee_items": [{"name": "管理费", "annual_rate_pct": 0.15}]},
        }
        out = etf_peers.same_index_quality_comparison(
            "510300", ["159919"], index_code="000300", evidence=ev)
        assert out["status"] == "ok"
        assert out["common_window"] == {"start": "2026-09-01", "end": "2026-09-03",
                                        "n_dates": 3}
        rows = {r["symbol"]: r for r in out["rows"]}
        assert rows["510300"]["window_return_pct"] == 10.0
        assert rows["159919"]["window_return_pct"] == 7.5
        assert rows["510300"]["status"] == "comparable"
        # 不排名：差分只作列示
        assert out["ranking"] is None
        assert "不排名" in out["no_ranking_note"]
        # 费率行必须绑定官方材料（URL+sha256+locator）
        assert rows["159919"]["fees"]["status"] == "ok"
        assert rows["159919"]["fees"]["source"]["sha256"] == "a" * 64

    def test_unread_peer_is_not_comparable_gap_not_fabricated(self, monkeypatch):
        dates = ["2026-09-01", "2026-09-02"]
        _patch_nav(monkeypatch, {
            "510300": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [1.0, 1.1])))},
            "159919": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [2.0, 2.2])))},
        })
        out = etf_peers.same_index_quality_comparison(
            "510300", ["159919"], index_code="000300", evidence={})
        assert out["status"] == "limited"
        row = out["rows"][1]
        assert row["status"] == "not_comparable"
        assert row["window_return_pct"] is None
        assert row["fees"]["status"] == "not_collected"
        assert any("不可比" in g and "000300" not in g.split("不可比")[0] for g in out["gaps"])

    def test_different_index_peer_excluded_from_comparison(self, monkeypatch):
        dates = ["2026-09-01", "2026-09-02"]
        _patch_nav(monkeypatch, {
            "510300": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [1.0, 1.1])))},
            "515080": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [2.0, 2.2])))},
        })
        ev = {
            "510300": {**_SUM_EVIDENCE, "index_code": "000300"},
            "515080": {**_SUM_EVIDENCE, "index_code": "000922"},   # 中证红利 ≠ 沪深300
        }
        out = etf_peers.same_index_quality_comparison(
            "510300", ["515080"], index_code="000300", evidence=ev)
        assert out["status"] == "limited"
        assert out["rows"][1]["status"] == "not_comparable"
        assert any("515080 不可比" in g and "未声明同指数" in g for g in out["gaps"])

    def test_unadjusted_nav_peer_not_comparable(self, monkeypatch):
        dates = ["2026-09-01", "2026-09-02"]
        _patch_nav(monkeypatch, {
            "510300": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [1.0, 1.1])))},
            "159919": {"status": "available", "adj_applied": False,
                       "nav_history": _nav_rows(list(zip(dates, [2.0, 2.2])))},
        })
        ev = {"510300": {**_SUM_EVIDENCE, "index_code": "000300"},
              "159919": {**_SUM_EVIDENCE, "index_code": "000300"}}
        out = etf_peers.same_index_quality_comparison(
            "510300", ["159919"], index_code="000300", evidence=ev)
        row = {r["symbol"]: r for r in out["rows"]}["159919"]
        assert row["status"] == "not_comparable"
        assert "未复权" in row["nav"]["note"]

    def test_fee_evidence_without_hash_is_invalid(self, monkeypatch):
        dates = ["2026-09-01", "2026-09-02"]
        _patch_nav(monkeypatch, {
            "510300": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [1.0, 1.1])))},
            "159919": {"status": "available", "adj_applied": True,
                       "nav_history": _nav_rows(list(zip(dates, [2.0, 2.2])))},
        })
        bad = {k: v for k, v in _SUM_EVIDENCE.items() if k != "sha256"}
        ev = {"510300": {**_SUM_EVIDENCE, "index_code": "000300"},
              "159919": {**bad, "index_code": "000300"}}
        out = etf_peers.same_index_quality_comparison(
            "510300", ["159919"], index_code="000300", evidence=ev)
        fees = {r["symbol"]: r for r in out["rows"]}["159919"]["fees"]
        assert fees["status"] == "invalid_evidence"
        assert "sha256" in fees["note"]


class TestEvidenceBudget:
    HOLDINGS = {
        "status": "ok", "holding_scope": "基金披露前十大（非完整持仓）",
        "report_date": "2026-06-30", "top10_sum_pct": 62.9,
        "rows": [{"code": "300308", "name": "中际旭创", "pct": 9.91},
                 {"code": "300502", "name": "新易盛", "pct": 7.8},
                 {"code": "600941", "name": "中国移动", "pct": None}],
    }

    def _patch(self, monkeypatch):
        monkeypatch.setattr(etf_data, "query_etf_holdings",
                            lambda symbol: dict(self.HOLDINGS))

    def test_without_evidence_rows_stay_not_collected(self, monkeypatch):
        self._patch(monkeypatch)
        out = etf_data.build_evidence_budget(
            "515050", target_cov_pct=50, max_names=10,
            research_question="AI 算力资本开支对光模块持仓的盈利弹性")
        assert out["research_question"].startswith("AI 算力")
        assert all(r["缺口"] == "未取证（预算内待查）" for r in out["evidence_rows"])
        assert out["evidence_summary"]["evidence_rows"] == 0
        assert out["evidence_summary"]["not_collected_rows"] == len(out["names"])

    def test_evidence_rows_filled_and_summarized(self, monkeypatch):
        self._patch(monkeypatch)
        evidence = {
            "300308": {
                "业务证据": "2026 年半年度业绩预告：光模块收入同比增长",
                "期间": "2026-06-30", "事件": "业绩预告（2026-07-14 披露）",
                "关联命题": "AI 资本开支 → 800G/1.6T 光模块需求",
                "来源": {"source_type": "深交所公告（巨潮）",
                         "url": "https://www.cninfo.com.cn/announcement/xxx",
                         "sha256": "b" * 64, "locator": "公告正文第 2 页「业绩变动原因」",
                         "read_at": "2026-10-08"},
            },
        }
        out = etf_data.build_evidence_budget(
            "515050", target_cov_pct=50, max_names=10, evidence=evidence)
        rows = {r["证券"][:8]: r for r in out["evidence_rows"]}
        first = next(r for r in out["evidence_rows"] if r["来源"])
        assert first["业务证据"].startswith("2026 年半年度业绩预告")
        assert first["来源"]["sha256"] == "b" * 64
        assert out["evidence_summary"]["evidence_rows"] == 1
        assert out["evidence_summary"]["evidence_weight_cov_pct"] == 9.91
        # 未给的证券保持未取证
        assert any(r["缺口"] == "未取证（预算内待查）" for r in out["evidence_rows"])

    def test_incomplete_source_not_counted_as_evidence(self, monkeypatch):
        self._patch(monkeypatch)
        evidence = {"300308": {"业务证据": "x", "来源": {"url": "https://x"}}}
        out = etf_data.build_evidence_budget(
            "515050", target_cov_pct=50, max_names=10, evidence=evidence)
        row = next(r for r in out["evidence_rows"] if r["证券"].startswith("中际旭创"))
        assert "来源不完整" in row["缺口"]
        assert out["evidence_summary"]["evidence_rows"] == 0
        assert "未执行 ≠ 完成" in out["evidence_summary"]["note"]

    def test_weight_missing_not_zero_and_shortfall_disclosed(self, monkeypatch):
        self._patch(monkeypatch)
        out = etf_data.build_evidence_budget(
            "515050", target_cov_pct=50, max_names=10)
        assert any("权重缺失" in g for g in out["gaps"])
        assert out["shortfall_note"] is None or "覆盖" in out["shortfall_note"]
        # 预算参数为试验参数（未标定硬阈值）
        assert out["budget"]["experimental_parameters"] is True

    @pytest.mark.parametrize("kwargs", [
        {"target_cov_pct": 0, "max_names": 10},
        {"target_cov_pct": 101, "max_names": 10},
        {"target_cov_pct": 50, "max_names": 0},
    ])
    def test_invalid_budget_parameters_refused(self, monkeypatch, kwargs):
        self._patch(monkeypatch)
        with pytest.raises(ValueError):
            etf_data.build_evidence_budget("515050", **kwargs)

def test_repeated_peer_cannot_create_two_comparable_products(monkeypatch):
    _patch_nav(monkeypatch, {
        code: {"status": "available", "adj_applied": True,
               "nav_history": _nav_rows([("2026-09-01", 1.0), ("2026-09-02", 1.1)])}
        for code in ("510300", "159919")})
    out = etf_peers.same_index_quality_comparison(
        "510300", ["159919", "159919"], index_code="000300",
        evidence={"159919": {**_SUM_EVIDENCE, "index_code": "000300"}})
    assert out["status"] == "limited"
    assert [r["symbol"] for r in out["rows"]] == ["510300", "159919"]
    assert out["common_window"] is None
    assert all(r["window_return_pct"] is None for r in out["rows"])


def test_evidence_budget_last_disclosed_row_reaches_target():
    holdings = {"status": "ok", "top10_sum_pct": 50.0,
                "rows": [{"code": "600001", "pct": 25.0},
                         {"code": "600002", "pct": 25.0}]}
    out = etf_data.build_evidence_budget("510300", target_cov_pct=50, max_names=2,
                                         holdings=holdings)
    assert out["budget"]["selected_cov_pct"] == 50.0
    assert out["budget"]["stop_reason"] == "target_reached"
    assert out["shortfall_note"] is None


@pytest.mark.parametrize("fee_items,expected", [
    (None, "not_collected"), ([], "not_collected"),
    ("bad", "invalid_evidence"), ([{"name": "管理费"}], "invalid_evidence"),
    ([{"name": "管理费", "annual_rate_pct": float("nan")}], "invalid_evidence"),
    ([{"name": "管理费", "annual_rate_pct": -0.5}], "invalid_evidence"),
    ([{"name": "管理费", "annual_rate_pct": 0.0}], "ok"),
])
def test_same_index_fees_require_actual_rate_items(fee_items, expected):
    row = etf_peers._same_index_fee_row("510300", {**_SUM_EVIDENCE, "fee_items": fee_items})
    assert row["status"] == expected
    if expected != "ok":
        assert row["items"] == [] and row["note"]
    else:
        assert row["items"][0]["annual_rate_pct"] == 0.0
