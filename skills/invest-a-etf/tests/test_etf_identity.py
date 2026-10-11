"""B01-1 身份三轴 / 适用性 / 费率原文登记（离线，无网络）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from etf_data import (
    ETF_FEE_SCHEDULE,
    ETF_IDENTITY,
    _CATEGORY_LABELS,
    _infer_category_from_name,
    query_etf_category,
    query_etf_fees,
    query_etf_data,
    query_etf_identity,
)


class TestVerifiedIdentity:
    def test_510880_is_stock_factor_not_bond(self):
        """纠正：510880 原误标 bond（红利ETF）→ 股票型因子（原文核验）。"""
        assert query_etf_category("510880")["category"] == "factor"
        assert _CATEGORY_LABELS["factor"] == "因子/策略ETF"
        ident = query_etf_identity("510880")
        assert ident["axes"] == {"asset_type": "stock", "channel": "domestic",
                                 "strategy": "factor"}
        assert ident["tracked_index"] == {"name": "上证红利指数", "code": "000015"}
        assert ident["applicability"]["index_pe"] is True

    def test_510300_broad_market_stock_domestic_passive(self):
        ident = query_etf_identity("510300")
        assert ident["axes"]["asset_type"] == "stock"
        assert ident["axes"]["channel"] == "domestic"
        assert ident["axes"]["strategy"] == "broad_market"
        assert ident["management"] == "passive"
        assert ident["verification"]["status"] == "verified_original"

    def test_515050_thematic_with_original_evidence(self):
        ident = query_etf_identity("515050")
        assert ident["axes"]["strategy"] == "thematic"
        assert ident["tracked_index"]["code"] == "931079"
        assert ident["verification"]["status"] == "verified_original"

    def test_verified_evidence_is_readable_and_traceable(self):
        """原文证据：URL + 实际读取内容 hash + 定位 + 读取时间（不以 URL 当已读）。"""
        for code in ("510300", "515050", "510880"):
            ident = query_etf_identity(code)
            assert ident["verification"]["verified_at"] == "2026-10-08"
            assert ident["evidence"], code
            ev = ident["evidence"][0]
            assert ev["url"].startswith("https://")
            assert ev["sha256"]
            assert ev["read_at"] == "2026-10-08"
            assert ev["locator"]
            assert ev["quote"]


class TestNonStockApplicability:
    @pytest.mark.parametrize("code,asset", [
        ("518880", "commodity"), ("511880", "money_market"),
        ("513100", "stock"), ("513050", "stock"),
    ])
    def test_axes_derived_for_registry_codes(self, code, asset):
        ident = query_etf_identity(code)
        assert ident["axes"]["asset_type"] == asset
        assert ident["verification"]["status"] == "mapped_unverified"

    def test_index_pe_not_applicable_for_non_stock(self):
        with patch("etf_data._bridge_get", return_value=None), \
             patch("etf_data._lookup_etf_spot_row", return_value=(None, "skip")):
            for code in ("518880", "511880"):
                ident = query_etf_identity(code)
                assert ident["applicability"]["index_pe"] is False
                assert ident["applicability"]["stock_pe_drilldown"] is False
            # 商品：期货展期口径；货币：不适用股票 PE
            assert query_etf_identity("518880")["applicability"]["futures_roll"] is True
            assert any("货币" in n for n in query_etf_identity("511880")["applicability"]["notes"])

    def test_cross_border_channel_note(self):
        ident = query_etf_identity("513100")
        assert ident["axes"]["channel"] == "cross_border"
        assert any("时区" in n for n in ident["applicability"]["notes"])

    def test_unknown_code_all_not_applicable(self):
        with patch("etf_data._bridge_get", return_value=None), \
             patch("etf_data._lookup_etf_spot_row", return_value=(None, "skip")):
            ident = query_etf_identity("999999")
        assert ident["axes"] == {"asset_type": "unknown", "channel": "unknown",
                                 "strategy": "unknown"}
        assert ident["verification"]["status"] == "unknown"
        assert ident["applicability"]["index_pe"] is False
        assert ident["applicability"]["stock_holdings_penetration"] is False
        assert any("待核" in n or "不得" in n for n in ident["applicability"]["notes"])

    def test_name_inference_is_marked_unverified(self):
        """名称命中类关键词 → bond 但标 inferred_unverified（未核不触发必填）。"""
        assert _infer_category_from_name("XX国债ETF") == "bond"
        assert _infer_category_from_name("某某精选ETF") == "unknown"  # 兜底不再猜 sector


class TestQueryEtfDataIdentityBlock:
    @patch("etf_data._lookup_etf_spot_row", return_value=(None, "etf_spot: test skip"))
    def test_query_etf_data_carries_identity_and_applicability(self, _mock_spot):
        """纯身份/适用性单元：**外部源边界必须隔离**（R3 计划覆盖待核）。

        此前只 mock spot，query_etf_data 内的 bridge 指数 PE 等会走真实
        data_bridge（无 L2 命中即联网）——组合 import/缓存顺序不同会造成
        伪通过/长等待。断言不缩减：全部字段断言保留。
        """
        with patch("etf_data._bridge_get", return_value=None):
            r = query_etf_data("510300")
        assert r["identity"]["axes"]["strategy"] == "broad_market"
        assert r["applicability"]["index_pe"] is True
        assert r["valuation_applicability"]["method"] == "index_official_only"
        assert r["valuation_applicability"]["no_stock_pe_weighting"] is True
        # category 兼容保留（旧消费方不破）
        assert r["category"]["category"] == "broad_market"

    @patch("etf_data._lookup_etf_spot_row", return_value=(None, "etf_spot: test skip"))
    def test_non_stock_valuation_applicability_false(self, _mock_spot):
        with patch("etf_data._bridge_get", return_value=None):
            r = query_etf_data("518880")
        assert r["valuation_applicability"]["pe_applicable"] is False
        assert "非股票类" in r["valuation_applicability"]["reason"]


class TestFeeSchedule:
    def test_510300_2024_material_is_historical_only(self):
        fees = query_etf_fees("510300")
        assert fees["status"] == "ok"
        assert fees["current_rate_verified"] is False
        assert fees["evidence_type"] == "historical_disclosure_only"
        assert "历史披露值" in fees["note"]
        for item in fees["items"]:
            assert "2024-06-20" in item["rate_as_of"]
        assert fees["source"]["sha256"]
        assert fees["source"]["read_at"] == "2026-10-08"
        assert fees["source"]["locator"]

    def test_510880_contract_disclosure_version_read_current_unverified(self):
        """R3：`current_rate_verified`=**报告日现行费率已核**（恢复原义）。

        只读到 2026-06-13 版本 → False（不得换义成「版本内已核」，也不另设
        currency/版本布尔）；版本已读由 source.version/as_of/locator 披露。
        """
        fees = query_etf_fees("510880")
        assert fees["current_rate_verified"] is False
        assert fees["evidence_type"] == "contract_disclosure"
        assert fees["source"]["version"].startswith("更新招募说明书 2026 年第 1 号")
        assert fees["source"]["as_of"] == "2026-06-13"
        assert "2026-06-13" in fees["note"] and "费率已读" in fees["note"]
        assert "报告日现行费率未核" in fees["note"]
        assert "现行费率已核" not in fees["note"]   # 未核时不得出现该断言
        # 不保留并行现行性合同（currency 在金融通常表示币种）
        assert "currency_verified" not in fees
        assert "current_rate_verified_as_of" not in fees

    def test_current_rate_verified_true_requires_verified_at(self):
        """True 必须绑定实际核查时点：无 verified_at 的内置 True 视为未核。"""
        from etf_data import ETF_FEE_SCHEDULE, query_etf_fees

        code = "510880"
        saved = dict(ETF_FEE_SCHEDULE[code])
        try:
            ETF_FEE_SCHEDULE[code] = {**saved, "current_rate_verified": True,
                                      "verified_at": None}
            assert query_etf_fees(code)["current_rate_verified"] is False
            ETF_FEE_SCHEDULE[code] = {**saved, "current_rate_verified": True}
            fees = query_etf_fees(code)
            assert fees["current_rate_verified"] is True
            assert "报告日现行费率已核" in fees["note"]
        finally:
            ETF_FEE_SCHEDULE[code] = saved

    def test_unknown_symbol_unverified_not_zero(self):
        fees = query_etf_fees("999999")
        assert fees["status"] == "unverified"
        assert fees["items"] == []
        assert fees["current_rate_verified"] is False
        assert "不得" in fees["note"]

    def test_all_schedule_entries_have_traceable_source(self):
        for code, entry in ETF_FEE_SCHEDULE.items():
            src = entry["source"]
            assert src["url"].startswith("https://"), code
            assert src.get("sha256") or src.get("locator"), code
            assert entry.get("current_rate_verified") in (True, False)
            assert entry.get("evidence_type") in (
                "historical_disclosure_only", "annual_report_actual",
                "contract_disclosure")
            # R3：current_rate_verified=报告日现行已核——True 必须绑定实际核查
            # 时点（verified_at），否则不允许 True；不再有 currency/as_of 第二套布尔
            if entry.get("current_rate_verified"):
                assert entry.get("verified_at"), code
            assert "currency_verified" not in entry, code
            assert "current_rate_verified_as_of" not in entry, code
            assert src.get("version"), code  # 版本用 source 元信息披露

    def test_identity_registry_verified_entries_have_evidence(self):
        for code, entry in ETF_IDENTITY.items():
            if entry.get("verification", {}).get("status") == "verified_original":
                assert entry.get("evidence"), code


def test_diagnose_hedge_map_check_is_cwd_independent(tmp_path, monkeypatch):
    """B06 现场：`_check_hedge_map_consistency` 曾用布局**相对路径**读文件
    （skill_relpath），CWD 非布局根时误报「对冲表文档缺失」rc 1（实测：从
    /tmp 运行仓内 CLI 失败）。改为绝对解析（default_reference）后必须通过。"""
    import importlib.util
    import sys
    from pathlib import Path as _P

    etf_py = _P(__file__).resolve().parent.parent / "scripts" / "etf.py"
    name = "etf_cli_diagnose_cwd_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, etf_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.chdir(tmp_path)  # CWD 与两种布局根都不同
    assert mod.cmd_diagnose() == 0


class TestAudit20261009IndexResolution:
    """R03/R12/R14：分类标签中文一致、PE 映射与跟踪身份统一解析、单次分类查询。"""

    def test_unknown_label_is_chinese_on_inference_paths(self):
        sina = {"status": "ok", "rows": [{"代码": "560000", "名称": "某某精选ETF"}]}

        def fake(getter, *a, **k):
            return sina if getter == "get_etf_category_sina" else None

        with patch("etf_data._bridge_get", fake):
            out = query_etf_category("560000")
        assert out == {"category": "unknown", "label": "未知", "source": "sina_dynamic"}
        assert _infer_category_from_name("某某精选ETF") == "unknown"

    def test_query_etf_identity_calls_category_once(self):
        calls: list[str] = []

        def counting(symbol):
            calls.append(symbol)
            return {"category": "unknown", "label": "未知", "source": "none"}

        with patch("etf_data.query_etf_category", counting):
            ident = query_etf_identity("999999")
        assert len(calls) == 1
        assert ident["category"]["category"] == "unknown"
        assert ident["axes"] == {"asset_type": "unknown", "channel": "unknown",
                                 "strategy": "unknown"}

    def test_510880_pe_mapping_and_tracking_identity_agree(self):
        """R12：510880 的 PE 映射与身份跟踪指数同为 000015（不再自相矛盾）。"""
        from etf_data import CSINDEX_MAP, resolve_tracked_index

        ident = query_etf_identity("510880")
        res = resolve_tracked_index("510880")
        assert CSINDEX_MAP["510880"] == "000015"
        assert ident["tracked_index"]["code"] == res["code"] == "000015"
        assert res["source"] == "CSINDEX_MAP" and res["pe_mapped"] is True

    def test_identity_only_code_marks_pe_unmapped_but_resolves(self):
        """CSINDEX_MAP 未登记、身份已核 → 代码可用且标明来源（PE 映射 ≠ 跟踪身份）。"""
        from etf_data import resolve_tracked_index

        fake_ident = {"tracked_index": {"name": "X", "code": "399999"},
                      "verification": {"status": "verified_original"}}
        with patch("etf_data.CSINDEX_MAP", {}):
            res = resolve_tracked_index("510880", identity=fake_ident)
        assert res["code"] == "399999" and res["pe_mapped"] is False
        assert "identity.tracked_index" in res["source"]

    def test_no_source_keeps_unknown(self):
        from etf_data import resolve_tracked_index

        with patch("etf_data.CSINDEX_MAP", {}):
            res = resolve_tracked_index("999999", identity={"tracked_index": None,
                                                            "verification": {}})
        assert res == {"code": None, "source": None, "pe_mapped": False,
                       "identity_status": None, "currency": None}

    def test_query_etf_data_records_index_code_source(self):
        """反例：PE 未映射且身份无代码 → 状态 not_mapped 且措辞不推断产品身份。"""
        with patch("etf_data._bridge_get", return_value=None), \
             patch("etf_data._lookup_etf_spot_row", return_value=(None, "skip")):
            r = query_etf_data("512660")
        assert r["index_pe_status"] == "not_mapped"
        assert r["index_code"] is None
        assert "不等于该产品没有跟踪指数" in r["index_pe_note"]
