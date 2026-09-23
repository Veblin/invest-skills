"""Offline smoke: report_formatter / kline_cache pure helpers + scan --help."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from kline_cache import KlineTTLCache  # noqa: E402（canonical: skills/lib）
from report_formatter import _fmt_amount, _fmt_pct, _fmt_price, _parse_universe_indices  # noqa: E402

_SCAN_PY = Path(__file__).resolve().parent.parent / "scripts" / "scan.py"


def test_fmt_helpers():
    assert _fmt_amount(2.5e8) == "2.50亿"
    assert _fmt_amount(5e4) == "5万"
    assert _fmt_pct(1.25) == "+1.25%"
    assert _fmt_pct(-0.5) == "-0.50%"
    assert _fmt_pct(float("nan")) == "N/A"
    assert _fmt_price(12.345) == "12.345"


def test_parse_universe_indices_default():
    labels = _parse_universe_indices({})
    assert ("沪深300", 300) in labels
    assert ("中证A500", 500) in labels


def test_kline_cache_roundtrip(tmp_path):
    """canonical KlineTTLCache 键布局兼容旧 gap 布局：{root}/{date}/{source}/{code}.pkl。"""
    cache = KlineTTLCache(lambda: tmp_path / "gap_scan_cache", 3 * 86400)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    cache.save("20260722", ("test", "000001.SZ"), df)
    loaded = cache.load("20260722", ("test", "000001.SZ"))
    assert loaded is not None
    assert list(loaded["close"]) == [1.0, 2.0]
    assert (tmp_path / "gap_scan_cache" / "20260722" / "test" / "000001.SZ.pkl").is_file()


def test_kline_cache_cross_day_hit(tmp_path):
    """固定段键下文件级 mtime TTL 跨日生效：1 天前保存命中，超 3 天 miss。

    缺陷 1 回归：旧实现把当日日期作键首段，次日查询必然 miss，TTL 退化为
    "当日有效"；修复后键为固定段 kline/，文件级 mtime TTL 真正生效。
    """
    import os
    import time

    cache = KlineTTLCache(lambda: tmp_path / "gap_scan_cache", 3 * 86400)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    parts = ("tushare", "000001.SZ")
    path = tmp_path / "gap_scan_cache" / "kline" / "tushare" / "000001.SZ.pkl"
    cache.save("kline", parts, df)
    assert path.is_file()
    # 拨回 1 天 → TTL 内，命中
    one_day = time.time() - 86400
    os.utime(path, (one_day, one_day))
    loaded = cache.load("kline", parts)
    assert loaded is not None
    assert list(loaded["close"]) == [1.0, 2.0]
    # 拨回 4 天 → 超 TTL，miss
    four_days = time.time() - 4 * 86400
    os.utime(path, (four_days, four_days))
    assert cache.load("kline", parts) is None


def test_scan_cache_segment_fixed():
    """键首段为固定段而非当日日期（缺陷 1 回归：跨日缓存命中）。"""
    mod = _load_scan_module()
    assert mod._CACHE_DATE_SEGMENT
    assert not (len(mod._CACHE_DATE_SEGMENT) == 8 and mod._CACHE_DATE_SEGMENT.isdigit())


def test_scan_cli_help_exit_0():
    r = subprocess.run(
        [sys.executable, str(_SCAN_PY), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "gap" in r.stdout.lower() or "universe" in r.stdout.lower()


def _load_scan_module():
    name = "gap_scan_scan_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, str(_SCAN_PY))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_path_has_seconds_timestamp():
    """report-conventions.md §1.2：详文档路径带时分秒，同日二次运行不覆盖（缺陷 5）。

    修复前写入 reports/gap-scan/{YYYYMMDD}.md（无时分秒）→ 同日二次运行覆盖历史。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    mod = _load_scan_module()
    tz = ZoneInfo("Asia/Shanghai")
    p1 = mod._report_path_for_now(datetime(2026, 8, 6, 9, 30, 5, tzinfo=tz))
    p2 = mod._report_path_for_now(datetime(2026, 8, 6, 9, 30, 6, tzinfo=tz))
    assert p1.name == "2026-08-06-09-30-05.md"
    assert p1.parent.name == "gap-scan"
    assert p1 != p2  # 秒级区分：同日二次运行不覆盖历史


