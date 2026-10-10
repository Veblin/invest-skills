"""zt_seesaw 跷跷板检验（合成 fixture 驱动，无网络）。

背景：v0.2.7 C1 死代码清理误删本函数（C1 仅看 Python 调用图，
invest-a-pulse/SKILL.md 文档契约未在扫描范围）→ 本文件为防复发回归测试。

A03（v0.3.2）：canonical 迁 skills/lib；zt_seesaw 改经 ``_zt_fetch_daily``
（单次拉矩阵）＋ ``_zt_flow_from_daily``/``_zt_seesaw_from_daily`` 聚合，
与 zt_rotation_bundle 复用同一路径——本文件为**journal 上下文（shim）**下的
接缝回归；请求计数/双窗口等价见 skills/lib/tests/test_zt_rotation_bundle.py。

手算锚点：10 日矩阵，每日总涨停 20 家（电子 X + 医药 20-X，X=1..10），
- 电子/AI算力 与 医药 完全负相关（r=-1.0，|r|>crit(10)=0.632 → seesaw_pairs）
- 其余簇恒为 0（无方差 → _pearson 返回 0.0，不产生显著对）
- half_split Δpp 手算：电子 first=15%（(5+10+15+20+25)/5），second=40%（(30+35+40+45+50)/5）→ Δ=+25.0
- 医药 first=85%，second=60% → Δ=-25.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import market_microstructure  # noqa: E402
from market_microstructure import zt_rotation_bundle, zt_seesaw  # noqa: E402


def _make_daily(days: int = 10):
    """构造 {日期: {行业: 家数}}；电子簇 X 递增、医药簇 20-X 递减。"""
    daily = {}
    for i in range(days):
        x = i + 1
        daily[f"202608{10 + i:02d}"] = {"半导体": x, "计算机设": 0,
                                        "化学制药": 20 - x, "中药Ⅱ": 0}
    return daily


def _patch_fetch(monkeypatch, daily: dict, *, errors: dict | None = None,
                 fatal: str | None = None):
    """替换 _zt_fetch_daily：按请求窗口返回 planned/daily/errors_by_date/fatal。"""
    planned = sorted(daily)

    def _fake(days: int):
        w = planned[-days:] if days > 0 else []
        return (w, {d: daily[d] for d in w}, dict(errors or {}), fatal)

    monkeypatch.setattr(market_microstructure, "_zt_fetch_daily", _fake)


def test_seesaw_negative_pair_and_half_split(monkeypatch):
    daily = _make_daily(10)
    _patch_fetch(monkeypatch, daily)

    r = zt_seesaw(days=10)

    assert r["available"] is True
    assert r["n_days"] == 10
    assert r["significance"] == {"n": 10, "r_crit": 0.632,
                                 "note": "双尾 p<0.05，|r|>0.632 为显著"}
    # 完全负相关对入 seesaw_pairs（r=-1.0）
    saw = [p for p in r["seesaw_pairs"] if p["a"] == "电子/AI算力"]
    assert saw and saw[0]["b"] == "医药" and saw[0]["r"] == -1.0
    # 完全正相关（医药 ↔ 电子为负对，反向无正对；常量簇 0 不入队）
    assert not r["sync_pairs"]
    # half_split Δpp 手算锚点
    rows = {x["cluster"]: x for x in r["half_split"]["rows"]}
    assert rows["电子/AI算力"]["first_half_share"] == 15.0
    assert rows["电子/AI算力"]["second_half_share"] == 40.0
    assert rows["电子/AI算力"]["delta_pp"] == 25.0
    assert rows["医药"]["delta_pp"] == -25.0
    assert r["_errors"] == []


def test_seesaw_available_false_when_flow_unavailable(monkeypatch):
    _patch_fetch(monkeypatch, {}, errors={"20260810": "20260810: ProxyError"})

    r = zt_seesaw()
    assert r["available"] is False
    assert "东财涨停池不可用，跷跷板检验跳过" in r["_errors"]


def test_seesaw_available_false_when_sample_too_small(monkeypatch):
    daily = _make_daily(6)
    _patch_fetch(monkeypatch, daily)

    r = zt_seesaw()
    assert r["available"] is False
    assert "样本不足: 6 日 < 10，跳过" in r["_errors"]


def test_seesaw_available_false_when_daily_empty(monkeypatch):
    _patch_fetch(monkeypatch, {})

    r = zt_seesaw()
    assert r["available"] is False
    assert "东财涨停池不可用，跷跷板检验跳过" in r["_errors"]


def test_journal_context_bundle_reachable_via_shim(monkeypatch):
    """journal 上下文（shim → 公共 canonical）同样可取 zt_rotation_bundle。"""
    import types

    daily = _make_daily(10)
    _patch_fetch(monkeypatch, daily)
    bundle = zt_rotation_bundle(short_days=5, long_days=10)
    assert bundle["rotation"]["available"] is True
    assert bundle["seesaw"]["available"] is True
    assert isinstance(sys.modules["market_microstructure"], types.ModuleType)
