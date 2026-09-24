"""Store diff 功能测试。

测试覆盖:
  - diff_collections 标量/列表变化
  - get_latest_two / get_collection（隔离 DB）
  - 缺维度、股东同日期键（H2 已知限制）
  - v0.1.2 plan §0.5 派生 diff 尚未实现
"""

from __future__ import annotations

import pytest

from stock_testutil import make_store_collection


class TestDiffCollections:
    def test_scalar_change(self):
        from lib.store import diff_collections

        old = make_store_collection(quote_close=10.0)
        new = make_store_collection(quote_close=12.0, fetched_at="2026-06-08T00:00:00Z")
        result = diff_collections(old, new)

        assert result["symbol"] == "000001"
        close_changes = [c for c in result["changed"] if c["path"] == "quote.close"]
        assert len(close_changes) == 1
        assert close_changes[0]["old"] == 10.0
        assert close_changes[0]["new"] == 12.0
        assert close_changes[0]["pct"] == pytest.approx(20.0)

    def test_no_change(self):
        from lib.store import diff_collections

        c = make_store_collection()
        result = diff_collections(c, c)
        assert len(result["changed"]) == 0

    def test_dimension_missing_in_old(self):
        from lib.store import diff_collections

        old = make_store_collection()
        new = make_store_collection()
        new["dimensions"].append({
            "dimension": "valuation",
            "display": "估值分析",
            "data": {"pe_ttm": 15.0},
            "status": "available",
            "_meta": {"source": "test"},
        })
        result = diff_collections(old, new)
        skipped_dims = [s["dimension"] for s in result["skipped"]]
        assert "valuation" in skipped_dims

    def test_both_none_data(self):
        from lib.store import diff_collections

        old = {
            "symbol": "000001",
            "dimensions": [{"dimension": "quote", "data": None, "status": "missing"}],
        }
        new = {
            "symbol": "000001",
            "dimensions": [{"dimension": "quote", "data": None, "status": "missing"}],
        }
        result = diff_collections(old, new)
        assert len(result["skipped"]) > 0

    def test_percentage_direction(self):
        from lib.store import diff_collections

        old = make_store_collection(quote_close=100.0)
        new = make_store_collection(quote_close=90.0, fetched_at="2026-06-08T00:00:00Z")
        result = diff_collections(old, new)
        close_changes = [c for c in result["changed"] if c["path"] == "quote.close"]
        assert close_changes[0]["pct"] == pytest.approx(-10.0)

    def test_no_derived_technical_in_v012(self):
        """H3: v0.1.2 diff 仅原始维度，不含 derived.technical。"""
        from lib.store import diff_collections

        old = make_store_collection(quote_close=10.0)
        new = make_store_collection(quote_close=12.0, fetched_at="2026-06-08T00:00:00Z")
        result = diff_collections(old, new)
        assert "derived" not in result
        paths = [c.get("path", "") for c in result["changed"]]
        assert not any(p.startswith("derived") for p in paths)