# ======================================================================
# V31-39/40/42/45 —— 报告披露与措辞（离线构造 ScanResult，无网络）
# ======================================================================


def _mk_result(**overrides):
    """构造最小 ScanResult（含 1 条命中），供报告层断言使用。"""
    from collections import Counter

    from gap_scanner import GapInfo, ScanHit, ScanResult

    hit = ScanHit(
        ts_code="603596.SH",
        name="伯特利",
        board="主板",
        index_members=["csi300"],
        gap=GapInfo(gap_date="20260828", gap_pct=5.36,
                    gap_low=26.10, gap_high=27.50),
        current_price=28.38,
        ma60=26.347,
        pct_from_ma60=7.72,
        pct_from_gap_high=3.20,
        vol_ratio=3.29,
        avg_amount_20d=5.86e8,
        data_date="20260919",
        gap_day_amount=9.5e8,
        vol_ratio_denom=2.888e8,
        ma60_valid_bars=60,
        ma60_total_bars=60,
    )
    result = ScanResult(
        hits=[hit],
        across_suspension_hits=[],
        exclude_reasons=Counter({"low_liquidity": 5}),
        non_hit_reasons=Counter({"no_gap": 266, "gap_unconfirmed": 1}),
        total_in_universe=480,
        total_scanned=475,
        total_with_kline=480,
        total_fetch_errors=0,
        params={
            "gap_min_pct": 1.0,
            "gap_lookback": 60,
            "gap_min_vol_ratio": 1.0,
            "min_avg_amount": 1e8,
            "min_list_days": 60,
            "universe_str": "csi300,a500,star50",
            "source_label": "Tushare Pro (前复权自算)",
            "source_name": "tushare",
            "source_note": "auto：首选源 Tushare 可用性检验通过，未降级",
            "cal_estimated": False,
        },
        data_as_of="20260919",
        cache_status={"enabled": True, "hit": 480, "planned_fetch": 0,
                      "fetched": 0, "fetch_failed": 0,
                      "ttl_days": 3.0, "min_age_hours": 0.4, "max_age_hours": 2.9},
        attempted_sources=["tushare"],
    )
    for k, v in overrides.items():
        setattr(result, k, v)
    return result


def test_brief_has_risk_statement_head_and_tail():
    """首尾各一条风险声明（AGENTS.md 检查项「首部/尾部有风险声明」）。"""
    from report_formatter import format_brief

    text = format_brief(_mk_result())
    lines = text.splitlines()
    head = "\n".join(lines[:12])
    tail = "\n".join(lines[-6:])
    assert "风险声明" in head and "非投资建议" in head
    assert "风险声明" in tail


def test_brief_declares_observation_list_not_signal():
    """V31-39：命中 = 观察清单，非交易信号/预测/推荐排序。"""
    from report_formatter import format_brief

    text = format_brief(_mk_result())
    assert "观察清单" in text
    assert "非交易信号" in text


def test_brief_shows_data_as_of_and_cache():
    """V31-42：数据截止与缓存状态必须显式（不得把数日前收盘读作「当前」）。"""
    from report_formatter import format_brief

    text = format_brief(_mk_result())
    assert "数据截止: 20260919" in text
    assert "不含今日" in text
    assert "K线缓存: 缓存命中 480 / 新拉 0" in text
    assert "尝试源: tushare" in text


def test_brief_hit_table_has_denominator_and_no_current_price_label():
    """V31-38：命中表给出量比分母（可复算），且「现价」改「最新收盘」。"""
    from report_formatter import format_brief

    text = format_brief(_mk_result())
    header = text.splitlines()[text.splitlines().index(
        [ln for ln in text.splitlines() if ln.startswith("| 代码")][0])]
    assert "量比分母" in header
    assert "最新收盘" in header
    assert "现价" not in header
    assert "当前20日均额" in header


