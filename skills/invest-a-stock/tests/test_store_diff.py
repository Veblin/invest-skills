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
        """v0.2.7 P2-1：60 分钟窗口剔除最新行之前的同会话重复行。

        P2-1 修正（code-review 第四轮）：窗口锚定最新行而非 now()——同会话
        多次 collect 只留最后一条，但最新行恒为 newer 保留。
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
