"""issue #34：期货基差新鲜度（实时拒绝 / 同日 merge 成对保留 / 历史读取判定）。

验收覆盖（独立核查 R3）：同日已有陈旧值后再次采集、历史行缺字段日期、
日历降级、无法解析日期、未来日期。全部在隔离库内（conftest autouse 隔离 +
连接边界守卫），不联网。

复检整改（2026-10-01 独立验收报告 P1）：新增**真实读取函数**（latest_snapshot /
load_history）、**两套标签函数**、**display 视图（env_label 文本同边界）**、
**旧 schema 幂等迁移**的回归用例——此前仅手动调用 `basis_is_current` helper，
未覆盖生产链路。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import market_microstructure as mm  # noqa: E402
from lib import store as store_mod  # noqa: E402

SESSION = "20260810"


def _rows_ending(asof: str, n: int = 21) -> list[dict]:
    """构造以 asof 结尾的 futures_daily 行（date 用带横线形态，验证归一化）。"""
    d0 = datetime.strptime(asof, "%Y%m%d").date()
    return [
        {
            "date": (d0 - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d"),
            "symbol": "IC", "contract": "IC2608.CFX",
            "basis_pct": round(-0.5072 + i * 0.0001, 6), "basis_pts": -40.0,
            "source": "tushare",
        }
        for i in range(n)
    ]


def _fake_result() -> dict:
    return {"_errors": []}


def _patch_rows(monkeypatch, rows) -> None:
    monkeypatch.setattr(store_mod, "load_futures_daily",
                        lambda symbol=None, limit=1000: rows)


def _patch_cal(monkeypatch, n: int) -> None:
    """让 trading_day_lag 走「日历可用」路径并返回固定交易日数 n。"""
    import lib.trade_cal as tc

    monkeypatch.setattr(tc, "fetch_trade_cal", lambda a, b: ([f"d{i}" for i in range(n)], False))


def _raise_cal(monkeypatch) -> None:
    """日历不可用 → 降级自然日（degraded=True）。"""
    import lib.trade_cal as tc

    def _boom(a, b):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(tc, "fetch_trade_cal", _boom)


def _patch_cal_natural_days(monkeypatch) -> None:
    """模拟「每天都是交易日」的日历：lag = 自然日差（与 _patch_cal 的定值不同，
    用于区分「行内相对判定」与「当日判定」两种 session 语义）。"""
    import lib.trade_cal as tc

    def _fake(a, b):
        d0 = datetime.strptime(str(a), "%Y%m%d")
        d1 = datetime.strptime(str(b), "%Y%m%d")
        return [f"d{i}" for i in range((d1 - d0).days)], False

    monkeypatch.setattr(tc, "fetch_trade_cal", _fake)


def _seed_snapshot(date: str, pct, basis_date, env_label: dict | None = None) -> None:
    """向隔离库直插一行快照（绕过采集，构造读取侧输入）。"""
    c = store_mod._conn()
    try:
        c.execute(
            "INSERT INTO market_snapshots "
            "(date, ad_ratio, futures_basis_pct, futures_basis_date, env_label) "
            "VALUES (?, 1.0, ?, ?, ?)",
            (date, pct, basis_date,
             json.dumps(env_label, ensure_ascii=False) if env_label is not None else None),
        )
        c.commit()
    finally:
        store_mod._safe_close(c)


@pytest.fixture
def frozen_session(monkeypatch):
    monkeypatch.setattr(mm, "shanghai_session_date", lambda: SESSION)
    return SESSION


class TestFetchFutures:
    def test_fresh_basis_sets_pair(self, frozen_session, monkeypatch):
        _patch_rows(monkeypatch, _rows_ending(SESSION))
        _patch_cal(monkeypatch, 0)  # asof == session，不查日历
        r = _fake_result()
        mm._fetch_futures(r)
        assert r["futures_basis_pct"] == pytest.approx(-0.5072 + 20 * 0.0001)
        assert r["futures_basis_date"] == SESSION
        assert "futures_basis_note" not in r
        assert r["_errors"] == []

    def test_stale_basis_refused_with_note(self, frozen_session, monkeypatch):
        _patch_rows(monkeypatch, _rows_ending("20260701"))
        _patch_cal(monkeypatch, 40)
        r = _fake_result()
        mm._fetch_futures(r)
        assert "futures_basis_pct" not in r and "futures_basis_date" not in r
        note = r["futures_basis_note"]
        assert "滞后 40 个交易日" in note and "2026-07-01" in note
        assert any("滞后" in e for e in r["_errors"])

    def test_unparseable_date_refused(self, frozen_session, monkeypatch):
        rows = _rows_ending(SESSION)
        rows[-1]["date"] = "not-a-date"
        _patch_rows(monkeypatch, rows)
        r = _fake_result()
        mm._fetch_futures(r)
        assert "futures_basis_pct" not in r
        assert "日期" in r["futures_basis_note"]

    def test_future_date_refused(self, frozen_session, monkeypatch):
        future = (datetime.strptime(SESSION, "%Y%m%d") + timedelta(days=10)).strftime("%Y%m%d")
        _patch_rows(monkeypatch, _rows_ending(future))
        r = _fake_result()
        mm._fetch_futures(r)
        assert "futures_basis_pct" not in r
        assert "未来" in r["futures_basis_note"]

    def test_calendar_degraded_annotated(self, frozen_session, monkeypatch):
        # 自然日 3 天（>0 → 需查日历），日历不可用 → 降级但仍在阈值内
        _patch_rows(monkeypatch, _rows_ending("20260807"))
        _raise_cal(monkeypatch)
        r = _fake_result()
        mm._fetch_futures(r)
        assert r["futures_basis_date"] == "20260807"
        assert "日历不可用" in r.get("futures_basis_note", "")

    def test_no_rows_records_error(self, frozen_session, monkeypatch):
        _patch_rows(monkeypatch, [])
        r = _fake_result()
        mm._fetch_futures(r)
        assert any("无数据" in e for e in r["_errors"])


class TestBasisIsCurrent:
    def test_fresh(self):
        ok, reason = mm.basis_is_current(
            {"futures_basis_pct": -0.51, "futures_basis_date": SESSION}, SESSION)
        assert ok is True and reason == ""

    def test_stale(self, monkeypatch):
        _patch_cal(monkeypatch, 40)
        ok, reason = mm.basis_is_current(
            {"futures_basis_pct": -0.51, "futures_basis_date": "20260701"}, SESSION)
        assert ok is False and "滞后" in reason

    def test_legacy_row_without_date(self):
        """验收：历史行缺字段日期 → 不可引用（禁用不明日期值）。"""
        ok, reason = mm.basis_is_current({"futures_basis_pct": -0.5072}, SESSION)
        assert ok is False and "缺数据日期" in reason

    def test_future_date(self):
        ok, reason = mm.basis_is_current(
            {"futures_basis_pct": -0.51, "futures_basis_date": "20260901"}, SESSION)
        assert ok is False and "未来" in reason

    def test_no_value(self):
        ok, reason = mm.basis_is_current({"futures_basis_date": SESSION}, SESSION)
        assert ok is False and reason == "无基差读数"


class TestPersistPaths:
    def test_fresh_pair_persisted(self, frozen_session):
        snap = {"date": SESSION, "ad_ratio": 1.0,
                "futures_basis_pct": -0.5072, "futures_basis_date": SESSION}
        mm._auto_persist(dict(snap))
        c = store_mod._conn()
        try:
            row = c.execute(
                "SELECT futures_basis_pct, futures_basis_date FROM market_snapshots WHERE date=?",
                (SESSION,),
            ).fetchone()
        finally:
            store_mod._safe_close(c)
        assert row[0] == pytest.approx(-0.5072) and row[1] == SESSION

    def test_same_day_stale_recollection_keeps_pair_and_read_side_flags(self, frozen_session, monkeypatch):
        """验收：同日已有陈旧值后再次采集——不刷新日期，读取侧判不可引用。"""
        snap_a = {"date": SESSION, "ad_ratio": 1.0,
                  "futures_basis_pct": -0.5072, "futures_basis_date": "20260701"}
        mm._auto_persist(dict(snap_a))

        _patch_rows(monkeypatch, _rows_ending("20260701"))
        _patch_cal(monkeypatch, 40)
        snap_b = {"date": SESSION, "ad_ratio": 1.1, "_errors": []}
        mm._fetch_futures(snap_b)
        assert "futures_basis_pct" not in snap_b  # 实时路径拒写
        mm._auto_persist(snap_b)

        c = store_mod._conn()
        try:
            row = c.execute(
                "SELECT futures_basis_pct, futures_basis_date FROM market_snapshots WHERE date=?",
                (SESSION,),
            ).fetchone()
        finally:
            store_mod._safe_close(c)
        assert row[0] == pytest.approx(-0.5072)   # COALESCE 保留成对旧值
        assert row[1] == "20260701"               # 日期未被刷新成当日
        # 读取侧（真实入口，非手动调 helper）：旧值不得作为当期读数返回/展示
        latest = mm.latest_snapshot()
        assert latest["futures_basis_pct"] is None
        assert "滞后" in latest["futures_basis_note"]
        hist_row = [h for h in mm.load_history(10) if h["date"] == SESSION][0]
        assert hist_row["futures_basis_pct"] is None
        assert "滞后" in hist_row["futures_basis_note"]

    def test_schema_and_columns_include_basis_date(self):
        assert "futures_basis_date" in mm._MARKET_SNAPSHOT_COLUMNS
        c = store_mod._conn()
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(market_snapshots)")}
        finally:
            store_mod._safe_close(c)
        assert "futures_basis_date" in cols


class TestReadPaths:
    """真实读取函数（latest_snapshot / load_history）——复检 P1「读取过滤接线」回归。

    此前用例只手动调 `basis_is_current`；这些用例走真实入口断言最终输出。
    """

    def test_latest_snapshot_gates_undated_row_and_strips_label_clause(self, frozen_session):
        """缺数据日期（生产库 18 行形态）→ 当期视图禁用；env_label 文本同边界。"""
        _seed_snapshot(SESSION, -0.5072, None,
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%", "summary": "正常"})
        snap = mm.latest_snapshot()
        assert snap["futures_basis_pct"] is None
        assert snap["futures_basis_date"] is None
        assert "缺数据日期" in snap["futures_basis_note"]
        # 值过滤与标签文本同一边界：旧标签中的基差子句不得残留（防止「字段 None
        # 但标签仍引用」的自相矛盾输出，复检最小补齐范围 #2）
        cap = json.loads(snap["env_label"])["capital_flow"]
        assert "IC 基差" not in cap
        assert "北向数据暂不可用" in cap
        assert json.loads(snap["env_label"])["summary"] == "正常"  # 其它键不动

    def test_latest_snapshot_gates_stale_and_keeps_fresh_pair(self, frozen_session, monkeypatch):
        _patch_cal(monkeypatch, 40)
        _seed_snapshot("20260809", -0.5072, "20260701",
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%（截至 2026-07-01）"})
        snap = mm.latest_snapshot()
        assert snap["futures_basis_pct"] is None
        assert "滞后" in snap["futures_basis_note"]
        assert "IC 基差" not in json.loads(snap["env_label"])["capital_flow"]

        _seed_snapshot(SESSION, -0.6050, SESSION)  # 新鲜成对值正例
        snap2 = mm.latest_snapshot()
        assert snap2["futures_basis_pct"] == pytest.approx(-0.6050)
        assert snap2["futures_basis_date"] == SESSION
        assert "futures_basis_note" not in snap2

    def test_latest_snapshot_vs_load_history_session_semantics(self, frozen_session, monkeypatch):
        """「当时新鲜、现已久远」的行：load_history（历史序列，行内相对判定）保留其
        历史读数与 as-of；latest_snapshot（当期视图，当日判定）必须禁用。"""
        _patch_cal_natural_days(monkeypatch)
        _seed_snapshot("20260710", -0.5072, "20260709",
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%（截至 2026-07-09）"})
        h = mm.load_history(10)[0]
        assert h["futures_basis_pct"] == pytest.approx(-0.5072)
        assert h["futures_basis_date"] == "20260709"
        assert "IC 基差" in json.loads(h["env_label"])["capital_flow"]  # 历史读数带 as-of，可审计

        latest = mm.latest_snapshot()
        assert latest["futures_basis_pct"] is None
        assert "滞后" in latest["futures_basis_note"]
        assert "IC 基差" not in json.loads(latest["env_label"])["capital_flow"]

    def test_load_history_gates_undated_and_stale_rows(self, frozen_session, monkeypatch):
        _patch_cal(monkeypatch, 40)
        _seed_snapshot("20260809", -0.5072, None,
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%"})
        _seed_snapshot("20260810", -0.5072, "20260701")
        hist = {h["date"]: h for h in mm.load_history(10)}
        assert hist["20260809"]["futures_basis_pct"] is None
        assert "缺数据日期" in hist["20260809"]["futures_basis_note"]
        assert "IC 基差" not in json.loads(hist["20260809"]["env_label"])["capital_flow"]
        assert hist["20260810"]["futures_basis_pct"] is None
        assert "滞后" in hist["20260810"]["futures_basis_note"]

    def test_null_value_residual_label_clause_stripped(self, frozen_session):
        """P2 边界（2026-10-02 整改后复验发现）：值列为 NULL 而旧标签残留基差
        子句（如未来数据清理只置值不清标签）→ 读取输出（含 JSON 出口）不得
        携带该子句；值本就为空 → 不新增 note（无被拒读数，避免噪声）。"""
        _seed_snapshot("20260809", None, None,
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%", "summary": "正常"})
        snap = mm.latest_snapshot()
        assert snap["futures_basis_pct"] is None
        assert "futures_basis_note" not in snap
        cap = json.loads(snap["env_label"])["capital_flow"]
        assert "IC 基差" not in cap
        assert "北向数据暂不可用" in cap          # 北向段保留
        assert json.loads(snap["env_label"])["summary"] == "正常"  # 其它键不动
        h = mm.load_history(10)[0]
        assert "IC 基差" not in json.loads(h["env_label"])["capital_flow"]

    def test_orphan_basis_date_without_value_cleared(self, frozen_session):
        """半写形态：只有数据日期没有值 → 同样按不可引用语义处理——残日期
        不得作为成对读数的一部分返回，标签文本同边界（复验记录要求）。"""
        _seed_snapshot("20260809", None, "20260701",
                       {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%（截至 2026-07-01）"})
        snap = mm.latest_snapshot()
        assert snap["futures_basis_pct"] is None
        assert snap["futures_basis_date"] is None
        assert "IC 基差" not in json.loads(snap["env_label"])["capital_flow"]
        h = mm.load_history(10)[0]
        assert h["futures_basis_date"] is None
        assert "IC 基差" not in json.loads(h["env_label"])["capital_flow"]


class TestLabelsGated:
    """两套标签函数对陈旧/无日期读数执行同一日期规则（复检最小补齐范围 #2）。"""

    def test_v1_undated_and_stale_no_clause(self, frozen_session, monkeypatch):
        _patch_cal(monkeypatch, 40)
        r = {"futures_basis_pct": -0.5072, "futures_basis_date": None, "date": SESSION}
        mm._compute_labels(r)
        assert "IC 基差" not in (r.get("label_capital_flow") or "")
        r2 = {"futures_basis_pct": -0.5072, "futures_basis_date": "20260701", "date": SESSION}
        mm._compute_labels(r2)
        assert "IC 基差" not in (r2.get("label_capital_flow") or "")

    def test_v1_fresh_pair_has_asof(self, frozen_session):
        r = {"futures_basis_pct": -0.5072, "futures_basis_date": SESSION, "date": SESSION}
        mm._compute_labels(r)
        assert "IC 基差 -0.51%（截至 2026-08-10）" in r["label_capital_flow"]

    def test_v2_undated_and_stale_no_clause(self, frozen_session, monkeypatch):
        _patch_cal(monkeypatch, 40)
        snap = {"date": SESSION, "futures_basis_pct": -0.5072, "futures_basis_date": None}
        mm._compute_labels_v2(snap, [])
        assert "IC 基差" not in (snap.get("label_capital_flow") or "")
        snap2 = {"date": SESSION, "futures_basis_pct": -0.5072, "futures_basis_date": "20260701"}
        mm._compute_labels_v2(snap2, [])
        assert "北向数据暂不可用" in snap2["label_capital_flow"]
        assert "IC 基差" not in snap2["label_capital_flow"]

    def test_v2_fresh_pair_has_asof(self, frozen_session):
        snap = {"date": SESSION, "futures_basis_pct": -0.5072, "futures_basis_date": SESSION}
        mm._compute_labels_v2(snap, [])
        assert "IC 基差 -0.51%（截至 2026-08-10）" in snap["label_capital_flow"]