def test_brief_vol_threshold_displayed_when_below_one():
    """V31-45 渲染半：`--gap-min-vol-ratio 0.5` 生效时必须显示该参数。"""
    from report_formatter import format_brief

    result = _mk_result()
    result.params["gap_min_vol_ratio"] = 0.5
    assert "量比≥0.5" in format_brief(result)


def test_vol_threshold_hidden_for_invalid_values():
    """渲染层门：NaN/inf/≤0 的阈值从未生效，不得打印。"""
    from report_formatter import format_brief

    for bad in (float("nan"), float("inf"), 0.0, -1.0, "abc"):
        result = _mk_result()
        result.params["gap_min_vol_ratio"] = bad
        assert "量比≥" not in format_brief(result)


def test_md_report_discloses_provenance(tmp_path):
    """V31-40：首部风险声明 + 数据与来源（实际源/尝试源/缓存）+ 字段级来源。"""
    from report_formatter import format_markdown_report

    out = tmp_path / "r.md"
    format_markdown_report(_mk_result(), str(out))
    text = out.read_text(encoding="utf-8")
    assert out.is_file()
    assert "风险声明" in text
    assert "## 数据与来源" in text
    assert "| 实际使用源 | Tushare Pro (前复权自算) |" in text
    # 降级原因由采集层给出（source_note），渲染层不臆测
    assert "未降级" in text
    assert "字段级来源" in text
    assert "**数据截止:** 20260919" in text


def test_md_report_no_placeholder_catalyst(tmp_path):
    """V31-40：`催化待查` 占位符不得出现在报告（不得充当事实）。"""
    from report_formatter import format_markdown_report

    out = tmp_path / "r.md"
    format_markdown_report(_mk_result(), str(out))
    text = out.read_text(encoding="utf-8")
    assert "[催化待查]" not in text
    assert "未采集" in text


def test_md_report_per_hit_data_date_and_coverage(tmp_path):
    """V31-38/41：逐股简析给出数据日、可复算量比与 MA60 覆盖度。"""
    from report_formatter import format_markdown_report

    out = tmp_path / "r.md"
    format_markdown_report(_mk_result(), str(out))
    text = out.read_text(encoding="utf-8")
    assert "**数据日:** 20260919" in text
    assert "分母 2.89亿" in text
    assert "MA60 判定覆盖:** 60/60" in text


def test_md_report_non_hit_semantics_note(tmp_path):
    """V31-37：报告说明桶归属优先级与「最新bar待收盘确认」的终结语义。"""
    from report_formatter import format_markdown_report

    out = tmp_path / "r.md"
    format_markdown_report(_mk_result(), str(out))
    text = out.read_text(encoding="utf-8")
    assert "最新bar待收盘确认" in text
    assert "终结桶" in text
    # 零值桶可由闭合推得
    assert "排除 + 未命中 + 命中 + 跨停牌" in text


def test_json_declares_nature_and_disclaimer():
    """V31-39 代码半：JSON 顶层带 nature/disclaimer（下游脚本消费同一接口）。"""
    import json

    from report_formatter import format_json

    payload = json.loads(format_json(_mk_result()))
    assert "观察清单" in payload["nature"]
    assert "不构成" in payload["disclaimer"]
    assert payload["data_as_of"] == "20260919"
    assert payload["hits"][0]["vol_ratio_denom"] == 2.888e8


# ======================================================================
# 评审回归（2026-09-23）：披露链不得与实际执行分叉
# ======================================================================


class _StubSource:
    """最小数据源桩：fetch 返回空 → 模拟「待拉标的拉取失败」。"""

    attempted_sources = ("tushare",)
    source_selection_note = "stub"

    def source_name(self) -> str:
        return "tushare"

    def fetch_daily_batch(self, trade_dates):
        import pandas as _pd

        return _pd.DataFrame()

    def fetch_adj_factor_batch(self, trade_dates):
        import pandas as _pd

        return _pd.DataFrame()


