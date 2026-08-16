"""v0.2.6 工作流评估 P1 数据层修复的回归测试（F1 系列 ETF 侧）。

覆盖缺陷（详见 host-docs/v0.2.6-workflow-eval/workflow-eval.md）：
  F1-1  spot 失败回退腾讯行情（不整列缺失）
  F1-2  HOLDINGS_CLUSTER_MAP 补军工/科创50 → 聚类不再 100% 未归类
  F1-4  futures-basis 品种映射与 hedge-map 对齐（科创50 期货）
  F1-3  events/512660.json 存在且为合法 JSON Lines
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EVENTS_DIR = Path(__file__).resolve().parents[1] / "events"


# ---------------------------------------------------------------------------
# F1-2: 聚类映射
# ---------------------------------------------------------------------------
class TestClusterMapCoverage:
    def test_military_top10_all_mapped(self):
        from etf_data import HOLDINGS_CLUSTER_MAP, _build_holdings_clusters
        rows = [
            {"code": "600150", "name": "中国船舶", "pct": 9.18},
            {"code": "002179", "name": "中航光电", "pct": 3.64},
            {"code": "688002", "name": "睿创微纳", "pct": 3.6},
            {"code": "600879", "name": "航天电子", "pct": 3.48},
            {"code": "300395", "name": "菲利华", "pct": 3.42},
            {"code": "002625", "name": "光启技术", "pct": 3.24},
            {"code": "600893", "name": "航发动力", "pct": 3.17},
            {"code": "600118", "name": "中国卫星", "pct": 3.0},
            {"code": "600760", "name": "中航沈飞", "pct": 2.89},
            {"code": "601698", "name": "中国卫通", "pct": 2.38},
        ]
        clusters = _build_holdings_clusters(rows)
        unmapped = [c for c in clusters if c["cluster"] == "未归类"]
        assert unmapped == []
        total = sum(c["sum_pct"] for c in clusters)
        assert total == pytest.approx(38.0, abs=0.1)
        assert clusters[0]["cluster"] == "船舶总装"

    def test_star50_top10_all_mapped(self):
        from etf_data import HOLDINGS_CLUSTER_MAP, _build_holdings_clusters
        rows = [
            {"code": "688256", "name": "寒武纪", "pct": 9.3},
            {"code": "688008", "name": "澜起科技", "pct": 8.13},
            {"code": "688012", "name": "中微公司", "pct": 8.0},
            {"code": "688041", "name": "海光信息", "pct": 7.89},
            {"code": "688981", "name": "中芯国际", "pct": 7.41},
            {"code": "688498", "name": "源杰科技", "pct": 4.94},
            {"code": "688525", "name": "佰维存储", "pct": 4.83},
            {"code": "688521", "name": "芯原股份", "pct": 4.06},
            {"code": "688072", "name": "拓荆科技", "pct": 4.03},
            {"code": "688347", "name": "华虹宏力", "pct": 3.62},
        ]
        clusters = _build_holdings_clusters(rows)
        unmapped = [c for c in clusters if c["cluster"] == "未归类"]
        assert unmapped == []
        total = sum(c["sum_pct"] for c in clusters)
        assert total == pytest.approx(62.21, abs=0.1)
        assert clusters[0]["cluster"] == "AI 芯片"
        # 映射表本身覆盖全部代码
        for r in rows:
            assert r["code"] in HOLDINGS_CLUSTER_MAP


# ---------------------------------------------------------------------------
# F1-4: futures-basis 品种映射
# ---------------------------------------------------------------------------
class TestFuturesSymbolForEtf:
    def test_star50_futures_recognized(self):
        from futures_basis import futures_symbol_for_etf
        assert futures_symbol_for_etf("588000") == "科创50"

    def test_im_etf_still_works(self):
        from futures_basis import futures_symbol_for_etf
        assert futures_symbol_for_etf("512100") == "IM"

    def test_no_futures_etf_none(self):
        from futures_basis import futures_symbol_for_etf
        assert futures_symbol_for_etf("512660") is None


class TestFuturesBasisNote:
    def test_star50_basis_unavailable_reason_honest(self, monkeypatch):
        """588000 基差不可得的 note 须说明「品种映射存在但数据层未覆盖」，
        不再输出与 hedge-map 矛盾的「无映射」。"""
        import futures_basis as fb

        class _FakeStore:
            @staticmethod
            def init_db():
                return None

            @staticmethod
            def load_futures_daily(**kwargs):
                return []

        monkeypatch.setattr("lib.store", _FakeStore, raising=False)
        # 仅验证符号解析路径（不依赖 store）：品种解析 + note 构造
        fsym = fb.futures_symbol_for_etf("588000")
        assert fsym == "科创50"
        note = fb.query_futures_basis("588000")
        assert note.get("futures_symbol") == "科创50"
        assert "品种映射存在" in (note.get("note") or "")
        assert "无映射" not in (note.get("note") or "")


# ---------------------------------------------------------------------------
# F1-1: spot 回退腾讯
# ---------------------------------------------------------------------------
class TestTencentEtfFallback:
    def test_market_mapping_for_etf(self):
        from etf_data import _q_tencent_etf_quote

        # 无网环境下不抛异常，返回 None 或 dict（结构校验）
        result = _q_tencent_etf_quote("512660")
        assert result is None or isinstance(result, dict)

    def test_quote_fallback_sets_status(self, monkeypatch):
        """spot 失败时 quote 走腾讯回退：status=available、折溢价不可得标注。"""
        import etf_data

        monkeypatch.setattr(
            etf_data, "_lookup_etf_spot_row",
            lambda symbol: (None, "etf_spot: fetch failed"),
        )
        monkeypatch.setattr(
            etf_data, "_q_tencent_etf_quote",
            lambda symbol: {"price": 1.152, "change_pct": 0.35,
                            "volume": 1768251.0, "amount": 203311509.0},
        )
        q = etf_data.query_etf_quote("512660")
        assert q["status"] == "available"
        assert q["price"] == 1.152
        assert q["premium_discount"] is None
        assert "回退腾讯" in q["_error"]


# ---------------------------------------------------------------------------
# F1-3: events/512660.json
# ---------------------------------------------------------------------------
class TestEventsFile512660:
    def test_events_file_valid_jsonl(self):
        p = EVENTS_DIR / "512660.json"
        assert p.exists()
        rows = []
        for line in p.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                rows.append(json.loads(line))
        assert len(rows) >= 4
        for r in rows:
            assert {"date", "event", "source_url", "published_date", "confidence"} <= set(r.keys())
            assert r["confidence"] in ("一手", "二手")
