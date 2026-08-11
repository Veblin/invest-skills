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


# ---------------------------------------------------------------------------
# R12 holdings / R13 peers CLI（D13: mock 打在 mod = etf.py 命名空间）
# ---------------------------------------------------------------------------


def _fake_holdings() -> dict:
    return {
        "symbol": "159206", "report_date": "2026-06-30", "quarter": "2026年2季度",
        "status": "ok",
        "rows": [
            {"code": "688002", "name": "睿创微纳", "pct": 9.07,
             "shares": 1036.01, "amount": 160260.70},
            {"code": "600879", "name": "航天电子", "pct": 8.44,
             "shares": 7004.94, "amount": 149135.26},
        ],
        "top1_pct": 9.07, "top5_sum_pct": 17.51, "top10_sum_pct": 17.51,
        "clusters": [
            {"cluster": "未归类", "sum_pct": 17.51,
             "members": [
                 {"code": "688002", "name": "睿创微纳", "pct": 9.07},
                 {"code": "600879", "name": "航天电子", "pct": 8.44},
             ]},
        ],
        "note": "前十大持仓合计可能 <100%（非前十大未列）；集中度仅覆盖前十大；子环节聚类由引擎按 HOLDINGS_CLUSTER_MAP 聚合，未映射股票归入「未归类」",
        "source": "天天基金(东财 FundArchivesDatas jjcc)",
    }


def test_cli_holdings_invalid_symbol_exit_2():
    main = _load_etf_main()
    assert main(["holdings", "abc"]) == 2


def test_cmd_holdings_json_contains_concentration(monkeypatch, capsys):
    mod = _load_etf_module()
    monkeypatch.setattr(mod, "query_etf_holdings", lambda s: _fake_holdings())
    assert mod.cmd_holdings("159206", as_json=True) == 0
    out = capsys.readouterr().out
    assert '"top5_sum_pct": 17.51' in out
    assert '"symbol": "159206"' in out
    assert '"clusters"' in out
    assert '"未归类"' in out


def test_cmd_holdings_human_readable_has_ai_hint(monkeypatch, capsys):
    mod = _load_etf_module()
    monkeypatch.setattr(mod, "query_etf_holdings", lambda s: _fake_holdings())
    assert mod.cmd_holdings("159206", as_json=False) == 0
    out = capsys.readouterr().out
    assert "集中度(引擎): top1 9.07%" in out
    assert "子环节聚类合计(引擎):" in out
    assert "未归类 17.51%" in out
    assert "AI 归类" in out  # 尾部提示仍含补充归类须标注「AI 归类」
    assert "睿创微纳" in out


def test_cmd_holdings_no_hint_when_all_mapped(monkeypatch, capsys):
    """全部持仓已映射（无「未归类」）→ 不打印补充归类提示（避免误导重复「AI 归类」）。"""
    mod = _load_etf_module()
    data = _fake_holdings()
    data["clusters"] = [{"cluster": "光模块/光器件", "sum_pct": 100.0,
                         "members": data["clusters"][0]["members"]}]
    monkeypatch.setattr(mod, "query_etf_holdings", lambda s: data)
    assert mod.cmd_holdings("159206", as_json=False) == 0
    out = capsys.readouterr().out
    assert "AI 归类" not in out
    assert "光模块/光器件 100.00%" in out


def test_cmd_holdings_missing_prints_note(monkeypatch, capsys):
    mod = _load_etf_module()
    monkeypatch.setattr(
        mod, "query_etf_holdings",
        lambda s: {"symbol": s, "status": "missing", "note": "持仓数据不可用",
                   "report_date": None, "quarter": None, "rows": []},
    )
    assert mod.cmd_holdings("159206", as_json=False) == 0
    assert "持仓数据不可用" in capsys.readouterr().out


def test_cmd_peers_unmapped_hints_explicit(monkeypatch, capsys):
    mod = _load_etf_module()
    monkeypatch.setattr(
        "etf_peers.query_etf_peers",
        lambda s, peers: {
            "symbol": s, "available": False, "peers": [],
            "peer_source": "etf_to_sw_industry",
            "note": "未映射申万行业（ETF_TO_SW_INDUSTRY 无此代码），请用 --peers 显式指定",
            "flow": None, "rs": None, "names": {}, "notes": [],
        },
    )
    assert mod.cmd_peers("512345", as_json=False, peers_str=None) == 0
    assert "--peers" in capsys.readouterr().out


def test_cmd_peers_table_and_rs(monkeypatch, capsys):
    mod = _load_etf_module()
    fake = {
        "symbol": "159206", "available": True, "peers": ["512660"],
        "peer_source": "etf_to_sw_industry:801740 国防军工",
        "flow": {"window_days": 20, "rows": [
            {"symbol": "159206", "flow_20d_e": -31.45, "flow_5d_e": 0.99,
             "share_change_pct": -16.93, "trend": "🔴 持续净流出", "note": None},
        ]},
        "rs": {"rs_latest": 94.27, "rs_window_start": 100.96,
               "rs_change": -6.69,
               "rank_20d": {"rank": 3, "total": 3}},
        "names": {"159206": "卫星ETF永赢"}, "notes": [],
    }
    monkeypatch.setattr("etf_peers.query_etf_peers", lambda s, peers: fake)
    assert mod.cmd_peers("159206", as_json=False, peers_str=None) == 0
    out = capsys.readouterr().out
    assert "20日流" in out
    assert "rs_latest 94.27" in out
    assert "rs_window_start 100.96" in out
    assert "20日收益排名 3/3" in out


