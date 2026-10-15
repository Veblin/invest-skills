"""B02-2 跟踪质量：同收益类型才可确认的 TD/TE（离线，独立复算）。"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from etf_data import (
    TRADING_DAYS_PER_YEAR,
    TrackingCaliberError,
    _align_legs_by_date,
    _dated_values,
    _norm_trade_date,
    _read_via,
    _tracking_deviation_raw,
    compute_etf_tracking,
    compute_tracking_metrics,
)


def _compounded(r: list[float]) -> float:
    """样本区间复利（仅测试内构造窗口端点时使用；不是窗口端点定义本身）。"""
    pf = 1.0
    for x in r:
        pf *= (1 + x)
    return pf - 1.0


def _manual_te(rf: list[float], rb: list[float]) -> float:
    """TE 独立复算（不得调用被测实现）：ddof=1 × √252。"""
    es = [a - b for a, b in zip(rf, rb)]
    n = len(es)
    mean = sum(es) / n
    var = sum((x - mean) ** 2 for x in es) / (n - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


class TestPureMetrics:
    def test_same_caliber_matches_independent_recompute(self):
        rf = [0.01, -0.02, 0.015, 0.003, -0.005] * 15   # 75 个样本
        rb = [0.008, -0.018, 0.012, 0.002, -0.004] * 15
        # 连续区间时窗口端点收益 = 样本复利（此处用构造值，语义上即首末端点比−1）
        window = (_compounded(rf), _compounded(rb))
        out = compute_tracking_metrics(
            rf, rb, fund_return_type="total_return", bench_return_type="total_return",
            fund_currency="CNY", bench_currency="CNY", window_returns=window)
        assert out["td_pct"] == round((window[0] - window[1]) * 100, 2)
        assert out["te_pct_annualized"] == round(_manual_te(rf, rb), 2)
        assert out["sample_n"] == 75
        assert out["annualization_factor"] == TRADING_DAYS_PER_YEAR
        assert out["td_scope"] == "window_endpoint"
        assert out["te_scope"] == "sampled_intervals"

    def test_window_td_from_endpoints_not_sample_compounding(self):
        """R3 负责人 TD 窗口反例（纯函数层）：不连续区间下，样本复利 ≠ 窗口端点差。

        区间（缺值剔除后保留）：09-01→09-02、09-04→09-07、09-07→09-08；
        基金端点 100→150（+50%）、基准 100→125（+25%）。样本复利给出
        26.92%−14.13%=12.79%（漏掉被剔除区间的价格变动），窗口端点差=25.0%。
        TD 只允许取窗口端点收益；不得由样本复利代替。
        """
        rf = [110 / 100 - 1, 140 / 130 - 1, 150 / 140 - 1]
        rb = [105 / 100 - 1, 120 / 115 - 1, 125 / 120 - 1]
        out = compute_tracking_metrics(
            rf, rb, fund_return_type="total_return", bench_return_type="total_return",
            fund_currency="CNY", bench_currency="CNY", min_common=2,
            window_returns=(150 / 100 - 1, 125 / 100 - 1))
        assert out["td_pct"] == 25.0
        sample_compound = (_compounded(rf) - _compounded(rb)) * 100
        assert round(sample_compound, 2) == 12.79        # 旧口径（错）
        assert out["td_pct"] != round(sample_compound, 2)

    def test_window_returns_required_no_silent_compounding(self):
        """缺窗口端点收益 → 报错（不得静默用样本复利充当窗口累计）。

        报错类型为 TrackingCaliberError（模块统一拒绝语义）——外部复现脚本
        （负责人-整体反例.py）按该类型断言。
        """
        with pytest.raises(TrackingCaliberError):
            compute_tracking_metrics(  # type: ignore[call-arg]
                [0.01] * 70, [0.0] * 70, fund_return_type="total_return",
                bench_return_type="total_return",
                fund_currency="CNY", bench_currency="CNY")

    @pytest.mark.parametrize("bad", [(float("nan"), 0.0), (0.0, -1.0),
                                     (float("inf"), 0.0), (None, 0.0)])
    def test_invalid_window_returns_refused(self, bad):
        with pytest.raises(TrackingCaliberError):
            compute_tracking_metrics(
                [0.01] * 70, [0.0] * 70, fund_return_type="total_return",
                bench_return_type="total_return",
                fund_currency="CNY", bench_currency="CNY", window_returns=bad)

    def test_ddof1_and_sqrt252_locked(self):
        """总体方差（ddof=0）或 √365 会被此断言拦截。"""
        rf = [0.01, 0.02, -0.01, 0.005] * 20
        rb = [0.0] * 80
        out = compute_tracking_metrics(
            rf, rb, fund_return_type="price_return", bench_return_type="price_return",
            fund_currency="CNY", bench_currency="CNY", window_returns=(0.1, 0.05))
        es = rf
        mean = sum(es) / len(es)
        ddof1 = math.sqrt(sum((x - mean) ** 2 for x in es) / (len(es) - 1))
        ddof0 = math.sqrt(sum((x - mean) ** 2 for x in es) / len(es))
        assert out["te_pct_annualized"] == round(ddof1 * math.sqrt(252) * 100, 2)
        assert out["te_pct_annualized"] != round(ddof0 * math.sqrt(252) * 100, 2)
        assert out["te_pct_annualized"] != round(ddof1 * math.sqrt(365) * 100, 2)

    @pytest.mark.parametrize("kwargs", [
        {"fund_return_type": "total_return", "bench_return_type": "price_return"},
        {"fund_return_type": "total_return", "bench_return_type": "total_return",
         "fund_currency": "CNY", "bench_currency": "USD"},
        {"fund_return_type": "total_return", "bench_return_type": "total_return",
         "fund_currency": "CNY", "bench_currency": "CNY", "frequency": "monthly"},
        {"fund_return_type": "total_return", "bench_return_type": "total_return",
         "fund_currency": "CNY", "bench_currency": "CNY", "min_common": 100},
    ])
    def test_caliber_mismatch_refused(self, kwargs):
        base = {"fund_currency": "CNY", "bench_currency": "CNY",
                "window_returns": (0.1, 0.05)}
        base.update(kwargs)
        with pytest.raises(TrackingCaliberError):
            compute_tracking_metrics([0.01] * 80, [0.0] * 80, **base)

    def test_non_finite_refused(self):
        with pytest.raises(TrackingCaliberError):
            compute_tracking_metrics([0.01] * 60 + [float("nan")], [0.0] * 61,
                                     fund_return_type="total_return",
                                     bench_return_type="total_return",
                                     fund_currency="CNY", bench_currency="CNY",
                                     window_returns=(0.1, 0.05))

    def test_unequal_length_refused_not_truncated(self):
        """B-F03 负责人反例：61/60 个收益样本不得静默截短后确认 TD/TE。"""
        with pytest.raises(TrackingCaliberError) as ei:
            compute_tracking_metrics(
                [0.001] * 61, [0.001] * 60,
                fund_return_type="total_return", bench_return_type="total_return",
                fund_currency="CNY", bench_currency="CNY",
                window_returns=(0.1, 0.05))
        assert "长度不等" in str(ei.value)
        # 研究数值路径同样拒绝截短（不得以 min(len) 尾对齐出数字）
        with pytest.raises(TrackingCaliberError):
            _tracking_deviation_raw([0.001] * 61, [0.001] * 60,
                                    window_returns=(0.1, 0.05))


def _nav_rows(n: int = 120) -> list[dict]:
    rows = []
    nav = 1.0
    for i in range(n):
        nav *= 1.001
        rows.append({"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "nav": round(nav, 4)})
    return rows


def _bench_rows(nav_rows: list[dict]) -> list[dict]:
    rows = []
    close = 3000.0
    for r in nav_rows:
        close *= 1.0005
        rows.append({"date": r["date"], "close": round(close, 2)})
    return rows


class TestPipelineContract:
    """默认 pipeline：基准只有价格指数 → limited + td/te=None + 研究数值单列。"""

    def _patch(self, kline: dict, bench_env: dict | None):
        return patch.multiple(
            "etf_data",
            query_etf_kline=lambda symbol, days=60: kline,
            _bridge_get=lambda getter, *a: bench_env if getter == "get_etf_index_daily" else None,
        )

    def test_price_benchmark_is_limited_not_confirmatory(self):
        nav_rows = _nav_rows()
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        bench = {"status": "ok", "rows": _bench_rows(nav_rows)}
        with self._patch(kline, bench):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited"
        assert out["td_pct"] is None and out["te_pct_annualized"] is None
        assert "缺总收益基准" in out["note"]
        dev = out["price_benchmark_deviation"]
        assert dev["research_only"] is True and dev["not_confirmatory"] is True
        assert "不构成跟踪差异" in dev["caliber"]
        assert dev["sample_n"] == len(nav_rows) - 1
        assert out["caliber"]["bench_return_type"] == "price_return"
        assert out["caliber"]["fund_return_type"] == "total_return"
        assert "√252" in out["caliber"]["annualization"]
        assert "不得再单独扣减" in out["not_double_count_fees"]

    def test_unadjusted_nav_stops(self):
        kline = {"status": "available", "adj_applied": False, "nav_history": _nav_rows()}
        with self._patch(kline, {"status": "ok", "rows": _bench_rows(_nav_rows())}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited"
        assert out["td_pct"] is None and out["price_benchmark_deviation"] is None
        assert "未复权" in out["note"]

    def test_missing_benchmark_stops(self):
        kline = {"status": "available", "adj_applied": True, "nav_history": _nav_rows()}
        with self._patch(kline, None):
            out = compute_etf_tracking("510300")
        assert out["status"] == "missing_benchmark"
        assert out["td_pct"] is None

    def test_insufficient_common_days_stops(self):
        nav_rows = _nav_rows(40)
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with self._patch(kline, {"status": "ok", "rows": _bench_rows(nav_rows)}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "insufficient"
        assert "39 < 60" in out["note"] or "共同有效日不足" in out["note"]
        assert out["td_pct"] is None

    def test_no_index_mapping_both_sources(self):
        """CSINDEX_MAP 与身份登记都无跟踪指数 → not_mapped（未知保持未知）。"""
        with patch.multiple(
                "etf_data", CSINDEX_MAP={},
                query_etf_identity=lambda symbol, category=None: {
                    "tracked_index": None,
                    "verification": {"status": "unknown"}}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "not_mapped" and out["td_pct"] is None
        assert out["attempted_sources"] == []
        assert len(out["skipped_sources"]) == 2

    def test_nav_missing(self):
        kline = {"status": "missing", "_error": "boom"}
        with self._patch(kline, {"status": "ok", "rows": []}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "missing_nav"


class TestBF05IdentityIndexFallback:
    """B-F05：PE 映射缺失不得等同「产品没有收益基准」——用已核身份跟踪指数。"""

    def test_identity_tracked_index_used_when_csindex_missing(self):
        nav_rows = _nav_rows()
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows,
                 "attempted_sources": ["fund_etf_fund_info_em（基金净值）",
                                       "Tushare fund_adj（复权因子）"],
                 "source_trace": [], "skipped_sources": []}
        seen: list[str] = []

        def spy(getter, *a):
            seen.append(f"{getter}:{a[0] if a else ''}")
            if getter == "get_etf_index_daily":
                return {"status": "ok", "rows": _bench_rows(nav_rows)}
            return None

        with patch.multiple(
                "etf_data", CSINDEX_MAP={},
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=spy,
                query_etf_identity=lambda symbol, category=None: {
                    "tracked_index": {"name": "上证红利指数", "code": "000015"},
                    "verification": {"status": "verified_original"}}):
            out = compute_etf_tracking("510880")
        assert out["status"] == "limited"  # 有基准腿 → 不再 not_mapped
        assert "get_etf_index_daily:000015" in seen  # 真实尝试 000015 日线
        assert out["index_code"] == "000015"
        assert "identity.tracked_index" in out["index_code_source"]
        assert any("指数日线" in s for s in out["attempted_sources"])

    def test_identity_fallback_failure_is_transparent(self):
        """定点取数失败 → missing_benchmark（如实记录尝试与理由，不猜源）。"""
        kline = {"status": "available", "adj_applied": True, "nav_history": _nav_rows(),
                 "attempted_sources": [], "source_trace": [], "skipped_sources": []}
        with patch.multiple(
                "etf_data", CSINDEX_MAP={},
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: None,
                query_etf_identity=lambda symbol, category=None: {
                    "tracked_index": {"name": "上证红利指数", "code": "000015"},
                    "verification": {"status": "verified_original"}}):
            out = compute_etf_tracking("510880")
        assert out["status"] == "missing_benchmark"
        assert out["td_pct"] is None and out["price_benchmark_deviation"] is None
        assert out["index_code"] == "000015"
        trace = [t for t in out["source_trace"] if "指数日线" in t["source"]]
        assert trace and trace[0]["outcome"] == "missing" and "000015" in trace[0]["reason"]


class TestRealAttemptsAndInputEvidence:
    """B-F01 / 同代冻结完整性：真实调用留痕 + 两腿底稿随 payload 冻结。"""

    def _patch(self, kline: dict, bench_env: dict | None):
        return patch.multiple(
            "etf_data",
            query_etf_kline=lambda symbol, days=60: kline,
            _bridge_get=lambda getter, *a: bench_env if getter == "get_etf_index_daily" else None,
        )

    def test_missing_nav_does_not_claim_index_attempt(self):
        """B-F01 现场：NAV 失败提前返回时指数源 0 次调用，不得登记为已尝试；
        fund_adj 也未被调用（无 token/提前返回），不得由惯例清单合成。"""
        kline = {"status": "missing", "_error": "boom"}
        called: list[str] = []

        def spy(getter, *a):
            called.append(getter)
            return {"status": "ok", "rows": _bench_rows(_nav_rows())}

        with patch.multiple("etf_data",
                            query_etf_kline=lambda symbol, days=60: kline,
                            _bridge_get=spy):
            out = compute_etf_tracking("510300")
        assert out["status"] == "missing_nav"
        assert called == []  # 指数源实际 0 次调用
        assert out["attempted_sources"] == []  # kline 未留痕 → 宁缺勿猜（无 fund_adj）
        assert all("指数日线" not in s for s in out["attempted_sources"])
        assert all("fund_adj" not in s for s in out["attempted_sources"])
        assert out["skipped_sources"][0]["source"].startswith("stock_zh_index_daily")

    def test_kline_trace_passthrough_no_token_skip(self):
        """B-F01：生产者留痕逐条沿用；无 token 跳过的 fund_adj 不得进 attempted。"""
        kline = {"status": "available", "adj_applied": False, "nav_history": _nav_rows(),
                 "source": "fund_etf_fund_info_em",
                 "attempted_sources": ["fund_etf_fund_info_em（基金净值）"],
                 "source_trace": [{"source": "fund_etf_fund_info_em（基金净值）",
                                   "via": "本次网络", "outcome": "ok", "reason": None}],
                 "skipped_sources": [{"source": "Tushare fund_adj（复权因子）",
                                      "reason": "无 TUSHARE_TOKEN：未发起网络（跳过）"}]}
        with self._patch(kline, {"status": "ok", "rows": _bench_rows(_nav_rows())}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited" and "未复权" in out["note"]
        assert out["attempted_sources"] == ["fund_etf_fund_info_em（基金净值）"]
        assert any("fund_adj" in s["source"] for s in out["skipped_sources"])
        assert out["source_trace"][0]["via"] == "本次网络"

    def test_cache_read_marked_distinct_from_network(self):
        assert _read_via({"_from_cache": True}) == "L2 缓存读取"
        assert _read_via({"status": "ok"}) == "本次网络"
        # R05：None（无信封）不得猜「本次网络」——未发起/已发起但失败由
        # _leg_call_state 的桥探针区分，本函数只如实报「路径未留痕」
        assert _read_via(None) == "路径未留痕（无信封）"

    def test_bridge_none_is_not_guessed_as_network(self, monkeypatch):
        """R05：桥/getter 不可用（未发起）与桥内失败（已发起）分开表述。"""
        import etf_data as ed

        # 桥/getter 不可用 → 未发起
        monkeypatch.setattr(ed, "_bridge_availability",
                            lambda getter: {"available": False,
                                            "reason": f"data_bridge.{getter} 不存在（未接线）"})
        st = ed._leg_call_state(None, getter="get_etf_index_daily")
        assert st["attempted"] is False
        assert "未发起" in st["via"] and "未接线" in st["skip_reason"]

        # 桥可用但返回 None → 已发起但失败/缺追溯（不得说未发起）
        monkeypatch.setattr(ed, "_bridge_availability",
                            lambda getter: {"available": True, "reason": None})
        st2 = ed._leg_call_state(None, getter="get_etf_index_daily")
        assert st2["attempted"] is True
        assert "已发起" in st2["via"] and "缺追溯" in st2["via"]

    def test_producer_skip_marker_means_not_attempted(self):
        """R07：producer 结构化 attempted=False（无 token）→ 未发起，不登记 attempted。"""
        import etf_data as ed

        env = {"status": "missing", "adj_map": None, "attempted": False,
               "skip_reason": "no_token", "note": "无 TUSHARE_TOKEN：未发起网络（跳过）"}
        st = ed._leg_call_state(env, getter="get_etf_adj_factor")
        assert st["attempted"] is False
        assert "未发起" in st["via"] and "TUSHARE_TOKEN" in st["skip_reason"]

    def test_missing_benchmark_records_attempt_with_reason(self):
        """已发起但失败 → attempted + source_trace 记因（不再是 skipped=没试）。"""
        kline = {"status": "available", "adj_applied": True, "nav_history": _nav_rows()}
        with self._patch(kline, None):
            out = compute_etf_tracking("510300")
        assert out["status"] == "missing_benchmark"
        assert any("指数日线" in s for s in out["attempted_sources"])
        trace = [t for t in out["source_trace"] if "指数日线" in t["source"]]
        assert trace and trace[0]["outcome"] == "missing" and trace[0]["reason"]
        assert not any("指数日线" in s.get("source", "") for s in out["skipped_sources"])

    def test_limited_records_both_legs_and_input_evidence(self):
        nav_rows = _nav_rows()
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows,
                 "source": "fund_etf_fund_info_em",
                 "attempted_sources": ["fund_etf_fund_info_em（基金净值）",
                                       "Tushare fund_adj（复权因子）"],
                 "source_trace": [], "skipped_sources": []}
        with self._patch(kline, {"status": "ok", "rows": _bench_rows(nav_rows)}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited"
        assert any("fund_etf_fund_info_em" in s for s in out["attempted_sources"])
        assert any("fund_adj" in s for s in out["attempted_sources"])
        assert any("指数日线" in s for s in out["attempted_sources"])
        ev = out["input_evidence"]
        fund_rows = ev["legs"]["fund"]["rows"]
        bench_rows = ev["legs"]["benchmark"]["rows"]
        assert len(fund_rows) == len(nav_rows) and len(bench_rows) == len(nav_rows)
        # 底稿=真正使用的两腿（同日匹配），且含日期+取值
        assert fund_rows[0][0] == nav_rows[0]["date"]
        assert fund_rows[0][1] == nav_rows[0]["nav"]
        assert ev["sample_n"] == len(nav_rows) - 1
        assert ev["legs"]["fund"]["return_type"] == "total_return"
        assert ev["legs"]["benchmark"]["return_type"] == "price_return"

    def test_input_evidence_changes_payload_seal(self):
        """原始序列变化 → 底稿变化 → 封存哈希变化（不许只冻结已算结果）。"""
        import sys
        from pathlib import Path
        etf_lib = Path(__file__).resolve().parents[1] / "scripts" / "lib"
        if str(etf_lib) not in sys.path:
            sys.path.insert(0, str(etf_lib))
        from etf_generation import seal_etf_payload

        def payload_for(nav_rows):
            kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
            with self._patch(kline, {"status": "ok", "rows": _bench_rows(nav_rows)}):
                tr = compute_etf_tracking("510300")
            p = {"symbol": "510300", "generated_at": "2026-10-08T10:00:00+08:00",
                 "profile": {"tracking": tr}}
            return seal_etf_payload(p, frozen_at="2026-10-08T10:00:00+08:00")

        h1 = payload_for(_nav_rows())
        rows2 = _nav_rows()
        rows2[10]["nav"] = round(rows2[10]["nav"] * 1.01, 4)  # 改原始序列一个点
        h2 = payload_for(rows2)
        assert h1 != h2


class TestBF03DateAlignment:
    """B-F03：日期归一 / 乱序 / 重复冲突 / 单腿缺值 / 不足样本（同窗口配对）。"""

    @pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf"), "conflict"])
    def test_both_legs_invalid_date_never_bridged(self, bad):
        fund = [{"date": "2026-09-01", "nav": 1.0},
                {"date": "20260902", "nav": bad if bad != "conflict" else 1.1},
                {"date": "2026-09-03", "nav": 1.21}]
        bench = [{"date": r["date"], "close": r["nav"]} for r in fund]
        if bad == "conflict":
            fund.append({"date": "2026-09-02", "nav": 1.2})
            bench.append({"date": "2026-09-02", "close": 1.2})
        aligned = _align_legs_by_date(fund, bench)
        assert aligned["intervals"] == []
        assert aligned["returns_fund"] == aligned["returns_bench"] == []
        assert aligned["dropped"]["skipped_intervals"] == 2
        assert aligned["dropped"]["interior_gaps"] == 2
        assert aligned["contiguous"] is False

    def test_norm_trade_date_iso_and_compact(self):
        assert _norm_trade_date("2026-01-02") == "2026-01-02"
        assert _norm_trade_date("20260102") == "2026-01-02"
        assert _norm_trade_date("2026-01-02 00:00:00") == "2026-01-02"
        assert _norm_trade_date("2026-13-40") is None  # 非法日历日
        assert _norm_trade_date("") is None
        assert _norm_trade_date(None) is None

    def test_dated_values_conflict_and_invalid_dropped(self):
        vals, stats = _dated_values(
            [{"date": "2026-01-02", "nav": 1.0},
             {"date": "20260102", "nav": 1.0},          # 同值重复 → 去重保留
             {"date": "2026-01-03", "nav": None},       # 缺失 → 弃
             {"date": "2026-01-04", "nav": 0.0},        # 零值 → 弃（收益不可定义）
             {"date": "2026-01-05", "nav": float("nan")},
             {"date": "2026-01-06", "nav": 1.2},
             {"date": "2026-01-06", "nav": 1.3}],       # 冲突 → 整日弃用
            "nav")
        assert vals == {"2026-01-02": 1.0}
        assert stats["conflict_dates"] == ["2026-01-06"]
        assert stats["dropped_values"] == 3  # None/零/NaN 各一；冲突行不计入

    def test_align_joint_effective_dates_only(self):
        """缺值处相邻区间整段剔除：不得桥接跨交易日收益充日频样本（R2）。"""
        nav = [{"date": "2026-01-05", "nav": 1.0},
               {"date": "2026-01-02", "nav": 0.9},       # 乱序
               {"date": "2026-01-03", "nav": 0.95},      # 单腿缺（bench 无 01-03）
               {"date": "2026-01-06", "nav": None}]      # 单腿缺值
        bench = [{"date": "20260106", "close": 3000.0},
                 {"date": "20260105", "close": 2900.0},
                 {"date": "2026-01-02", "close": 2800.0},
                 {"date": "2026-01-04", "close": 2850.0}]  # 基金腿无此日
        a = _align_legs_by_date(nav, bench)
        # 并集脊 = 01-02 / 01-03 / 01-04 / 01-05 / 01-06；4 个相邻日对全部
        # 至少一端缺值 → 无可信相邻区间（旧行为会把 01-02→01-05 跨日收益
        # 当 daily 样本乘 √252）
        assert a["intervals"] == []
        assert a["dates"] == []
        assert a["returns_fund"] == [] and a["returns_bench"] == []
        assert a["dropped"]["skipped_intervals"] == 4
        # 公共区间（01-02 ~ 01-05）内 3 个相邻日对全部被剔除 → 样本内无日频区间
        assert a["dropped"]["interior_gaps"] == 3
        assert a["dropped"]["joint_days_dropped"] == 3
        assert a["window"] is None and a["contiguous"] is False

    def test_align_gap_does_not_bridge(self):
        """负责人日频缺口反例：单腿缺中间日 → 两侧区间同时剔除、不桥接。"""
        fund = [{"date": "2026-09-01", "nav": 1.0},
                {"date": "2026-09-02", "nav": None},
                {"date": "2026-09-03", "nav": 1.21}]
        bench = [{"date": "2026-09-01", "close": 100.0},
                 {"date": "2026-09-02", "close": 110.0},
                 {"date": "2026-09-03", "close": 121.0}]
        a = _align_legs_by_date(fund, bench)
        assert a["returns_fund"] == [] and a["returns_bench"] == []
        assert a["intervals"] == [] and a["dates"] == []
        assert a["window"] is None
        assert a["dropped"]["skipped_intervals"] == 2
        assert a["dropped"]["interior_gaps"] == 2
        assert a["contiguous"] is False

    def test_align_gap_middle_keeps_other_intervals(self):
        """缺值只剔除相邻区间，其余相邻日对保留（区间表可不连续）。"""
        fund = [{"date": "2026-09-01", "nav": 1.0},
                {"date": "2026-09-02", "nav": None},
                {"date": "2026-09-03", "nav": 1.21},
                {"date": "2026-09-04", "nav": 1.331}]
        bench = [{"date": "2026-09-01", "close": 100.0},
                 {"date": "2026-09-02", "close": 110.0},
                 {"date": "2026-09-03", "close": 121.0},
                 {"date": "2026-09-04", "close": 133.1}]
        a = _align_legs_by_date(fund, bench)
        assert a["intervals"] == [["2026-09-03", "2026-09-04"]]
        assert a["dates"] == ["2026-09-03", "2026-09-04"]
        assert a["returns_fund"][0] == pytest.approx(1.331 / 1.21 - 1)
        assert a["returns_bench"][0] == pytest.approx(133.1 / 121.0 - 1)
        assert a["dropped"]["skipped_intervals"] == 2
        # 公共区间 09-01 ~ 09-04：3 个相邻日对保留 1、缺值两侧剔除 2
        assert a["dropped"]["interior_gaps"] == 2
        assert a["contiguous"] is False
        assert a["window"] == "2026-09-03 ~ 2026-09-04"

    def test_pipeline_single_leg_none_drops_adjacent_intervals(self):
        """单腿缺值：两侧相邻区间整段剔除，两腿收益仍恒等长且区间冻结。"""
        nav_rows = _nav_rows()
        bench_rows = _bench_rows(nav_rows)
        # 基金腿在第 10 个交易日缺值 → 相邻区间 (9,10) 与 (10,11) 同时剔除
        nav_rows[10]["nav"] = None
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: {"status": "ok", "rows": bench_rows}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited"
        ev = out["input_evidence"]
        n = len(nav_rows) - 3  # 119 对 − 2 个剔除对
        assert ev["sample_n"] == n
        assert len(ev["intervals"]) == n
        assert ev["dropped"]["skipped_intervals"] == 2
        assert "非连续区间" in ev["intervals_note"]
        dates = [r[0] for r in ev["legs"]["fund"]["rows"]]
        assert dates == [r[0] for r in ev["legs"]["benchmark"]["rows"]]
        assert nav_rows[10]["date"] not in dates
        # 冻结区间之外不得出现桥接对：逐区间与底稿取值一致
        fv = {d: v for d, v in ev["legs"]["fund"]["rows"]}
        bv = {d: v for d, v in ev["legs"]["benchmark"]["rows"]}
        for (a_, b_) in ev["intervals"]:
            assert a_ in fv and b_ in fv and a_ in bv and b_ in bv
        assert out["price_benchmark_deviation"]["sample_n"] == n

    def test_pipeline_both_legs_missing_keeps_gap_and_daily_te(self):
        nav_rows = _nav_rows()
        bench_rows = _bench_rows(nav_rows)
        nav_rows[10]["nav"] = None
        bench_rows[10]["close"] = None
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: {
                    "status": "available", "adj_applied": True, "nav_history": nav_rows},
                _bridge_get=lambda getter, *a: {"status": "ok", "rows": bench_rows}):
            out = compute_etf_tracking("510300")
        ev = out["input_evidence"]
        expected_pairs = [(i, i + 1) for i in range(len(nav_rows) - 1)
                          if i not in (9, 10)]
        assert ev["intervals"] == [[nav_rows[a]["date"], nav_rows[b]["date"]]
                                   for a, b in expected_pairs]
        assert ev["sample_n"] == len(expected_pairs)
        assert ev["dropped"]["interior_gaps"] == 2
        assert ev["sampled_coverage"]["interior_adjacent_pairs"] == len(nav_rows) - 1
        rf = [nav_rows[b]["nav"] / nav_rows[a]["nav"] - 1 for a, b in expected_pairs]
        rb = [bench_rows[b]["close"] / bench_rows[a]["close"] - 1
              for a, b in expected_pairs]
        assert out["price_benchmark_deviation"]["te_pct_annualized"] == round(
            _manual_te(rf, rb), 2)

    def test_pipeline_conflicting_duplicate_date_excluded_both_legs(self):
        nav_rows = _nav_rows()
        dup = dict(nav_rows[20])
        dup["nav"] = round(dup["nav"] * 1.5, 4)  # 同日期不同值 → 冲突
        nav_rows.append(dup)
        bench_rows = _bench_rows(_nav_rows())
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: {"status": "ok", "rows": bench_rows}):
            out = compute_etf_tracking("510300")
        ev = out["input_evidence"]
        conflict_date = dup["date"]
        assert ev["dropped"]["fund"]["conflict_dates"] == [conflict_date]
        assert conflict_date not in [r[0] for r in ev["legs"]["fund"]["rows"]]
        # 两腿日期脊仍完全一致（同一窗口）
        assert ([r[0] for r in ev["legs"]["fund"]["rows"]]
                == [r[0] for r in ev["legs"]["benchmark"]["rows"]])

    def test_pipeline_unsorted_iso_compact_inputs_still_aligned(self):
        nav_rows = _nav_rows()
        bench_rows = _bench_rows(nav_rows)
        for r in nav_rows:
            r["date"] = r["date"].replace("-", "")  # compact
        nav_rows = list(reversed(nav_rows))           # 乱序
        bench_rows = list(reversed(bench_rows))
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: {"status": "ok", "rows": bench_rows}):
            out = compute_etf_tracking("510300")
        ev = out["input_evidence"]
        assert ev["sample_n"] == len(nav_rows) - 1
        dates = [r[0] for r in ev["legs"]["fund"]["rows"]]
        assert dates == sorted(dates)                 # 底稿=升序 ISO 日期脊
        assert all(d.count("-") == 2 for d in dates)  # compact 已归一为 ISO


class TestR3WindowEndpointDeviation:
    """R3 负责人 TD 窗口反例（producer 层）：缺值剔除后的样本复利不得充当窗口 TD。

    现场：2026-09-01~09-08，基金腿 09-03 缺值 → 区间 (09-02,09-03)/(09-03,09-04)
    整对剔除；保留区间基金 100→110、130→140、140→150、基准 100→105、115→120、
    120→125。样本复利 = 26.92%−14.13% = 12.79%（错），窗口首末端点差 = 50%−25%
    = 25.0%（引擎必须输出这个，并披露日偏离波动的样本覆盖 3/5）。
    """

    DATES = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
             "2026-09-07", "2026-09-08"]
    FUND = [100.0, 110.0, None, 130.0, 140.0, 150.0]
    BENCH = [100.0, 105.0, 110.0, 115.0, 120.0, 125.0]

    def _run(self):
        nav = {"status": "available", "adj_applied": True, "source": "fixture",
               "nav_history": [{"date": d, "nav": v}
                               for d, v in zip(self.DATES, self.FUND)],
               "attempted_sources": ["fixture NAV"]}
        bench = {"status": "ok",
                 "rows": [{"date": d, "close": v}
                          for d, v in zip(self.DATES, self.BENCH)]}
        with patch.multiple("etf_data",
                            query_etf_kline=lambda symbol, days=60: nav,
                            _bridge_get=lambda getter, *a: bench):
            return compute_etf_tracking("510300", min_common=2)

    def test_td_is_window_endpoint_not_sample_compounding(self):
        out = self._run()
        dev = out["price_benchmark_deviation"]
        assert dev["td_pct"] == 25.0, "窗口累计偏离必须是首末有效端点差"
        assert dev["td_scope"] == "window_endpoint"
        assert dev["sample_n"] == 3
        ev = out["input_evidence"]
        assert ev["window_endpoints"]["fund"] == [["2026-09-01", 100.0],
                                                  ["2026-09-08", 150.0]]
        assert ev["window_returns_pct"] == {"fund": 50.0, "bench": 25.0}
        assert ev["sampled_coverage"]["sampled_intervals"] == 3
        assert ev["sampled_coverage"]["interior_adjacent_pairs"] == 5
        assert ev["sampled_coverage"]["coverage_pct"] == 60.0
        assert "非连续区间" in ev["intervals_note"]
        assert "12.79" not in str(dev), "旧错误口径（样本复利）不得出现在输出中"

    def test_te_still_covers_sampled_intervals_only(self):
        out = self._run()
        dev = out["price_benchmark_deviation"]
        rf = [110 / 100 - 1, 140 / 130 - 1, 150 / 140 - 1]
        rb = [105 / 100 - 1, 120 / 115 - 1, 125 / 120 - 1]
        assert dev["te_pct_annualized"] == round(_manual_te(rf, rb), 2)
        assert dev["te_scope"] == "sampled_intervals"

    def test_contiguous_window_endpoint_equals_compounding(self):
        """连续窗口：端点累计与区间复利一致（初始三份正常报告数值不变的结构保证）。"""
        nav_rows = _nav_rows()
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: {"status": "ok",
                                                "rows": _bench_rows(nav_rows)}):
            out = compute_etf_tracking("510300")
        ev = out["input_evidence"]
        dev = out["price_benchmark_deviation"]
        wf = ev["window_returns_pct"]["fund"] / 100
        wb = ev["window_returns_pct"]["bench"] / 100
        # 端点差 == 区间复利（连续 → 望远镜求和）
        compound = (_compounded(ev_dummy(ev, "fund"))
                    - _compounded(ev_dummy(ev, "benchmark")))
        # 底稿 window_returns_pct 保留 4 位小数（百分比）→ 换算容差 ~2e-6
        assert abs(compound - (wf - wb)) < 1e-5
        assert dev["td_pct"] == round((wf - wb) * 100, 2)
        assert ev["sampled_coverage"]["coverage_pct"] == 100.0
        assert "窗口内连续" in ev["intervals_note"]


def ev_dummy(ev: dict, leg: str) -> list[float]:
    """由底稿 intervals + legs rows 独立重算该腿区间收益（测试内，不调用被测函数）。"""
    vals = {d: v for d, v in ev["legs"][leg]["rows"]}
    return [vals[b] / vals[a] - 1.0 for a, b in ev["intervals"]]


class TestAudit20261009Tracking:
    """R07/R10/R16/R17：结构化跳过、同一次跳过去重、偏离核统一校验。"""

    def test_adj_skip_by_structured_marker_not_note_text(self):
        """无 token 跳过由 attempted=False 表达：note 改写不影响判定。"""
        nav = {"status": "ok", "source": "fund_etf_fund_info_em",
               "rows": [{"date": f"2026-09-{d:02d}", "nav": 1.0 + d / 1000,
                         "change_pct": 0.1} for d in range(1, 12)]}
        adj = {"status": "missing", "adj_map": None, "attempted": False,
               "skip_reason": "no_token",
               "note": "TUSHARE_TOKEN 未配置，跳过复权"}  # 文案不含旧子串「未发起网络」

        def fake(getter, *a, **k):
            return nav if getter == "get_etf_nav" else (adj if getter == "get_etf_adj_factor"
                                                        else None)

        import etf_data as ed
        with patch.object(ed, "_bridge_get", fake):
            out = ed.query_etf_kline("510300", days=5)
        assert not any("fund_adj" in s for s in out["attempted_sources"])
        assert any("fund_adj" in s["source"] for s in out["skipped_sources"])

    def test_index_leg_skip_recorded_once(self):
        """R10：kline 已记录指数腿未发起 → 消费者不得再追加同腿第二遍。"""
        kline = {"status": "missing", "_error": "boom",
                 "skipped_sources": [{"source": "stock_zh_index_daily（指数日线）",
                                      "reason": "净值序列不足，未发起（提前返回）"}]}
        with patch("etf_data.query_etf_kline", lambda symbol, days=250: kline):
            out = compute_etf_tracking("510300")
        idx = [s for s in out["skipped_sources"] if "指数日线" in s["source"]]
        assert len(idx) == 1
        assert "净值序列不足" in idx[0]["reason"]  # 保留更贴近真实原因的一条

    def test_index_leg_skip_added_when_kline_did_not_record_it(self):
        kline = {"status": "missing", "_error": "boom", "skipped_sources": []}
        with patch("etf_data.query_etf_kline", lambda symbol, days=250: kline):
            out = compute_etf_tracking("510300")
        idx = [s for s in out["skipped_sources"] if "指数日线" in s["source"]]
        assert len(idx) == 1 and "基准腿未发起取数" in idx[0]["reason"]

    @pytest.mark.parametrize("rf,rb,kwargs,err", [
        ([], [], {"window_returns": (0.1, 0.05)}, "空样本"),
        ([0.1] * 61, [0.1] * 60, {"window_returns": (0.1, 0.05)}, "长度不等"),
        ([0.1] * 60 + [float("nan")], [0.1] * 61,
         {"window_returns": (0.1, 0.05)}, "非有限值"),
        ([0.1], [0.1], {"window_returns": (0.1, 0.05)}, "样本不足以估计日偏离波动"),
        ([0.1] * 61, [0.1] * 61, {}, "缺窗口端点收益"),
    ])
    def test_deviation_raw_input_validation(self, rf, rb, kwargs, err):
        """R17：空/不等长/nonfinite/n<2 统一拒绝，不得 ZeroDivisionError/NaN。"""
        with pytest.raises(TrackingCaliberError) as ei:
            _tracking_deviation_raw(rf, rb, **kwargs)
        assert err in str(ei.value)

    def test_raw_and_confirm_path_share_one_core(self):
        """R16：同一输入下两条路径数值一致（同公式、同年化常数）。"""
        rf = [0.01, -0.02, 0.015, 0.003, -0.005] * 15
        rb = [0.008, -0.018, 0.012, 0.002, -0.004] * 15
        window = (_compounded(rf), _compounded(rb))
        raw = _tracking_deviation_raw(rf, rb, window_returns=window)
        conf = compute_tracking_metrics(
            rf, rb, fund_return_type="total_return", bench_return_type="total_return",
            fund_currency="CNY", bench_currency="CNY", window_returns=window)
        assert raw["td_pct"] == conf["td_pct"]
        assert raw["te_pct_annualized"] == conf["te_pct_annualized"]
        assert raw["annualization_factor"] == TRADING_DAYS_PER_YEAR

    def test_annualization_factor_is_parameterized(self):
        """反例：年化系数由参数给（√365 ≠ √252），不再硬编码。"""
        rf = [0.01, 0.02, -0.01, 0.005] * 20
        rb = [0.0] * 80
        a252 = _tracking_deviation_raw(rf, rb, window_returns=(0.1, 0.05),
                                       annualization_factor=252)
        a365 = _tracking_deviation_raw(rf, rb, window_returns=(0.1, 0.05),
                                       annualization_factor=365)
        assert a252["te_pct_annualized"] != a365["te_pct_annualized"]
        assert a365["te_pct_annualized"] == round(
            a252["te_pct_annualized"] / math.sqrt(252) * math.sqrt(365), 2)

    def test_pipeline_nonfinite_nav_dropped_at_alignment_layer(self):
        """R17 分层：非有限 NAV 在 `_dated_values` 已按无效值剔除（第一道防线），
        管道不崩、缺口如实入底稿；核层校验（第二道防线）由上面的纯函数用例覆盖。"""
        nav_rows = _nav_rows()
        nav_rows[5]["nav"] = float("inf")
        kline = {"status": "available", "adj_applied": True, "nav_history": nav_rows}
        with patch.multiple(
                "etf_data",
                query_etf_kline=lambda symbol, days=60: kline,
                _bridge_get=lambda getter, *a: {"status": "ok",
                                                "rows": _bench_rows(_nav_rows())}):
            out = compute_etf_tracking("510300")
        assert out["status"] == "limited"
        assert out["input_evidence"]["dropped"]["fund"]["dropped_values"] == 1
        assert nav_rows[5]["date"] not in [r[0] for r in
                                           out["input_evidence"]["legs"]["fund"]["rows"]]


class TestAudit20261009KlineHistoryTrace:
    """R05/R06 同类：query_etf_kline_history 透传 nav 腿的真实留痕。"""

    def test_nav_success_passes_trace(self):
        import etf_data as ed

        nav = {"symbol": "510300", "status": "available",
               "nav_history": [{"date": "2026-10-01", "nav": 1.0}],
               "attempted_sources": ["fund_etf_fund_info_em（基金净值）"],
               "skipped_sources": [{"source": "Tushare fund_adj（复权因子）",
                                    "reason": "无 TUSHARE_TOKEN：未发起网络（跳过）"}],
               "source_trace": []}
        with patch.object(ed, "query_etf_kline", lambda symbol, days=250: nav):
            out = ed.query_etf_kline_history("510300")
        assert out["status"] == "available"
        assert out["attempted_sources"] == ["fund_etf_fund_info_em（基金净值）"]
        assert out["skipped_sources"][0]["source"].startswith("Tushare fund_adj")

    def test_baostock_fallback_records_nav_attempt_and_fallback(self):
        import etf_data as ed

        nav = {"status": "missing", "_error": "boom", "nav_history": [],
               "attempted_sources": ["fund_etf_fund_info_em（基金净值）"],
               "skipped_sources": []}
        bs_env = {"status": "ok", "rows": [{"date": "2026-10-01", "close": 3.0}],
                  "error": None}
        with patch.object(ed, "query_etf_kline", lambda symbol, days=250: nav), \
             patch.object(ed, "fetch_etf_kline_baostock", lambda symbol, days=250: bs_env):
            out = ed.query_etf_kline_history("510300")
        assert out["source"] == "baostock"
        assert "fund_etf_fund_info_em（基金净值）" in out["attempted_sources"]
        assert any("baostock" in s for s in out["attempted_sources"])


class TestAudit20261009F5InvalidSymbolSkip:
    """F5（独立复检）：无效代码未创建 Tushare client（client_calls=0）→ 未发起。"""

    def test_invalid_symbol_is_skip_not_attempt(self, monkeypatch):
        import etf_data as ed

        monkeypatch.setenv("TUSHARE_TOKEN", "bogus")
        with patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "bogus"}):
            env = ed.fetch_etf_adj_factor("bad")
        assert env["status"] == "missing"
        assert env["attempted"] is False
        assert env["skip_reason"] == "invalid_symbol"
        assert "未发起" in env["note"]

    def test_valid_symbol_query_failure_is_attempt(self, monkeypatch):
        import etf_data as ed

        with patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "bogus"}), \
             patch.object(ed, "_fetch_fund_adj_factor", return_value=None):
            env = ed.fetch_etf_adj_factor("510300")
        assert env["attempted"] is True and env["skip_reason"] is None
