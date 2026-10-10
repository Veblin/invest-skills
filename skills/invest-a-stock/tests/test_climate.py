"""R5 行业景气状态卡 — 纯规则引擎真值表测试（零网络）。

覆盖验收点：
- 维度缺失矩阵（0-5 维有效各断言结论正确性，U4）
- 有效=3 恰好给结论、=2 输出「数据不完整」
- 政策证据空 → 「未查」且不影响其他维呈现
- 输出含缺失维度清单
- 真值表 8 组合
- 分位档边界（恰好 0.30 / 0.70）
- 分位无效 + 方向↑ → 扩张（回退路径）
"""

from __future__ import annotations

from lib.climate import industry_climate_card


def _dims(
    *,
    pct: float = 0.5,
    earn: str = "up",
    rs: str = "up",
    flow: str = "in",
    policy: str = "未查",
    pct_valid: bool = True,
    earn_valid: bool = True,
    rs_valid: bool = True,
    flow_valid: bool = True,
    policy_valid: bool = False,
) -> list[dict]:
    return [
        {"name": "估值分位", "value": pct, "source": "test", "valid": pct_valid},
        {"name": "盈利趋势", "value": earn, "source": "test", "valid": earn_valid},
        {"name": "相对强度", "value": rs, "source": "test", "valid": rs_valid},
        {"name": "资金流", "value": flow, "source": "test", "valid": flow_valid},
        {"name": "政策证据", "value": policy, "source": "test", "valid": policy_valid},
    ]


class TestDimensionMissingMatrix:
    """① 维度缺失矩阵（0-5 维有效各断言结论）。"""

    def test_zero_to_two_valid_gives_incomplete(self):
        cases = [
            _dims(pct_valid=False, earn_valid=False, rs_valid=False, flow_valid=False),
            _dims(rs_valid=False, flow_valid=False),
        ]
        for dims in cases:
            card = industry_climate_card(dims)
            assert card["state"] == "数据不完整", dims
            assert card["direction"] is None

    def test_three_valid_gives_conclusion(self):
        """② 有效=3 恰好给结论。"""
        card = industry_climate_card(_dims(flow_valid=False, pct=0.2))
        assert card["valid_count"] == 3
        assert card["state"] != "数据不完整"
        assert card["state"] == "复苏"  # up×up×low

    def test_four_and_five_valid(self):
        card = industry_climate_card(_dims())
        assert card["valid_count"] == 4  # 政策「未查」不计
        assert card["state"] == "扩张"  # up×up×in × mid
        card5 = industry_climate_card(_dims(policy="有", policy_valid=True))
        assert card5["valid_count"] == 5

    def test_two_valid_reports_incomplete(self):
        """② =2 输出「数据不完整」+ 缺失清单。"""
        dims = _dims(rs_valid=False, flow_valid=False)
        card = industry_climate_card(dims)
        assert card["state"] == "数据不完整"
        assert card["valid_count"] == 2
        assert "相对强度" in card["missing_dims"]
        assert "资金流" in card["missing_dims"]

    def test_missing_list_included_in_output(self):
        """④ 输出含缺失维度清单。"""
        dims = _dims(flow_valid=False, pct_valid=False)
        card = industry_climate_card(dims)
        assert set(card["missing_dims"]) >= {"资金流", "估值分位"}
        # format 路径含缺失清单
        from lib.climate import format_climate_card
        out = format_climate_card(card)
        assert "数据不完整" in out
        assert "资金流" in out


class TestPolicyDimension:
    """③ 政策证据空 → 「未查」且不影响其他维呈现。"""

    def test_policy_unchecked_ignored(self):
        card = industry_climate_card(_dims())
        assert card["valid_count"] == 4  # 政策未查不计入
        assert card["policy_note"] is None
        assert card["state"] == "扩张"

    def test_policy_has_evidence_notes_only(self):
        card = industry_climate_card(_dims(policy="有", policy_valid=True))
        assert card["policy_note"] == "政策加持（需人工核验强度）"
        # 政策证据不改变状态判定
        assert card["state"] == "扩张"

    def test_policy_has_evidence_on_down_state(self):
        dims = _dims(earn="down", rs="down", flow="out", policy="有", policy_valid=True)
        card = industry_climate_card(dims)
        assert card["policy_note"] is not None
        assert card["state"] == "收缩"  # 仍按方向×语境，不因政策改变