class TestIndexByDate:
    def test_shareholders_same_end_date_uses_compound_key(self):
        """同 end_date 多股东时用 holder_name 构建复合键，避免静默覆盖（H2 修复）。"""
        from lib.store import _index_by_date

        data = [
            {"end_date": "20251231", "holder_name": f"股东{i}", "hold_ratio": i}
            for i in range(10)
        ]
        indexed = _index_by_date(data)
        # 10 条记录应有 10 个不同键（end_date + holder_name）
        assert len(indexed) == 10
        assert "20251231_股东0" in indexed
        assert indexed["20251231_股东9"]["hold_ratio"] == 9

    def test_first_without_holder_name_preserved(self):
        """首条无 holder_name 仍保留（序号兜底，不静默丢弃）。"""
        from lib.store import _index_by_date

        data = [
            {"end_date": "20251231", "hold_ratio": 5.0},          # 无名称
            {"end_date": "20251231", "holder_name": "股东A", "hold_ratio": 3.0},
        ]
        indexed = _index_by_date(data)
        assert len(indexed) == 2
        # 首条用 "0" 兜底
        assert "20251231_0" in indexed
        assert indexed["20251231_0"]["hold_ratio"] == 5.0
        assert "20251231_股东A" in indexed

    def test_same_date_same_holder_multiple_records_preserved(self):
        """同日期同 holder 多条记录不互相覆盖（review fix #8）。"""
        from lib.store import _index_by_date

        data = [
            {"end_date": "20240101", "holder_name": "张三", "change_ratio": 1.0},
            {"end_date": "20240101", "holder_name": "张三", "change_ratio": 2.0},
            {"end_date": "20240101", "holder_name": "张三", "change_ratio": 3.0},
        ]
        indexed = _index_by_date(data)
        # 3 条记录全部保留（复合键 + 递增后缀），不得折叠为 1 条
        assert len(indexed) == 3
        ratios = sorted(v["change_ratio"] for v in indexed.values())
        assert ratios == [1.0, 2.0, 3.0]
        assert "20240101_张三" in indexed
        assert "20240101_张三_2" in indexed
        assert "20240101_张三_3" in indexed

    def test_same_holder_records_stable_keys_across_order(self):
        """同 (date, holder) 记录键跨快照稳定（内容序，review fix #12）。"""
        from lib.store import _index_by_date

        def rec(ratio: float) -> dict:
            return {"end_date": "20240101", "holder_name": "张三", "change_ratio": ratio}

        fwd = _index_by_date([rec(1.0), rec(2.0), rec(3.0)])
        rev = _index_by_date([rec(3.0), rec(2.0), rec(1.0)])
        # 键集合一致，且内容序（change_ratio 升序）映射到相同后缀
        assert sorted(fwd) == sorted(rev)
        assert fwd["20240101_张三"]["change_ratio"] == 1.0
        assert fwd["20240101_张三_2"]["change_ratio"] == 2.0
        assert fwd["20240101_张三_3"]["change_ratio"] == 3.0
        assert rev["20240101_张三"]["change_ratio"] == 1.0