class TestLegacyMigration:
    """旧 schema 幂等迁移（复检最小补齐范围 #4）：老库（无 futures_basis_date）
    迁移后旧记录保留、重复迁移幂等、旧行读取侧自动不可引用。"""

    def _make_legacy_db(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy_research.db"
        monkeypatch.setattr(store_mod, "_db_override", legacy)
        c = store_mod.connect_db(legacy)
        try:
            c.executescript(
                "CREATE TABLE market_snapshots ("
                "date TEXT PRIMARY KEY, ad_ratio REAL, "
                "futures_basis_pct REAL, env_label TEXT);"
            )
            c.execute(
                "INSERT INTO market_snapshots (date, ad_ratio, futures_basis_pct, env_label) "
                "VALUES (?, 1.0, -0.5072, ?)",
                ("20260810", json.dumps(
                    {"capital_flow": "北向数据暂不可用；IC 基差 -0.51%"}, ensure_ascii=False)),
            )
            c.commit()
        finally:
            store_mod.safe_close(c)
        return legacy

    def test_migration_idempotent_and_legacy_row_gated(self, tmp_path, monkeypatch, frozen_session):
        legacy = self._make_legacy_db(tmp_path, monkeypatch)

        store_mod.init_db()  # ① 迁移（补列）
        store_mod.init_db()  # ② 重复迁移：幂等不抛

        c = store_mod.connect_db(legacy)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(market_snapshots)")}
            assert "futures_basis_date" in cols
            rows = c.execute(
                "SELECT date, futures_basis_pct, futures_basis_date FROM market_snapshots"
            ).fetchall()
        finally:
            store_mod.safe_close(c)
        assert len(rows) == 1  # 旧记录保留
        assert rows[0][0] == "20260810"
        assert rows[0][1] == pytest.approx(-0.5072)  # 旧值未被迁移破坏
        assert rows[0][2] is None                    # 新列对旧记录为空

        # ③ 旧行（无日期）读取侧自动不可引用，值过滤与标签文本同边界
        h = mm.load_history(10)[0]
        assert h["futures_basis_pct"] is None
        assert "缺数据日期" in h["futures_basis_note"]
        assert "IC 基差" not in json.loads(h["env_label"])["capital_flow"]
        latest = mm.latest_snapshot()
        assert latest["futures_basis_pct"] is None