class TestTruthTable:
    """⑤ 真值表 8 组合。"""

    def test_up_low_recovers(self):
        assert industry_climate_card(_dims(pct=0.2))["state"] == "复苏"

    def test_up_mid_high_expands(self):
        assert industry_climate_card(_dims(pct=0.5))["state"] == "扩张"
        assert industry_climate_card(_dims(pct=0.8))["state"] == "扩张"

    def test_down_high_cools(self):
        assert industry_climate_card(_dims(earn="down", rs="down", flow="out", pct=0.8))["state"] == "降温"

    def test_down_low_mid_contracts(self):
        assert industry_climate_card(_dims(earn="down", rs="down", flow="out", pct=0.2))["state"] == "收缩"
        assert industry_climate_card(_dims(earn="down", rs="down", flow="out", pct=0.5))["state"] == "收缩"

    def test_tie_broken_by_priority(self):
        """2 个方向维 1-1 平局由优先级打破（盈利趋势 > 资金流），不再无法定论。"""
        dims = _dims(rs_valid=False, earn="up", flow="out")
        card = industry_climate_card(dims)
        assert card["valid_count"] == 3
        assert card["direction"] == "up"
        assert card["state"] == "扩张"

    def test_too_few_direction_dims_unresolved(self):
        """STEP 3：有效方向维 <2 → 无法定论（即使总数 ≥3：估值/政策不算方向证据）。"""
        dims = _dims(rs_valid=False, flow_valid=False, policy="有", policy_valid=True)
        card = industry_climate_card(dims)
        assert card["valid_count"] == 3
        assert card["state"] == "无法定论"


class TestDirectionPriority:
    """⑥ 方向冲突优先级：盈利趋势 > 资金流 > 相对强度（STEP 4，非多数投票）。"""

    def test_2v1_earnings_down_wins_over_two_ups(self):
        """2-1 投票但盈利趋势=down → 收缩（多数投票旧实现会误判扩张）。"""
        dims = _dims(earn="down", flow="in", rs="up", pct=0.5)
        card = industry_climate_card(dims)
        assert card["direction"] == "down"
        assert card["state"] == "收缩"
        assert card["conflict"] is True  # 三票分歧，按优先级裁决

    def test_1v1_tie_earnings_wins(self):
        """1-1 平局（盈利趋势 vs 资金流）→ 盈利趋势决定方向。"""
        dims = _dims(rs_valid=False, earn="up", flow="out")
        card = industry_climate_card(dims)
        assert card["direction"] == "up"
        assert card["state"] == "扩张"

    def test_priority_flow_over_rs_when_earnings_neutral(self):
        """盈利趋势中性（value=None）时由资金流裁决，而非相对强度。"""
        dims = _dims(earn=None, earn_valid=True, flow="out", rs="up", pct=0.5)
        card = industry_climate_card(dims)
        assert card["direction"] == "down"
        assert card["state"] == "收缩"

    def test_all_direction_dims_neutral_gives_unresolved(self):
        """全部方向维缺失/中性 → 无法定论（优先级无法打破）。"""
        dims = _dims(earn=None, flow=None, rs=None,
                     earn_valid=True, flow_valid=True, rs_valid=True)
        card = industry_climate_card(dims)
        assert card["valid_count"] == 4
        assert card["direction"] is None
        assert card["state"] == "无法定论"


