"""merge_collections 回归测试（/code-review max F2/F9，无网络）。

F2: 多采集器合并时 payload 恒为 dims[0] → 有数据的维度被丢弃；
    research_summary 不跨采集器合并 → forecast/业绩预告静默消失。
F9: available 计数用 status != 'failed'（该状态从未被发射）→ 恒等于 total。
"""

from __future__ import annotations

import pytest

from merge_collections import merge_collections


def _dim(name, status="available", data=None, summary=None, source="tushare"):
    d = {
        "dimension": name,
        "display": name,
        "data": data,
        "status": status,
        "error": None if data is not None else "err",
        "_meta": {
            "source": source,
            "success": data is not None,
            "all_sources": [{"source": source, "success": data is not None,
                             "data": data}],
        },
    }
    if summary is not None:
        d["research_summary"] = summary
    return d


def _collection(dims, symbol="600176"):
    return {"symbol": symbol, "dimensions": dims}


class TestMergeDataFallback:
    """F2: 首个有数据的维度成为主数据（连带 status/_meta 跟随）。"""

    def test_second_collector_data_promoted(self):
        a = _dim("research", status="missing", data=None, summary={"latest_ratings": "A"})
        b = _dim("research", status="available",
                 data=[{"source": "forecast", "title": "业绩预告"}],
                 summary={"company_guidance": "B 预告"})
        merged = merge_collections([_collection([a]), _collection([b])])
        dims = {d["dimension"]: d for d in merged["dimensions"]}
        r = dims["research"]
        # 主数据来自 B（有数据）而非 A
        assert r["data"] == b["data"]
        assert r["status"] == "available"
        assert r["_meta"]["success"] is True

    def test_research_summary_merged_keywise(self):
        a = _dim("research", data=[{"source": "report_rc"}],
                 summary={"latest_ratings": ["A"], "eps_forecasts": None})
        b = _dim("research", data=[{"source": "forecast"}],
                 summary={"company_guidance": "B 预告", "eps_forecasts": [1.0, 2.0]})
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["research"]
        sm = r["research_summary"]
        # first-non-None per key：A 的 ratings + B 的 guidance/eps
        assert sm["latest_ratings"] == ["A"]
        assert sm["company_guidance"] == "B 预告"
        assert sm["eps_forecasts"] == [1.0, 2.0]

    def test_failed_collector_skeleton_summary_not_shadowing(self):
        """失败采集器的骨架默认值（[]/''/no_data）不得遮蔽健康采集器数据。"""
        a = _dim("research", status="missing", data=None,
                 summary={"latest_ratings": [], "eps_forecasts": [],
                          "profit_forecasts": [], "summary_text": "",
                          "status": "no_data"})
        b = _dim("research", data=[{"source": "forecast"}],
                 summary={"latest_ratings": [{"rating": "买入"}],
                          "profit_forecasts": [1.0, 2.0], "status": "ok"})
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["research"]
        sm = r["research_summary"]
        # A 的骨架默认值被跳过，B 的真实数据胜出
        assert sm["latest_ratings"] == [{"rating": "买入"}]
        assert sm["profit_forecasts"] == [1.0, 2.0]
        assert sm["status"] == "ok"

    def test_empty_container_data_not_chosen(self):
        """空 list/dict 的 data 不得当选主数据（非交易日 quote 空行场景）。"""
        a = _dim("quote", status="available", data=[],
                 summary=None, source="tushare.daily")
        b = _dim("quote", status="available", data={"close": 10.0},
                 summary=None, source="tencent_finance")
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["quote"]
        assert r["data"] == {"close": 10.0}
        assert r["_meta"]["source"] == "tencent_finance"
        assert merged["summary"]["available"] == 1

    def test_cross_validate_uses_data_bearing_pair(self):
        """交叉验证比较两个有数据的源（dims[0] 无数据时不静默消失）。"""
        a = _dim("valuation", status="missing", data=None, source="tushare")
        b = _dim("valuation", data={"pe_ttm": 1.0, "pb": 1.0, "ps_ttm": 1.0},
                 source="akshare")
        c = _dim("valuation", data={"pe_ttm": 1.4, "pb": 1.0, "ps_ttm": 1.0},
                 source="baostock")
        merged = merge_collections([_collection([a]), _collection([b]),
                                    _collection([c])])
        cv = merged["_cross_validation"]
        assert cv["results"], "B vs C 的 40% 分歧必须被发现"
        # _diff_pct 用相对差：|1.4-1.0| / ((1.4+1.0)/2) = 33.3%
        assert cv["results"][0]["max_diff_pct"] >= 30.0
        assert cv["need_tiebreaker"] is True
        assert cv["results"][0]["source_a"] == "akshare"
        assert cv["results"][0]["source_b"] == "baostock"

    def test_single_dim_untouched(self):
        a = _dim("quote", data={"close": 10.0})
        merged = merge_collections([_collection([a])])
        assert merged["dimensions"][0] is a or merged["dimensions"][0] == a

    def test_input_not_polluted_by_merge_meta(self):
        """D7：浅拷贝主数据 + 全新 _meta，合并产物不得原地污染输入 collection。"""
        a = _dim("quote", data={"close": 10.0}, source="tushare.daily")
        b = _dim("quote", data={"close": 10.1}, source="akshare")
        a_meta_before = dict(a["_meta"])
        merge_collections([_collection([a]), _collection([b])])
        # 输入 _meta 未被写入 alternative_sources/all_sources/multi_source_count
        assert a["_meta"] == a_meta_before
        assert "alternative_sources" not in a["_meta"]
        assert "multi_source_count" not in a["_meta"]
        # 输入 data 未被原地修改
        assert a["data"] == {"close": 10.0}


