"""申万成分整表（index_member_all）解析、同行池与产业链候选。

背景：Tushare `stock_basic.industry` 是自有粗分类，与申万2021 不对齐
（实测 110 个粗名仅 36 个精确命中申万名，覆盖 55.2% 的在上市公司）。
本文件覆盖新引入的 L0 路径、四级降级链、以及同行取 top-N 由字母序改市值序
的回归（后者是本次修掉的旧缺陷）。

缓存隔离由 conftest 的 `_isolate_sw_member_cache` 自动 fixture 负责。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fixtures.collections import collection_v2_minimal

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _df(rows: list[dict]) -> MagicMock:
    """伪 DataFrame：只需 .empty / .to_dict('records') / .iterrows。"""
    m = MagicMock()
    m.empty = not rows
    m.to_dict = MagicMock(return_value=list(rows))
    m.iterrows = MagicMock(return_value=iter(list(enumerate(rows))))
    m.iloc = [rows[0]] if rows else []
    return m


def _row(ts_code: str, name: str, l1=("801730.SI", "电力设备"),
         l2=("801737.SI", "电池"), l3=("857371.SI", "锂电池"),
         out_date=None, is_new="Y") -> dict:
    return {
        "ts_code": ts_code, "name": name,
        "l1_code": l1[0] if l1 else "", "l1_name": l1[1] if l1 else "",
        "l2_code": l2[0] if l2 else "", "l2_name": l2[1] if l2 else "",
        "l3_code": l3[0] if l3 else "", "l3_name": l3[1] if l3 else "",
        "in_date": "20180101", "out_date": out_date, "is_new": is_new,
    }


def _healthy(tc: Any) -> Any:
    """装上健康 client 的失败信号。

    `query` 失败时返回**空帧且不抛**，失败信号在 ``last_error`` /
    ``is_permission_denied``；无信号的空帧才是「接口正常返回空」（= 取完了）。
    裸 MagicMock 的这两个属性是自动生成的 Mock（真值），会让空页被误判成
    取数失败，故伪 client 必须显式声明健康。
    """
    tc.is_permission_denied = MagicMock(return_value=False)
    tc.last_error = None
    return tc


def _tc(member_rows: list[dict], *, page_size: int = 3000,
        offset_ignored: bool = False) -> Any:
    """伪 TushareClient：只实现 index_member_all 分页。"""
    tc = MagicMock()
    _healthy(tc)
    calls: list[dict] = []

    def _query(api: str, **kw: Any) -> MagicMock:
        calls.append({"api": api, **kw})
        if api != "index_member_all":
            return _df([])
        off = 0 if offset_ignored else int(kw.get("offset") or 0)
        return _df(member_rows[off:off + page_size])

    tc.query.side_effect = _query
    tc.calls = calls
    return tc


# ── A. 整表与缓存 ────────────────────────────────────────────────────────

class TestSwMemberTable:
    def test_pagination_stops_and_passes_offset(self, monkeypatch: Any):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_SW_MEMBER_PAGE", 3)
        rows = [_row("00000%d.SZ" % i, "C%d" % i) for i in range(4)]
        tc = _tc(rows, page_size=3)
        got = orch._load_sw_member_table(tc)
        assert len(got) == 4
        offs = [c.get("offset") for c in tc.calls if c["api"] == "index_member_all"]
        # offset 按已取行数推进（0 → 3 → 4）；末页判据是**空页**而非短页
        # （短页不可靠：页长取决于服务端，未传 limit），故末页之后还发一次确认。
        assert offs == [0, 3, 4], "应按已取行数推进偏移，并以空页确认末页"

    def test_offset_ignored_does_not_loop_forever(self, monkeypatch: Any):
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_SW_MEMBER_PAGE", 3)
        rows = [_row("00000%d.SZ" % i, "C%d" % i) for i in range(3)]
        tc = _tc(rows, page_size=3, offset_ignored=True)
        got = orch._load_sw_member_table(tc)
        # 每页都返回同样的首页 → 第二页即判定重复并停止
        assert len(tc.calls) == 2
        # 首页满页 ⇒ 服务端还有更多行；offset 被忽略时完整性不可证，
        # 故不得把首页前缀当权威成分（改动：原实现返回了这 3 行）
        assert got == [], "完整性不可证 → 返回空，交由既有路径回落"

    def test_mid_pagination_failure_returns_empty_and_is_not_cached(
        self, monkeypatch: Any,
    ):
        """分页中途失败：截断表既不得返回、也不得写缓存。

        page0 满页 ⇒ 后面还有内容，此时 break 拿到的是**前缀**。返回它会让
        后续页的标的被当作「不在申万成分中」、同行池按残缺全域算分位，且结果
        以 source=sw_member_all 的权威名义输出；写缓存更会把残缺钉死 24h。
        """
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_SW_MEMBER_PAGE", 2)
        rows = [_row("00000%d.SZ" % i, "C%d" % i) for i in range(6)]
        cache_writes: list[Any] = []

        class FakeCache:
            def get(self, dim: str, key: str) -> Any:
                return None

            def set(self, dim: str, key: str, val: Any, **kw: Any) -> None:
                cache_writes.append(val)

        tc = MagicMock()
        offsets: list[Any] = []

        def _query(api: str, **kw: Any) -> MagicMock:
            if api != "index_member_all":
                return _df([])
            off = int(kw.get("offset") or 0)
            offsets.append(off)
            if off >= 2:
                raise RuntimeError("page 1 failed (simulated)")
            return _df(rows[off:off + 2])

        tc.query.side_effect = _query
        with patch.object(orch, "_sw_cache", lambda: FakeCache()):
            got = orch._load_sw_member_table(tc)

        assert offsets == [0, 2], "首页满页后应尝试第二页"
        assert got == [], "截断表不得作为申万权威成分返回"
        assert cache_writes == [], "截断表不得写缓存（否则 24h 内持续残缺）"

    def test_empty_page_with_failure_signal_is_not_complete(
        self, monkeypatch: Any,
    ):
        """空页 ≠ 取完：client 失败时返回**空帧且不抛**（信号在 last_error）。

        page0 满页后 page1 因超时/配额返回空帧——若把空页一律当「末页」，
        3000 行前缀仍会被当完整表写缓存 24h（正是本修复要消除的路径）。
        """
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_SW_MEMBER_PAGE", 2)
        rows = [_row("00000%d.SZ" % i, "C%d" % i) for i in range(6)]
        cache_writes: list[Any] = []

        class FakeCache:
            def get(self, dim: str, key: str) -> Any:
                return None

            def set(self, dim: str, key: str, val: Any, **kw: Any) -> None:
                cache_writes.append(val)

        tc = MagicMock()
        tc.is_permission_denied = MagicMock(return_value=False)

        def _query(api: str, **kw: Any) -> MagicMock:
            if api != "index_member_all":
                return _df([])
            off = int(kw.get("offset") or 0)
            if off >= 2:
                tc.last_error = "Timeout: request timed out"
                return _df([])
            tc.last_error = None
            return _df(rows[off:off + 2])

        tc.query.side_effect = _query
        with patch.object(orch, "_sw_cache", lambda: FakeCache()):
            got = orch._load_sw_member_table(tc)

        assert got == [], "带失败信号的空页不得当作末页"
        assert cache_writes == [], "截断表不得写缓存"

    def test_complete_table_is_cached(self, monkeypatch: Any):
        """前置守卫：完整性判定不得把正常翻页（末页短页）误判为不完整。"""
        from lib.collector import _orchestrate as orch

        monkeypatch.setattr(orch, "_SW_MEMBER_PAGE", 2)
        rows = [_row("00000%d.SZ" % i, "C%d" % i) for i in range(3)]
        cache_writes: list[Any] = []

        class FakeCache:
            def get(self, dim: str, key: str) -> Any:
                return None

            def set(self, dim: str, key: str, val: Any, **kw: Any) -> None:
                cache_writes.append(val)

        tc = _tc(rows, page_size=2)
        with patch.object(orch, "_sw_cache", lambda: FakeCache()):
            got = orch._load_sw_member_table(tc)
        assert len(got) == 3
        assert len(cache_writes) == 1, "末页短页 = 正常结束，应写缓存"

    def test_cache_hit_makes_one_round_then_zero(self):
        from lib.collector import _orchestrate as orch

        rows = [_row("000001.SZ", "A")]
        tc = _tc(rows, page_size=3000)
        orch._load_sw_member_table(tc)
        first = len(tc.calls)
        tc.calls.clear()
        orch._load_sw_member_table(tc)
        assert first >= 1
        assert tc.calls == [], "第二次应命中缓存，零网络调用"

    def test_empty_result_not_cached(self):
        from lib.collector import _orchestrate as orch

        tc = _tc([])
        assert orch._load_sw_member_table(tc) == []
        tc.calls.clear()
        orch._load_sw_member_table(tc)
        assert tc.calls, "空结果不得写缓存，下次仍应重试网络（D6）"

    def test_query_exception_degrades_to_empty(self):
        from lib.collector import _orchestrate as orch

        tc = MagicMock()
        tc.query.side_effect = RuntimeError("no permission")
        assert orch._load_sw_member_table(tc) == []

    def test_env_switch_disables(self, monkeypatch: Any):
        from lib.collector import _orchestrate as orch

        monkeypatch.setenv("INVEST_SW_MEMBER_ALL", "0")
        tc = _tc([_row("000001.SZ", "A")])
        assert orch._load_sw_member_table(tc) == []
        assert tc.calls == [], "关闭开关后不得发起任何请求"

    def test_history_rows_filtered(self):
        from lib.collector import _orchestrate as orch

        rows = [
            _row("000001.SZ", "当期", out_date=None, is_new="Y"),
            _row("000002.SZ", "已调出", out_date="20250101", is_new="N"),
            _row("000003.SZ", "已退市", out_date="20250601", is_new="N"),
        ]
        got = orch._load_sw_member_table(_tc(rows))
        assert [r["ts_code"] for r in got] == ["000001.SZ"]


# ── B. 单标的申万归属与同行池层级 ────────────────────────────────────────

class TestSwContext:
    def test_picks_l3_and_pool(self):
        from lib.collector import _orchestrate as orch

        rows = [_row("300750.SZ", "宁德时代")] + [
            _row("3000%02d.SZ" % i, "P%d" % i) for i in range(6)
        ]
        ctx = orch._resolve_sw_context(_tc(rows), "300750")
        assert ctx["source"] == "sw_member_all"
        assert ctx["industry_name"] == "锂电池"
        assert ctx["level"] == "L3"
        assert ctx["chain_names"] == ["锂电池", "电池", "电力设备"]
        assert ctx["pool_level"] == "L3"
        assert len(ctx["pool"]) == 7

    def test_escalates_when_l3_too_thin(self):
        from lib.collector import _orchestrate as orch

        # L3 仅 2 家（< _SW_POOL_MIN=5）→ 应升到 L2
        rows = [
            _row("300750.SZ", "宁德时代"),
            _row("300001.SZ", "同行1"),
        ] + [
            _row("3001%02d.SZ" % i, "L2同业%d" % i, l3=("", ""))
            for i in range(6)
        ]
        ctx = orch._resolve_sw_context(_tc(rows), "300750")
        assert ctx["industry_name"] == "锂电池"       # 展示仍取最深层
        assert ctx["pool_level"] == "L2"              # 分位基数升层
        assert len(ctx["pool"]) >= 5

    def test_escalated_pool_forwards_pool_name_and_level(self):
        """L3 过薄升层时，结果须带上池层与池名（供渲染披露分母口径）。"""
        from lib.collector import _orchestrate as orch

        rows = [
            _row("300750.SZ", "宁德时代"),
            _row("300001.SZ", "同行1"),
        ] + [
            _row("3001%02d.SZ" % i, "L2同业%d" % i, l3=("", ""))
            for i in range(6)
        ]
        ctx = orch._resolve_sw_context(_tc(rows), "300750")
        assert ctx["level"] == "L3" and ctx["pool_level"] == "L2"
        assert ctx["pool_name"] == "电池", "池名未导出 → 渲染侧无法披露升层"

    def test_industry_position_discloses_escalated_pool(self):
        """分位分母来自 L2 池而名称展示 L3 名时，报告须披露这一口径差异。

        读者按报告所写的「锂电池」重算会得到不同的 N（L3 仅 2 家，分母实际是
        L2 的 9 家）。其他降级路径都有披露（粗分类回落有 warning、名称不匹配有
        tag），唯独池升层此前无任何提示。
        """
        from lib.render_markdown._v3 import (
            _FundamentalsContext,
            _section_4a_industry_position,
        )

        coll = collection_v2_minimal()
        coll["industry_peers"] = {
            "industry_name": "锂电池", "industry_level": "L3",
            "peer_source": "sw_index_member", "peer_level": "L2",
            "pool_name": "电池", "sufficient": True,
            "rankings": {"revenue_yoy_pct": 33.3, "revenue_yoy_rank": 3,
                         "revenue_yoy_total": 9},
            "target": {"revenue_yoy": 12.0},
        }
        dims = {d["dimension"]: d for d in coll["dimensions"]}
        ctx = _FundamentalsContext(dims, coll, {})
        out = "\n".join(_section_4a_industry_position(dims, ctx, []))
        line = next(ln for ln in out.splitlines() if ln.startswith("所属行业："))
        assert "申万二级行业" in line, f"升层口径未披露: {line}"
        assert "电池" in line, f"未点名实际分母池: {line}"

    def test_industry_position_same_level_has_no_extra_tag(self):
        """守卫：未升层时文案与既有基线一致（不新增披露）。"""
        from lib.render_markdown._v3 import (
            _FundamentalsContext,
            _section_4a_industry_position,
        )

        coll = collection_v2_minimal()
        coll["industry_peers"] = {
            "industry_name": "锂电池", "industry_level": "L3",
            "peer_source": "sw_index_member", "peer_level": "L3",
            "pool_name": "锂电池", "sufficient": True,
            "rankings": {"revenue_yoy_pct": 33.3, "revenue_yoy_rank": 3,
                         "revenue_yoy_total": 9},
            "target": {"revenue_yoy": 12.0},
        }
        dims = {d["dimension"]: d for d in coll["dimensions"]}
        ctx = _FundamentalsContext(dims, coll, {})
        out = "\n".join(_section_4a_industry_position(dims, ctx, []))
        line = next(ln for ln in out.splitlines() if ln.startswith("所属行业："))
        assert "申万二级行业" not in line
        assert "「」" not in line, "空行业名不得渲染成「」"

    def test_unknown_symbol_returns_empty(self):
        from lib.collector import _orchestrate as orch

        ctx = orch._resolve_sw_context(_tc([_row("000001.SZ", "A")]), "999999")
        assert ctx["source"] == "none" and ctx["pool"] == []


# ── C. collect_industry_peers 主路径与降级 ───────────────────────────────

class TestCollectIndustryPeersLadder:
    def _run(self, tc: Any, symbol: str = "300750", **kw: Any) -> dict:
        from lib import collector

        _healthy(tc)
        with patch.object(collector.env, "is_tushare_available", return_value=True), \
             patch.object(collector.env, "get_config",
                          return_value={"TUSHARE_TOKEN": "x" * 32}), \
             patch.object(collector._orchestrate, "_tushare_client", return_value=tc):
            return collector.collect_industry_peers(symbol, **kw)

    def test_l0_uses_sw_member_all(self):
        rows = [_row("300750.SZ", "宁德时代")] + [
            _row("3000%02d.SZ" % i, "P%d" % i) for i in range(6)
        ]
        tc = _tc(rows)
        tc.query.side_effect = None
        tc.query.return_value = _df([])
        # stock_basic 返回在上市宇宙（含全部成分）
        def _q(api: str, **kw: Any) -> Any:
            if api == "index_member_all":
                off = int(kw.get("offset") or 0)
                return _df(rows[off:off + 3000])
            if api == "stock_basic":
                return _df([{"ts_code": r["ts_code"], "name": r["name"]} for r in rows])
            return _df([])
        tc.query.side_effect = _q

        r = self._run(tc)
        assert r["industry_name"] == "锂电池"
        assert r["peer_source"] == "sw_index_member"
        assert r["peer_level"] == "L3"
        assert r["sufficient"] is True
        assert r.get("warning") is None

    def test_topn_by_market_cap_not_alphabetical(self):
        """锁旧缺陷：此前 other_codes 按代码字母序截断，等于随机取前 N 家。"""
        rows = [_row("300750.SZ", "宁德时代")] + [
            _row("0001%02d.SZ" % i, "小市值%d" % i) for i in range(8)
        ]
        big = [_row("6009%02d.SH" % i, "大市值%d" % i) for i in range(3)]
        rows += big

        def _q(api: str, **kw: Any) -> Any:
            if api == "index_member_all":
                off = int(kw.get("offset") or 0)
                return _df(rows[off:off + 3000])
            if api == "stock_basic":
                return _df([{"ts_code": r["ts_code"], "name": r["name"]} for r in rows])
            if api == "daily_basic" and "trade_date" in kw:
                # 大市值成交额高：显式给 total_mv（万元）
                return _df([{"ts_code": r["ts_code"],
                             "total_mv": 9e6 if r["name"].startswith("大市值") else 1e5}
                            for r in rows])
            return _df([])

        tc = MagicMock()
        tc.query.side_effect = _q
        r = self._run(tc, max_peers=3)
        # 字母序前 3 应是 000100/000101/000102（小市值）；市值序前 3 必须是大市值
        assert len(r["peers"]) == 3, r
        for p in r["peers"]:
            assert p.get("name", "").startswith("大市值"), \
                f"应按市值取 top-N，实际拿到 {p.get('name')}"

    def test_env_switch_falls_back_to_stock_basic(self, monkeypatch: Any):
        monkeypatch.setenv("INVEST_SW_MEMBER_ALL", "0")
        member = [{"ts_code": "002001.SZ", "name": "同行A", "industry": "电气设备"},
                  {"ts_code": "002002.SZ", "name": "同行B", "industry": "电气设备"},
                  {"ts_code": "002003.SZ", "name": "同行C", "industry": "电气设备"}]
        tc = MagicMock()

        def _q(api: str, **kw: Any) -> Any:
            if api == "stock_basic":
                if kw.get("ts_code"):
                    return _df([{"ts_code": kw["ts_code"], "name": "目标",
                                 "industry": "电气设备"}])
                return _df(member)
            if api == "index_classify":
                return _df([{"industry_name": "电气设备", "index_code": "851121.SI"}])
            return _df([])

        tc.query.side_effect = _q
        r = self._run(tc, symbol="600176")
        assert r["peer_source"] == "stock_basic_fallback"
        assert r.get("warning"), "回退路径必须保留降级警告"
        assert "peer_level" not in r

    def test_l1_reads_con_code(self):
        """index_member 的列是 con_code（无 name 列）——旧实现读 ts_code 恒空。"""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("INVEST_SW_MEMBER_ALL", "0")
        try:
            tc = MagicMock()

            def _q(api: str, **kw: Any) -> Any:
                if api == "stock_basic":
                    if kw.get("ts_code"):
                        return _df([{"ts_code": kw["ts_code"], "name": "目标",
                                     "industry": "电气设备"}])
                    return _df([{"ts_code": "002001.SZ", "name": "同行A", "industry": "电气设备"},
                                {"ts_code": "002002.SZ", "name": "同行B", "industry": "电气设备"}])
                if api == "index_classify":
                    return _df([{"industry_name": "电气设备", "index_code": "851121.SI"}])
                if api == "index_member":
                    # 真实列集：index_code, con_code, in_date, out_date, is_new
                    return _df([{"index_code": "851121.SI", "con_code": "002001.SZ",
                                 "in_date": "20200101", "out_date": None, "is_new": "Y"},
                                {"index_code": "851121.SI", "con_code": "002002.SZ",
                                 "in_date": "20200101", "out_date": None, "is_new": "Y"}])
                return _df([])

            tc.query.side_effect = _q
            with patch.object(_orch_env(), "is_tushare_available", return_value=True), \
                 patch.object(_orch_env(), "get_config",
                              return_value={"TUSHARE_TOKEN": "x" * 32}), \
                 patch.object(_orch_mod(), "_tushare_client", return_value=tc), \
                 patch.object(_orch_mod(), "_ms_lookup_sw_index_code",
                              return_value="851121.SI"):
                r = _collect_mod().collect_industry_peers("600176", max_peers=5)
            assert r["peer_source"] == "sw_index_member"
            names = {p.get("name") for p in r["peers"]}
            assert names & {"同行A", "同行B"}, f"con_code 未被解析: {names}"
        finally:
            monkeypatch.undo()

    def test_delisted_filtered_out(self):
        """index_member_all 不更新退市状态（实测 300116 out_date=None/is_new=Y）。"""
        rows = [_row("300750.SZ", "宁德时代")] + [
            _row("0001%02d.SZ" % i, "在上市%d" % i) for i in range(6)
        ]
        listed = [r for r in rows]
        tc = MagicMock()

        def _q(api: str, **kw: Any) -> Any:
            if api == "index_member_all":
                off = int(kw.get("offset") or 0)
                # 表里额外混入一只「已退市但 is_new=Y」的
                allrows = rows + [_row("300116.SZ", "*ST保力(退市)")]
                return _df(allrows[off:off + 3000])
            if api == "stock_basic":
                return _df([{"ts_code": r["ts_code"], "name": r["name"]} for r in listed])
            return _df([])

        tc.query.side_effect = _q
        r = self._run(tc, max_peers=10)
        got = {p.get("symbol") for p in r["peers"]}
        assert "300116" not in got, "退市股必须被剔除"


def _orch_env() -> Any:
    from lib import env as _env
    return _env


def _orch_mod() -> Any:
    from lib.collector import _orchestrate as _o
    return _o


def _collect_mod() -> Any:
    from lib import collector as _c
    return _c


# ── D. 产业链 ────────────────────────────────────────────────────────────

class TestChainCandidates:
    def test_new_keys_exist(self):
        from lib.chain import _CHAIN_MAP, _match_chain_keyword

        got = _match_chain_keyword("锂电池", _CHAIN_MAP)
        assert got and got["position"] == "中游制造"
        assert "电解液" in got["upstream"]

    def test_l1_power_equipment_no_longer_loses_block(self):
        """回归：加键前「电力设备」无任何键命中 → 产业链块整块消失。"""
        from lib.chain import collect_chain_context

        r = collect_chain_context("300750", industry="电力设备")
        assert r["chain_position"], "L1 电力设备 不得再丢块"

    def test_candidate_priority_prefers_l3(self):
        from lib.chain import collect_chain_context

        r = collect_chain_context(
            "300750", industry="电气设备",
            chain_candidates=["锂电池", "电池", "电力设备"],
        )
        assert r["chain_matched_on"] == "锂电池"
        assert "电解液" in r["upstream"]

    def test_empty_candidates_keeps_today_behavior(self):
        from lib.chain import collect_chain_context

        r = collect_chain_context("600036", industry="银行")
        assert r["chain_matched_on"] == "银行"
        assert r["chain_position"] == "金融"
        r2 = collect_chain_context("999999", industry="不存在的行业XYZ")
        assert r2["chain_matched_on"] is None
