"""fetch_etf_index_pe 取行缺陷回归（2026-08-22 发现）。

csindex（akshare stock_zh_index_value_csindex）返回新日期在前，
原 `df.iloc[-1]` 取到的是最早行 → index_pe 滞后约 3.5 周（588000 实测
103.37 实为 7/27 行，最新 8/21 应为 93.37），index_pe_pct 分位随之反转。

修复：按「日期」列显式升序后取末行，不依赖返回顺序。

无网络：monkeypatch sys.modules['akshare'] + akshare_direct_session。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import etf_data  # noqa: E402
from etf_data import fetch_etf_index_pe  # noqa: E402


def _csindex_df(rows_desc: list[tuple[str, float]]) -> pd.DataFrame:
    """构造与 akshare 返回同构的 DataFrame（新日期在前）。"""
    return pd.DataFrame([
        {
            "日期": datetime.date.fromisoformat(d),
            "指数代码": 688,
            "指数中文简称": "科创50",
            "市盈率1": pe,
            "市盈率2": pe + 2.0,
            "股息率1": 0.26,
            "股息率2": 0.24,
        }
        for d, pe in rows_desc
    ])


class _FakeAk:
    """fake akshare：stock_zh_index_value_csindex 可注入任意顺序。"""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def stock_zh_index_value_csindex(self, symbol):
        return self._df.copy()


@pytest.fixture(autouse=True)
def _patch_akshare(monkeypatch):
    monkeypatch.setattr(etf_data, "akshare_direct_session", __import__("contextlib").nullcontext)


def test_index_pe_takes_latest_row_newest_first(monkeypatch):
    """新日期在前（akshare 实际返回顺序）→ 取最新行 8/21 93.37，而非最早行 7/27 103.37。"""
    rows = [
        ("2026-08-21", 93.37),
        ("2026-08-20", 93.11),
        ("2026-08-19", 94.33),
        ("2026-07-27", 103.37),
    ]
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(_csindex_df(rows)))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "ok"
    assert out["index_pe"] == 93.37


def test_index_pe_latest_row_oldest_first(monkeypatch):
    """旧日期在前（防御顺序反转）→ 仍取最新行 93.37，不依赖返回顺序。"""
    rows = [
        ("2026-07-27", 103.37),
        ("2026-08-19", 94.33),
        ("2026-08-20", 93.11),
        ("2026-08-21", 93.37),
    ]
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(_csindex_df(rows)))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "ok"
    assert out["index_pe"] == 93.37


def test_index_pe_pe1_missing_falls_back_pe2(monkeypatch):
    """市盈率1 缺失（NaN）→ 回退市盈率2；取行逻辑不受影响。"""
    df = _csindex_df([("2026-08-21", 93.37), ("2026-07-27", 103.37)])
    df.loc[0, "市盈率1"] = float("nan")
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(df))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "ok"
    assert out["index_pe"] == 95.37  # 市盈率2 = pe + 2.0


def test_index_pe_empty_df_returns_missing(monkeypatch):
    """空 DataFrame → status=missing（原有语义保持）。"""
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(pd.DataFrame()))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "missing"
    assert out["index_pe"] is None


def test_index_pe_nan_date_row_dropped(monkeypatch):
    """NaN 日期行先剔除：若残留，pandas 升序 NaN 置末 → iloc[-1] 取到无日期行。"""
    df = _csindex_df([("2026-08-21", 93.37), ("2026-07-27", 103.37)])
    df.loc[len(df)] = {
        "日期": float("nan"),
        "指数代码": 688,
        "指数中文简称": "科创50",
        "市盈率1": 999.0,
        "市盈率2": 999.0,
        "股息率1": 0.0,
        "股息率2": 0.0,
    }
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(df))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "ok"
    assert out["index_pe"] == 93.37


def test_index_pe_all_nan_dates_returns_missing(monkeypatch):
    """日期全 NaN（dropna 后为空）→ status=missing，不落 IndexError。"""
    df = _csindex_df([("2026-08-21", 93.37)])
    df["日期"] = float("nan")
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(df))
    out = fetch_etf_index_pe("000688")
    assert out["status"] == "missing"
    assert out["index_pe"] is None


def test_index_pe_missing_date_col_warns(monkeypatch, caplog):
    """「日期」列缺失（列名漂移）→ 记录警告 fail-loud，不复现静默滞后。

    无日期列时无法排序，沿用原始行序取末行（本场景即最早行 103.37，
    滞后行为保留）——关键是不再静默：警告明确提示取行偏移风险。
    """
    df = _csindex_df([("2026-08-21", 93.37), ("2026-07-27", 103.37)])
    df = df.drop(columns=["日期"])
    monkeypatch.setitem(sys.modules, "akshare", _FakeAk(df))
    with caplog.at_level("WARNING", logger="etf_data"):
        out = fetch_etf_index_pe("000688")
    assert out["status"] == "ok"
    assert out["index_pe"] == 103.37  # 原始行序（新在前）末行 = 最早行
    assert any("列名漂移" in rec.message for rec in caplog.records)


def test_index_pe_404_logs_debug(monkeypatch, caplog):
    """csindex 404 → missing 信封 + 仅 debug 级日志（v0.2.7 P2-4 降噪）。

    404 属确定性资源缺失（指数代码无 csindex PE 文件），且 missing 信封
    不缓存 → 每次报告重复打印；改 debug 级静默降级，调用方凭 status 判断。
    """
    import sys
    import urllib.error

    class _FakeAk404:
        def stock_zh_index_value_csindex(self, symbol):
            raise urllib.error.HTTPError(symbol, 404, "Not Found", None, None)

    monkeypatch.setitem(sys.modules, "akshare", _FakeAk404())
    with caplog.at_level("DEBUG", logger="etf_data"):
        out = fetch_etf_index_pe("399006")
    assert out["status"] == "missing"
    assert "404" in out["error"]
    # 无 WARNING/ERROR 级 csindex 记录（仅 debug）
    assert not any(
        rec.levelno >= 30 and "csindex" in rec.getMessage()
        for rec in caplog.records)


# ---------- R2/T9-3：PE 口径须显式（不得静默替换后仍标 PE(1)）----------


class TestIndexPeCaliberDisclosure:
    """根因：`pe = pe1 if pe1 is not None else pe2` 在最新行「市盈率1」为 NaN 时
    **静默落回流通加权（市盈率2）**，而徽章恒标「PE(1) 股本加权口径」→ 误标。
    累积表 `index_pe_history.pe` 只存市盈率1，两处由此表现不一致（实测 58.49 vs 54.92）。

    修复取任务允许的「显式双字段标注」口径：暴露两个加权口径 + 标明实际取值口径。
    """

    @staticmethod
    def _patch(monkeypatch, df: pd.DataFrame):
        from contextlib import nullcontext

        class _FakeAk:
            @staticmethod
            def stock_zh_index_value_csindex(symbol):
                return df

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk())
        monkeypatch.setattr(etf_data, "akshare_direct_session", lambda: nullcontext())

    def test_fallback_to_circulating_is_disclosed(self, monkeypatch):
        """最新行市盈率1 缺失 → 取值回落市盈率2，须显式标注为流通加权。"""
        self._patch(monkeypatch, pd.DataFrame([
            {"日期": "2026-09-04", "市盈率1": 58.49, "市盈率2": 54.92},
            {"日期": "2026-09-07", "市盈率1": float("nan"), "市盈率2": 54.92},
        ]))
        r = fetch_etf_index_pe("515050")
        assert r["index_pe"] == pytest.approx(54.92)
        assert r["index_pe_caliber"] == "流通加权", "回退到市盈率2 却未标注实际口径"
        assert r["index_pe_circulating"] == pytest.approx(54.92), "双字段须显式暴露"

    def test_primary_caliber_is_disclosed(self, monkeypatch):
        self._patch(monkeypatch, pd.DataFrame([
            {"日期": "2026-09-07", "市盈率1": 58.49, "市盈率2": 54.92},
        ]))
        r = fetch_etf_index_pe("515050")
        assert r["index_pe"] == pytest.approx(58.49)
        assert r["index_pe_caliber"] == "股本加权"
        assert r["index_pe_circulating"] == pytest.approx(54.92)

    def test_missing_status_carries_none_caliber(self, monkeypatch):
        """不可得路径口径键位须在（否则消费者 .get 之外的访问会 KeyError）。"""
        self._patch(monkeypatch, pd.DataFrame())
        r = fetch_etf_index_pe("515050")
        assert r["status"] == "missing"
        assert r["index_pe_caliber"] is None

    def test_note_short_labels_actual_caliber(self):
        """徽章须按**实际**口径出字，不得恒标 PE(1)。"""
        from etf_html import index_pe_note_short

        assert index_pe_note_short(
            {"index_pe": 54.92, "index_pe_caliber": "流通加权"}) == "PE(2) 流通加权口径"
        assert index_pe_note_short(
            {"index_pe": 58.49, "index_pe_caliber": "股本加权"}) == "PE(1) 股本加权口径"
        # 口径未知时不得臆断为 PE(1)
        assert "PE(1)" not in index_pe_note_short({"index_pe": 58.49})
        assert index_pe_note_short({"index_pe": None}) == "指数 PE 不可得"

    def test_profile_propagates_caliber_from_envelope(self, monkeypatch):
        """口径须随信封一起进 profile——只传 index_pe 会让徽章拿到 None。"""
        env = {"status": "ok", "index_pe": 54.92, "index_pe_caliber": "流通加权",
               "index_pe_note": "n", "rows": [{"日期": "2026-09-07"}]}
        monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: env)
        monkeypatch.setattr(etf_data, "_index_pe_percentile_from_db",
                            lambda *a, **k: None)
        result: dict = {"_errors": []}
        etf_data._fetch_csindex_pe(result, "515050")
        assert result.get("index_pe_caliber") == "流通加权", "口径未透传（徽章将失据）"


class TestIndexPePercentileCaliber:
    """分位必须用**同口径**的历史序列（R0~R2 review：T9-3 只标了值，分位仍混口径）。

    `index_pe_history` 同时存 `pe`（市盈率1/股本加权）与 `pe_circulating`
    （市盈率2/流通加权），而分位实现写死读 `pe` —— 于是「PE(2) 流通加权口径」
    的徽章旁边，分位却把该值排进了股本加权历史（本 diff 注释自述约 6.5% 口径差）。
    """

    @staticmethod
    def _rows(n=24):
        return [{"date": f"2026-08-{d:02d}", "pe": 100.0, "pe_circulating": 50.0}
                for d in range(1, n + 1)]

    def test_percentile_column_selectable(self, monkeypatch):
        import index_pe_snapshot as snap

        monkeypatch.setattr(snap, "get_index_pe_history", lambda code: self._rows())
        # 当前值 50：对 pe 序列（恒 100）是 0% 分位；对 pe_circulating（恒 50）是 100%
        assert etf_data._index_pe_percentile_from_db(
            "515050", 50.0, None, column="pe") == 0.0
        assert etf_data._index_pe_percentile_from_db(
            "515050", 50.0, None, column="pe_circulating") == 100.0

    def test_caliber_drives_percentile_column(self, monkeypatch):
        """回落到流通加权时，分位须请求 `pe_circulating` 列。"""
        seen: dict = {}
        env = {"status": "ok", "index_pe": 54.92, "index_pe_caliber": "流通加权",
               "index_pe_note": "n", "rows": [{"日期": "2026-09-07"}]}
        monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: env)

        def fake_pct(idx, cur, date, *, column="pe"):
            seen["column"] = column
            return None

        monkeypatch.setattr(etf_data, "_index_pe_percentile_from_db", fake_pct)
        etf_data._fetch_csindex_pe({"_errors": []}, "515050")
        assert seen.get("column") == "pe_circulating", \
            f"分位口径与徽章口径不一致: {seen}"

    def test_primary_caliber_uses_pe_column(self, monkeypatch):
        seen: dict = {}
        env = {"status": "ok", "index_pe": 58.49, "index_pe_caliber": "股本加权",
               "index_pe_note": "n", "rows": [{"日期": "2026-09-07"}]}
        monkeypatch.setattr(etf_data, "_bridge_get", lambda *a, **k: env)

        def fake_pct(idx, cur, date, *, column="pe"):
            seen["column"] = column
            return None

        monkeypatch.setattr(etf_data, "_index_pe_percentile_from_db", fake_pct)
        etf_data._fetch_csindex_pe({"_errors": []}, "515050")
        assert seen.get("column") == "pe"
