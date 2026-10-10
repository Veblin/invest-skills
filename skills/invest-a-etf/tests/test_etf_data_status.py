"""B06 数据侧状态合同：五态分开 + 实际尝试源（不得静态合成）。"""

from __future__ import annotations

import pytest

from etf_data import DIMENSION_STATES, build_data_status


def _identity(status="verified_original", asset="stock"):
    return {
        "axes": {"asset_type": asset, "channel": "domestic", "strategy": "broad_market"},
        "verification": {"status": status, "verified_at": "2026-10-08"},
        "evidence": [{"url": "x"}],
        "applicability": {
            "index_pe": asset == "stock",
            "stock_pe_drilldown": asset == "stock",
            "stock_holdings_penetration": asset == "stock",
        },
    }


def _base_kwargs(**over):
    kw = dict(
        identity=_identity(),
        profile={
            "index_pe": 12.5, "index_pe_status": "mapped",
            "index_pe_caliber": "股本加权", "index_pe_note": "取值日期 2026-10-07",
            "premium_discount": 0.1, "industry_allocation": [{"industry": "银行", "pct": 30.0}],
            "industry_allocation_date": "2026-06-30", "industry_pe": None,
            "share_price_mv_est": {"value_yi": 100.0},
            "hedge_coverage": {"coverage": "high",
                               "verification": {"status": "static_map_only", "note": "静态映射，未逐条原文核验"}},
        },
        quote={"status": "available", "premium_discount": 0.1, "_error": None},
        kline={"status": "available", "adj_applied": True, "latest_nav_date": "2026-10-07"},
        share_history={"available": True, "date_range": "2026-09-01 ~ 2026-10-07",
                       "caliber_note": "估算口径"},
        history_block=None, events_block=None,
        # B-F01：tracking 块必须携带 producer 的实际调用留痕（attempted_sources），
        # 消费者沿用；缺失即视为无尝试记录（未执行 ≠ PASS，不得静态补源）
        # F2：limited 分两种真实出口——带 price_benchmark_deviation（两腿已采集，
        # 研究偏离已算）→ available；无偏离（基金腿未复权提前返回，基准未取）→ unknown。
        # 本 fixture 对应前者（价格基准 limited 的实际输出形态）。
        tracking={"status": "limited", "note": "缺总收益基准",
                  "window": "2025-10-01 ~ 2026-10-07",
                  "price_benchmark_deviation": {
                      "td_pct": 1.0, "te_pct_annualized": 2.0,
                      "research_only": True, "not_confirmatory": True},
                  "attempted_sources": ["fund_etf_fund_info_em（基金净值）",
                                        "Tushare fund_adj（复权因子）",
                                        "stock_zh_index_daily（指数日线）"]},
        # R3：fees 的 note 由 query_etf_fees 出字（data_status 只复用，不再自造
        # 现行性注记）；status 记录口径沿用
        fees={"status": "ok", "current_rate_verified": False,
              "source": {"source_type": "年度报告", "as_of": "2025-12-31"},
              "note": "年报实际计费口径；非现行合同费率核验——报告日现行费率未核"},
        est_net_assets={"status": "ok", "as_of": "2026-10-06", "caliber": "同日份额×净值估算"},
    )
    kw.update(over)
    return kw