class _PartialSource(_StubSource):
    """桩源：只返回 000001.SZ 的日线（用于「部分拉取成功」场景）。"""

    def fetch_daily_batch(self, trade_dates):
        import pandas as _pd

        return _pd.DataFrame({
            "ts_code": ["000001.SZ"] * 2,
            "trade_date": ["20260101", "20260102"],
            "open": [10.0, 10.1], "high": [10.2, 10.3],
            "low": [9.9, 10.0], "close": [10.1, 10.2],
            "vol": [1e6, 1e6], "amount": [1e8, 1e8],
        })


class _StubStock:
    def __init__(self, ts_code, name="测试", board="主板"):
        self.ts_code = ts_code
        self.name = name
        self.board = board
        self.index_membership = []


def test_scan_reports_actual_fetch_failures(tmp_path, monkeypatch, capsys):
    """F1 回归：待拉标的拉取失败时不得写成「新拉」。

    构造 1 只缓存命中 + 1 只待拉，数据源返回空日线（拉取失败）→ 扫描仅用
    缓存继续；`cache_status` 必须如实区分 新拉 0 / 拉取失败 1（修复前
    `fetched` 直接写入计划拉取数 1，报告把没拿到的标的写成「已新拉」）。
    """
    import json as _json

    import pandas as _pd

    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260101", "20260102"], True))
    cached_kline = _pd.DataFrame({
        "trade_date": ["20260101", "20260102"],
        "open_qfq": [10.0, 10.1], "high_qfq": [10.2, 10.3],
        "low_qfq": [9.9, 10.0], "close_qfq": [10.1, 10.2],
        "amount": [1e8, 1e8],
    })
    mod._KLINE_CACHE.save("kline", ("tushare", "000001.SZ"), cached_kline)

    stocks = [_StubStock("000001.SZ"), _StubStock("000002.SZ")]
    args = mod.build_parser().parse_args(["--json", "--no-save-report"])
    rc = mod._run_scan(
        args, stocks, {"000001.SZ", "000002.SZ"}, _StubSource(),
        "stub (前复权)", False, 0.0,
    )

    payload = _json.loads(capsys.readouterr().out)
    cache = payload["cache_status"]
    assert rc == 0
    assert cache["hit"] == 1
    assert cache["planned_fetch"] == 1
    assert cache["fetched"] == 0, "拉取失败的标的不得计入「新拉」"
    assert cache["fetch_failed"] == 1


def test_cache_row_shows_fetch_failures_and_disambiguates_hits():
    """F1 报告面：缓存行给出真实三数，且「缓存命中」不与扫描「命中」撞词。"""
    from report_formatter import format_brief, format_markdown_report

    result = _mk_result()
    result.cache_status = {
        "enabled": True, "hit": 470, "planned_fetch": 10, "fetched": 7,
        "fetch_failed": 3, "ttl_days": 3.0,
        "min_age_hours": 0.5, "max_age_hours": 2.0,
    }
    text = format_brief(result)
    assert "缓存命中 470 / 新拉 7 / 拉取失败 3" in text
    assert "K线缓存: 命中 470" not in text  # 旧措辞（与扫描命中混淆）不得残留

    import tempfile
    from pathlib import Path

    out = Path(tempfile.mkdtemp()) / "r.md"
    format_markdown_report(result, str(out))
    md = out.read_text(encoding="utf-8")
    assert "缓存命中 470 / 新拉 7 / 拉取失败 3" in md
    assert "计入「获取失败/数据缺失」排除桶" in md


