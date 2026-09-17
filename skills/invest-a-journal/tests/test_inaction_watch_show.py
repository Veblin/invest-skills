"""journal show：未行动观察（R-C04）条目渲染。

`evaluation_json.inaction_watch` 在 SKILL.md 中是 **TEXT 直通**（无写入侧校验），
`backfill` 的约定形态是 `null` 或 `{"as_of","price","pct_change"}`。LLM 写入偏离
约定形态（如字符串「待补」）时，`show` 不得崩在 `.get` 上中断整条渲染。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

import journal  # noqa: E402


def _entry_with_backfill(backfill) -> dict:
    return {
        "id": 1, "symbol": "300308", "direction": "buy", "asset_type": "stock",
        "created_at": "2026-09-01 10:00",
        "evaluation_json": json.dumps({
            "inaction_watch": [{
                "symbol": "600176", "observed_at": "2026-08-20",
                "skip_reason": "涨幅已大", "skip_kind": "追高恐惧",
                "price_at_observation": 12.3,
                "backfill": backfill,
                "review_class": None,
            }],
        }, ensure_ascii=False),
    }


@pytest.mark.parametrize("bad", ["待补", ["x"], 3, True])
def test_show_survives_non_dict_backfill(monkeypatch, capsys, bad):
    """形态异常 → 照常渲染该条并**外显**异常，不得中断其后内容。"""
    monkeypatch.setattr(journal, "get_journal", lambda jid: _entry_with_backfill(bad))
    rc = journal.cmd_show(1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "600176" in out, "条目须照常渲染（异常字段不得中断整条）"
    assert "创建时间" in out, "异常字段不得中断该条之后的内容"
    assert "格式异常" in out, "字段形态异常须外显，不得伪装成「待补」"


def test_show_renders_wellformed_backfill(monkeypatch, capsys):
    """约定形态照常渲染（守卫不得误伤正常路径）。"""
    monkeypatch.setattr(journal, "get_journal", lambda jid: _entry_with_backfill(
        {"as_of": "2026-09-10", "price": 15.2, "pct_change": 23.6}))
    rc = journal.cmd_show(1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "2026-09-10" in out and "15.2" in out


@pytest.mark.parametrize("pending", [None, {}])
def test_show_renders_pending_backfill(monkeypatch, capsys, pending):
    """`null` / 空对象 = 待补（既有语义保持，守卫不得误伤）。"""
    monkeypatch.setattr(journal, "get_journal", lambda jid: _entry_with_backfill(pending))
    rc = journal.cmd_show(1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "回填：待补" in out
