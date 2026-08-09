"""Offline CLI smoke for invest-a-etf (no live network)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from etf_data import _spot_row_to_quote

_ETF_PY = Path(__file__).resolve().parent.parent / "scripts" / "etf.py"


def _load_etf_module():
    """Load etf.py by path so scripts/ is not put on sys.path (avoids lib shadow)."""
    name = "etf_cli_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ETF_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_etf_main():
    return _load_etf_module().main


def test_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_ETF_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "report" in r.stdout


def test_invalid_symbol_exit_2():
    main = _load_etf_main()
    assert main(["report", "abc"]) == 2
    assert main(["report", "12345"]) == 2


def test_diagnose_ok():
    main = _load_etf_main()
    assert main(["diagnose"]) == 0


def test_cmd_report_none_pct_chg_no_crash(monkeypatch, capsys):
    """停牌 ETF 行 pct_chg=None / summary 字段 None → '-' 占位，不崩溃（缺陷 6）。

    修复前 f-string 格式说明符直接格式化 None（{r.get('pct_chg', 0):>+7.2f}
    等）→ TypeError 崩溃；None 须输出占位符 '-'。
    """
    mod = _load_etf_module()
    monkeypatch.setattr(mod, "prefetch_etf_spot", lambda: None)
    monkeypatch.setattr(mod, "query_etf_data", lambda sym: {})
    monkeypatch.setattr(
        mod, "query_etf_quote",
        lambda sym: {"price": None, "status": "停牌", "change_pct": None, "amount": None},
    )
    monkeypatch.setattr(mod, "query_etf_kline", lambda sym: {"status": "ok"})
    monkeypatch.setattr(
        mod, "query_etf_share_history",
        lambda sym, days=20: {
            "available": True,
            "rows": [{
                "date": "20260805", "open": None, "high": None, "low": None,
                "close": None, "pct_chg": None, "amount": None,
                "turnover_rate": None, "share_change": None,
                "flow_est": None, "direction": None,
            }],
            "summary": {"trend": "?", "row_count": 1, "total_flow_est": None,
                        "avg_amount_e": None, "share_total_change": None},
        },
    )
    rc = mod.cmd_report("510300", as_json=False, with_nav=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "      -" in out  # pct_chg None → '-' 占位（列宽对齐 7）
    assert "合计: - 亿" in out  # summary 字段 None → '-'


def test_spot_row_to_quote_maps_fields():
    row = pd.Series(
        {
            "最新价": 1.23,
            "涨跌幅": -0.5,
            "成交量": 1000,
            "成交额": 12300,
            "基金折价率": 0.2,  # EM discount → normalized premium_discount = -0.2
        }
    )
    q = _spot_row_to_quote("510300", row)
    assert q["symbol"] == "510300"
    assert q["price"] == pytest.approx(1.23)
    assert q["change_pct"] == pytest.approx(-0.5)
    assert q["premium_discount"] == pytest.approx(-0.2)
    assert q["status"] == "available"
