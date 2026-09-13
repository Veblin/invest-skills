"""R-C03 权重最大持仓处置效应弱提示 — 离线单测。

规格（源文档 R-C03）：portfolio 视图对**组合权重最大**持仓标注「处置效应相关弱提示」
（Sui-Wang 2025：组合权重越大处置效应越强）；**权重非随机决定——相关非因果，
不泛化到过度交易等其它偏差**；弱显著样式；**模拟/观察仓单独标注**
（stakes 低 ≠ 无偏差）。验收：渲染输出 + 文案合规（非建议）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import positions as P  # noqa: E402


def _row(sym, weight, *, name=None, kind=None, extra=None):
    r = {"symbol": sym, "name": name or sym, "weight": weight, "band": "unknown",
         "pnl_pct": None, "holding_days": 10, "note": ""}
    if kind is not None:
        r["kind"] = kind
    if extra:
        r.update(extra)
    return r


def test_hint_targets_max_weight_row():
    rows = [_row("600176", 0.2), _row("000612", 0.5), _row("600036", 0.3)]
    h = P.disposition_hint(rows)
    assert h is not None
    assert h["symbol"] == "000612", "须指向权重最大者"


def test_hint_is_correlational_not_causal():
    """文案硬约束：**相关非因果**，且不泛化到其它偏差。"""
    h = P.disposition_hint([_row("600176", 0.4), _row("000612", 0.6)])
    note = h["note"]
    assert "相关非因果" in note or "非因果" in note
    assert "不泛化" in note or "不推广" in note


@pytest.mark.parametrize("banned", ["建议减持", "建议加仓", "应减仓", "应加仓", "减仓", "加仓"])
def test_hint_has_no_advice_language(banned):
    h = P.disposition_hint([_row("600176", 0.4), _row("000612", 0.6)])
    assert banned not in h["note"]


def test_hint_cites_literature():
    h = P.disposition_hint([_row("600176", 0.4), _row("000612", 0.6)])
    assert "Sui-Wang" in h["note"]


def test_no_hint_when_weights_missing():
    """权重全缺 → None（不给提示，也不臆造「最大」）。"""
    assert P.disposition_hint([_row("600176", None), _row("000612", None)]) is None
    assert P.disposition_hint([]) is None


def test_no_hint_when_weights_all_zero():
    assert P.disposition_hint([_row("600176", 0), _row("000612", 0)]) is None


def test_weight_string_form_supported():
    """权重可为 fraction(0.4) 或 '40%' 字符串——最大者判定须在同一口径下比较。"""
    h = P.disposition_hint([_row("600176", "40%"), _row("000612", 0.6)])
    assert h["symbol"] == "000612"


def test_paper_position_gets_separate_note():
    """模拟/观察仓**单独标注**——stakes 低 ≠ 无偏差。"""
    h = P.disposition_hint([_row("600176", 0.4), _row("000612", 0.6, kind="模拟")])
    assert h["paper"] is True
    assert "stakes 低 ≠ 无偏差" in h["note"] or "stakes" in h["note"]


def test_real_position_has_no_paper_note():
    h = P.disposition_hint([_row("600176", 0.4), _row("000612", 0.6)])
    assert h["paper"] is False


def test_table_renders_hint_as_weak_signal():
    """渲染须为**弱显著**样式（与 ⚠️ 强信号区分），且不写进某一行单元格。"""
    rows = [_row("600176", 0.4), _row("000612", 0.6)]
    text = P.position_table(rows)
    assert "处置效应相关" in text and "弱提示" in text
    hint_lines = [l for l in text.splitlines() if "处置效应" in l]
    assert len(hint_lines) == 1
    stripped = hint_lines[0].strip()
    assert stripped.startswith("*") or stripped.startswith(">"), \
        f"弱提示须为斜体/引用样式，实得：{stripped[:40]!r}"
    assert "⚠️" not in hint_lines[0], "弱提示不得用强信号样式"
    assert "600176" not in hint_lines[0], "提示不应混入表格单元格"


def test_table_without_hint_still_renders():
    text = P.position_table([_row("600176", None)])
    assert "位置状态表仅描述持仓事实" in text
    assert "处置效应" not in text


# ── 轮末评审修复（2026-09-13）─────────────────────────────────────────────

def test_bare_integer_weight_uses_same_caliber_as_renderer():
    """裸整数权重须与 `_fmt_weight` **同口径归一**（40 → 0.40）。

    ⚠️ 原实现 `_weight_num` 直接 `float(v)` → 40.0，而渲染侧把 40 显示成 40%
    → `disposition_hint` 会挑**错**「权重最大」持仓（拿 40.0 比 0.6），
    并返回 100 倍权重（消费方 `{:.0%}` 会渲染成 4000%）。
    """
    h = P.disposition_hint([_row("600176", 40), _row("000612", 0.6)])
    assert h["symbol"] == "000612", "40 是 40%，小于 60% —— 最大者应是 000612"
    assert h["weight"] == 0.6, f"权重须为 fraction，实得 {h['weight']}"
    assert P.disposition_hint([_row("600176", 40)])["weight"] == 0.4


def test_weight_num_rejects_nonfinite():
    """NaN/Inf 权重不得参与「最大」比较。"""
    assert P._weight_num(float("nan")) is None
    assert P._weight_num(float("inf")) is None
    assert P._weight_num(True) is None


def test_build_rows_carries_paper_keys():
    """模拟/观察仓标识须**穿过行构造器**到达位置行（生产路径可达性）。

    ⚠️ `build_position_row` 只输出 symbol/name/weight/pnl_pct/band/holding_days/note，
    把 holdings 的 kind/account/asset_type/tag/type 全丢掉 → `_is_paper` 在**生产路径**
    上恒 False，「模拟仓单独标注」的 R-C03 分支不可达（此前只有手工构造 row 的测试
    能触发它，因此缺陷未被发现）。
    夹具用 `cost=None` → 不触发任何取价网络调用。
    """
    # 模拟仓设为**权重最大**者——提示只针对权重最大持仓，否则测不到该分支
    holdings = [{"symbol": "600176", "name": "中国巨石", "weight": 0.6, "kind": "模拟仓"},
                {"symbol": "000612", "name": "焦作万方", "weight": 0.4, "kind": "实盘"}]
    rows = P.build_position_rows_from_holdings(holdings, today="2026-09-13")
    assert [r["symbol"] for r in rows] == ["600176", "000612"], "顺序须与 holdings 对齐"
    assert rows[0]["kind"] == "模拟仓", "标识键须随行携带"
    h = P.disposition_hint(rows)
    assert h["symbol"] == "600176" and h["paper"] is True