def test_cmd_peers_json_contains_flow(monkeypatch, capsys):
    mod = _load_etf_module()
    fake = {
        "symbol": "159206", "available": True, "peers": ["512660"],
        "peer_source": "explicit", "flow": {"window_days": 20, "rows": []},
        "rs": None, "names": {}, "notes": [],
    }
    monkeypatch.setattr("etf_peers.query_etf_peers", lambda s, peers: fake)
    assert mod.cmd_peers("159206", as_json=True, peers_str="512660") == 0
    assert '"peer_source": "explicit"' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# R15 sector-flow / collect-sector-flow CLI（D13: mock 打定义模块）
# ---------------------------------------------------------------------------


def test_cmd_sector_flow_json(monkeypatch, capsys):
    mod = _load_etf_module()
    fake = {
        "symbol": "159206", "sw_code": "801740", "sw_name": "国防军工",
        "available": True, "as_of": "20260811", "history_days": 1,
        "industries": [
            {"industry": "军工电子", "net_1d": -2.91, "net_3d": -3.31,
             "net_5d": 6.26, "net_10d": -20.86, "chg_10d": 8.78,
             "trend_label": "持续净流出", "trend_detail": "近端减速",
             "trend_5d": None, "turn_5d": None},
        ],
        "notes": ["序列积累中（1 日 < 6 日）"],
    }
    monkeypatch.setattr("sector_flow.query_sector_flow", lambda s: fake)
    assert mod.cmd_sector_flow("159206", as_json=True) == 0
    assert '"trend_label": "持续净流出"' in capsys.readouterr().out


def test_cmd_sector_flow_human_and_unmapped(monkeypatch, capsys):
    mod = _load_etf_module()
    fake = {
        "symbol": "159206", "sw_code": "801740", "sw_name": "国防军工",
        "available": True, "as_of": "20260811", "history_days": 1,
        "industries": [
            {"industry": "军工电子", "net_1d": -2.91, "net_3d": -3.31,
             "net_5d": 6.26, "net_10d": -20.86, "chg_10d": 8.78,
             "trend_label": "持续净流出", "trend_detail": "近端减速（日均强度 r=-0.44）",
             "trend_5d": None, "turn_5d": None},
        ],
        "notes": [],
    }
    monkeypatch.setattr("sector_flow.query_sector_flow", lambda s: fake)
    assert mod.cmd_sector_flow("159206", as_json=False) == 0
    out = capsys.readouterr().out
    assert "军工电子" in out
    assert "持续净流出" in out

    unmapped = {
        "symbol": "510300", "sw_code": None, "sw_name": None,
        "available": False, "as_of": None, "industries": [],
        "history_days": 0, "notes": ["未映射申万行业（ETF_TO_SW_INDUSTRY 无此代码）"],
    }
    monkeypatch.setattr("sector_flow.query_sector_flow", lambda s: unmapped)
    assert mod.cmd_sector_flow("510300", as_json=False) == 0
    assert "未映射" in capsys.readouterr().out


def test_cmd_collect_sector_flow_paths(monkeypatch, capsys):
    mod = _load_etf_module()

    def fake_fetch():
        return {"date": "20260811", "available": True,
                "industries": {"半导体": {}}, "errors": []}

    monkeypatch.setattr("sector_flow.fetch_sector_flow_snapshot", fake_fetch)
    monkeypatch.setattr("sector_flow.check_mapping_coverage", lambda snap: [])

    def fake_save(snapshot):
        assert snapshot["available"] is True
        return {"date": "20260811", "rows_saved": 358, "skipped": False,
                "error": None, "note": None}

    monkeypatch.setattr("sector_flow.save_sector_flow_snapshot", fake_save)
    assert mod.cmd_collect_sector_flow() == 0
    assert "358 行" in capsys.readouterr().out

    def fake_skip(snapshot):
        return {"date": "20260812", "rows_saved": 0, "skipped": True,
                "error": None, "note": "数据与 20260811 全等，疑似非交易日/无变化，跳过写入"}

    monkeypatch.setattr("sector_flow.save_sector_flow_snapshot", fake_skip)
    assert mod.cmd_collect_sector_flow() == 0
    assert "跳过" in capsys.readouterr().out

    def fake_err(snapshot):
        return {"date": "20260812", "rows_saved": 0, "skipped": False,
                "error": "同花顺行业资金流不可用: boom", "note": None}

    monkeypatch.setattr("sector_flow.save_sector_flow_snapshot", fake_err)
    assert mod.cmd_collect_sector_flow() == 1
    assert "采集失败" in capsys.readouterr().out

    # 映射自检路径：check_mapping_coverage 发现缺失行业 → 警告输出
    monkeypatch.setattr("sector_flow.check_mapping_coverage", lambda snap: ["银行"])
    monkeypatch.setattr("sector_flow.save_sector_flow_snapshot", fake_save)
    assert mod.cmd_collect_sector_flow() == 0
    assert "映射自检" in capsys.readouterr().out
