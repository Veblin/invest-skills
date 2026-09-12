"""v2 池模式测试（离线）：清单解析 / 批次 diff / 状态读写 / 采集信号 / 渲染 / CLI 端到端。

D13 注：``uc.fetch_symbol_unlocks`` 是模块级导入引用——mock 必须打在调用方模块
（unlock_calendar）命名空间；unlock_source 源模块不在本文件 mock。
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import unlock_calendar as uc  # noqa: E402


# ── 清单解析 ─────────────────────────────────────────────────────────────

def test_parse_pool_comments_badlines_dedupe(tmp_path):
    p = tmp_path / "pool.txt"
    p.write_text("# 持仓池\n600176\n\n002466  # 天齐\nbad123\n12345\n600176\n",
                 encoding="utf-8")
    syms, bad = uc.parse_pool(p)
    assert syms == ["600176", "002466"]      # 去重保序
    assert len(bad) == 2 and bad[0].startswith("L5") and bad[1].startswith("L6")


# ── 批次 diff ────────────────────────────────────────────────────────────

def test_diff_batches_added_removed_changed():
    old = {"2026-11-05": {"qty_yi": 3.2, "holders": 5, "kind": "定增"}}
    new = {"2026-11-05": {"qty_yi": 3.2, "holders": 5, "kind": "定增"},
           "2026-12-01": {"qty_yi": 1.0, "holders": None, "kind": "首发"}}
    assert uc.diff_batches(old, new) == {"added": ["2026-12-01"], "removed": [],
                                         "changed": []}
    changed = {"2026-11-05": {"qty_yi": 4.0, "holders": 5, "kind": "定增"}}
    assert uc.diff_batches(old, changed)["changed"] == ["2026-11-05"]
    assert uc.diff_batches(old, {})["removed"] == ["2026-11-05"]


def test_trading_days_until_far_field():
    """远场（>365 自然日）→ 自然日 + degraded（防交易日历覆盖截断，2026-09-10 实测缺陷）。"""
    lag, degraded = uc._trading_days_until("2030-07-01", _dt.date(2026, 9, 10))
    assert degraded is True and lag == (_dt.date(2030, 7, 1) - _dt.date(2026, 9, 10)).days
    bad_lag, bad_deg = uc._trading_days_until("not-a-date", _dt.date(2026, 9, 10))
    assert bad_lag is None and bad_deg is True


def test_trading_days_until_estimated_calendar_marked(monkeypatch):
    """估算日历（无 token/取数失败）→ 距今列标「自然日粗判」，不得给精确交易日数。

    降级标记依赖 freshness 透传 is_estimated（曾被丢弃）：否则「距今(交易日)」列
    在无 token 部署下以估算值冒充交易日数且无任何标注。
    """
    import lib.trade_cal as tc

    monkeypatch.setattr(tc, "fetch_trade_cal", lambda s, e: (["20260907"], True))
    lag, degraded = uc._trading_days_until("2026-09-16", _dt.date(2026, 9, 10))
    assert degraded is True
    assert lag == 6          # 自然日差，非估算日历给出的交易日数


def test_diff_batches_none_none_is_equal():
    """双侧 None（如股东数不可得）不得判为变化（区别于 freshness NULL 语义）。"""
    same = {"2026-11-05": {"qty_yi": 3.2, "holders": None, "kind": ""}}
    assert uc.diff_batches(same, dict(same))["changed"] == []


# ── 状态读写 ─────────────────────────────────────────────────────────────

def test_diff_batches_ignores_expired_past_batches():
    """早于 not_before 的批次从（严格前向的）抓取窗消失属正常，不得报「消失」。

    回归：解禁日过后的首次运行会把**真实发生过**的解禁渲染为「❌ 消失｜上次运行
    曾出现」，读起来像记录被撤回，并淹没真正的新增/临近提醒。
    """
    old = {"2026-09-20": {"qty_yi": 3.2, "holders": 5, "kind": "定增"},
           "2026-12-01": {"qty_yi": 1.0, "holders": None, "kind": "首发"}}
    new = {"2026-12-01": {"qty_yi": 1.0, "holders": None, "kind": "首发"}}
    assert uc.diff_batches(old, new, not_before="2026-09-25")["removed"] == []
    # 仍在窗口内的批次消失才是真信号
    assert uc.diff_batches(old, new, not_before="2026-09-01")["removed"] == ["2026-09-20"]


def test_collect_pool_merges_same_day_batches(monkeypatch):
    """同日多批解禁（定增 + 首发）须合并为日级总量，不得互相覆盖。

    回归：按 date 建键的 dict 推导让后一批覆盖前一批，当日解禁数量被低估。
    """
    rows = [
        {"date": "2026-11-05", "qty_yi": 3.2, "holders": 5, "kind": "定增"},
        {"date": "2026-11-05", "qty_yi": 1.0, "holders": 2, "kind": "首发"},
    ]
    monkeypatch.setattr(uc, "fetch_symbol_unlocks", lambda s, **k: (rows, None))
    out = uc.collect_pool(["600176"], lookahead_days=90, sleep_s=0)
    b = out["600176"]["batches"]["2026-11-05"]
    assert b["qty_yi"] == pytest.approx(4.2), "当日解禁数量须为各批之和"
    assert b["holders"] == 7
    assert b["kind"] == "定增+首发"


def test_collect_pool_forwards_today(monkeypatch):
    """取数窗口须按调用方给的日期（北京口径），不得回落宿主本地 `date.today()`。"""
    seen = {}

    def fake(sym, *, lookahead_days, include_past_days=0, today=None):
        seen["today"] = today
        return ([], None)

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    uc.collect_pool(["600176"], lookahead_days=90, sleep_s=0, today=_dt.date(2026, 12, 25))
    assert seen["today"] == _dt.date(2026, 12, 25), "today 未透传 → 回落宿主本地日期"


def test_cli_pool_fetches_by_beijing_date(tmp_path, monkeypatch, capsys):
    """端到端：取数用的 today 须等于 `_beijing_today()`（与报告名/提醒窗同源）。

    美西主机在「北京已是 09-12、宿主还是 09-11」时，窗口止于 host+90 而非
    北京+90 → 恰在边界的那批被静默滤掉，排雷工具给出假「无解禁记录」。
    """
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n", encoding="utf-8")
    seen = {}

    def fake(sym, **k):
        seen.setdefault("today", k.get("today"))
        return ([{"date": "2026-12-31", "qty_yi": 1.0, "holders": 1, "kind": "x"}], None)

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_beijing_today", lambda: _dt.date(2026, 12, 25))
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 0
    assert seen["today"] == _dt.date(2026, 12, 25), "取数未按北京日期（窗口会差一天）"


def test_state_roundtrip_and_corrupt(tmp_path):
    sp = tmp_path / "state.json"
    assert uc.load_state(sp) == {"updated": None, "symbols": {}}   # 不存在=首跑
    st = {"updated": "2026-09-10 10:00",
          "symbols": {"600176": {"batches": {}, "last_run": "20260910"}}}
    assert uc.save_state(sp, st) is None
    assert uc.load_state(sp)["symbols"]["600176"]["last_run"] == "20260910"
    sp.write_text("{broken", encoding="utf-8")
    assert uc.load_state(sp)["symbols"] == {}                       # 损坏=首跑


# ── 采集信号（失败与空记录必须可区分）───────────────────────────────────

def test_collect_pool_error_signal(monkeypatch):
    def fake(sym, *, lookahead_days, include_past_days=0, today=None):
        if sym == "600176":
            return ([{"date": "2026-10-01", "qty_yi": 1.5, "holders": 3,
                      "kind": "定增"}], None)
        return ([], "ConnectionError: boom")

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    out = uc.collect_pool(["600176", "600000"], lookahead_days=90, sleep_s=0)
    assert out["600176"]["error"] is None
    assert out["600176"]["batches"]["2026-10-01"]["qty_yi"] == 1.5
    assert out["600000"]["error"].startswith("ConnectionError")
    assert out["600000"]["batches"] == {}


# ── 渲染 ─────────────────────────────────────────────────────────────────

_TODAY = _dt.date(2026, 9, 10)


def _collected():
    return {
        "600176": {"batches": {"2026-09-20": {"qty_yi": 3.2, "holders": 5,
                                              "kind": "定增"}}, "error": None},
        "002466": {"batches": {}, "error": None},
        "600000": {"batches": {}, "error": "ProxyError: blocked"},
    }


def test_render_pool_alerts_changes_failures(monkeypatch):
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (2, False))
    md = uc.render_pool_md(
        _collected(), today=_TODAY, alert_days=30, lookahead_days=90,
        pool_path="pool.txt",
        changes={"600176": {"added": ["2026-09-20"], "removed": [], "changed": []},
                 "002466": {"added": [], "removed": [], "changed": []}},
        baseline_symbols=["600000"], state_updated="2026-09-10 10:00")
    assert "🔔 提醒" in md and "| 600176 | 2026-09-20 | 2 | 3.20 | 5 | 定增 |" in md
    assert "🆕 新增" in md and "首次建基线 1 个标的" in md
    assert "取数失败：ProxyError" in md          # 失败逐行标注（不静默当空）
    assert "无解禁记录（90 日内）" in md


def test_render_pool_no_changes(monkeypatch):
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (2, False))
    md = uc.render_pool_md(
        _collected(), today=_TODAY, alert_days=30, lookahead_days=90,
        pool_path="pool.txt",
        changes={"600176": {"added": [], "removed": [], "changed": []},
                 "002466": {"added": [], "removed": [], "changed": []}},
        baseline_symbols=[], state_updated="2026-09-10 10:00")
    assert "无变化" in md


# ── CLI 端到端 ───────────────────────────────────────────────────────────

def _fake_fetch_factory():
    def fake(sym, *, lookahead_days, include_past_days=0, today=None):
        if sym == "600176":
            return ([{"date": "2026-09-20", "qty_yi": 2.0, "holders": None,
                      "kind": ""}], None)
        return ([], None)

    return fake


def test_cli_pool_baseline_then_no_change(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n# c\n002466\n", encoding="utf-8")
    state = tmp_path / "state.json"
    out = tmp_path / "out"
    monkeypatch.setattr(uc, "fetch_symbol_unlocks", _fake_fetch_factory())
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (3, False))

    argv = ["unlock_calendar.py", "--pool-file", str(pool),
            "--state-file", str(state), "--out-dir", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    assert uc.main() == 0
    first = capsys.readouterr().out
    assert "池来源" in first and "🔔 提醒" in first
    assert "首次建基线 2 个标的" in first          # 首跑两次都为基线
    st = json.loads(state.read_text(encoding="utf-8"))
    assert set(st["symbols"]) == {"600176", "002466"}

    monkeypatch.setattr(sys, "argv", argv)
    assert uc.main() == 0
    second = capsys.readouterr().out
    assert "无变化" in second                      # 第二次运行无变化
    assert list(out.glob("*-pool.md")), "池报告须落盘"


def test_cli_pool_all_failed_exit3(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n002466\n", encoding="utf-8")
    monkeypatch.setattr(uc, "fetch_symbol_unlocks",
                        lambda s, **k: ([], "ProxyError: blocked"))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 3
    assert "不可得" in capsys.readouterr().err


def test_cli_rejects_alert_days_beyond_lookahead(tmp_path, monkeypatch, capsys):
    """--alert-days > --lookahead 须在参数层拒绝（默认值下亦不得放行）。

    否则提醒窗宽于任何被拉取过的窗口：窗内批次不在 batches 中，报告仍打印
    「提醒窗内无解禁批次 ✅」——自述排雷首选的技能给出假全清结论。
    """
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--alert-days", "120", "--lookahead", "90",
                                      "--no-out", "--no-state"])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == 2
    assert "--alert-days" in capsys.readouterr().err


def test_cli_pool_expired_batch_not_reported_as_removed(tmp_path, monkeypatch, capsys):
    """解禁日过后首次运行：已发生的批次不得渲染为「❌ 消失」，真消失的仍须报出。"""
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "updated": "2026-09-10 10:00",
        "symbols": {"600176": {"last_run": "20260910", "batches": {
            "2020-01-15": {"qty_yi": 3.2, "holders": 5, "kind": "定增"},   # 已过期
            "2030-11-11": {"qty_yi": 2.0, "holders": 3, "kind": "首发"},   # 真消失
            "2030-12-01": {"qty_yi": 1.0, "holders": None, "kind": "首发"},  # 仍在
        }}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(uc, "fetch_symbol_unlocks",
                        lambda s, **k: ([{"date": "2030-12-01", "qty_yi": 1.0,
                                          "holders": None, "kind": "首发"}], None))
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--state-file", str(state), "--no-out"])
    assert uc.main() == 0
    out = capsys.readouterr().out
    assert "2020-01-15" not in out, "过期批次不得出现（既非消失也非新增）"
    assert "❌ 消失" in out and "2030-11-11" in out, "真消失的批次仍须报出"
    assert "🆕 新增" not in out


def test_cli_pool_missing_file_exit2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file",
                                      str(tmp_path / "nope.txt"), "--no-out"])
    assert uc.main() == 2


def test_cli_pool_partial_failure_exit0_with_marker(tmp_path, monkeypatch, capsys):
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n600000\n", encoding="utf-8")

    def fake(sym, *, lookahead_days, include_past_days=0, today=None):
        if sym == "600176":
            return ([{"date": "2026-09-20", "qty_yi": 1.0, "holders": 1,
                      "kind": "x"}], None)
        return ([], "timeout")

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (1, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 0
    out = capsys.readouterr().out
    assert "取数失败：timeout" in out and "失败 1" in out


# ── P0-5：全池空返回（源侧空帧）不得当「无解禁」 ─────────────────────────

def test_cli_pool_all_empty_is_unavailable_and_keeps_baseline(tmp_path, monkeypatch, capsys):
    """全池**空返回且无错误** → 判不可得、**不落基线**（防假全清 + 防后续假变动）。

    回归（R0~R2 review）：东财反爬/限流会返回**空帧而不抛异常** →
    `fetch_symbol_unlocks` 返回 `([], None)` → 报告渲染「无解禁记录」= **假全清**，
    且空批次写进状态文件当新基线 → 下次 API 恢复时所有真实批次被标 🆕 新增，
    再抖动一次又被标 ❌ 消失——上游一次抽风引发大规模假变动告警。
    """
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n002466\n600000\n", encoding="utf-8")
    state = tmp_path / "state.json"
    old = {"updated": "2026-09-01 10:00",
           "symbols": {"600176": {"last_run": "20260901",
                                  "batches": {"2026-11-05": {"qty_yi": 3.2, "holders": 5,
                                                             "kind": "定增"}}}}}
    state.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", lambda s, **k: ([], None))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--state-file", str(state), "--no-out"])
    assert uc.main() == 3, "全池空返回须判不可得（不得渲染成无解禁）"
    err = capsys.readouterr().err
    assert "空" in err and "不可得" in err
    # 基线不得被空集覆写
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["symbols"]["600176"]["batches"], "空返回污染了基线（真实批次被抹掉）"


def test_cli_pool_partial_failure_does_not_wipe_empty_symbols_baseline(tmp_path, monkeypatch, capsys):
    """部分标的失败时，**空返回标的的基线不得被覆写**（R2 review P1）。

    全池空守卫要求「池内无一失败」，故一个标的失败即让守卫失效；其余标的的空批次
    照常写进状态文件 → 同一轮 `diff_batches` 就把旧基线里仍未来的批次渲染成
    「❌ 消失」，API 恢复后又全标「🆕 新增」。**部分限流是常见情形**，
    守卫须做到每标的一粒度：空结果不可验证时跳过状态写入（基线保留）。
    """
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n002466\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "updated": "2026-09-01 10:00",
        "symbols": {"002466": {"last_run": "20260901",
                               "batches": {"2026-11-05": {"qty_yi": 3.2, "holders": 5,
                                                          "kind": "定增"}}}},
    }, ensure_ascii=False), encoding="utf-8")

    def fake(sym, **k):
        if sym == "600176":
            return ([], "ProxyError: blocked")      # 部分限流：一个失败、一个空返回
        return ([], None)

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--state-file", str(state), "--no-out"])
    assert uc.main() == 0, "非全失败 → 照常出报告（不因部分失败而 exit 3）"
    cap = capsys.readouterr()
    assert "❌ 消失" not in cap.out, "空返回标的的基线被覆写 → 渲染出假「消失」"
    assert "跳过" in cap.err and "002466" in cap.err, "跳过的标的须显式披露"
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["symbols"]["002466"]["batches"], "空结果不可验证却覆写了基线"


def test_cli_pool_clean_run_still_updates_empty_symbol(tmp_path, monkeypatch, capsys):
    """**无失败**轮里空结果照常落账（守卫不得过度触发，真全清要能写进基线）。"""
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n002466\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "updated": "2026-09-01 10:00",
        "symbols": {"002466": {"last_run": "20260901",
                               "batches": {"2026-11-05": {"qty_yi": 3.2, "holders": 5,
                                                          "kind": "定增"}}}},
    }, ensure_ascii=False), encoding="utf-8")

    def fake(sym, **k):
        if sym == "600176":
            return ([{"date": "2026-11-05", "qty_yi": 1.0, "holders": 1, "kind": "x"}], None)
        return ([], None)

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--state-file", str(state), "--no-out"])
    assert uc.main() == 0
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["symbols"]["002466"]["batches"] == {}, "无失败轮的空结果须落账"


def test_cli_pool_partial_empty_still_reports(tmp_path, monkeypatch, capsys):
    """只有部分标的空返回时照常出报告（守卫不得过度触发）。"""
    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n600000\n", encoding="utf-8")

    def fake(sym, **k):
        if sym == "600176":
            return ([{"date": "2026-11-05", "qty_yi": 3.2, "holders": 5,
                     "kind": "定增"}], None)
        return ([], None)

    monkeypatch.setattr(uc, "fetch_symbol_unlocks", fake)
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--no-out", "--no-state"])
    assert uc.main() == 0
    out = capsys.readouterr().out
    assert "2026-11-05" in out


# ── P2-3：报告日期须用北京时间，而非宿主机本地时区 ─────────────────────

def test_pool_report_filename_uses_beijing_date(tmp_path, monkeypatch, capsys):
    """报告文件名/标题须按**北京日期**（同文件的状态戳已用 shanghai_now）。

    回归（R0~R2 review P2）：三处 `_dt.date.today()` 取**本地时区** → 美西机器在
    北京上午运行时，文件名与窗口都比北京日期晚一天，与同报告内的时间戳、
    以及仓库「文件名包含实际北京时间」的惯例自相矛盾。

    用与宿主不同的北京日期（2026-12-25）才能判别——若代码仍读本地日期，
    文件名会是宿主的今天。
    """
    import datetime as _d
    from zoneinfo import ZoneInfo

    import dates

    beijing = _d.datetime(2026, 12, 25, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(dates, "shanghai_now", lambda: beijing)

    pool = tmp_path / "pool.txt"
    pool.write_text("600176\n", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(uc, "fetch_symbol_unlocks",
                        lambda s, **k: ([{"date": "2026-11-05", "qty_yi": 1.0,
                                          "holders": 1, "kind": ""}], None))
    monkeypatch.setattr(uc, "_trading_days_until", lambda d, t: (5, False))
    monkeypatch.setattr(sys, "argv", ["unlock_calendar.py", "--pool-file", str(pool),
                                      "--state-file", str(tmp_path / "s.json"),
                                      "--out-dir", str(out)])
    assert uc.main() == 0
    assert list(out.glob("20261225-pool.md")), \
        f"文件名未用北京日期: {[p.name for p in out.iterdir()]}"
