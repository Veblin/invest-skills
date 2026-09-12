"""T11-1 南向资金接入 — 离线单测（无网络、无 DB）。

注入纪律（D13）：patch 打在**消费方查找的模块命名空间** —— 三个叶子取数函数
`_fetch_hist_df` / `_fetch_summary_df` / `_fetch_tushare_df` 都定义在 `hk_southbound`
模块内，消费方按模块属性查找，故 `monkeypatch.setattr(hk_southbound, ...)` 有效。
fixture 带**独特标记**（日期 2099-12-31 / 值 999.99），patch 失效时断言必然失败，
不会因真实源碰巧成功而假绿。

口径纪律（2026-09-12 实测，见 r3 §1.1）：
- `当日成交净买额` 是唯一可用净额列（亿元）；`当日资金流入`/`当日余额` 恒 NaN → 三态，**不得填 0**
- tushare `moneyflow_hsgt` 是**累计口径**（ggt_ss/ggt_sz/south_money），必须差分；
  直接引用是 550 亿 vs 44 亿的量级错误
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

import hk_southbound as sb

# ── 夹具：akshare 日频帧（列名照 2026-09-12 实测）─────────────────────────

HIST_COLUMNS = ["日期", "当日成交净买额", "买入成交额", "卖出成交额", "历史累计净买额",
                "当日资金流入", "当日余额", "持股市值", "领涨股", "领涨股-涨跌幅",
                "恒生指数", "恒生指数-涨跌幅", "领涨股-代码"]


def _hist_frame(*, last_net: float | None = 31.9191) -> pd.DataFrame:
    """两行日频帧；`当日资金流入`/`当日余额` 照实测恒 NaN。"""
    return pd.DataFrame([
        {"日期": "2026-09-10", "当日成交净买额": 42.6033, "买入成交额": 283.5187,
         "卖出成交额": 240.9154, "历史累计净买额": 3.205720, "当日资金流入": None,
         "当日余额": None, "持股市值": 6.044089e12, "领涨股": "挚达科技",
         "领涨股-涨跌幅": 107.22, "恒生指数": 24954.47, "恒生指数-涨跌幅": -1.27,
         "领涨股-代码": "02650.HK"},
        {"日期": "2026-09-11", "当日成交净买额": last_net, "买入成交额": 302.5128,
         "卖出成交额": 270.5937, "历史累计净买额": 3.208912, "当日资金流入": None,
         "当日余额": None, "持股市值": 6.000251e12, "领涨股": "华沿机器人",
         "领涨股-涨跌幅": 25.62, "恒生指数": 24805.63, "恒生指数-涨跌幅": -0.60,
         "领涨股-代码": "01021.HK"},
    ], columns=HIST_COLUMNS)


# ── parse_hist_rows ──────────────────────────────────────────────────────

def test_parse_hist_rows_dead_columns_absent():
    """NaN 列不得进 payload（填 0 会被读成「当日零流入」这一事实断言）。"""
    rows = sb.parse_hist_rows(_hist_frame())
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["date"] == "2026-09-10"
    assert r0["net_buy_yi"] == pytest.approx(42.6033)
    assert r0["buy_yi"] == pytest.approx(283.5187)
    assert r0["sell_yi"] == pytest.approx(240.9154)
    # 历史累计净买额单位为万亿元 → ×1e4 得亿元（20260911：3.208912 → 32089.12）
    assert r0["cum_yi"] == pytest.approx(32057.20)
    assert rows[1]["cum_yi"] == pytest.approx(32089.12)
    assert r0["hsi"] == pytest.approx(24954.47)
    assert r0["hsi_chg_pct"] == pytest.approx(-1.27)
    for dead in ("fund_inflow_yi", "balance_yi", "当日资金流入", "当日余额"):
        assert dead not in r0, f"恒 NaN 列 {dead} 不得进 payload"


def test_parse_hist_rows_nan_net_is_none_not_zero():
    rows = sb.parse_hist_rows(_hist_frame(last_net=None))
    assert rows[1]["net_buy_yi"] is None, "缺失净额须三态（None），不得填 0.0"


def test_parse_hist_rows_pandas_nat_does_not_crash():
    """NaT 是 datetime 的伪子类，`NaT.strftime()` 抛 ValueError——
    自实现 isinstance 分支会让日期脏值一路冒到 cmd_report 把整份报告打崩。"""
    df = _hist_frame()
    df.loc[0, "日期"] = pd.NaT
    rows = sb.parse_hist_rows(df)
    assert [r["date"] for r in rows] == ["2026-09-11"], "NaT 行按不可解析跳过"


def test_summary_nat_date_is_none_not_crash(monkeypatch):
    """NaT 是 datetime 的伪子类：`NaT.strftime()` 抛 ValueError。
    自实现 isinstance 分支时该异常会穿透 southbound_summary（其 try 只包取数）
    一路冒到 cmd_report——整份报告崩且不留档。"""
    frame = _summary_frame()
    frame["交易日"] = pd.NaT
    monkeypatch.setattr(sb, "_fetch_summary_df", lambda: frame)
    out = sb.southbound_summary()          # 不得抛
    assert out["available"] is True, "净额仍可解析 → 仍算可得"
    assert out["date"] is None, "NaT 日期须为 None（三态），不得崩溃"


def test_cross_check_uses_latest_common_date(monkeypatch):
    """两源末行日期常不一致 → 必须取**共有最新日**对照，
    否则 cross_check 恒返回「不可比」，交叉核对形同虚设。"""
    def _fake_hist(symbol):
        base = _hist_frame()
        if symbol == "港股通深":
            base["当日成交净买额"] = [12.3868, 12.3911]
        return base

    monkeypatch.setattr(sb, "_fetch_hist_df", _fake_hist)
    monkeypatch.setattr(sb, "_fetch_summary_df", _summary_frame)
    # tushare 多出一行更晚的日期（09-12），末行日期与 akshare 不同
    monkeypatch.setattr(sb, "_fetch_tushare_df", lambda days: pd.DataFrame([
        {"trade_date": "20260909", "ggt_ss": 32014.6, "ggt_sz": 22918.14, "south_money": 54932.73},
        {"trade_date": "20260910", "ggt_ss": 32057.2, "ggt_sz": 22921.04, "south_money": 54978.24},
        {"trade_date": "20260911", "ggt_ss": 32089.12, "ggt_sz": 22933.43, "south_money": 55022.55},
    ]))
    out = sb.fetch_southbound(days=20)
    assert out["cross"] is not None, "共有日期存在却未对照"
    assert out["cross"]["comparable"] is True
    assert out["cross"]["consistent"] is True


def test_summary_only_fallback_requires_actual_net_values(monkeypatch):
    """汇总帧有行但净额列全空 → 不得判 available=True（否则报告出
    「可得（合计 — 亿）」并**抑制** LAW 5 的不可得标注）。"""
    def _boom(*a, **kw):
        raise RuntimeError("不可用")

    frame = _summary_frame()
    frame["成交净买额"] = None
    monkeypatch.setattr(sb, "_fetch_hist_df", _boom)
    monkeypatch.setattr(sb, "_fetch_tushare_df", _boom)
    monkeypatch.setattr(sb, "_fetch_summary_df", lambda: frame)
    out = sb.fetch_southbound(days=20)
    assert out["available"] is False and out["reason"]


def test_parse_hist_rows_sorted_ascending():
    df = _hist_frame().iloc[::-1]          # 倒序输入
    rows = sb.parse_hist_rows(df.reset_index(drop=True))
    assert [r["date"] for r in rows] == ["2026-09-10", "2026-09-11"]


# ── diff_cumulative（累计口径 → 当日净额）─────────────────────────────────

def test_diff_cumulative_basic():
    rows = [{"date": "2026-09-10", "v": 32057.2}, {"date": "2026-09-11", "v": 32089.12}]
    out = sb.diff_cumulative(rows, "v")
    assert len(out) == 1
    # 32089.12 − 32057.2 = 31.92 ↔ akshare 同日 31.9191（实测吻合）
    assert out[0]["value"] == pytest.approx(31.92)
    assert out[0]["date"] == "2026-09-11"
    assert out[0]["prev_date"] == "2026-09-10"
    assert out[0]["cal_days"] == 1
    assert out[0]["span"] == "1 日"


def test_diff_cumulative_gap_absorbs_into_one_row():
    """停市/长假缺行 → 单条差值（区间累计），**不补 0 行**、不伪造中间日。"""
    rows = [{"date": "2026-09-04", "v": 32014.6}, {"date": "2026-09-08", "v": 32089.12}]
    out = sb.diff_cumulative(rows, "v")
    assert len(out) == 1, "缺行不得补 0 行冒充逐日数据"
    assert out[0]["value"] == pytest.approx(74.52)
    assert out[0]["cal_days"] == 4
    assert out[0]["span"] == "跨 4 自然日"


def test_diff_cumulative_empty_fails_loud():
    """D5：空输入 fail loud——静默返回空列表会让调用方误以为「无净买入」。"""
    with pytest.raises(ValueError):
        sb.diff_cumulative([], "v")
    assert sb.diff_cumulative([{"date": "2026-09-11", "v": 32089.12}], "v") == []


# ── cross_check（tushare 差分 ↔ akshare 同日）────────────────────────────

def _ts_row(date="2026-09-11", total=44.31, sh=31.92, sz=12.39):
    return {"date": date, "sh_yi": sh, "sz_yi": sz, "total_yi": total,
            "prev_date": "2026-09-10", "cal_days": 1, "span": "1 日"}


def test_cross_check_matches_same_day():
    out = sb.cross_check({"date": "2026-09-11", "total_yi": 44.31}, _ts_row())
    assert out["comparable"] is True
    assert out["consistent"] is True
    assert out["delta_yi"] == pytest.approx(0.0, abs=0.01)


def test_cross_check_inconsistency_is_not_false_green():
    """写死一个不一致值——防「永远 True」的假绿。"""
    out = sb.cross_check({"date": "2026-09-11", "total_yi": 550.22}, _ts_row())
    assert out["comparable"] is True
    assert out["consistent"] is False
    assert out["delta_yi"] > 100, "550 亿 vs 44 亿：累计口径未差分就是这个量级"


def test_cross_check_requires_same_date():
    out = sb.cross_check({"date": "2026-09-10", "total_yi": 44.31}, _ts_row())
    assert out["comparable"] is False
    assert out["consistent"] is None
    assert "日期" in out["note"]


def test_cross_check_missing_side_is_three_state():
    a = sb.cross_check(None, _ts_row())
    assert a["comparable"] is False and a["consistent"] is None
    b = sb.cross_check({"date": "2026-09-11", "total_yi": 44.31}, None)
    assert b["comparable"] is False and b["consistent"] is None


# ── southbound_daily（沪+深合并）─────────────────────────────────────────

def test_southbound_daily_merges_two_symbols(monkeypatch):
    def _fake_hist(symbol):
        assert symbol in sb.DIRECT_SYMBOLS
        base = _hist_frame()
        if symbol == "港股通深":
            base["当日成交净买额"] = [12.3868, 12.3911]
        return base

    monkeypatch.setattr(sb, "_fetch_hist_df", _fake_hist)
    out = sb.southbound_daily(days=20)
    assert out["available"] is True
    r = out["rows"][-1]
    assert r["date"] == "2026-09-11"
    assert r["sh_yi"] == pytest.approx(31.9191)
    assert r["sz_yi"] == pytest.approx(12.3911)
    assert r["total_yi"] == pytest.approx(44.3102)     # Python 求和（P0）
    assert r["hsi"] == pytest.approx(24805.63)
    assert "akshare" in out["source"]


def test_southbound_daily_one_side_missing_is_three_state(monkeypatch):
    """深向不可得 → sz_yi/total_yi 为 None，**不得**拿沪向冒充合计。"""
    def _fake_hist(symbol):
        if symbol == "港股通深":
            raise RuntimeError("源不可用")
        return _hist_frame()

    monkeypatch.setattr(sb, "_fetch_hist_df", _fake_hist)
    out = sb.southbound_daily(days=20)
    assert out["available"] is True
    r = out["rows"][-1]
    assert r["sh_yi"] == pytest.approx(31.9191)
    assert r["sz_yi"] is None
    assert r["total_yi"] is None
    assert any("深" in w for w in out["warnings"])


def test_southbound_daily_limits_window(monkeypatch):
    monkeypatch.setattr(sb, "_fetch_hist_df", lambda symbol: _hist_frame())
    assert len(sb.southbound_daily(days=1)["rows"]) == 1


# ── southbound_summary ───────────────────────────────────────────────────

def _summary_frame() -> pd.DataFrame:
    cols = ["交易日", "类型", "板块", "资金方向", "交易状态", "成交净买额",
            "资金净流入", "当日资金余额", "上涨数", "持平数", "下跌数",
            "相关指数", "指数涨跌幅"]
    return pd.DataFrame([
        {"交易日": "2026-09-11", "类型": "沪港通", "板块": "沪股通", "资金方向": "北向",
         "交易状态": 3, "成交净买额": 0.0, "资金净流入": 0.0, "当日资金余额": 0.0,
         "上涨数": 203, "持平数": 15, "下跌数": 1425, "相关指数": "上证指数", "指数涨跌幅": -1.18},
        {"交易日": "2026-09-11", "类型": "沪港通", "板块": "港股通(沪)", "资金方向": "南向",
         "交易状态": 3, "成交净买额": 31.919155, "资金净流入": 420.0, "当日资金余额": 0.0,
         "上涨数": 164, "持平数": 16, "下跌数": 480, "相关指数": "恒生指数", "指数涨跌幅": -0.60},
        {"交易日": "2026-09-11", "类型": "深港通", "板块": "港股通(深)", "资金方向": "南向",
         "交易状态": 3, "成交净买额": 12.391082, "资金净流入": 420.0, "当日资金余额": 0.0,
         "上涨数": 164, "持平数": 16, "下跌数": 480, "相关指数": "恒生指数", "指数涨跌幅": -0.60},
    ], columns=cols)


def test_summary_registers_unverified_columns_as_unused(monkeypatch):
    """`交易状态`/`资金净流入`/`当日资金余额` 语义未核（实测 420.0 疑为每日额度而非净流入）
    → 显式登记为 unused_fields，不猜语义、不上报告。"""
    monkeypatch.setattr(sb, "_fetch_summary_df", _summary_frame)
    out = sb.southbound_summary()
    assert out["available"] is True
    assert out["date"] == "2026-09-11"
    assert out["sh"]["net_buy_yi"] == pytest.approx(31.919155)
    assert out["sz"]["net_buy_yi"] == pytest.approx(12.391082)
    assert out["sh"]["up"] == 164 and out["sh"]["down"] == 480
    assert out["hsi_chg_pct"] == pytest.approx(-0.60)
    assert set(out["unused_fields"]) == {"交易状态", "资金净流入", "当日资金余额"}
    assert out["note"]


def test_summary_failure_is_three_state(monkeypatch):
    monkeypatch.setattr(sb, "_fetch_summary_df",
                        lambda: (_ for _ in ()).throw(RuntimeError("不可用")))
    out = sb.southbound_summary()
    assert out["available"] is False and out["sh"] is None and out["note"]


# ── fetch_southbound 降级链 ──────────────────────────────────────────────

def test_degradation_chain_to_tushare_cross(monkeypatch):
    """akshare 日频失败 → tushare 累计口径差分接手，且口径注记随行。"""
    def _boom(symbol):
        raise RuntimeError("akshare 不可用")

    monkeypatch.setattr(sb, "_fetch_hist_df", _boom)
    # ⚠️ 三个叶子都必须注入：fetch_southbound 无条件调用三者，
    # 漏掉任何一个都会让「离线单测」实际打真实网络（实测：漏 _fetch_summary_df 时
    # 本测试向 datacenter-web.eastmoney.com 开 8 条连接）
    monkeypatch.setattr(sb, "_fetch_summary_df", _summary_frame)
    monkeypatch.setattr(sb, "_fetch_tushare_df", lambda days: pd.DataFrame([
        {"trade_date": "20260910", "ggt_ss": 32057.2, "ggt_sz": 22921.04, "south_money": 54978.24},
        {"trade_date": "20260911", "ggt_ss": 32089.12, "ggt_sz": 22933.43, "south_money": 55022.55},
    ]))
    out = sb.fetch_southbound(days=20)
    assert out["available"] is True
    assert "tushare" in out["source"]
    assert "累计口径" in out["caliber_note"], "累计口径注记必须随 payload 走"
    r = out["rows"][-1]
    assert r["date"] == "2026-09-11"
    assert r["sh_yi"] == pytest.approx(31.92)
    assert r["sz_yi"] == pytest.approx(12.39)
    assert r["total_yi"] == pytest.approx(44.31)
    assert out["warnings"], "降级原因须逐项标注"


def test_degradation_chain_all_fail_is_three_state(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("源不可用")

    monkeypatch.setattr(sb, "_fetch_hist_df", _boom)
    monkeypatch.setattr(sb, "_fetch_tushare_df", _boom)
    monkeypatch.setattr(sb, "_fetch_summary_df", _boom)
    out = sb.fetch_southbound(days=20)
    assert out["available"] is False
    assert out["rows"] == []
    assert out["reason"], "全失败须给原因（供报告三态标注）"


def test_fetch_southbound_prefers_akshare_and_cross_checks(monkeypatch):
    def _fake_hist(symbol):
        base = _hist_frame()
        if symbol == "港股通深":
            base["当日成交净买额"] = [12.3868, 12.3911]
        return base

    monkeypatch.setattr(sb, "_fetch_hist_df", _fake_hist)
    monkeypatch.setattr(sb, "_fetch_summary_df", _summary_frame)
    monkeypatch.setattr(sb, "_fetch_tushare_df", lambda days: pd.DataFrame([
        {"trade_date": "20260910", "ggt_ss": 32057.2, "ggt_sz": 22921.04, "south_money": 54978.24},
        {"trade_date": "20260911", "ggt_ss": 32089.12, "ggt_sz": 22933.43, "south_money": 55022.55},
    ]))
    out = sb.fetch_southbound(days=20)
    assert "akshare" in out["source"]
    assert out["summary"]["sh"]["net_buy_yi"] == pytest.approx(31.919155)
    cross = out["cross"]
    # akshare 沪 31.9191 + 深 12.3911 = 44.3102 ↔ tushare 差分 44.31（同向同量级）
    assert cross["comparable"] is True
    assert abs(cross["delta_yi"]) < 0.05


# ── CLI：报告「模块 3 市场结构 — 南向资金」节 ──────────────────────────────

_CLI = None


def _cli():
    """按**文件路径**加载 CLI（conftest 刻意不插 `scripts/` 根，避免抢命中本 skill 的 lib）。"""
    global _CLI
    if _CLI is None:
        p = Path(__file__).resolve().parent.parent / "scripts" / "hk.py"
        spec = importlib.util.spec_from_file_location("hk_cli_under_test", p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _CLI = mod
    return _CLI


_SB_OK = {
    "available": True,
    "source": "akshare.stock_hsgt_hist_em（港股通沪/深）",
    "caliber_note": None,
    "warnings": [],
    "summary": {"available": True, "date": "2099-12-31",
                "sh": {"net_buy_yi": 31.919155, "up": 164, "flat": 16, "down": 480},
                "sz": {"net_buy_yi": 12.391082, "up": 164, "flat": 16, "down": 480},
                "hsi": None, "hsi_chg_pct": -0.60},
    "cross": {"comparable": True, "consistent": True, "delta_yi": 0.0002, "note": ""},
    "rows": [{"date": "2099-12-31", "sh_yi": 31.9191, "sz_yi": 12.3911,
              "total_yi": 44.3102, "hsi": 24805.63, "hsi_chg_pct": -0.60,
              "cum_yi": 32089.12}],
}


def _stub_report(monkeypatch, southbound):
    hk_mod = _cli()
    monkeypatch.setattr(hk_mod, "_snapshot_row", lambda s: {
        "price": 600.0, "name": "腾讯控股", "ts": "20260912", "pe_ttm": 20.0,
        "chg_pct": 1.0, "mcap_hkd_yi": 50000.0, "low_52w": 400.0, "high_52w": 700.0})
    monkeypatch.setattr(hk_mod.hk_tushare, "fetch_basic", lambda s: {})
    monkeypatch.setattr(hk_mod.hk_yfinance, "fetch_info", lambda s: {})
    monkeypatch.setattr(hk_mod.hk_financials, "fetch_financials", lambda s: [])
    monkeypatch.setattr(hk_mod.hk_valuation, "fetch_valuation_series", lambda *a, **kw: [])
    monkeypatch.setattr(hk_mod.hk_kline, "fetch_kline", lambda *a, **kw: {"data": []})
    monkeypatch.setattr(hk_mod.hk_southbound, "fetch_southbound", lambda days=20: southbound)
    return hk_mod


def test_cli_report_contains_southbound_section(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_report(monkeypatch, dict(_SB_OK))
    args = argparse.Namespace(symbol="00700", outdir=str(tmp_path))
    assert hk_mod.cmd_report(args) == 0
    out = capsys.readouterr().out
    assert "模块 3 市场结构" in out and "南向资金" in out
    assert "2099-12-31" in out, "mock 标记缺失说明 patch 未生效"
    assert "44.31" in out, "合计须为 Python 求和后的值"
    assert "Python calc" in out, "P0：加工数字（合计）须带公式标签"
    for banned in ("建议买入", "建议卖出", "建议持有"):
        assert banned not in out
    body = list(tmp_path.glob("00700-腾讯控股/*.md"))[0].read_text(encoding="utf-8")
    assert "模块 3 市场结构" in body


def test_cli_report_southbound_unavailable_is_three_state(monkeypatch, tmp_path, capsys):
    bad = {"available": False, "rows": [], "warnings": [],
           "reason": "南向数据源全部不可得", "source": None,
           "caliber_note": None, "summary": None, "cross": None}
    hk_mod = _stub_report(monkeypatch, bad)
    args = argparse.Namespace(symbol="00700", outdir=str(tmp_path))
    assert hk_mod.cmd_report(args) == 0
    out = capsys.readouterr().out
    assert "南向资金不可得" in out
    assert "LAW 5" in out, "不可得须给三态说明（不得读作「南向无净买入」）"
    assert "44.31" not in out, "不可得时不得渲染数值（0 或沿用上次值都会被读成事实断言）"


def test_cli_report_identical_channel_counts_are_market_scope(monkeypatch, tmp_path, capsys):
    """源对沪/深两行返回**相同**涨跌家数（2026-09-12 实测）→ 是港股市场整体口径；
    分开渲染会暗示不存在的分通道粒度（D4：聚合数据须标注覆盖范围）。"""
    hk_mod = _stub_report(monkeypatch, dict(_SB_OK))
    hk_mod.cmd_report(argparse.Namespace(symbol="00700", outdir=str(tmp_path)))
    out = capsys.readouterr().out
    assert "港股市场整体" in out
    assert "沪通道" not in out, "计数相同即为市场级口径，不得渲染成分通道"


def test_cli_report_does_not_render_dead_columns(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_report(monkeypatch, dict(_SB_OK))
    hk_mod.cmd_report(argparse.Namespace(symbol="00700", outdir=str(tmp_path)))
    out = capsys.readouterr().out
    for dead in ("当日资金流入", "当日余额"):
        assert dead not in out, f"{dead} 恒 NaN，不得出现在报告"
