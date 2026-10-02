"""issue #34 复检回归：`market-status` 展示/输出不得引用不可引用的已存基差。

背景（2026-10-01 独立验收报告 P1）：生产库最新快照的 `env_label.capital_flow`
仍含无日期的「IC 基差 -0.51%」，且 `market-status`（读取模式默认优先持久化快照）
会直接展示它。本文件走**真实命令入口** `cmd_market_status`：
latest_snapshot → `_print_env_labels` / `--json` 两个出口。

覆盖：缺日期（生产库 18 行形态）、陈旧日期、新鲜成对值正例、JSON 输出同边界。
不联网：读取模式命中持久化快照即不触发实时采集；新鲜正例 lag=0 不查日历。
"""
from __future__ import annotations

import json
from argparse import Namespace

import pytest


@pytest.fixture
def stock_db(isolated_store):
    """隔离库（stock conftest 的 isolated_store：_db_override + init_db）。"""
    return isolated_store


def _seed(store_mod, date: str, pct, basis_date, cap_text: str | None) -> None:
    c = store_mod.connect_db(store_mod.get_db_path())
    try:
        env = {"capital_flow": cap_text, "leverage": "中性", "summary": "正常"} if cap_text else None
        c.execute(
            "INSERT INTO market_snapshots "
            "(date, ad_ratio, futures_basis_pct, futures_basis_date, env_label) "
            "VALUES (?, 1.0, ?, ?, ?)",
            (date, pct, basis_date, json.dumps(env, ensure_ascii=False) if env else None),
        )
        c.commit()
    finally:
        store_mod.safe_close(c)


def _args(*, json_mode: bool = False) -> Namespace:
    return Namespace(save=False, json=json_mode, days=5, industry="")


def test_undated_legacy_label_not_displayed(stock_db, capsys):
    """生产库形态：基差无数据日期 + 已存标签含无 as-of 子句 → 展示层两者同边界。"""
    import invest  # noqa: F401 —— 先导入以装配 journal lib 路径（import 顺序契约）

    _seed(stock_db, "20260928", -0.5072, None, "北向数据暂不可用；IC 基差 -0.51%")
    rc = invest.cmd_market_status(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "IC 基差" not in out
    assert "北向数据暂不可用" in out


def test_stale_dated_label_not_displayed(stock_db, capsys, monkeypatch):
    """陈旧日期（滞后 > 阈值交易日）→ 同样禁用（含 as-of 的旧子句也不得展示）。"""
    import invest  # noqa: F401
    import market_microstructure as mm
    import lib.trade_cal as tc

    monkeypatch.setattr(mm, "shanghai_session_date", lambda: "20260810")
    monkeypatch.setattr(tc, "fetch_trade_cal", lambda a, b: ([f"d{i}" for i in range(40)], False))
    _seed(stock_db, "20260810", -0.5072, "20260701",
          "北向数据暂不可用；IC 基差 -0.51%（截至 2026-07-01）")
    rc = invest.cmd_market_status(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "IC 基差" not in out


def test_fresh_pair_label_displayed_with_asof(stock_db, capsys, monkeypatch):
    """新鲜成对值正例：不得误伤——标签照常展示且带 as-of（lag=0，不查日历）。"""
    import invest  # noqa: F401
    import market_microstructure as mm

    monkeypatch.setattr(mm, "shanghai_session_date", lambda: "20260810")
    _seed(stock_db, "20260810", -0.6050, "20260810",
          "北向数据暂不可用；IC 基差 -0.61%（截至 2026-08-10）")
    rc = invest.cmd_market_status(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "IC 基差 -0.61%（截至 2026-08-10）" in out


def test_json_mode_label_text_same_boundary(stock_db, capsys):
    """`--json` 出口同样不得携带已存旧基差文本（值 None 与标签文本同一边界）。"""
    import invest  # noqa: F401

    _seed(stock_db, "20260928", -0.5072, None, "北向数据暂不可用；IC 基差 -0.51%")
    rc = invest.cmd_market_status(_args(json_mode=True))
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["futures_basis_pct"] is None
    assert "不可引用" in payload["futures_basis_note"]
    cap = json.loads(payload["env_label"])["capital_flow"]
    assert "IC 基差" not in cap
    assert "北向数据暂不可用" in cap


def test_json_mode_null_value_residual_label_text_stripped(stock_db, capsys):
    """P2 边界（2026-10-02 复验发现）：值列已为 NULL 而旧标签残留基差文字 →
    JSON 出口同样不得携带；值本就为空 → 不新增 note（北向段保留）。"""
    import invest  # noqa: F401

    _seed(stock_db, "20260928", None, None, "北向数据暂不可用；IC 基差 -0.51%")
    rc = invest.cmd_market_status(_args(json_mode=True))
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["futures_basis_pct"] is None
    assert "futures_basis_note" not in payload
    cap = json.loads(payload["env_label"])["capital_flow"]
    assert "IC 基差" not in cap
    assert "北向数据暂不可用" in cap
