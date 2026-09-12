"""T11-3 / HK-1 v2 — 模块覆盖声明 + 双港股 compare（离线单测）。

两条纪律被测试钉住：
1. **对照表只搬运引擎字段**（P0）：每个单元格的值必须能在输入 payload 中原样找到，
   派生项（元→亿）带 calc 公式标签。
2. **无静默缺节**：覆盖声明里标「已覆盖」的模块，报告里必须有对应的真实节标题
   （逐模块参数化断言，缺一即红）。
"""
from __future__ import annotations

import argparse

import pytest
from _hk_cli import load_hk_cli

import hk_compare as hc


def _side(code: str, name: str, *, price=600.0, pe=20.5) -> dict:
    return {
        "code": code,
        "name": name,
        "snapshot": {"price": price, "chg_pct": 1.23, "pe_ttm": pe,
                     "mcap_hkd_yi": 50000.0, "low_52w": 400.0, "high_52w": 700.0},
        "valuation_pctl": {"pe": {"pct": 55.5, "median": 18.0, "n": 1200},
                           "pb": {"pct": 44.4, "median": 3.2}},
        "financials": {"latest": {"report_date": "2026-06-30", "revenue_yi": 1200.0,
                                  "net_profit_yi": 340.0, "roe": 12.5}},
        "technical": {"latest_close": price, "ma": {"5": 598.0, "20": 590.0, "60": 580.0},
                      "macd": {"dif": 1.2, "dea": 0.9}, "rsi": 55.0},
    }


# ── 纯函数层 ─────────────────────────────────────────────────────────────

def test_build_compare_rows_come_from_payload():
    """P0：对照表每个值必须能在输入 payload 中原样找到（防 AI 加工数字）。"""
    left, right = _side("00700", "腾讯控股"), _side("09988", "阿里巴巴", price=100.0, pe=15.0)
    cmp = hc.build_compare(left, right)
    assert cmp["left_code"] == "00700" and cmp["right_code"] == "09988"
    lflat, rflat = repr(left), repr(right)
    checked = 0
    for row in cmp["rows"]:
        for side_raw, flat in ((row["left_raw"], lflat), (row["right_raw"], rflat)):
            if side_raw is None:
                continue
            assert str(side_raw) in flat, f"{row['metric']} 的值 {side_raw} 不在 payload 内"
            checked += 1
    assert checked >= 20, "对照维度数不足——覆盖矩阵要求 4 维齐备"


def test_build_compare_none_is_dash_not_zero():
    left = _side("00700", "腾讯控股")
    right = _side("09988", "阿里巴巴")
    right["snapshot"]["pe_ttm"] = None
    right["valuation_pctl"]["pe"] = None
    right["financials"]["latest"] = None
    right["technical"] = {}
    cmp = hc.build_compare(left, right)
    rows = {r["metric"]: r for r in cmp["rows"]}
    assert rows["PE(TTM)"]["right"] == "—"
    assert rows["PE 序列分位"]["right"] == "—"
    assert rows["ROE"]["right"] == "—"
    assert rows["MA20"]["right"] == "—"
    assert "0" not in (rows["PE(TTM)"]["right"], rows["MA20"]["right"])


def test_build_compare_records_derived_rows_with_formula():
    """派生项（元→亿）必须带 calc 公式标签，不得以「引擎字段」面貌出现。"""
    cmp = hc.build_compare(_side("00700", "腾讯控股"), _side("09988", "阿里巴巴"))
    derived = [r for r in cmp["rows"] if r.get("calc")]
    assert {r["metric"] for r in derived} >= {"营收", "归母净利"}
    for r in derived:
        assert "1e8" in r["calc"] or "/1e8" in r["calc"]


def test_render_compare_table_is_markdown_with_sources():
    cmp = hc.build_compare(_side("00700", "腾讯控股"), _side("09988", "阿里巴巴"))
    lines = hc.render_compare_table(cmp)
    text = "\n".join(lines)
    assert "| 维度 | 指标 |" in text
    assert "00700" in text and "09988" in text
    assert "腾讯控股" in text and "阿里巴巴" in text
    assert "Python calc" in text, "派生行须带公式标签"
    derived = [r for r in cmp["rows"] if r.get("calc")]
    assert text.count("Python calc") == len(derived), \
        "派生标签须**每行一次**（挂在单元格末尾会让左列看起来像引擎原值、右列才是派生）"
    for banned in ("建议买入", "建议卖出", "建议持有"):
        assert banned not in text