def test_hit_table_has_per_hit_data_date():
    """F2 回归：命中表必须有逐股「数据日」列（停牌/落后标的早于全池截止）。"""
    import copy

    from report_formatter import format_brief

    result = _mk_result()
    behind = copy.deepcopy(result.hits[0])
    behind.ts_code, behind.name, behind.data_date = "000001.SZ", "滞后标的", "20260810"
    result.hits.append(behind)

    text = format_brief(result)
    header = [ln for ln in text.splitlines() if ln.startswith("| 代码")][0]
    assert "数据日" in header
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "20260810" in ln]
    assert rows, "逐股数据日须出现在行内，不能只写在说明里"


def test_data_as_of_label_is_pool_scope():
    """F2 文案：全池截止日须标明是**全池**口径，不冒充逐股价格时点。"""
    from report_formatter import _data_as_of_label

    label = _data_as_of_label(_mk_result())
    assert "全池最新 bar" in label
    assert "逐股数据日见「数据日」列" in label


def test_cli_rejects_non_finite_and_out_of_range(tmp_path):
    """F3 回归：非有限/越界参数在入口报错（引擎与报告共用有效性规则）。"""
    cases = [
        ["--gap-min-vol-ratio", "inf"],
        ["--gap-min-vol-ratio", "nan"],
        ["--gap-min-vol-ratio", "-0.5"],
        ["--gap-min-pct", "inf"],
        ["--gap-min-pct", "-1"],
        ["--gap-lookback", "0"],
        ["--min-avg-amount", "-1"],
        ["--universe-limit", "0"],
    ]
    for extra in cases:
        r = subprocess.run(
            [sys.executable, str(_SCAN_PY), "--no-save-report", *extra],
            capture_output=True, text=True, check=False, timeout=60,
        )
        assert r.returncode == 2, f"{extra} 应被 argparse 拒绝，实际 rc={r.returncode}"
        assert "error" in r.stderr.lower() or "必须" in r.stderr


def test_vol_threshold_renderer_agrees_with_engine_for_accepted_values():
    """F3 一致性：CLI 接受的量比门槛，渲染层必须显示（不得隐藏生效门槛）。"""
    import math as _math

    from report_formatter import _vol_threshold_active

    for v in (0.1, 0.5, 1.0, 1.5, 100.0):
        engine_active = not _math.isclose(v, 1.0, abs_tol=1e-9)
        assert _vol_threshold_active(v) is engine_active, f"量比 {v} 引擎/渲染判据不一致"
    # inf/nan 已在 CLI 入口被拒（上一条用例），此处渲染层的有限性门是纵深防御
    assert _vol_threshold_active(float("inf")) is False
    assert _vol_threshold_active(float("nan")) is False


def test_no_cache_branch_shows_counts_not_plan_only():
    """评审续（2026-09-23）：--no-cache 分支不得只写「全部重新拉取」。

    「全部重拉」是计划；实际成功/失败必须分别列出，否则禁用缓存的运行同样
    会把拉取失败的标的写成已拉取（与启用分支的 F1 同一缺陷形态）。
    """
    import tempfile
    from pathlib import Path

    from report_formatter import format_brief, format_markdown_report

    result = _mk_result()
    result.cache_status = {
        "enabled": False, "hit": 0, "planned_fetch": 480, "fetched": 477,
        "fetch_failed": 3, "ttl_days": 3.0,
        "min_age_hours": None, "max_age_hours": None,
    }

    brief = format_brief(result)
    assert "已禁用（--no-cache）" in brief
    assert "计划重拉 480" in brief and "新拉 477" in brief and "拉取失败 3" in brief
    assert "全部重新拉取" not in brief  # 固定文案不得残留

    out = Path(tempfile.mkdtemp()) / "r.md"
    format_markdown_report(result, str(out))
    md = out.read_text(encoding="utf-8")
    assert "计划全部重拉 480 只 / 新拉 477 / 拉取失败 3（计入「获取失败/数据缺失」排除桶）" in md