class TestStatesContract:
    def test_all_dimensions_use_vocab(self):
        out = build_data_status(**_base_kwargs())
        assert out["dimensions"]
        for name, dim in out["dimensions"].items():
            assert dim["status"] in DIMENSION_STATES, name
            assert isinstance(dim["attempted_sources"], list), name

    def test_missing_requires_actual_attempted_sources(self):
        kw = _base_kwargs(quote={"status": "missing", "premium_discount": None,
                                 "_error": "spot 与回退源均不可用"},
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        out = build_data_status(**kw)["dimensions"]
        assert out["quote"]["status"] == "missing"
        assert out["quote"]["attempted_sources"]  # 实际尝试过的源必须非空
        assert out["premium_discount"]["status"] == "missing"
        assert out["premium_discount"]["attempted_sources"]

    def test_not_executed_has_no_fake_attempts(self):
        out = build_data_status(**_base_kwargs())["dimensions"]
        for name in ("holdings", "index_exposure", "pcf"):
            assert out[name]["status"] == "not_executed", name
            assert out[name]["attempted_sources"] == []
        assert "PCF" in out["pcf"]["note"]

    def test_not_applicable_for_non_stock(self):
        kw = _base_kwargs(identity=_identity(asset="commodity"))
        kw["profile"] = dict(kw["profile"], index_pe=None, index_pe_status="not_mapped",
                             industry_allocation=None, industry_pe=None)
        out = build_data_status(**kw)["dimensions"]
        assert out["index_pe"]["status"] == "not_applicable"
        assert out["holdings"]["status"] == "not_applicable"
        assert out["pcf"]["status"] == "not_executed"  # 未接入 ≠ 不适用

    def test_unknown_identity_dimensions_unknown(self):
        kw = _base_kwargs(identity=_identity(status="unknown", asset="unknown"))
        kw["profile"] = dict(kw["profile"], index_pe=None, index_pe_status="unknown_etf",
                             industry_allocation=None, industry_pe=None)
        out = build_data_status(**kw)["dimensions"]
        assert out["identity"]["status"] == "unknown"
        # 身份未核 → 适用性未知：状态记 unknown（≠ 确定不适用、≠ 失败），且不套股票 PE
        assert out["index_pe"]["status"] == "unknown"
        assert out["industry_allocation"]["status"] == "unknown"
        assert out["holdings"]["status"] == "unknown"

    def test_tencent_fallback_records_both_sources(self):
        """F3：只有 producer 结构化 source=tencent 才记录两源（不看文案）。"""
        kw = _base_kwargs(quote={"status": "available", "premium_discount": None,
                                 "source": "tencent",
                                 "_error": "spot 源不可用（empty response），已回退腾讯行情；折溢价不可得"})
        out = build_data_status(**kw)["dimensions"]
        assert out["quote"]["attempted_sources"] == ["fund_etf_spot_em", "腾讯行情"]

    def test_fees_historical_note(self):
        out = build_data_status(**_base_kwargs())["dimensions"]
        assert out["fees"]["status"] == "available"
        assert "报告日现行费率未核" in out["fees"]["note"]
        assert out["fees"]["as_of"] == "2025-12-31"

    def test_tracking_limited_is_available_state_with_caliber_note(self):
        """R01：limited = 两腿已采集、仅口径受限 → 五态记 available + producer_status。

        旧映射记 missing 会把「已取得的净值/指数序列与价格基准偏离研究数值」
        误报成源失败；口径细节由 producer_status/note 保留，TD/TE=None 的
        事实在 tracking 块本身。
        """
        out = build_data_status(**_base_kwargs())["dimensions"]
        assert out["tracking"]["status"] == "available"
        assert out["tracking"]["producer_status"] == "limited"
        assert "缺总收益基准" in out["tracking"]["note"]
        assert out["tracking"]["attempted_sources"]

    def test_tracking_missing_nav_is_missing_state(self):
        """反例：净值不可得（missing_nav）仍记 missing，不被 available 吞掉。"""
        tracking = {"status": "missing_nav", "note": "基金净值序列不可用",
                    "attempted_sources": []}
        dim = build_data_status(**_base_kwargs(tracking=tracking))["dimensions"]["tracking"]
        assert dim["status"] == "missing"
        assert dim["producer_status"] == "missing_nav"

    def test_history_not_requested_is_not_executed(self):
        out = build_data_status(**_base_kwargs())["dimensions"]
        assert out["history"]["status"] == "not_executed"
        assert out["history"]["attempted_sources"] == []

    def test_note_declares_flags_not_execution(self):
        out = build_data_status(**_base_kwargs())
        assert "flags" in out["note"] and "不代表" in out["note"]

    def test_source_record_attached_for_available_dims(self):
        out = build_data_status(**_base_kwargs())["dimensions"]
        assert out["nav"]["source_record"]["channel"]
        assert out["index_pe"]["source_record"]["original_disclosure"] is True


class TestRealAttemptedSourcesBF01:
    """B-F01：attempted_sources 必须来自 producer 实际调用，不得静态合成。

    现场：NAV 失败提前返回时指数源实际 0 次调用，旧实现却登记
    [fund_etf_fund_info_em, 指数日线]。修正后消费者只沿用 producer 记录，
    未发起的腿进 skipped_sources（带原因）。
    """

    def test_missing_nav_tracking_does_not_claim_index_attempt(self):
        tracking = {
            "status": "missing_nav", "note": "基金净值序列不可用",
            "attempted_sources": ["fund_etf_fund_info_em（基金净值）",
                                  "Tushare fund_adj（复权因子）"],
            "skipped_sources": [{"source": "stock_zh_index_daily（指数日线）",
                                 "reason": "基金净值不可用，基准腿未发起取数（提前返回）"}],
        }
        out = build_data_status(**_base_kwargs(tracking=tracking))["dimensions"]["tracking"]
        assert out["status"] == "missing"
        assert "指数日线" not in "".join(out["attempted_sources"])
        assert out["skipped_sources"][0]["source"].startswith("stock_zh_index_daily")
        assert "提前返回" in out["skipped_sources"][0]["reason"]

    def test_not_mapped_has_empty_attempts(self):
        tracking = {"status": "not_mapped", "note": "无指数映射",
                    "attempted_sources": [],
                    "skipped_sources": [{"source": "基金净值", "reason": "无指数映射，未发起取数"}]}
        out = build_data_status(**_base_kwargs(tracking=tracking))["dimensions"]["tracking"]
        assert out["status"] == "unknown"
        assert out["attempted_sources"] == []
        assert out["skipped_sources"]

    def test_est_net_assets_missing_share_skips_nav_leg(self):
        kw = _base_kwargs(est_net_assets={
            "status": "missing", "value_yi": None,
            "attempted_sources": ["Tushare fund_share"],
            "skipped_sources": [{"source": "fund_etf_fund_info_em（净值）",
                                 "reason": "份额不可用，净值腿未发起取数（提前返回）"}],
            "note": "份额序列不可用",
        })
        out = build_data_status(**kw)["dimensions"]["est_net_assets"]
        assert out["attempted_sources"] == ["Tushare fund_share"]
        assert out["skipped_sources"][0]["source"].startswith("fund_etf_fund_info_em")

    def test_tracking_without_producer_record_stays_empty(self):
        """producer 未记录尝试 → 不得由消费者静态补源（宁缺毋猜）。"""
        out = build_data_status(
            **_base_kwargs(tracking={"status": "missing_nav", "note": "x"}))["dimensions"]["tracking"]
        assert out["attempted_sources"] == []

    def test_extra_dims_collected_are_not_not_executed(self):
        """--payload-out 实采到 holdings/peers 后，状态不得固定 not_executed。"""
        kw = _base_kwargs(extra_dims={
            "holdings": {"status": "ok", "report_date": "2026-06-30",
                         "holding_scope": "基金披露前十大（非完整持仓）"},
        })
        dims = build_data_status(**kw)["dimensions"]
        assert dims["holdings"]["status"] == "available"
        assert dims["holdings"]["as_of"] == "2026-06-30"
        assert dims["index_exposure"]["status"] == "not_executed"  # 未请求仍为未执行

    def test_extra_dims_missing_recorded_missing(self):
        kw = _base_kwargs(extra_dims={"holdings": {"status": "missing", "note": "页面解析失败"}})
        dims = build_data_status(**kw)["dimensions"]
        assert dims["holdings"]["status"] == "missing"
        assert "页面解析失败" in dims["holdings"]["note"]

    def test_index_exposure_cross_month_not_joinable(self):
        kw = _base_kwargs(index_exposure={
            "status": "ok",
            "period_alignment": {"same_period": False, "joinable": False,
                                 "cons_as_of": "2026-09-30", "weight_as_of": "2026-08-31"},
        })
        dim = build_data_status(**kw)["dimensions"]["index_exposure"]
        assert dim["status"] == "available"
        assert "错月" in dim["note"] and "当期完整组合" in dim["note"]


class TestEtfAudit20261009:
    """R01/R02/R06/R08/R09/R11 复核回归（2026-10-09 ETF 审计）。"""

    # —— R02：as_of 不得是 "~ 20261009" 脏字符串 ——
    def test_share_history_compact_range_as_of_iso(self):
        kw = _base_kwargs(share_history={"available": True,
                                         "date_range": "20260904 ~ 20261009",
                                         "caliber_note": "x"})
        dim = build_data_status(**kw)["dimensions"]["share_history"]
        assert dim["as_of"] == "2026-10-09"

    def test_share_history_iso_range_as_of_iso(self):
        kw = _base_kwargs(share_history={"available": True,
                                         "date_range": "2026-09-04 ~ 2026-10-09"})
        assert build_data_status(**kw)["dimensions"]["share_history"]["as_of"] == "2026-10-09"

    def test_share_history_unparsable_range_as_of_none_not_dirty(self):
        kw = _base_kwargs(share_history={"available": True, "date_range": "n/a"})
        dim = build_data_status(**kw)["dimensions"]["share_history"]
        assert dim["as_of"] is None and "~" not in str(dim["as_of"])

    def test_tracking_window_as_of_iso(self):
        dim = build_data_status(**_base_kwargs())["dimensions"]["tracking"]
        assert dim["as_of"] == "2026-10-07"

    # —— R08：未发生的「回退腾讯」不得写成事实 ——
    def test_premium_missing_from_spot_row_does_not_claim_tencent_fallback(self):
        kw = _base_kwargs(quote={"status": "available", "premium_discount": None,
                                 "source": "fund_etf_spot_em", "_error": None},
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        dim = build_data_status(**kw)["dimensions"]["premium_discount"]
        assert dim["status"] == "missing"
        assert "未发生腾讯回退" in dim["note"] and "回退腾讯" not in dim["note"]

    def test_premium_missing_after_tencent_fallback_says_so(self):
        kw = _base_kwargs(quote={"status": "available", "premium_discount": None,
                                 "source": "tencent", "_error": "spot 源不可用（x），已回退腾讯行情"},
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        dim = build_data_status(**kw)["dimensions"]["premium_discount"]
        assert dim["status"] == "missing" and "回退腾讯" in dim["note"]

    def test_legacy_payload_without_source_field_says_unknown(self):
        """F3：旧 payload 无 quote.source（无追溯）→ 来源未知，不得按文案
        制造「腾讯已尝试/已成功」；premium 缺失同样不得断言回退。"""
        kw = _base_kwargs(quote={"status": "available", "premium_discount": None,
                                 "_error": "spot 源不可用，已回退腾讯行情"},
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        dims = build_data_status(**kw)["dimensions"]
        assert dims["quote"]["status"] == "available"
        assert dims["quote"]["attempted_sources"] == []
        assert "来源未留痕" in dims["quote"]["note"]
        assert dims["premium_discount"]["attempted_sources"] == []
        assert "不得断言" in dims["premium_discount"]["note"]

    def test_explicit_source_none_is_not_inferred_from_text(self):
        """F3 反例：显式 source=None（新 payload 两源均失败）不得被文案覆盖。"""
        kw = _base_kwargs(quote={"status": "missing", "premium_discount": None,
                                 "source": None,
                                 "_error": "spot 未取到（x）；腾讯行情回退未取得数据"},
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        dims = build_data_status(**kw)["dimensions"]
        assert dims["quote"]["status"] == "missing"
        assert dims["premium_discount"]["status"] == "missing"
        assert "回退" in dims["quote"]["note"]  # note 保留 producer 文案，但来源结论不推断

    # —— R06：est_net_assets 尝试源来自 producer（空列表 ≠ 缺字段）——
    def test_est_net_assets_empty_attempts_not_replaced_by_default(self):
        kw = _base_kwargs(est_net_assets={
            "status": "missing", "value_yi": None,
            "attempted_sources": [],
            "skipped_sources": [{"source": "Tushare fund_share（份额）",
                                 "reason": "无 TUSHARE_TOKEN：未发起网络（跳过）"}],
            "note": "份额序列不可用（Tushare 不可用）"})
        dim = build_data_status(**kw)["dimensions"]["est_net_assets"]
        assert dim["attempted_sources"] == []
        assert "TUSHARE_TOKEN" in dim["skipped_sources"][0]["reason"]

    def test_est_net_assets_legacy_missing_field_uses_default_sources(self):
        kw = _base_kwargs(est_net_assets={"status": "missing", "value_yi": None,
                                          "note": "x"})
        dim = build_data_status(**kw)["dimensions"]["est_net_assets"]
        assert dim["attempted_sources"] == ["Tushare fund_share", "fund_etf_fund_info_em（净值）"]

    # —— R09：行业 PE 有映射但被 csindex PE 早退，原因不得写成「无行业映射」——
    def test_industry_pe_skipped_reason_is_reported(self):
        kw = _base_kwargs(profile=dict(_base_kwargs()["profile"], industry_pe=None,
                                       industry_pe_status="skipped_index_pe_present",
                                       industry_pe_note="已有 csindex 指数 PE（931079）"))
        dim = build_data_status(**kw)["dimensions"]["industry_pe"]
        assert dim["status"] == "not_executed"
        assert "931079" in dim["note"] and "无行业映射" not in dim["note"]

    def test_industry_pe_source_null_is_missing(self):
        kw = _base_kwargs(profile=dict(_base_kwargs()["profile"], industry_pe=None,
                                       industry_pe_status="source_null",
                                       industry_pe_note="行业 PE 为空"))
        dim = build_data_status(**kw)["dimensions"]["industry_pe"]
        assert dim["status"] == "missing" and "为空" in dim["note"]

    # —— R11/F4：维度面在股票/非股票间稳定（不得少键），且非股票不被强写 ——
    def _non_stock(self, **over):
        kw = _base_kwargs(identity=_identity(asset="commodity"), **over)
        kw["profile"] = dict(kw["profile"], index_pe=None, index_pe_status="not_mapped",
                             industry_allocation=None, industry_pe=None)
        return kw

    def test_non_stock_has_same_dimension_face(self):
        stock = build_data_status(**_base_kwargs())["dimensions"]
        non_stock = build_data_status(**self._non_stock())["dimensions"]
        assert set(stock) == set(non_stock)
        assert non_stock["holdings_evidence"]["status"] == "not_applicable"
        assert non_stock["holdings_evidence"]["applicable"] is False
        # F4：同指数比较无股票前置（未请求=not_executed，不得强写 not_applicable）
        assert non_stock["same_index_comparison"]["status"] == "not_executed"

    def test_unknown_identity_non_stock_dims_are_unknown(self):
        kw = _base_kwargs(identity=_identity(status="unknown", asset="unknown"))
        kw["profile"] = dict(kw["profile"], index_pe=None, index_pe_status="unknown_etf",
                             industry_allocation=None, industry_pe=None)
        dims = build_data_status(**kw)["dimensions"]
        assert dims["holdings_evidence"]["status"] == "unknown"
        # 未请求的同指数比较仍是「未执行」（≠ 身份未知影响适用性）
        assert dims["same_index_comparison"]["status"] == "not_executed"

    def test_non_stock_with_provided_same_index_result_is_mapped(self):
        """F4 反例：商品 ETF 提交了 ok 比较结果 → 必须是 available（不得被
        非股票前置覆盖成 not_applicable）。"""
        kw = self._non_stock(extra_dims={"same_index_comparison": {
            "status": "ok", "index_code": "黄金9999",
            "common_window": {"start": "2025-10-09", "end": "2026-10-09",
                              "n_dates": 240},
            "gaps": []}})
        dim = build_data_status(**kw)["dimensions"]["same_index_comparison"]
        assert dim["status"] == "available"
        assert dim["as_of"] == "2026-10-09"
        assert "黄金9999" in dim["note"]

    def test_stock_with_provided_holdings_evidence_is_mapped(self):
        kw = _base_kwargs(extra_dims={"holdings_evidence": {
            "evidence_summary": {"evidence_rows": 2, "not_collected_rows": 1},
            "evidence_rows": [{"权重来源日期": "2026-06-30"}],
            "research_question": "q"}})
        dim = build_data_status(**kw)["dimensions"]["holdings_evidence"]
        assert dim["status"] == "available"

    def test_non_stock_with_provided_holdings_evidence_not_clobbered(self):
        """反例：非股票若真的提交了取证结果，也按实况（不被 not_applicable 覆盖）。"""
        kw = self._non_stock(extra_dims={"holdings_evidence": {
            "evidence_summary": {"evidence_rows": 0, "not_collected_rows": 2},
            "evidence_rows": [], "research_question": "q"}})
        dim = build_data_status(**kw)["dimensions"]["holdings_evidence"]
        assert dim["status"] == "missing"


class TestAudit20261009ProducerTraceConsumption:
    """R05/R06 同类（D11）：nav/share_history 的 attempted 也按 producer 留痕。"""

    def test_share_history_no_token_is_not_attempted(self):
        kw = _base_kwargs(share_history={
            "available": False, "note": "Tushare 不可用",
            "attempted_sources": [],
            "skipped_sources": [{"source": "Tushare fund_share",
                                 "reason": "Tushare 不可用"},
                                {"source": "Tushare fund_daily",
                                 "reason": "Tushare 不可用"}]})
        dim = build_data_status(**kw)["dimensions"]["share_history"]
        assert dim["status"] == "missing"
        assert dim["attempted_sources"] == []
        assert len(dim["skipped_sources"]) == 2

    def test_share_history_legacy_shape_keeps_static_sources(self):
        kw = _base_kwargs(share_history={"available": False, "note": "源失败"})
        dim = build_data_status(**kw)["dimensions"]["share_history"]
        assert dim["attempted_sources"] == ["Tushare fund_share", "Tushare fund_daily"]
        assert "skipped_sources" not in dim

    def test_nav_missing_with_producer_trace(self):
        kw = _base_kwargs(kline={"status": "missing",
                                 "_error": "etf_nav: empty response",
                                 "attempted_sources": [],
                                 "skipped_sources": [{"source": "fund_etf_fund_info_em（基金净值）",
                                                      "reason": "未发起取数：data_bridge 不可导入"}]})
        dim = build_data_status(**kw)["dimensions"]["nav"]
        assert dim["status"] == "missing"
        assert dim["attempted_sources"] == []
        assert "未发起" in dim["skipped_sources"][0]["reason"]

    def test_history_missing_with_producer_trace(self):
        kw = _base_kwargs(history_block={"history": {
            "status": "missing", "error": "nav 与 baostock 均不可用",
            "attempted_sources": ["fund_etf_fund_info_em（基金净值）",
                                  "baostock 日线（前复权）"],
            "skipped_sources": [{"source": "Tushare fund_adj（复权因子）",
                                 "reason": "无 TUSHARE_TOKEN：未发起网络（跳过）"}]}})
        dim = build_data_status(**kw)["dimensions"]["history"]
        assert dim["status"] == "missing"
        assert "baostock" in " ".join(dim["attempted_sources"])
        assert dim["skipped_sources"]


class TestAudit20261009TrackingLimitedExits:
    """F2（独立复检）：limited 的两条**真实 producer 出口**必须分派到不同五态。

    出口 A：基准为价格指数、偏离已算 → 已采集（available, producer_status=limited）
    出口 B：基金腿未复权提前返回（基准腿根本没取）→ unknown（已采集但不可验证）
    用例直接跑 compute_etf_tracking（不手造 limited 字段），再喂 build_data_status。
    """

    @staticmethod
    def _nav_rows(n: int = 90) -> list[dict]:
        rows = []
        nav = 1.0
        for i in range(n):
            nav *= 1.001
            rows.append({"date": f"2026-{1 + i // 28:02d}-{1 + i % 28 + 1:02d}",
                         "nav": round(nav, 4)})
        return rows

    @staticmethod
    def _bench_rows(nav_rows: list[dict]) -> list[dict]:
        rows, close = [], 3000.0
        for r in nav_rows:
            close *= 1.0005
            rows.append({"date": r["date"], "close": round(close, 2)})
        return rows

    def _tracking(self, *, adj_applied: bool) -> dict:
        from unittest.mock import patch as P

        nav_rows = self._nav_rows()
        kline = {"status": "available", "adj_applied": adj_applied,
                 "nav_history": nav_rows, "source": "fixture",
                 "attempted_sources": ["fixture NAV"], "source_trace": [],
                 "skipped_sources": []}
        with P.multiple("etf_data",
                        query_etf_kline=lambda symbol, days=60: kline,
                        _bridge_get=lambda getter, *a: (
                            {"status": "ok", "rows": self._bench_rows(nav_rows)}
                            if getter == "get_etf_index_daily" else None)):
            from etf_data import compute_etf_tracking
            return compute_etf_tracking("510300")

    def _dim(self, tracking: dict) -> dict:
        kw = _base_kwargs(tracking=tracking)
        return build_data_status(**kw)["dimensions"]["tracking"]

    def test_price_benchmark_limited_with_deviation_is_available(self):
        tracking = self._tracking(adj_applied=True)
        assert tracking["status"] == "limited"
        assert tracking["price_benchmark_deviation"]        # 出口 A 的实际形态
        dim = self._dim(tracking)
        assert dim["status"] == "available"
        assert dim["producer_status"] == "limited"

    def test_unadjusted_limited_without_deviation_is_unknown(self):
        tracking = self._tracking(adj_applied=False)
        assert tracking["status"] == "limited"
        assert tracking["price_benchmark_deviation"] is None  # 基准腿未取
        dim = self._dim(tracking)
        assert dim["status"] == "unknown"
        assert dim["producer_status"] == "limited"
        assert "未复权" in dim["note"]
        # 不得声称完整 tracking 可得：基准腿根本没发起
        assert not any("指数日线" in s for s in dim["attempted_sources"])


class TestAudit20261009BridgeUnavailableSites:
    """R05 同类扫描收口：index_pe / industry_allocation 在桥不可用时不得记已尝试。"""

    def test_index_pe_not_initiated_is_unknown_with_skip(self):
        kw = _base_kwargs(profile=dict(_base_kwargs()["profile"], index_pe=None,
                                       index_pe_status="mapped",
                                       index_pe_fetch_status="not_initiated",
                                       index_pe_fetch_skip_reason="未发起取数：data_bridge 模块不可导入"))
        dim = build_data_status(**kw)["dimensions"]["index_pe"]
        assert dim["status"] == "unknown"
        assert dim["attempted_sources"] == []
        assert "未发起" in dim["skipped_sources"][0]["reason"]

    def test_index_pe_attempted_failure_is_still_missing(self):
        kw = _base_kwargs(profile=dict(_base_kwargs()["profile"], index_pe=None,
                                       index_pe_status="mapped",
                                       index_pe_fetch_status="attempted"))
        dim = build_data_status(**kw)["dimensions"]["index_pe"]
        assert dim["status"] == "missing"
        assert dim["attempted_sources"] == ["csindex 指数 PE 文件"]

    def test_industry_allocation_not_initiated_on_stock(self):
        kw = _base_kwargs(profile=dict(_base_kwargs()["profile"], industry_allocation=None,
                                       industry_allocation_fetch_status="not_initiated",
                                       industry_allocation_skip_reason="未发起取数：x"))
        dim = build_data_status(**kw)["dimensions"]["industry_allocation"]
        assert dim["status"] == "unknown"
        assert dim["attempted_sources"] == []
        assert dim["skipped_sources"]

    def test_industry_allocation_non_stock_stays_not_applicable(self):
        """资产闸门优先于传输状态：非股票 ETF 不因桥状态变成 unknown。"""
        kw = _base_kwargs(identity=_identity(asset="commodity"),
                          profile=dict(_base_kwargs()["profile"], index_pe=None,
                                       index_pe_status="not_mapped",
                                       industry_allocation=None,
                                       industry_allocation_fetch_status="not_initiated"))
        dim = build_data_status(**kw)["dimensions"]["industry_allocation"]
        assert dim["status"] == "not_applicable"


class TestAudit20261009UnknownSourceWording:
    """F3 措辞准确性：无来源信息**不假定 payload 新旧**（显式 None 与键缺失同述）。"""

    @pytest.mark.parametrize("quote", [
        {"status": "available", "source": None, "premium_discount": None, "_error": "x"},
        {"status": "available", "premium_discount": None, "_error": "x"},
    ])
    def test_unknown_source_note_covers_both_forms(self, quote):
        kw = _base_kwargs(quote=quote,
                          profile=dict(_base_kwargs()["profile"], premium_discount=None))
        dims = build_data_status(**kw)["dimensions"]
        assert "source 为空或缺失" in dims["quote"]["note"]
        assert "旧 payload" not in dims["quote"]["note"]
        assert "source 为空或缺失" in dims["premium_discount"]["note"]
        assert "旧 payload" not in dims["premium_discount"]["note"]
