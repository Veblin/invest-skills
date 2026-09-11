"""invest-a-event-calendar 纯函数测试（离线，不触网）。"""

import datetime as _dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from unlock_calendar import percentile_rank, process, rank_in_sorted, render_md  # noqa: E402


def test_rank_in_sorted_empty_guard():
    """R1 收尾项：空序列不得 ZeroDivisionError → None。"""
    assert rank_in_sorted([], 1.0) is None
    assert rank_in_sorted([1.0, 2.0], 1.5) == 50.0


def test_window_has_trading_day_helper(monkeypatch):
    """R1 审查 F8：空返回鉴别依赖交易日历；三级降级语义。"""
    import lib.trade_cal as tc

    import unlock_calendar as uc

    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20260910"], False))
    assert uc._window_has_trading_day("20260901", "20260910") is True
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: ([], False))
    assert uc._window_has_trading_day("20260901", "20260910") is False

    def boom(s, e):
        raise RuntimeError("calendar offline")

    monkeypatch.setattr(tc, "fetch_trade_cal", boom)
    assert uc._window_has_trading_day("20260901", "20260910") is None


def test_main_empty_window_no_trading_day_exit0(monkeypatch, capsys):
    """窄窗口/全非交易日空返回 → 不再是误报「数据不可得」（exit 0）。"""
    import akshare as ak

    import unlock_calendar as uc

    monkeypatch.setattr(ak, "stock_restricted_release_summary_em",
                        lambda **kw: pd.DataFrame())
    monkeypatch.setattr(uc, "_window_has_trading_day", lambda s, e: False)
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--days-past", "1", "--no-out"])
    assert uc.main() == 0
    assert "无交易日" in capsys.readouterr().out


def test_main_empty_window_with_trading_day_exit3(monkeypatch, capsys):
    import akshare as ak

    import unlock_calendar as uc

    monkeypatch.setattr(ak, "stock_restricted_release_summary_em",
                        lambda **kw: pd.DataFrame())
    monkeypatch.setattr(uc, "_window_has_trading_day", lambda s, e: True)
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--days-past", "5", "--no-out"])
    assert uc.main() == 3
    assert "不可得" in capsys.readouterr().err


def test_market_mode_uses_akshare_direct_session(monkeypatch, capsys):
    """市场模式须经 akshare_direct_session（东财直连 + ≥0.5s 节流）。

    回归：市场模式直调东财端点、绕过 proxy 会话——池模式同环境可用而市场模式在
    Clash/VPN 下 ProxyError（CLAUDE.md 记载东财 API 需直连），且以无限流方式打东财。
    """
    import contextlib

    import akshare as ak
    import lib.proxy as proxy_mod

    import unlock_calendar as uc

    entered: list[str] = []

    @contextlib.contextmanager
    def fake_session():
        entered.append("entered")
        yield

    monkeypatch.setattr(proxy_mod, "akshare_direct_session", fake_session)

    def boom(**_kw):
        raise RuntimeError("sentinel")

    monkeypatch.setattr(ak, "stock_restricted_release_summary_em", boom)
    monkeypatch.setattr(sys, "argv",
                        ["unlock_calendar.py", "--days-past", "5", "--no-out"])
    assert uc.main() == 3
    assert entered, "市场模式未经 akshare_direct_session"
    assert "不可得" in capsys.readouterr().err


def test_percentile_rank():
    vals = [100.0, 200.0, 300.0, 400.0]
    assert percentile_rank(vals, 100.0) == 0.0
    assert percentile_rank(vals, 250.0) == 50.0
    assert percentile_rank(vals, 500.0) == 100.0
    assert percentile_rank([], 1.0) is None  # R0 F6：空基准无分位（原哨兵 100 造伪「极高压力」）


def _df():
    today = _dt.date(2026, 9, 8)
    rows = []
    # 回看 3 个样本日（低/中/高市值）
    for i, mv in enumerate([1e9, 3e9, 20e9]):
        rows.append({"解禁时间": (today - _dt.timedelta(days=10 * (i + 1))).strftime("%Y-%m-%d"),
                     "当日解禁股票家数": 5, "解禁数量": 1e8, "实际解禁数量": 1e8,
                     "实际解禁市值": mv, "沪深300指数": 4500.0, "沪深300指数涨跌幅": 0.5})
    return pd.DataFrame(rows)