# ── CLI 层 ───────────────────────────────────────────────────────────────

def _args(tmp_path, left="00700", right="09988"):
    return argparse.Namespace(left=left, right=right, outdir=str(tmp_path))


def _no_fetch(monkeypatch):
    """任何取数被调用即失败——用来证明校验先于取数。"""
    hk_mod = load_hk_cli()

    def _boom(*a, **kw):
        raise AssertionError("校验阶段不得发生取数")

    monkeypatch.setattr(hk_mod, "_snapshot_row", _boom)
    return hk_mod


def test_compare_rejects_a_share_with_ah_pointer(monkeypatch, tmp_path, capsys):
    hk_mod = _no_fetch(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path, left="600036")) == 2
    err = capsys.readouterr().err
    assert "600036" in err and "ah" in err, "须指路 A/H 比价子命令"
    assert not list(tmp_path.glob("**/*.md")), "参数非法不得落盘"


def test_compare_rejects_a_share_suffix_form(monkeypatch, tmp_path, capsys):
    hk_mod = _no_fetch(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path, right="600036.SH")) == 2
    assert "ah" in capsys.readouterr().err


def test_compare_rejects_same_code(monkeypatch, tmp_path, capsys):
    hk_mod = _no_fetch(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path, left="00700", right="00700")) == 2
    assert "❌" in capsys.readouterr().err


def test_compare_rejects_invalid_hk_symbol(monkeypatch, tmp_path, capsys):
    hk_mod = _no_fetch(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path, right="ABCDE")) == 2
    assert "❌" in capsys.readouterr().err


def _kline_rows(n=80):
    """合成 K 线（≥60 行才能算出 MA60）——走真实 technical.compute，避免假形状。"""
    return [{"trade_date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
             "open": 100.0 + i * 0.1, "close": 100.0 + i * 0.1,
             "high": 101.0 + i * 0.1, "low": 99.0 + i * 0.1, "vol": 1.0e6}
            for i in range(n)]


_FIN_ROW = {"report_date": "2026-06-30", "revenue": 1.2e11, "net_profit": 3.4e10, "roe": 12.5}


def _stub_sides(monkeypatch, *, right_ok=True, pb=3.3, fin=None, kline=True):
    """默认**四维齐备**：维度全空会让 cmd_compare 正确地返回 1（关键维度不可得），
    故需要 0 的用例必须显式给数据，而不是靠空桩碰巧通过。"""
    hk_mod = load_hk_cli()
    monkeypatch.setattr(hk_mod, "_snapshot_row", lambda s: dict(
        {"price": 600.0, "name": f"标的{s}", "ts": "20991231", "pe_ttm": 20.0,
         "chg_pct": 1.0, "mcap_hkd_yi": 50000.0, "low_52w": 400.0, "high_52w": 700.0})
        if (right_ok or s != "09988") else {"error": "快照不可得"})
    monkeypatch.setattr(hk_mod.hk_yfinance, "fetch_info", lambda s: ({"pb": pb} if pb else {}))
    monkeypatch.setattr(hk_mod.hk_valuation, "fetch_valuation_series",
                        lambda *a, **kw: [{"date": f"2025-01-{i:02d}", "value": float(i)}
                                          for i in range(1, 11)])
    monkeypatch.setattr(hk_mod.hk_financials, "fetch_financials",
                        lambda s: [dict(fin or _FIN_ROW)])
    monkeypatch.setattr(hk_mod.hk_kline, "fetch_kline",
                        lambda *a, **kw: {"data": _kline_rows() if kline else []})
    return hk_mod


def test_compare_writes_to_disk(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_sides(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path)) == 0
    out = capsys.readouterr().out
    # 断言须可失败：原先 `... or "口径" in out` 因静态免责句含「口径」而恒真
    assert "Python calc" in out, "派生行须带公式标签"
    files = list(tmp_path.glob("00700-09988-compare/*.md"))
    assert files, "compare 须落盘（与 report 同契约）"
    body = files[0].read_text(encoding="utf-8")
    assert "00700" in body and "09988" in body
    # mock 名称标记：真实取数不会产出「标的<code>」——缺失即说明 patch 未生效
    assert "标的00700" in body and "标的09988" in body, "mock 标记缺失说明 patch 未生效"