class TestGetLatestTwo:
    def test_single_record_returns_none(self, isolated_store):
        isolated_store.save_collection(
            make_store_collection(symbol="999999", fetched_at="2026-06-01T00:00:00Z"))
        assert isolated_store.get_latest_two("999999") is None

    def test_two_records_returns_tuple(self, isolated_store):
        isolated_store.save_collection(
            make_store_collection(symbol="999998", fetched_at="2026-06-01T00:00:00Z",
                                quote_close=10.0))
        isolated_store.save_collection(
            make_store_collection(symbol="999998", fetched_at="2026-06-08T00:00:00Z",
                                quote_close=12.0))
        result = isolated_store.get_latest_two("999998")
        assert result is not None
        older, newer = result
        assert older["fetched_at"] < newer["fetched_at"]

    def test_non_existent_symbol(self, isolated_store):
        assert isolated_store.get_latest_two("NONEXIST") is None

    def test_skips_same_session_rows(self, isolated_store):
        """v0.2.7 P2-1：10 分钟窗口剔除最新行之前的同会话重复行。

        P2-1 修正（code-review 第四轮）：窗口锚定最新行而非 now()——同会话
        多次 collect 只留最后一条，但最新行恒为 newer 保留。
        新增回归（第五轮）：09:05/09:31 间隔 26 分钟的两次真实采集必须配对
        ——窗宽 60→10 分钟,独立会话不再被误并入。
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        # 10 秒 / 31 秒前（同会话窗口内）、125 / 130 分钟前（窗口外）
        ts_10s = (now - timedelta(seconds=10)).isoformat()
        ts_31s = (now - timedelta(seconds=31)).isoformat()
        ts_125m = (now - timedelta(minutes=125)).isoformat()
        ts_130m = (now - timedelta(minutes=130)).isoformat()
        for ts in (ts_10s, ts_31s, ts_125m, ts_130m):
            isolated_store.save_collection(
                make_store_collection(symbol="999995", fetched_at=ts))
        pair = isolated_store.get_latest_two("999995")
        assert pair is not None
        older, newer = pair
        assert older["fetched_at"] == ts_125m  # 窗口外最近行
        assert newer["fetched_at"] == ts_10s  # 最新行保留（锚定最新行修正）

    def test_cross_session_pair_26min_kept(self, isolated_store):
        """第五轮回归：09:05/09:31 两次独立会话（26 分钟）不得被窗口误并。

        60 分钟窗曾丢弃 09:05 行 → diff 假报「至少需要 2 次采集」；窗宽
        60→10 分钟后,26 分钟间隔视为真实跨会话,配对 (前次, 最新)。
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        ts_early = (now - timedelta(minutes=26)).isoformat()
        ts_late = now.isoformat()
        isolated_store.save_collection(
            make_store_collection(symbol="999991", fetched_at=ts_early))
        isolated_store.save_collection(
            make_store_collection(symbol="999991", fetched_at=ts_late))
        pair = isolated_store.get_latest_two("999991")
        assert pair is not None
        older, newer = pair
        assert older["fetched_at"] == ts_early
        assert newer["fetched_at"] == ts_late

    def test_pair_keeps_newest_when_diff_immediately_after_collect(self, isolated_store):
        """code-review 第四轮：采集后立即 diff，最新快照不得被自身窗口排除。

        此前后退为锚定 datetime.now()：Tue 09:05 采集、09:06 diff 时 Tue 行
        被排 → 配对 None/退化，「至少需要 2 次采集」假报。锚定最新行后
        配对 (前次会话, 最新)。
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        ts_recent = (now - timedelta(minutes=1)).isoformat()  # 刚采集（1 分钟前）
        ts_prev = (now - timedelta(days=1)).isoformat()       # 前次会话（1 天前）
        isolated_store.save_collection(
            make_store_collection(symbol="999993", fetched_at=ts_prev))
        isolated_store.save_collection(
            make_store_collection(symbol="999993", fetched_at=ts_recent))
        pair = isolated_store.get_latest_two("999993")
        assert pair is not None
        older, newer = pair
        assert older["fetched_at"] == ts_prev
        assert newer["fetched_at"] == ts_recent


class TestGetCollection:
    def test_by_id(self, isolated_store):
        cid = isolated_store.save_collection(
            make_store_collection(symbol="999997", fetched_at="2026-06-01T00:00:00Z"))
        result = isolated_store.get_collection(cid)
        assert result is not None
        assert result["symbol"] == "999997"

    def test_invalid_id(self, isolated_store):
        assert isolated_store.get_collection(-1) is None


class TestEventsKeySnapshot:
    def test_extract_key_snapshot_90_day_event_count(self):
        from lib.store import extract_key_snapshot, _events_count_from_summary

        collection = {
            "symbol": "600176",
            "fetched_at": "2026-06-01T00:00:00Z",
            "_meta": {
                "events_summary": {
                    "count_90d": 7,
                    "window_days": 90,
                    "latest_date": "2026-06-15",
                    "top_types": [{"type": "buyback", "count": 3}],
                },
            },
        }
        snap = extract_key_snapshot(collection)
        assert snap["events"]["event_count"] == 7
        assert snap["events"]["window_days"] == 90
        assert _events_count_from_summary(collection["_meta"]["events_summary"]) == 7

    def test_diff_key_snapshots_detects_event_count_change(self):
        from lib.store import diff_key_snapshots

        old = {
            "symbol": "600176",
            "fetched_at": "2026-06-01T00:00:00Z",
            "_meta": {
                "events_summary": {
                    "count_30d": 2,
                    "event_count": 2,
                    "window_days": 30,
                    "top_types": [{"type": "buyback", "count": 2}],
                },
            },
        }
        new = {
            "symbol": "600176",
            "fetched_at": "2026-06-08T00:00:00Z",
            "_meta": {
                "events_summary": {
                    "count_30d": 5,
                    "event_count": 5,
                    "window_days": 30,
                    "top_types": [{"type": "buyback", "count": 3}, {"type": "dividend", "count": 2}],
                },
            },
        }
        result = diff_key_snapshots(old, new)
        events_diff = result.get("events") or {}
        assert events_diff.get("count_change") == 3

    def test_other_in_old_ranking_does_not_produce_phantom_type_change(self):
        """同口径两侧：旧榜里的 `other` 不得读成「移除了 other + 新增一堆类型」。

        （同口径快照也可能带 `other`——v0.3.1 实现迭代中途存下的快照有计数却未过滤榜，
        故 `_signal_types` 的低信号过滤是承重的，不是冗余。）
        """
        from lib.store import diff_key_snapshots

        def _coll(fetched: str, top_types: list, proc: int, uncl: int) -> dict:
            return {
                "symbol": "600176",
                "fetched_at": fetched,
                "_meta": {"events_summary": {
                    "event_count": 3, "window_days": 30, "top_types": top_types,
                    "procedural_count": proc, "unclassified_count": uncl,
                }},
            }

        old = _coll("2026-06-01T00:00:00Z",
                    [{"type": "other", "count": 68}, {"type": "buyback", "count": 3}], 0, 68)
        new = _coll("2026-06-08T00:00:00Z", [{"type": "buyback", "count": 3}], 0, 68)

        events_diff = diff_key_snapshots(old, new).get("events") or {}
        assert events_diff.get("new_types", []) == []
        assert events_diff.get("removed_types", []) == []

    def test_pre_v031_snapshot_does_not_produce_phantom_types(self):
        """**跨口径**比较不得报类型变化，并须显式说明「未比较」。

        v0.3.1 前的 `top_types` 取**未过滤**前 5（低信号占位），现在的取「剔除低信号后
        前 5」。同一批公告，新榜能显示旧榜被 `other` 挤出前 5 的实质类型——只做集合相减
        就会把它报成 new_types。过滤救不回来（旧榜根本没存第 6 位），只能按口径闸门跳过，
        且必须**说出来**：静默跳过与「无变化」在输出上无法区分。
        """
        from lib.store import diff_key_snapshots

        old = {  # 无低信号计数 = v0.3.1 前口径；other(68) 占掉一个榜位
            "symbol": "600176",
            "fetched_at": "2026-06-01T00:00:00Z",
            "_meta": {"events_summary": {
                "event_count": 58, "window_days": 30,
                "top_types": [
                    {"type": "other", "count": 68}, {"type": "buyback", "count": 3},
                    {"type": "dividend", "count": 2}, {"type": "earnings_report", "count": 2},
                    {"type": "holder_decrease", "count": 1},
                ],
            }},
        }
        new = {  # 同一批公告：equity_incentive(1) 此前被 other 挤出前 5
            "symbol": "600176",
            "fetched_at": "2026-06-08T00:00:00Z",
            "_meta": {"events_summary": {
                "event_count": 60, "window_days": 30,
                "top_types": [
                    {"type": "buyback", "count": 3}, {"type": "dividend", "count": 2},
                    {"type": "earnings_report", "count": 2}, {"type": "holder_decrease", "count": 1},
                    {"type": "equity_incentive", "count": 1},
                ],
                "procedural_count": 5, "unclassified_count": 3,
            }},
        }

        events_diff = diff_key_snapshots(old, new).get("events") or {}
        assert events_diff, "有 count_change（58→60）时应产出 diff 对象"
        assert events_diff.get("new_types", []) == [], "跨口径不得报幻影新增类型"
        assert events_diff.get("removed_types", []) == []
        assert events_diff.get("types_incomparable") is True, "跳过比较须显式说明"
        assert events_diff.get("count_change") == 2, "数量变化仍可比、照常报告"

    def test_low_signal_surge_is_reported(self):
        """程序性/未分类公告的激增须可见（top_types 已剔除这两类，只能靠计数）。"""
        from lib.store import diff_key_snapshots

        def _coll(fetched: str, proc: int, uncl: int) -> dict:
            return {
                "symbol": "600176",
                "fetched_at": fetched,
                "_meta": {"events_summary": {
                    "event_count": 3, "window_days": 30,
                    "top_types": [{"type": "buyback", "count": 3}],
                    "procedural_count": proc, "unclassified_count": uncl,
                }},
            }

        result = diff_key_snapshots(_coll("2026-06-01T00:00:00Z", 10, 0),
                                    _coll("2026-06-08T00:00:00Z", 50, 0))
        assert (result.get("events") or {}).get("low_signal_change") == {"procedural": 40}

    def test_low_signal_bucket_shift_is_reported_despite_equal_total(self):
        """10 条从 procedural 移到 unclassified（合计不变）也须报告。

        两桶语义不同（源标注程序性 vs 源未分类），只看合计会把这次口径平移抹平。
        """
        from lib.store import diff_key_snapshots

        def _coll(fetched: str, proc: int, uncl: int) -> dict:
            return {
                "symbol": "600176",
                "fetched_at": fetched,
                "_meta": {"events_summary": {
                    "event_count": 20, "window_days": 30,
                    "top_types": [{"type": "buyback", "count": 3}],
                    "procedural_count": proc, "unclassified_count": uncl,
                }},
            }

        result = diff_key_snapshots(_coll("2026-06-01T00:00:00Z", 10, 0),
                                    _coll("2026-06-08T00:00:00Z", 0, 10))
        events_diff = result.get("events") or {}
        assert events_diff, "合计不变但分桶平移 → 必须产出 diff"
        assert events_diff.get("low_signal_change") == {
            "procedural": -10, "unclassified": 10,
        }

    def test_low_signal_change_skipped_when_legacy_snapshot_lacks_counts(self):
        """v0.3.1 前的 summary 无低信号计数字段 → 不可比，不得报成「从 0 涨到 N」。"""
        from lib.store import diff_key_snapshots

        old = {
            "symbol": "600176",
            "fetched_at": "2026-06-01T00:00:00Z",
            "_meta": {"events_summary": {
                "event_count": 3, "window_days": 30,
                "top_types": [{"type": "buyback", "count": 3}],
            }},
        }
        new = {  # event_count 5 ≠ 3：确保产出 diff 对象，断言才非空转
            "symbol": "600176",
            "fetched_at": "2026-06-08T00:00:00Z",
            "_meta": {"events_summary": {
                "event_count": 5, "window_days": 30,
                "top_types": [{"type": "buyback", "count": 3}],
                "procedural_count": 50, "unclassified_count": 0,
            }},
        }

        events_diff = diff_key_snapshots(old, new).get("events") or {}
        assert events_diff, "应有 count_change，否则本测试空转"
        assert "low_signal_change" not in events_diff, "跨口径不得比低信号计数"
        assert events_diff.get("types_incomparable") is True

    def test_diff_skips_count_when_window_days_differ(self):
        from lib.store import diff_key_snapshots

        old = {
            "symbol": "600176",
            "fetched_at": "2026-06-01T00:00:00Z",
            "_meta": {
                "events_summary": {
                    "count_30d": 2,
                    "event_count": 2,
                    "window_days": 30,
                    "top_types": [{"type": "buyback", "count": 2}],
                },
            },
        }
        new = {
            "symbol": "600176",
            "fetched_at": "2026-06-08T00:00:00Z",
            "_meta": {
                "events_summary": {
                    "count_90d": 10,
                    "event_count": 10,
                    "window_days": 90,
                    "top_types": [{"type": "buyback", "count": 10}],
                },
            },
        }
        result = diff_key_snapshots(old, new)
        events_diff = result.get("events") or {}
        assert events_diff.get("count_change") == 0
        assert events_diff.get("window_days_changed") == {"old": 30, "new": 90}


class TestDiffEventsRendering:
    """`invest.py _print_diff_events` 的展示层。

    该函数此前无测试——而它是 `diff` CLI 的唯一出口（用户看到的就是这几行）。
    """

    def test_prints_low_signal_buckets_separately(self, capsys):
        """两桶分列打印，且不把英文键直接打进中文产物。"""
        import invest

        invest._print_diff_events({"events": {
            "count_change": 0, "new_types": [], "removed_types": [],
            "low_signal_change": {"procedural": -10, "unclassified": 10},
        }})
        out = capsys.readouterr().out
        assert "低信号变化" in out
        assert "程序性公告 -10" in out
        assert "未分类公告 +10" in out
        assert "procedural" not in out and "unclassified" not in out, "英文键泄漏进中文输出"

    def test_prints_types_incomparable_note(self, capsys):
        """跨口径比较须显式说明「未比较」，不能与「无变化」看起来一样。"""
        import invest

        invest._print_diff_events({"events": {
            "count_change": 2, "new_types": [], "removed_types": [],
            "types_incomparable": True,
        }})
        out = capsys.readouterr().out
        assert "未比较类型集合" in out
        assert "事件数量变化: +2" in out