def test_no_cache_branch_without_failures():
    """--no-cache 且全部成功：仍给计划数与成功数，不显示失败项。"""
    from report_formatter import format_brief

    result = _mk_result()
    result.cache_status = {
        "enabled": False, "hit": 0, "planned_fetch": 12, "fetched": 12,
        "fetch_failed": 0, "ttl_days": 3.0,
        "min_age_hours": None, "max_age_hours": None,
    }
    brief = format_brief(result)
    assert "计划重拉 12 / 新拉 12" in brief
    assert "拉取失败" not in brief


def test_scan_no_cache_reports_plan_and_failures(tmp_path, monkeypatch, capsys):
    """集成：--no-cache 下 cache_status 同时给出计划数、成功数与失败数。

    桩源只返回 000001.SZ 的日线（000002.SZ 无数据 → 拉取失败），故
    计划 2 / 新拉 1 / 拉取失败 1——三者必须都能在报告里读到。
    """
    import json as _json

    import pandas as _pd

    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260101", "20260102"], True))

    stocks = [_StubStock("000001.SZ"), _StubStock("000002.SZ")]
    args = mod.build_parser().parse_args(
        ["--json", "--no-save-report", "--no-cache"])
    rc = mod._run_scan(
        args, stocks, {"000001.SZ", "000002.SZ"}, _PartialSource(),
        "stub (前复权)", True,  # already_qfq：桩源给的就是前复权价
        0.0,
    )

    payload = _json.loads(capsys.readouterr().out)
    cache = payload["cache_status"]
    assert rc == 0
    assert cache["enabled"] is False
    assert cache["planned_fetch"] == 2
    assert cache["fetched"] == 1
    assert cache["fetch_failed"] == 1


def test_scan_no_cache_all_failed_returns_early_without_report(
    tmp_path, monkeypatch, capsys,
):
    """--no-cache 且全部拉取失败：明确失败退出（rc=1），不产出报告。

    这条钉住行为边界：全失败时引擎走 `未获取到任何日线数据` 早退，
    不打印 JSON / 不落盘——宁可不产出，也不出一份没有任何数据的报告。
    """
    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260101", "20260102"], True))

    stocks = [_StubStock("000001.SZ"), _StubStock("000002.SZ")]
    args = mod.build_parser().parse_args(
        ["--json", "--no-save-report", "--no-cache"])
    rc = mod._run_scan(
        args, stocks, {"000001.SZ", "000002.SZ"}, _StubSource(),
        "stub (前复权)", False, 0.0,
    )

    assert rc == 1
    assert capsys.readouterr().out.strip() == ""


# ======================================================================
# 评审续（2026-09-23）：成分股池来源不得默认写成首选源
# ======================================================================


def _md_field_sources(result) -> str:
    import tempfile
    from pathlib import Path

    from report_formatter import format_markdown_report

    out = Path(tempfile.mkdtemp()) / "r.md"
    format_markdown_report(result, str(out))
    md = out.read_text(encoding="utf-8")
    row = [ln for ln in md.splitlines() if ln.startswith("| 成分股池 |")][0]
    return row


def test_universe_source_unknown_when_cache_lacks_provenance():
    """无 provenance（缓存复用且无 sidecar）→ 明确写「未记录」，不回落首选源。"""
    result = _mk_result()
    result.params.pop("universe_provenance", None)
    row = _md_field_sources(result)
    assert "未记录" in row
    assert "index_stock_cons" not in row, "不得默认写成首选源"


def test_universe_source_unknown_for_legacy_empty_provenance():
    """provenance 存在但 per_index 为空（旧缓存复用）→ 同样写「未记录」。"""
    result = _mk_result()
    result.params["universe_provenance"] = {"from_cache": True, "per_index": {}}
    row = _md_field_sources(result)
    assert "未记录" in row
    assert "index_stock_cons" not in row