def test_compare_pb_percentile_uses_current_pb(monkeypatch, tmp_path):
    """PB 分位需要**当前 PB** 才能算——不提供当前值会让该行永远显示「—」（实测踩坑）。"""
    hk_mod = _stub_sides(monkeypatch)
    assert hk_mod.cmd_compare(_args(tmp_path)) == 0
    body = list(tmp_path.glob("00700-09988-compare/*.md"))[0].read_text(encoding="utf-8")
    row = next(ln for ln in body.splitlines() if "PB 序列分位" in ln)
    assert "—" not in row, "当前 PB 已提供时分位不得为不可得"


def test_compare_nan_pb_is_not_a_fabricated_percentile(monkeypatch, tmp_path):
    """PB 为 NaN 时须三态：`percentile_position` 的守卫对 NaN 失效
    （`cur is None or cur <= 0` 两个比较均为 False）→ 会算出**引擎从未产出过的 0.0 分位**。"""
    hk_mod = _stub_sides(monkeypatch, pb=float("nan"))
    assert hk_mod.cmd_compare(_args(tmp_path)) == 0
    body = list(tmp_path.glob("00700-09988-compare/*.md"))[0].read_text(encoding="utf-8")
    row = next(ln for ln in body.splitlines() if "PB 序列分位" in ln)
    assert "+0.0%" not in row, "NaN 不得被算成 0 分位"
    assert "当前 PB 不可得" in body


def test_compare_non_numeric_financials_do_not_crash(monkeypatch, tmp_path):
    """东财原值可能非数值（占位串/'1,234'）——裸 `/1e8` 会让整条命令崩且不落盘。"""
    hk_mod = _stub_sides(monkeypatch, fin={"report_date": "2026-06-30", "revenue": "1,234",
                                           "net_profit": "n/a", "roe": "—"})
    assert hk_mod.cmd_compare(_args(tmp_path)) == 0
    body = list(tmp_path.glob("00700-09988-compare/*.md"))[0].read_text(encoding="utf-8")
    assert "nan" not in body.lower(), "NaN 不得渲染成字面 nan"
    assert "n/a" not in body


def test_compare_unavailable_dimension_returns_1(monkeypatch, tmp_path):
    """维度全空（如估值/财务/技术均不可得）须返回 1——只看 snapshot 会让调用方以为跑完整了。"""
    hk_mod = _stub_sides(monkeypatch, kline=False)
    monkeypatch.setattr(hk_mod.hk_valuation, "fetch_valuation_series", lambda *a, **kw: [])
    monkeypatch.setattr(hk_mod.hk_financials, "fetch_financials", lambda s: [])
    assert hk_mod.cmd_compare(_args(tmp_path)) == 1
    body = list(tmp_path.glob("00700-09988-compare/*.md"))[0].read_text(encoding="utf-8")
    assert "不可得维度" in body, "报告须显式列出不可得维度"


def test_compare_pb_unavailable_is_three_state_with_note(monkeypatch, tmp_path):
    hk_mod = _stub_sides(monkeypatch, pb=None)
    assert hk_mod.cmd_compare(_args(tmp_path)) == 0
    body = list(tmp_path.glob("00700-09988-compare/*.md"))[0].read_text(encoding="utf-8")
    assert "当前 PB 不可得" in body, "缺当前值须显式说明，不静默留空"


def test_compare_one_side_unavailable_returns_1_and_still_writes(monkeypatch, tmp_path, capsys):
    hk_mod = _stub_sides(monkeypatch, right_ok=False)
    assert hk_mod.cmd_compare(_args(tmp_path)) == 1, "单侧不可得须三态并返回 1"
    files = list(tmp_path.glob("00700-09988-compare/*.md"))
    assert files, "三态也要落盘（不留空文件、不静默跳过）"
    assert "不可得" in files[0].read_text(encoding="utf-8")


# ── 覆盖声明 ─────────────────────────────────────────────────────────────

