"""B01-2/B04 持仓披露范围、指数样本/权重同期性、取证预算（离线 fixture）。"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from etf_data import (
    HOLDING_SCOPE_TOP10,
    HOLDINGS_EVIDENCE_FIELDS,
    build_evidence_budget,
    fetch_etf_index_cons,
    fetch_etf_index_weight,
    query_etf_holdings,
    query_etf_index_exposure,
)


def _holdings_env(rows, report_date="2026-06-30"):
    return {"status": "ok", "report_date": report_date, "quarter": "2026年2季度",
            "rows": rows, "error": None}


def _rows(*pcts):
    return [{"code": f"60{i:04d}", "name": f"股票{i}", "pct": p, "shares": 1.0,
             "amount": 1.0} for i, p in enumerate(pcts, start=1)]


class TestHoldingsScope:
    def test_scope_coverage_pcf_explicit(self):
        with patch("etf_data._bridge_get",
                   return_value=_holdings_env(_rows(9.0, 8.0, 7.0))):
            out = query_etf_holdings("515050")
        assert out["holding_scope"] == HOLDING_SCOPE_TOP10
        assert "非完整持仓" in out["holding_scope"]
        cov = out["coverage"]
        assert cov["disclosed_sum_pct"] == 24.0
        assert "缺失权重不得按 0" in cov["uncovered_note"]
        assert out["pcf"]["status"] == "not_connected"
        assert "8000 积分" in out["pcf"]["note"]
        # rows/clusters 兼容面保留
        assert out["rows"] and out["clusters"] is not None

    def test_missing_pct_not_counted(self):
        with patch("etf_data._bridge_get",
                   return_value=_holdings_env(_rows(9.0, None, 7.0))):
            out = query_etf_holdings("515050")
        assert out["coverage"]["pct_missing_rows"] == 1
        assert out["top10_sum_pct"] == 16.0  # 缺失不按 0 之外口径重算；亦不重归一化

    def test_cli_stdout_never_says_full_holdings(self, monkeypatch, capsys):
        etf_py = Path(__file__).resolve().parent.parent / "scripts" / "etf.py"
        name = "etf_cli_exposure_under_test"
        sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(name, etf_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "query_etf_holdings", lambda s: query_holdings_stub())
        assert mod.cmd_holdings("515050", as_json=False) == 0
        out = capsys.readouterr().out
        # 正向声明必须是「非完整持仓」；不得出现把前十称完整的正向断言
        assert "非完整持仓" in out
        assert not re.search(r"(?<!非)完整持仓", out)
        assert "全部持仓" not in out
        assert "披露范围" in out


def query_holdings_stub():
    import etf_data as ed
    with patch("etf_data._bridge_get",
               return_value=_holdings_env(_rows(9.0, 8.0))):
        return ed.query_etf_holdings("515050")


class TestIndexExposure:
    def _env(self, code, kind, as_of, rows):
        if kind == "cons":
            return {"status": "ok", "index_code": code, "as_of": as_of,
                    "rows": rows, "row_count": len(rows), "error": None}
        return {"status": "ok", "index_code": code, "as_of": as_of, "rows": rows,
                "row_count": len(rows),
                "weight_sum_pct": round(sum(r["weight_pct"] for r in rows), 3),
                "error": None}

    def _bridge(self, cons, weight):
        def fake(getter, *a):
            if getter == "get_etf_index_cons":
                return cons
            if getter == "get_etf_index_weight":
                return weight
            return None
        return fake

    def test_same_period_joinable(self):
        cons = self._env("000300", "cons", "2026-09-30",
                         [{"code": "000001", "name": "平安银行", "exchange": "深"}])
        weight = self._env("000300", "weight", "2026-09-30",
                           [{"code": "000001", "name": "平安银行", "weight_pct": 0.433}])
        with patch("etf_data._bridge_get", side_effect=self._bridge(cons, weight)):
            out = query_etf_index_exposure("510300")
        assert out["status"] == "ok"
        pa = out["period_alignment"]
        assert pa["same_period"] is True and pa["joinable"] is True
        assert out["weight"]["weight_sum_pct"] == 0.433  # 原样、不重归一化

    def test_mismatched_months_not_joinable(self):
        """错月铁律：cons 9 月 + weight 8 月不得拼「当期组合」。"""
        cons = self._env("000300", "cons", "2026-09-30",
                         [{"code": "000001", "name": "平安银行", "exchange": "深"}])
        weight = self._env("000300", "weight", "2026-08-31",
                           [{"code": "000001", "name": "平安银行", "weight_pct": 0.4}])
        with patch("etf_data._bridge_get", side_effect=self._bridge(cons, weight)):
            out = query_etf_index_exposure("510300")
        pa = out["period_alignment"]
        assert pa["joinable"] is False and pa["same_period"] is False
        assert pa["cons_as_of"] == "2026-09-30" and pa["weight_as_of"] == "2026-08-31"
        assert "禁止拼" in pa["note"]

    def test_missing_weight_partial(self):
        cons = self._env("000300", "cons", "2026-09-30",
                         [{"code": "000001", "name": "平安银行", "exchange": "深"}])
        with patch("etf_data._bridge_get", side_effect=self._bridge(cons, None)):
            with patch("etf_data.fetch_etf_index_weight",
                       return_value={"status": "missing", "index_code": "000300",
                                     "as_of": None, "rows": [], "weight_sum_pct": None,
                                     "error": "boom"}):
                out = query_etf_index_exposure("510300")
        assert out["status"] == "partial"
        assert out["cons"]["as_of"] == "2026-09-30"
        assert out["period_alignment"]["joinable"] is False

    def test_unmapped_returns_not_mapped(self):
        with patch("etf_data.CSINDEX_MAP", {}), \
             patch("etf_data.query_etf_identity", return_value={"tracked_index": None}):
            out = query_etf_index_exposure("999999")
        assert out["status"] == "not_mapped"


class TestIndexFetchers:
    def test_fetch_cons_normalizes(self):
        df = pd.DataFrame({"日期": ["2026-09-30"], "成分券代码": ["000001"],
                           "成分券名称": ["平安银行"], "交易所": ["深圳证券交易所"]})
        fake_ak = type("AK", (), {"index_stock_cons_csindex": staticmethod(lambda symbol: df)})()
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            env = fetch_etf_index_cons("000300")
        assert env["status"] == "ok" and env["as_of"] == "2026-09-30"
        assert env["rows"][0]["code"] == "000001"

    def test_fetch_weight_sum_verbatim(self):
        df = pd.DataFrame({"日期": ["2026-08-31", "2026-08-31"],
                           "成分券代码": ["000001", "000002"],
                           "成分券名称": ["平安银行", "万科A"], "权重": [0.4, 0.2]})
        fake_ak = type("AK", (), {
            "index_stock_cons_weight_csindex": staticmethod(lambda symbol: df)})()
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            env = fetch_etf_index_weight("000300")
        assert env["weight_sum_pct"] == 0.6  # 不补到 100%、不重归一化

    def test_fetch_failure_missing_envelope(self):
        class Boom:
            @staticmethod
            def index_stock_cons_csindex(symbol):
                raise RuntimeError("net down")
        with patch.dict(sys.modules, {"akshare": Boom}):
            env = fetch_etf_index_cons("000300")
        assert env["status"] == "missing" and "net down" in env["error"]


class TestEvidenceBudget:
    def _mock_holdings(self):
        rows = _rows(9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0)
        return _holdings_env(rows)

    def test_requires_explicit_params(self):
        with pytest.raises(TypeError):
            build_evidence_budget("515050")  # 试验参数必填，无固定默认

    def test_budget_stops_at_target(self):
        """目标 25%：9+8+7+6=30 ≥ 25 → 取 4 只（除目标外不继续取）。"""
        with patch("etf_data.query_etf_holdings", return_value=query_holdings_stub2()):
            out = build_evidence_budget("515050", target_cov_pct=25.0, max_names=10)
        assert len(out["names"]) == 4
        assert out["budget"]["selected_cov_pct"] == 30.0
        assert out["shortfall_note"] is None  # 42% 披露 ≥ 25% 目标
        assert out["evidence_fields"] == list(HOLDINGS_EVIDENCE_FIELDS)
        assert all(r["缺口"] == "未取证（预算内待查）" for r in out["evidence_rows"])

    def test_budget_disclosed_shortfall(self):
        """披露合计 42% < 目标 50% → 如实缺口，不扩采、不重归一化。"""
        with patch("etf_data.query_etf_holdings", return_value=query_holdings_stub2()):
            out = build_evidence_budget("515050", target_cov_pct=50.0, max_names=10)
        assert len(out["names"]) == 7
        assert out["budget"]["selected_cov_pct"] == 42.0
        assert out["budget"]["disclosed_sum_pct"] == 42.0
        assert "不足以覆盖" in out["shortfall_note"]

    def test_max_names_caps_selection(self):
        with patch("etf_data.query_etf_holdings", return_value=query_holdings_stub2()):
            out = build_evidence_budget("515050", target_cov_pct=90.0, max_names=2)
        assert len(out["names"]) == 2
        assert out["shortfall_note"]

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            build_evidence_budget("515050", target_cov_pct=0, max_names=3)
        with pytest.raises(ValueError):
            build_evidence_budget("515050", target_cov_pct=50, max_names=0)


def query_holdings_stub2():
    """前十合计 42%（不足 50% 目标）的离线持仓。"""
    env = _holdings_env(_rows(9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0))
    with patch("etf_data._bridge_get", return_value=env):
        return query_etf_holdings("515050")


class TestAudit20261009EvidenceBudget:
    """R04/R18：缺口文案按停止原因分派 + 覆盖加权按索引取权重。"""

    def _holdings(self, pcts, report_date="2026-06-30"):
        env = _holdings_env(_rows(*pcts), report_date=report_date)
        with patch("etf_data._bridge_get", return_value=env):
            return query_etf_holdings("515050")

    def test_max_names_cap_not_reported_as_disclosed_shortfall(self):
        """现场：披露 55% ≥ 目标 50%，max_names=2 截到 27% —— 不得说披露不足。"""
        h = self._holdings([15.0, 12.0, 10.0, 9.0, 5.0, 4.0])
        out = build_evidence_budget("515050", target_cov_pct=50.0, max_names=2,
                                    holdings=h)
        assert h["top10_sum_pct"] == 55.0
        assert out["budget"]["selected_cov_pct"] == 27.0
        assert out["budget"]["stop_reason"] == "max_names"
        assert "max_names=2" in out["shortfall_note"]
        assert "不足以覆盖" not in out["shortfall_note"]
        assert "55.0" in out["shortfall_note"]  # 已披露范围如实给出（供扩选决策）

    def test_disclosed_exhausted_reports_disclosed_shortfall(self):
        h = self._holdings([9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0])
        out = build_evidence_budget("515050", target_cov_pct=50.0, max_names=10,
                                    holdings=h)
        assert out["budget"]["stop_reason"] == "disclosed_exhausted"
        assert "不足以覆盖" in out["shortfall_note"]

    def test_target_reached_has_no_shortfall_note(self):
        h = self._holdings([15.0, 12.0, 10.0])
        out = build_evidence_budget("515050", target_cov_pct=25.0, max_names=10,
                                    holdings=h)
        assert out["budget"]["stop_reason"] == "target_reached"
        assert out["shortfall_note"] is None

    def test_coverage_weight_uses_row_index_not_dict_equality(self):
        """两条选中（等权重、仅一条有证据）→ 覆盖加权 = 该行权重，不重复计。"""
        h = self._holdings([10.0, 10.0])
        ev = {"600001": {"业务证据": "x", "期间": "Q2", "事件": "e", "关联命题": "p",
                         "来源": {"url": "https://x", "sha256": "ab", "locator": "§1"}}}
        out = build_evidence_budget("515050", target_cov_pct=15.0, max_names=10,
                                    holdings=h, evidence=ev)
        assert out["evidence_summary"]["evidence_rows"] == 1
        assert out["evidence_summary"]["evidence_weight_cov_pct"] == 10.0
        assert out["evidence_rows"][0]["来源"]["locator"] == "§1"
        assert out["evidence_rows"][1]["缺口"] == "未取证（预算内待查）"

    def test_invalid_evidence_source_marks_row_and_excludes_weight(self):
        h = self._holdings([10.0, 10.0])
        ev = {"600001": {"业务证据": "x", "来源": {"url": "https://x"}}}
        out = build_evidence_budget("515050", target_cov_pct=15.0, max_names=10,
                                    holdings=h, evidence=ev)
        assert out["evidence_summary"]["evidence_rows"] == 0
        assert out["evidence_summary"]["evidence_weight_cov_pct"] == 0.0
        assert "不完整" in out["evidence_rows"][0]["缺口"]