def test_universe_source_reports_actual_fallback_chain():
    """来源按指数逐项披露（含降级到 sina / Tushare / 未纳入）。"""
    result = _mk_result()
    result.params["universe_provenance"] = {
        "from_cache": False,
        "per_index": {
            "csi300": "akshare index_stock_cons_sina",
            "a500": "akshare index_stock_cons",
            "star50": "Tushare index_weight",
        },
    }
    row = _md_field_sources(result)
    assert "akshare index_stock_cons_sina（沪深300）" in row
    assert "akshare index_stock_cons（中证A500）" in row
    assert "Tushare index_weight（科创50）" in row


def test_universe_source_marks_total_failure_index():
    """全部源失败的指数须显式标注「未纳入池」，不能静默消失。"""
    result = _mk_result()
    result.params["universe_provenance"] = {
        "from_cache": False,
        "per_index": {"csi300": "akshare index_stock_cons", "star50": None},
    }
    row = _md_field_sources(result)
    assert "全部源失败未纳入池（科创50）" in row


def test_universe_source_marks_cache_reuse():
    """来源取自 sidecar 时注明本次是复用缓存。"""
    result = _mk_result()
    result.params["universe_provenance"] = {
        "from_cache": True,
        "per_index": {"csi300": "akshare index_stock_cons"},
    }
    row = _md_field_sources(result)
    assert "复用当日成分股缓存（来源取自 sidecar）" in row


def test_scan_params_carry_universe_provenance(tmp_path, monkeypatch, capsys):
    """接线：provenance 从 build_universe 一路进 params（JSON 可读回）。"""
    import json as _json

    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260101", "20260102"], True))

    stocks = [_StubStock("000001.SZ")]
    args = mod.build_parser().parse_args(["--json", "--no-save-report", "--no-cache"])
    prov = {"from_cache": False, "per_index": {"csi300": "akshare index_stock_cons_sina"}}
    mod._run_scan(args, stocks, {"000001.SZ"}, _PartialSource(), "stub (前复权)",
                  True, 0.0, prov)

    payload = _json.loads(capsys.readouterr().out)
    assert payload["params"]["universe_provenance"] == prov


def test_scan_refreshes_cache_with_unsettled_last_bar(tmp_path, monkeypatch, capsys):
    """评审续二：缓存末根 bar 未定稿（盘中写入）→ 次日必须重拉，不得复用。

    实测缺陷：盘中 10:30 写入的缓存（末根 bar = 当日）在次日 09:00 复用，
    日期比较成立即当成已完成 bar → 用未走完的低点断言「未回补」，凭空产出命中。
    这里钉住修复后的行为：该股不计入缓存命中、计入「未定稿重拉」。
    """
    import json as _json
    import os as _os

    import pandas as _pd

    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260923", "20260924"], True))
    fixed_now = _now_day("20260924", "0900")
    monkeypatch.setattr(mod, "shanghai_now", lambda: fixed_now)

    def _kline(last_date: str):
        n = 5
        return _pd.DataFrame({
            "trade_date": [f"2026090{i}" for i in range(1, n)] + [last_date],
            "open_qfq": [10.0] * n, "high_qfq": [10.2] * n,
            "low_qfq": [9.9] * n, "close_qfq": [10.1] * n,
            "amount": [1e8] * n,
        })

    cache = mod._KLINE_CACHE
    # ① 已定稿：末根 bar 20260922，缓存在该 bar 收盘后（16:00）写入
    cache.save("kline", ("tushare", "000001.SZ"), _kline("20260922"))
    closed = _now_day("20260922", "1600").timestamp()
    p1 = cache.path_for("kline", ("tushare", "000001.SZ"))
    _os.utime(p1, (closed, closed))
    # ② 未定稿：末根 bar 20260923，但缓存写在**该 bar 盘中** 10:30
    cache.save("kline", ("tushare", "000002.SZ"), _kline("20260923"))
    intraday = _now_day("20260923", "1030").timestamp()
    p2 = cache.path_for("kline", ("tushare", "000002.SZ"))
    _os.utime(p2, (intraday, intraday))

    stocks = [_StubStock("000001.SZ"), _StubStock("000002.SZ")]
    args = mod.build_parser().parse_args(["--json", "--no-save-report"])
    rc = mod._run_scan(args, stocks, {"000001.SZ", "000002.SZ"}, _StubSource(),
                       "stub (前复权)", False, 0.0)

    payload = _json.loads(capsys.readouterr().out)
    cache_status = payload["cache_status"]
    assert rc == 0
    assert cache_status["hit"] == 1, "只有已定稿的缓存可复用"
    assert cache_status["refreshed_unsettled"] == 1
    assert cache_status["fetch_failed"] == 1  # 重拉时桩源无数据
    # 未定稿那只不得出现在命中里（它已被排除在复用之外）
    assert all(h["ts_code"] != "000002.SZ" for h in payload["hits"])