def test_coverage_summary_is_7_of_7():
    hk_mod = load_hk_cli()
    cs = hk_mod.coverage_summary()
    assert cs["mappable"] == len(hk_mod.MAPPABLE_MODULES) == 7
    assert cs["covered"] == 7, "v2 验收：可映射维度须全覆盖"
    assert cs["ratio"] == pytest.approx(1.0)
    assert {i["module"] for i in cs["items"]} >= {"0", "1", "2", "3b", "3c", "4", "5", "6", "7", "8"}


def test_coverage_anchors_are_all_rendered(monkeypatch, tmp_path):
    """无静默缺节：**从 coverage_summary() 派生**待核清单（不硬编码模块号——
    硬编码会恰好漏掉唯一不合格的那个模块，让声明不可证伪）。"""
    hk_mod = load_hk_cli()
    body = _render_report(hk_mod, monkeypatch, tmp_path)
    anchors = [i for i in hk_mod.coverage_summary()["items"] if i["anchor"]]
    assert len(anchors) >= 6, "引擎侧锚点数量异常"
    for item in anchors:
        assert item["anchor"] in body, f"模块 {item['module']} 静默缺节（找不到 {item['anchor']}）"


def test_unanchored_covered_modules_are_declared_not_counted_as_engine():
    """模块 0 无引擎节：须在报告里**显式说明**，且不计入「引擎侧可核验」数——
    否则 7/7 会把「无节可核」的项也计入，使「无静默缺节」变成装饰性声明。"""
    hk_mod = load_hk_cli()
    cs = hk_mod.coverage_summary()
    assert cs["unanchored"] == ["0"], "预期只有模块 0 无引擎节"
    assert cs["engine_covered"] == cs["covered"] - 1
    assert cs["engine_covered"] < cs["mappable"]


def test_report_declares_uncovered_modules_with_reason(monkeypatch, tmp_path):
    hk_mod = load_hk_cli()
    body = _render_report(hk_mod, monkeypatch, tmp_path)
    assert "模块覆盖声明" in body
    for module in ("2", "3b", "3c"):
        item = next(i for i in hk_mod.coverage_summary()["items"] if i["module"] == module)
        assert item["status"] == "声明未接入"
        assert item["basis"] and item["basis"] != "—", f"模块 {module} 未给原因"
        assert item["basis"][:12] in body
    assert "HK 无季报" in body, "模块 4 部分覆盖须给港股制度差异原因"


def test_report_module5_6_have_no_directional_claims(monkeypatch, tmp_path):
    """模块 5/6 是框架性陈述——不得出现方向断言字样。"""
    hk_mod = load_hk_cli()
    body = _render_report(hk_mod, monkeypatch, tmp_path)
    for banned in ("将上涨", "将下跌", "看多", "看空", "预期收益", "目标价"):
        assert banned not in body, f"模块 5/6 出现方向性表述：{banned}"


def _render_report(hk_mod, monkeypatch, tmp_path) -> str:
    monkeypatch.setattr(hk_mod, "_snapshot_row", lambda s: {
        "price": 600.0, "name": "腾讯控股", "ts": "20991231", "pe_ttm": 20.0,
        "chg_pct": 1.0, "mcap_hkd_yi": 50000.0, "low_52w": 400.0, "high_52w": 700.0,
        "amount": 5.0e9})
    monkeypatch.setattr(hk_mod.hk_tushare, "fetch_basic", lambda s: {})
    monkeypatch.setattr(hk_mod.hk_yfinance, "fetch_info", lambda s: {})
    monkeypatch.setattr(hk_mod.hk_financials, "fetch_financials", lambda s: [])
    monkeypatch.setattr(hk_mod.hk_valuation, "fetch_valuation_series", lambda *a, **kw: [])
    monkeypatch.setattr(hk_mod.hk_kline, "fetch_kline", lambda *a, **kw: {"data": []})
    monkeypatch.setattr(hk_mod.hk_southbound, "fetch_southbound",
                        lambda days=20: {"available": False, "rows": [], "warnings": [],
                                         "reason": "测试桩：源不可得", "source": None,
                                         "caliber_note": None, "summary": None, "cross": None})
    hk_mod.cmd_report(argparse.Namespace(symbol="00700", outdir=str(tmp_path)))
    return list(tmp_path.glob("00700-腾讯控股/*.md"))[0].read_text(encoding="utf-8")