class TestMergeSummaryAvailable:
    """F9: available 只计有数据的 available/partial 维度。"""

    def test_missing_dim_not_counted(self):
        a = _dim("quote", data={"close": 10.0})
        b = _dim("financials", status="missing", data=None)
        c = _dim("research", status="partial", data=[{"x": 1}])
        merged = merge_collections([_collection([a, b, c])])
        assert merged["summary"]["total"] == 3
        assert merged["summary"]["available"] == 2  # quote + partial research

    def test_all_missing_zero_available(self):
        a = _dim("quote", status="missing", data=None)
        merged = merge_collections([_collection([a])])
        assert merged["summary"]["total"] == 1
        assert merged["summary"]["available"] == 0


class TestAllSourcesUnion:
    """A8: 合并 all_sources 保留失败条目、source_count 用 chosen 计数（不虚增）。"""

    def test_failed_source_kept_and_count_not_inflated(self):
        a_srcs = [
            {"source": "tushare.daily", "success": True, "data": [{"close": 10.0}],
             "data_available": True},
        ]
        b_srcs = [
            {"source": "tushare.daily", "success": True, "data": [{"close": 20.0}],
             "data_available": True},
            {"source": "akshare.stock_zh_a_hist", "success": False, "data": None,
             "data_available": False, "error": "Connection refused"},
        ]
        a = {
            "dimension": "kline", "display": "日K线",
            "data": [{"close": 10.0}], "status": "available",
            "_meta": {"source": "tushare.daily", "success": True,
                      "source_count": 1, "multi_source": False,
                      "cross_validation": None,
                      "all_sources": a_srcs},
        }
        b = {
            "dimension": "kline", "display": "日K线",
            "data": [{"close": 20.0}], "status": "available",
            "_meta": {"source": "tushare.daily", "success": True,
                      "source_count": 2, "multi_source": True,
                      "cross_validation": "convergence",
                      "all_sources": b_srcs},
        }
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["kline"]
        meta = r["_meta"]
        # primary.data 来自 chosen（a）→ source_count 恒为 chosen 计数，不并集虚增
        assert meta["source_count"] == 1
        assert meta["multi_source"] is False
        # 失败条目保留（provenance）：evidence 的 ❌ 渲染分支重新可见
        names = [s["source"] for s in meta["all_sources"]]
        assert names == ["tushare.daily", "akshare.stock_zh_a_hist"]
        failed = [s for s in meta["all_sources"] if s["source"].startswith("akshare")][0]
        assert failed["success"] is False
        # 同名去重：chosen（a）的 payload 优先，不被 b 的同名条目覆盖
        td = [s for s in meta["all_sources"] if s["source"] == "tushare.daily"][0]
        assert td["data"] == a_srcs[0]["data"]


class TestMetaNull:
    """缺陷 2: 维度 _meta 为 null 时不得崩溃（None.get 防护）。"""

    def test_meta_null_no_crash(self):
        """两份 _meta: null 的 financials → 合并成功，交叉验证不抛异常。"""
        a = _dim("financials", data={"roe": 5.0, "eps": 1.0}, source="tushare")
        b = _dim("financials", data={"roe": 5.1, "eps": 1.0}, source="akshare")
        a["_meta"] = None
        b["_meta"] = None
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["financials"]
        assert r["_meta"]["multi_source_count"] == 2
        assert merged["_cross_validation"]["results"]
        # 无来源信息时标记 unknown
        assert merged["_cross_validation"]["results"][0]["source_a"] == "unknown"

    def test_meta_null_mixed_with_normal(self):
        """_meta: null 的源在 alternative_sources 中记录 source unknown。"""
        a = _dim("quote", data={"close": 10.0}, source="tushare.daily")
        b = _dim("quote", data={"close": 10.1}, source="akshare")
        b["_meta"] = None
        merged = merge_collections([_collection([a]), _collection([b])])
        r = {d["dimension"]: d for d in merged["dimensions"]}["quote"]
        # chosen 是正常 meta 的 a；alt 仅含 b（_meta: null）→ source unknown
        assert r["_meta"]["alternative_sources"] == [
            {"source": "unknown", "fetched_at": ""}
        ]


class TestCollectionGuards:
    """缺陷 3: 空/非法输入守卫 + symbol 不一致告警。"""

    def test_empty_collections_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            merge_collections([])

    def test_non_dict_element_raises(self):
        with pytest.raises(ValueError, match=r"collections\[1\] 不是 dict"):
            merge_collections([_collection([]), "junk"])

    def test_symbol_mismatch_warns_and_uses_first(self, caplog):
        a = _dim("quote", data={"close": 10.0})
        b = _dim("quote", data={"close": 10.1})
        merged = merge_collections(
            [_collection([a], symbol="600176"), _collection([b], symbol="000001")]
        )
        assert merged["symbol"] == "600176"
        assert "symbol 不一致" in caplog.text