class TestPercentileBoundaries:
    """⑥ 分位档边界（恰好 0.30 / 0.70）。"""

    def test_just_below_low_is_low(self):
        assert industry_climate_card(_dims(pct=0.29))["state"] == "复苏"  # up×low

    def test_exact_low_boundary_is_mid(self):
        # 0.30 不满足 <0.30 → mid；up×mid = 扩张（区别于 0.29 的复苏）
        assert industry_climate_card(_dims(pct=0.30))["state"] == "扩张"

    def test_exact_high_boundary_is_high(self):
        # 0.70 满足 ≥0.70 → high；down×high = 降温（区别于 0.699 的收缩）
        assert industry_climate_card(_dims(pct=0.70, earn="down", rs="down", flow="out"))["state"] == "降温"
        assert industry_climate_card(_dims(pct=0.699, earn="down", rs="down", flow="out"))["state"] == "收缩"

    def test_invalid_percentile_falls_back_to_mid(self):
        """⑦ 分位无效 + 方向↑ → 扩张（回退路径）。"""
        card = industry_climate_card(_dims(pct_valid=False))
        assert card["context"] == "mid"
        assert card["state"] == "扩张"


# ---------------------------------------------------------------------------
# _dim_rs：近期窗口相对强度（修复：全历史累计 RS 恒 up）
# ---------------------------------------------------------------------------

class TestDimRsRecentWindow:
    """构造「长端涨 300% 但近 120 日下跌」的序列 → 方向必须是 down（近期信号）。"""

    @staticmethod
    def _make_closes(n: int, long_start: float, long_end: float,
                     near_start: float, near_end: float) -> list[float]:
        """前 (n-120) 日线性上行（long_start→long_end），尾部 120 日线性下行。"""
        long_len = n - 120
        long_vals = [long_start + (long_end - long_start) * i / max(long_len - 1, 1)
                     for i in range(long_len)]
        near_vals = [near_start + (near_end - near_start) * i / 119 for i in range(120)]
        return long_vals + near_vals

    def test_long_gain_but_recent_decline_gives_down(self, monkeypatch):
        from types import SimpleNamespace
        import pandas as pd
        from lib.climate import _dim_rs

        n = 1000
        # 行业指数：全历史 100 → 400（+300%），近 120 日 400 → 300（-25%）
        ind_closes = self._make_closes(n, 100, 400, 400, 300)
        # 基准：全历史 1000 → 1100（+10%），近 120 日持平
        bench_closes = self._make_closes(n, 1000, 1100, 1100, 1100)
        fake_ak = SimpleNamespace(
            index_hist_sw=lambda **kw: pd.DataFrame({"close": ind_closes}),
            stock_zh_index_daily=lambda **kw: pd.DataFrame({"close": bench_closes}),
        )
        monkeypatch.setattr(
            "lib.climate._sw_tables",
            lambda: {"first": [], "second": [],
                     "map": {"半导体": ("801081", "second")}},
        )
        monkeypatch.setattr("lib.climate._ak", lambda: fake_ak)

        result = _dim_rs("半导体")
        assert result["valid"] is True
        # 旧实现（全历史对齐）会得 "up"；修复后必须是近期窗口的 "down"
        assert result["value"] == "down"
        assert result.get("window") == 120
        assert "近 120 交易日" in result["source"]

    def test_recent_window_rise_gives_up(self, monkeypatch):
        from types import SimpleNamespace
        import pandas as pd
        from lib.climate import _dim_rs

        n = 1000
        # 行业指数：全历史 100 → 200，近 120 日 200 → 260（+30%）→ 近端 up
        ind_closes = self._make_closes(n, 100, 200, 200, 260)
        bench_closes = self._make_closes(n, 1000, 1100, 1100, 1100)
        fake_ak = SimpleNamespace(
            index_hist_sw=lambda **kw: pd.DataFrame({"close": ind_closes}),
            stock_zh_index_daily=lambda **kw: pd.DataFrame({"close": bench_closes}),
        )
        monkeypatch.setattr(
            "lib.climate._sw_tables",
            lambda: {"first": [], "second": [],
                     "map": {"半导体": ("801081", "second")}},
        )
        monkeypatch.setattr("lib.climate._ak", lambda: fake_ak)

        result = _dim_rs("半导体")
        assert result["valid"] is True
        assert result["value"] == "up"


# ---------------------------------------------------------------------------
# A03 R5：一次 build 只取申万两表各一次（6 → 2 请求）
# ---------------------------------------------------------------------------

