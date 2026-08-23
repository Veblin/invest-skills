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
