"""unlock_source（共享限售解禁源）回归测试（离线，全 mock）。

R0~R2 review 修复：
- `_parse_unlock_date` 缺 NaT 守卫 → 返回 `pd.NaT`，随后 `lo <= d <= hi` 抛
  `TypeError`。它替换掉的 `shared_dates.parse_date` 有显式守卫（见
  `invest-a-stock/tests/test_catalyst.py::TestParseDate::test_pandas_nat`）。
  后果分两路：池模式 CLI 带 traceback 崩；`catalyst._fetch_restricted_unlock_events`
  被 `except Exception` 吞掉 → 该标的**整个限售解禁事件列表静默消失**。
"""

from __future__ import annotations

import datetime as _dt
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import unlock_source as us  # noqa: E402


# ── 日期解析守卫（对齐 dates.parse_date 的既有契约）─────────────────────

def test_parse_unlock_date_nat_returns_none():
    """NaT 是 datetime 伪子类：`.date()` 返回 NaT **不抛** → 必须显式判空。"""
    assert us._parse_unlock_date(pd.NaT) is None


def test_parse_unlock_date_normal_inputs_still_work():
    assert us._parse_unlock_date("2026-11-05") == _dt.date(2026, 11, 5)
    assert us._parse_unlock_date("20261105") == _dt.date(2026, 11, 5)
    assert us._parse_unlock_date(_dt.datetime(2026, 11, 5, 10, 30)) == _dt.date(2026, 11, 5)
    assert us._parse_unlock_date(_dt.date(2026, 11, 5)) == _dt.date(2026, 11, 5)
    assert us._parse_unlock_date(None) is None
    assert us._parse_unlock_date("") is None
    assert us._parse_unlock_date("bad") is None


# ── 含 NaT 行的源帧不得让整次取数抛错 ───────────────────────────────────

@pytest.fixture
def fake_ak(monkeypatch):
    """假 akshare + 假 proxy 会话（绕开网络与 lib.proxy 的真实依赖）。"""
    fake_proxy = types.ModuleType("lib.proxy")
    fake_proxy.akshare_direct_session = nullcontext
    monkeypatch.setitem(sys.modules, "lib.proxy", fake_proxy)

    def _install(df: pd.DataFrame):
        class _Ak:
            @staticmethod
            def stock_restricted_release_queue_em(symbol):
                return df
        monkeypatch.setitem(sys.modules, "akshare", _Ak())

    return _install


def test_fetch_survives_nat_row_in_frame(fake_ak):
    """一行 NaT 解禁时间只应跳过该行，不得让整次取数崩（那会退化成静默空）。"""
    within = _dt.date.today() + _dt.timedelta(days=30)
    fake_ak(pd.DataFrame([
        {"解禁时间": pd.NaT, "解禁数量": 1e8, "解禁股东数": 1, "限售股类型": "首发"},
        {"解禁时间": within.isoformat(), "解禁数量": 2e8, "解禁股东数": 2,
         "限售股类型": "定增"},
    ]))

    rows, err = us.fetch_symbol_unlocks("600176", lookahead_days=90)
    assert err is None, f"取数不应报错: {err}"
    assert len(rows) == 1, "NaT 行被跳过，正常行照常返回"
    assert rows[0]["qty_yi"] == 2.0


def test_fetch_all_nat_rows_yields_legit_empty(fake_ak):
    """全是 NaT → 合法空结果（error=None），而不是异常。"""
    fake_ak(pd.DataFrame([
        {"解禁时间": pd.NaT, "解禁数量": 1e8, "解禁股东数": 1, "限售股类型": "首发"},
    ]))
    rows, err = us.fetch_symbol_unlocks("600176", lookahead_days=90)
    assert rows == [] and err is None


def test_fetch_missing_required_columns_is_unavailable_not_empty(fake_ak):
    """上游列名漂移时不能把含解禁行的帧渲染为「无解禁记录」。"""
    fake_ak(pd.DataFrame([{"日期": "2026-12-01", "数量": 2e8}]))
    rows, err = us.fetch_symbol_unlocks("600176", lookahead_days=365)
    assert rows == []
    assert err and "字段缺失" in err and "不可将其视为无解禁" in err