class TestBuildSingleTableFetch:
    """build_industry_climate 单次取表 + 三维复用；失败维度不静默变 0。"""

    @staticmethod
    def _fake_ak(calls: dict, *, fail_first: bool = False):
        import pandas as pd
        from types import SimpleNamespace

        def _first():
            calls["first"] += 1
            if fail_first:
                raise ConnectionError("swsindex blocked")
            return pd.DataFrame([
                {"行业代码": "801010.SI", "行业名称": "半导体", "TTM(滚动)市盈率": 30.0},
                {"行业代码": "801020.SI", "行业名称": "医药", "TTM(滚动)市盈率": 40.0},
            ])

        def _second():
            calls["second"] += 1
            return pd.DataFrame([
                {"行业代码": "801081.SI", "行业名称": "二级科技", "TTM(滚动)市盈率": 25.0},
            ])

        return SimpleNamespace(
            sw_index_first_info=_first,
            sw_index_second_info=_second,
            index_hist_sw=lambda **kw: pd.DataFrame({"close": [1.0, 1.1, 1.2]}),
            stock_zh_index_daily=lambda **kw: pd.DataFrame({"close": [1.0, 1.0, 1.0]}),
            stock_fund_flow_industry=lambda **kw: pd.DataFrame([{"行业": "半导体", "净额": 1.0}]),
        )

    def test_build_fetches_each_sw_table_once(self, monkeypatch):
        """spy：build 一次 = 两表各 1 次；旧路径（三维各自取）为 3+3 次。"""
        from lib import climate

        calls = {"first": 0, "second": 0}
        monkeypatch.setattr(climate, "_ak", lambda: self._fake_ak(calls))

        card = climate.build_industry_climate("半导体")
        assert calls == {"first": 1, "second": 1}, "单次 build 应两表各取一次"

        by_name = {d["name"]: d for d in card["dims"]}
        # 三维复用同一份表：来源标签（层级/窗口）与取值正常
        assert by_name["估值分位"]["valid"] is True
        assert by_name["估值分位"]["source"] == "申万一级行业 TTM PE 分布"
        assert by_name["盈利趋势"]["valid"] is True
        assert by_name["相对强度"]["valid"] is True
        assert by_name["资金流"]["valid"] is True

        # 反例对照：私有函数独立调用（无 tables 入参）仍各自取表 = 6 次
        calls.update(first=0, second=0)
        climate._dim_valuation("半导体")
        climate._dim_earnings("半导体")
        climate._dim_rs("半导体")
        assert calls == {"first": 3, "second": 3}

    def test_first_table_failure_marks_mapped_dims_invalid(self, monkeypatch):
        """单表失败：依赖映射的三维 valid=False，其余维度不静默变 0。"""
        from lib import climate

        calls = {"first": 0, "second": 0}
        monkeypatch.setattr(climate, "_ak",
                            lambda: self._fake_ak(calls, fail_first=True))

        card = climate.build_industry_climate("半导体")
        by_name = {d["name"]: d for d in card["dims"]}
        # _sw_tables 既有失败语义：首表抛错即整体降级（不继续请求第二表）
        assert calls["first"] == 1 and calls["second"] == 0
        assert by_name["估值分位"]["valid"] is False
        assert by_name["盈利趋势"]["valid"] is False
        assert by_name["相对强度"]["valid"] is False
        assert by_name["估值分位"]["value"] is None  # 不以 0 冒充
        assert by_name["资金流"]["valid"] is True     # 非申万源维度不受累
        assert card["state"] == "数据不完整"

    def test_private_dims_accept_shared_tables(self, monkeypatch):
        """兼容入参：tables 显式传入时零取表；None 时自行取（旧路径）。"""
        from lib import climate

        calls = {"first": 0, "second": 0}
        monkeypatch.setattr(climate, "_ak", lambda: self._fake_ak(calls))
        tables = climate._sw_tables()
        assert calls == {"first": 1, "second": 1}

        r1 = climate._dim_valuation("半导体", tables)
        r2 = climate._dim_earnings("半导体", tables)
        assert r1["valid"] is True and r2["valid"] is True
        assert calls == {"first": 1, "second": 1}, "传入 tables 不得再取表"