def test_process_future_high_pressure_flag():
    today = _dt.date(2026, 9, 8)
    df = _df()
    big = {"解禁时间": (today + _dt.timedelta(days=3)).strftime("%Y-%m-%d"),
           "当日解禁股票家数": 8, "解禁数量": 2e8, "实际解禁数量": 2e8,
           "实际解禁市值": 30e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": None}
    df = pd.concat([df, pd.DataFrame([big])], ignore_index=True)
    out = process(df, today, past_days=120, future_days=30)
    assert len(out["past"]) == 3
    fut = out["future"]
    assert len(fut) == 1
    r = fut[0]
    assert r["市值亿"] == 300.0                      # 30e9 元 → 300 亿
    assert r["rank"] == 100.0                        # 大于回看 3 样本全部
    assert r["flag"] == "🔴 极高压力"                 # >=90 分位
    assert r["hs300_chg"] is None                    # 未来行无当日表现
    past_last = out["past"][-1]
    assert past_last["hs300_chg"] == 0.5             # 回看行带沪深300表现


def test_process_units_and_past_window_rank_base():
    today = _dt.date(2026, 9, 8)
    out = process(_df(), today, past_days=120, future_days=30)
    assert out["past"][0]["市值亿"] == 200.0         # 最早日 20e9 元 → 200 亿
    assert out["past"][-1]["市值亿"] == 10.0         # 最近日 1e9 元 → 10 亿
    assert out["past"][0]["数量亿股"] == 1.0         # 1e8 股 = 1 亿股
    # 回看样本全部进入分位基准（都在 120 日内）
    assert len(out["hist_billion"]) == 3


def test_process_future_outside_window_excluded():
    today = _dt.date(2026, 9, 8)
    df = _df()
    far = {"解禁时间": (today + _dt.timedelta(days=90)).strftime("%Y-%m-%d"),
           "当日解禁股票家数": 1, "解禁数量": 1e8, "实际解禁数量": 1e8,
           "实际解禁市值": 5e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": None}
    df = pd.concat([df, pd.DataFrame([far])], ignore_index=True)
    out = process(df, today, past_days=120, future_days=30)
    assert len(out["future"]) == 0                   # 90 天外被排除


def test_process_empty_df():
    out = process(pd.DataFrame(), _dt.date(2026, 9, 8), 120, 30)
    assert out["past"] == [] and out["future"] == [] and out["error"] is None
    # R0 F5：空分支须与正常路径同构（render_md 无条件读 past_days，原缺键 → KeyError 崩溃）
    assert out["past_days"] == 120 and out["past_recent"] == []
    md = render_md(out, "20260908", 30)   # 不再 KeyError
    assert "展望窗口内无解禁日" in md


def test_empty_baseline_no_false_pressure():
    """R0 F6：仅未来行、回看零样本 → 不输出分位/压力标注（无基准即无分位）。"""
    today = _dt.date(2026, 9, 8)
    only_future = pd.DataFrame([{
        "解禁时间": (today + _dt.timedelta(days=3)).strftime("%Y-%m-%d"),
        "当日解禁股票家数": 8, "解禁数量": 2e8, "实际解禁数量": 2e8,
        "实际解禁市值": 30e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": None,
    }])
    out = process(only_future, today, past_days=120, future_days=30)
    r = out["future"][0]
    assert r["rank"] is None and r["flag"] == ""      # 不再伪造「100.0% 极高压力」
    md = render_md(out, "20260908", 30)
    assert "分位基准为空" in md and "极高压力" not in md


def test_past_recent_is_calendar_window():
    """R0 F12：「近 30 日回看」按日历窗口过滤（原实现取最后 30 行，语义不符）。"""
    import pandas as _pd

    today = _dt.date(2026, 9, 8)
    rows = []
    for days_ago in (5, 40):    # 一条在 30 日内、一条在外
        rows.append({"解禁时间": (today - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%d"),
                     "当日解禁股票家数": 3, "解禁数量": 1e8, "实际解禁数量": 1e8,
                     "实际解禁市值": 5e9, "沪深300指数": 4500.0, "沪深300指数涨跌幅": 0.1})
    out = process(_pd.DataFrame(rows), today, past_days=120, future_days=30)
    assert len(out["past"]) == 2                 # 全窗口回看仍含两条
    assert len(out["past_recent"]) == 1          # 近 30 日只含 5 天前那条
    assert out["past"][0]["rank"] is None        # 回看行不再计算分位（F12）


def test_window_has_trading_day_distrusts_estimated_calendar(monkeypatch):
    """估算日历（无 token → 工作日近似、节假日混入）**不得当权威**。

    回归（R0~R2 review）：丢弃 `is_estimated` → 长假窗口下 has_td 为 True →
    打印「解禁数据不可得（疑代理阻断或接口变化）」并 exit 3，把**窗口无交易日
    误报成数据源故障**。同批给 `freshness.trading_day_lag` /
    `sector_flow._is_trading_day` 立的估算日历纪律，此处漏了。
    """
    import lib.trade_cal as tc

    import unlock_calendar as uc

    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20261001"], True))
    assert uc._window_has_trading_day("20261001", "20261008") is None


def test_main_empty_window_estimated_calendar_not_blamed_on_source(monkeypatch, capsys):
    """日历不可信时空返回须说明「无法鉴别」，不得归因成「疑代理阻断」。"""
    import akshare as ak
    import lib.trade_cal as tc

    import unlock_calendar as uc

    monkeypatch.setattr(ak, "stock_restricted_release_summary_em",
                        lambda **kw: pd.DataFrame())
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20261001"], True))
    monkeypatch.setattr(sys, "argv",
                        ["unlock_calendar.py", "--days-past", "5", "--no-out"])
    assert uc.main() == 3
    err = capsys.readouterr().err
    assert ("日历" in err) or ("无法鉴别" in err), f"未说明日历不可信: {err}"
    assert "疑代理阻断" not in err, f"归因仍指向数据源: {err}"