def _now_day(ymd: str, hhmm: str):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.strptime(f"{ymd}{hhmm}", "%Y%m%d%H%M").replace(
        tzinfo=ZoneInfo("Asia/Shanghai"))


def test_cache_row_discloses_unsettled_refresh():
    """未定稿重拉须在报告里说明原因（不混同于普通缓存未命中）。"""
    from report_formatter import format_brief, format_markdown_report

    import tempfile
    from pathlib import Path

    result = _mk_result()
    result.cache_status = {
        "enabled": True, "hit": 470, "planned_fetch": 10, "fetched": 9,
        "fetch_failed": 1, "refreshed_unsettled": 3, "ttl_days": 3.0,
        "min_age_hours": 0.5, "max_age_hours": 2.0,
    }
    brief = format_brief(result)
    assert "含未定稿重拉 3" in brief

    out = Path(tempfile.mkdtemp()) / "r.md"
    format_markdown_report(result, str(out))
    md = out.read_text(encoding="utf-8")
    assert "3 只为「缓存末根 bar 未定稿（写入早于当日收盘）」而重拉" in md


def test_scan_survives_malformed_cached_date(tmp_path, monkeypatch, capsys):
    """评审续三（集成）：单条坏缓存日期不得中止整次扫描。

    实测：缓存 trade_date = "20260230"（8 位但非法日历）此前在缓存校验里抛
    ValueError，冒泡出 `_run_scan` → 整个扫描中止。修复后按「未定稿」处理：
    该股重拉（记入 refreshed_unsettled），其余缓存照常命中。
    """
    import json as _json
    import os as _os

    import pandas as _pd

    from lib import env as _env

    mod = _load_scan_module()
    monkeypatch.setattr(_env, "STORE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_fetch_trade_cal", lambda a, b: (["20260923", "20260924"], True))
    monkeypatch.setattr(mod, "shanghai_now", lambda: _now_day("20260924", "0900"))

    def _kline(last_date: str):
        n = 5
        return _pd.DataFrame({
            "trade_date": [f"2026090{i}" for i in range(1, n)] + [last_date],
            "open_qfq": [10.0] * n, "high_qfq": [10.2] * n,
            "low_qfq": [9.9] * n, "close_qfq": [10.1] * n,
            "amount": [1e8] * n,
        })

    cache = mod._KLINE_CACHE
    cache.save("kline", ("tushare", "000001.SZ"), _kline("20260922"))
    closed = _now_day("20260922", "1600").timestamp()
    p1 = cache.path_for("kline", ("tushare", "000001.SZ"))
    _os.utime(p1, (closed, closed))
    cache.save("kline", ("tushare", "000002.SZ"), _kline("20260230"))  # 非法日期

    stocks = [_StubStock("000001.SZ"), _StubStock("000002.SZ")]
    args = mod.build_parser().parse_args(["--json", "--no-save-report"])
    rc = mod._run_scan(args, stocks, {"000001.SZ", "000002.SZ"}, _StubSource(),
                       "stub (前复权)", False, 0.0)   # 不得抛异常

    payload = _json.loads(capsys.readouterr().out)
    cache_status = payload["cache_status"]
    assert rc == 0
    assert cache_status["hit"] == 1
    assert cache_status["refreshed_unsettled"] == 1
