"""A03 R4：涨停双窗口复用（zt_rotation_bundle）——请求计数 + 冻结等价反例。

证明（mock 按日期计数，无网络）：
1. 旧编排 `zt_industry_flow(10)` + `zt_seesaw(30)` = 40 次 ``stock_zt_pool_em``
   日请求；`zt_rotation_bundle(10, 30)` = 30 次（同一日历窗口只拉一次）；
2. 同 mock 输入下 `bundle["rotation"]` 与 `zt_industry_flow(10)`、
   `bundle["seesaw"]` 与 `zt_seesaw(30)` **逐字段相等**（含 `_errors`）；
3. 短窗按**长窗末 10 个计划交易日**取：失败日只在计划窗内缺位，
   不得用「最后 10 个成功日」补位（反例：抽掉计划窗内一日 → covered 9 日，
   计划窗前的日期不得进入短窗）。
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pandas as pd
import pytest

_SKILLS_LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILLS_LIB))  # 无条件插 0：防其他 skill 目录先行入 path 遮蔽同名模块

import market_microstructure as mm  # noqa: E402

# 40 个「计划交易日」：字典序即日期序；全部 <= 今日（shanghai_today 真实值 20xx）
_CAL = [f"202601{i:02d}" for i in range(1, 41)]
_INDUSTRIES = ("半导体", "化学制药", "元件", "软件开发", "证券Ⅱ")


class _FakeAk:
    """akshare 替身：记录按日期的涨停池请求；日历返回 40 日。"""

    def __init__(self, fail_dates: set[str] | None = None):
        self.calls: list[str] = []
        self.fail_dates = set(fail_dates or ())

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({"trade_date": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in _CAL]})

    def stock_zt_pool_em(self, date: str):
        self.calls.append(date)
        if date in self.fail_dates:
            raise RuntimeError("eastmoney blocked")
        # 行业家数随日期变化（保证簇占比有方差 → 相关系数有意义）
        i = _CAL.index(date)
        rows = []
        for k, ind in enumerate(_INDUSTRIES):
            for _ in range(1 + (i + k) % 4):
                rows.append({"代码": f"{k}{i:02d}", "所属行业": ind})
        return pd.DataFrame(rows)


@pytest.fixture
def fake_ak(monkeypatch):
    fake = _FakeAk()
    monkeypatch.setitem(sys.modules, "akshare", fake)
    # 全 mock 场景：跳过东财入口限流/代理 scope（本测只数请求与对齐口径）
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)
    return fake


def test_bundle_halves_request_count(fake_ak):
    """40 → 30：两条旧命令各自拉窗，bundle 单次拉长窗。"""
    fake_ak.calls.clear()
    mm.zt_industry_flow(10)
    n_short = len(fake_ak.calls)
    short_dates = list(fake_ak.calls)
    fake_ak.calls.clear()
    mm.zt_seesaw(30)
    n_long = len(fake_ak.calls)
    long_dates = list(fake_ak.calls)
    fake_ak.calls.clear()
    mm.zt_rotation_bundle(10, 30)
    n_bundle = len(fake_ak.calls)

    assert n_short == 10 and n_long == 30
    assert n_short + n_long == 40, "旧编排应为 40 次日请求"
    assert n_bundle == 30, f"bundle 应为 30 次（长窗单拉），实为 {n_bundle}"
    assert fake_ak.calls == long_dates  # 只拉长窗计划交易日（按序、无重复）
    assert short_dates == long_dates[-10:]
    assert len(set(fake_ak.calls)) == 30  # 无重复日


def test_bundle_outputs_equal_standalone_calls(fake_ak):
    """冻结等价：bundle 两子结果与独立函数逐字段相等（含 _errors）。"""
    rotation = mm.zt_industry_flow(10)
    seesaw = mm.zt_seesaw(30)
    # 注意：先算独立函数，再算 bundle（同一 fake 输入，无状态）
    bundle = mm.zt_rotation_bundle(10, 30)

    assert bundle["rotation"] == rotation
    assert bundle["seesaw"] == seesaw
    assert bundle["planned_dates"] == _CAL[-30:]
    assert bundle["short_window_dates"] == _CAL[-10:]
    assert bundle["short_days"] == 10 and bundle["long_days"] == 30


def test_short_window_uses_planned_days_not_last_successful(monkeypatch):
    """失败日反例：短窗 = 计划窗内成功日，不得用「最后 10 个成功日」补位。"""
    fake = _FakeAk(fail_dates={_CAL[-3]})  # 计划窗末 10 日内的失败日
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)

    bundle = mm.zt_rotation_bundle(10, 30)
    rot = bundle["rotation"]
    assert rot["available"] is True
    # 计划窗 = 末 10 个计划交易日；其中 1 日失败 → 覆盖 9 日
    assert rot["covered_dates"] == [d for d in _CAL[-10:] if d != _CAL[-3]]
    assert len(rot["covered_dates"]) == 9
    # 「最后 10 个成功日」会把 _CAL[-11] 补进来 —— 必须不出现
    assert _CAL[-11] not in rot["covered_dates"]
    # 与单独调用 zt_industry_flow(10) 等价（同一失败模式）
    assert rot == mm.zt_industry_flow(10)
    # 失败记录逐条保留（短窗窗口内），且窗口外失败不进短窗 _errors
    assert any(_CAL[-3] in e for e in rot["_errors"])
    assert bundle["seesaw"] == mm.zt_seesaw(30)


def test_outer_window_failure_kept_in_seesaw_not_short(monkeypatch):
    """窗口外失败日：短窗不越窗带入（同窗口等价）；长窗缺位语义保持。"""
    fake = _FakeAk(fail_dates={_CAL[-12]})  # 在长窗内、短窗（末 10 日）外
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)

    bundle = mm.zt_rotation_bundle(10, 30)
    # 短窗窗口内无失败 → 短窗 _errors 为空（失败日属长窗，不越窗带入）
    assert bundle["rotation"]["_errors"] == []
    # 长窗侧：失败日在 dates/covered 中缺位，且失败记录**保留**（A-F02）
    assert _CAL[-12] not in bundle["seesaw"]["dates"]
    assert bundle["seesaw"]["n_days"] == 29
    assert any(_CAL[-12] in e for e in bundle["seesaw"]["_errors"])
    assert any(_CAL[-12] in e for e in bundle["_errors"])  # 顶层长窗失败记录
    # 等价性同时成立：短窗与独立 10 日调用同错；长窗与独立 30 日调用同错
    assert bundle["rotation"] == mm.zt_industry_flow(10)
    assert bundle["seesaw"] == mm.zt_seesaw(30)


def test_long_window_failure_outside_short_survives_in_bundle(monkeypatch):
    """A-F02 负责人 probe：长窗 3 日、首日失败且在短窗外、其余有效，bundle(1,3,min_days=2)。

    修复前：rotation._errors=[]、seesaw.available=True、seesaw._errors=[]、
    顶层无失败字段 → 整个组合无失败记录。修复后：可用结果保留 + 失败记录不丢。
    """
    fake = _FakeAk(fail_dates={_CAL[-3]})  # 长窗首日（短窗 = 仅 _CAL[-1]）
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)

    bundle = mm.zt_rotation_bundle(1, 3, min_days=2)
    assert bundle["planned_dates"] == _CAL[-3:]
    assert bundle["short_window_dates"] == _CAL[-1:]
    assert bundle["rotation"]["_errors"] == []          # 失败日在短窗外
    assert bundle["seesaw"]["available"] is True        # 可用结果保留（partial）
    assert bundle["seesaw"]["n_days"] == 2
    assert any(_CAL[-3] in e for e in bundle["seesaw"]["_errors"])  # 长窗失败可见
    assert any(_CAL[-3] in e for e in bundle["_errors"])            # 顶层失败字段存在
    # 同族独立函数同步（zt_seesaw 成功分支同携带窗口失败记录）
    assert mm.zt_seesaw(3, min_days=2) == bundle["seesaw"]


def test_seesaw_carries_fetch_errors_on_success(monkeypatch):
    """A-F02 同族：zt_seesaw 成功分支携带逐日失败记录（旧成功分支恒空）。"""
    fake = _FakeAk(fail_dates={_CAL[-2]})
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)

    r = mm.zt_seesaw(5, min_days=2)
    assert r["available"] is True
    assert any(_CAL[-2] in e for e in r["_errors"])
    assert _CAL[-4] not in r["_errors"][0]  # 记录内容确为失败日


def test_bundle_degrade_when_calendar_fails(monkeypatch):
    """日历失败 → 两子结果 available=False + fatal 保留，不抛出。"""

    class _BoomAk:
        def tool_trade_date_hist_sina(self):
            raise ConnectionError("calendar down")

    monkeypatch.setitem(sys.modules, "akshare", _BoomAk())
    monkeypatch.setattr(mm, "akshare_direct_session", contextlib.nullcontext)
    bundle = mm.zt_rotation_bundle(10, 30)
    assert bundle["rotation"]["available"] is False
    assert bundle["seesaw"]["available"] is False
    assert any("fatal" in e for e in bundle["rotation"]["_errors"])
    assert "东财涨停池不可用，跷跷板检验跳过" in bundle["seesaw"]["_errors"]
