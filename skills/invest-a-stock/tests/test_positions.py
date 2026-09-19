"""持仓位置状态（P-1, v0.2.9）——纯状态描述，零判断文本（隔离纪律）。

设计依据：host-docs/v0.2.9/deep-research/00-research-summary-2026-09-06.md §2-P
与 p-domain-behavioral-foundations-2026-09-05.md（成本显著性 = 偏差放大器）。
"""

from __future__ import annotations

import pytest

from lib.positions import POSITION_BANDS, PositionError, band_for_pnl, build_position_row


def test_band_for_pnl_thresholds():
    # 四档：深亏 ≤ -20% / 浅亏 (-20%, 0] / 浮盈 (0, +30%] / 浮盈厚 > +30%
    assert band_for_pnl(-0.25) == "deep_loss"
    assert band_for_pnl(-0.20) == "deep_loss"      # 边界含 -20%
    assert band_for_pnl(-0.05) == "loss"
    assert band_for_pnl(0.0) == "loss"
    assert band_for_pnl(0.15) == "gain"
    assert band_for_pnl(0.30) == "gain"
    assert band_for_pnl(0.45) == "gain_thick"
    assert band_for_pnl(None) == "unknown"


def test_band_labels_no_numeric_no_direction():
    # 隔离纪律：档位标签不含数值与方向词（无"建议/应/止损/卖/买"）
    assert POSITION_BANDS["gain_thick"] == "浮盈厚"
    assert POSITION_BANDS["deep_loss"] == "深亏"
    assert all(not any(ch.isdigit() for ch in label) for label in POSITION_BANDS.values())
    banned = ("建议", "应", "止损", "止盈", "跑", "卖", "买")
    assert all(not any(w in label for w in banned) for label in POSITION_BANDS.values())


def test_build_position_row():
    row = build_position_row(
        symbol="300308", price=135.0,
        cost=150.0, buy_date="2025-06-01", today="2026-09-06",
        name="中际旭创", weight=0.4,
    )
    assert row["symbol"] == "300308"
    assert row["name"] == "中际旭创"
    assert row["pnl_pct"] == pytest.approx(-0.10, abs=1e-6)   # 135/150 - 1
    assert row["band"] == "loss"                                # 浅亏档
    assert row["holding_days"] == pytest.approx(462, abs=2)     # 2025-06-01 → 2026-09-06
    assert row["weight"] == 0.4
    assert "cost" not in row  # 输出层不带原始成本，防渲染污染


def test_build_position_row_no_cost_uses_unknown_band():
    # 无成本（只有市值权重）时：位置状态降级为"位置不可判"，而非用现价冒充成本
    row = build_position_row(symbol="600176", price=20.0, cost=None,
                             buy_date=None, today="2026-09-06")
    assert row["band"] == "unknown" and row["pnl_pct"] is None


def test_invalid_dates_rejected():
    with pytest.raises(PositionError):
        build_position_row(symbol="x", price=1.0, cost=1.0,
                           buy_date="2025/06/01", today="2026-09-06")


def test_build_position_rows_from_holdings_with_kline(monkeypatch):
    from unittest.mock import patch
    from lib._invest_path import ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from lib import collector as col
    from lib.positions import build_position_rows_from_holdings

    with patch.object(col, "collect_kline", return_value={
        "dimension": "kline", "data": [{"trade_date": "2026-09-04", "close": 135.0}],
        "status": "available",
    }):
        rows = build_position_rows_from_holdings(
            [{"symbol": "300308", "weight": 0.4, "cost": 150.0, "buy_date": "2025-06-01"}],
            today="2026-09-05",
        )
    assert rows[0]["band"] == "loss"
    assert rows[0]["pnl_pct"] == pytest.approx(-0.10, abs=1e-6)


def test_build_position_rows_kline_failure_degrades_to_unknown():
    from unittest.mock import patch
    from lib._invest_path import ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from lib import collector as col
    from lib.positions import build_position_rows_from_holdings

    with patch.object(col, "collect_kline", side_effect=RuntimeError("net down")):
        rows = build_position_rows_from_holdings(
            [{"symbol": "600176", "weight": 0.5, "cost": 20.0, "buy_date": "2026-01-05"}],
            today="2026-09-05",
        )
    assert rows[0]["band"] == "unknown" and rows[0]["pnl_pct"] is None
    assert rows[0]["note"]


class TestReviewFixes:
    """code-review max 2026-09-06：positions 输入卫生修复回归。"""

    def test_nan_cost_rejected(self):
        import math
        with pytest.raises(PositionError, match="NaN"):
            build_position_row(symbol="x", price=10.0, cost=float("nan"),
                               buy_date=None, today="2026-09-06")

    def test_nan_price_degrades_unknown(self):
        import math
        row = build_position_row(symbol="x", price=float("nan"), cost=10.0,
                                 buy_date=None, today="2026-09-06")
        assert row["band"] == "unknown" and row["pnl_pct"] is None

    def test_nan_pnl_never_gain_thick(self):
        # NaN 比较恒 False → 旧实现落入 gain_thick（最激进档）——必须降级 unknown
        assert band_for_pnl(float("nan")) == "unknown"

    def test_future_buy_date_no_crash(self):
        row = build_position_row(symbol="x", price=10.0, cost=10.0,
                                 buy_date="2027-01-01", today="2026-09-06")
        assert row["holding_days"] is None and "buy_date" in row["note"]

    def test_fmt_weight_variants(self):
        from lib.positions import _fmt_weight
        assert _fmt_weight(0.4) == "40%"
        assert _fmt_weight("40%") == "40%"
        assert _fmt_weight(40) == "40%"          # 百分比直觉写法不渲染 4000%
        assert _fmt_weight(1.0) == "100%"
        assert _fmt_weight("N/A") == "—"
        assert _fmt_weight(None) == "—"
        assert _fmt_weight(float("nan")) == "—"

    def test_rows_no_cost_skips_network(self, monkeypatch):
        """无 cost 的行不触发 get_kline（档位恒 unknown，不付网络成本）。"""
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", side_effect=AssertionError("不应拉取")) as m:
            rows = build_position_rows_from_holdings(
                [{"symbol": "600176", "weight": 0.5}], today="2026-09-05",
            )
        assert rows[0]["band"] == "unknown"
        m.assert_not_called()

    def test_rows_dedup_fetch_once_per_symbol(self):
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", return_value={
            "dimension": "kline",
            "data": [{"trade_date": "2026-09-04", "close": 135.0}],
            "status": "available",
        }) as m:
            rows = build_position_rows_from_holdings([
                {"symbol": "300308", "cost": 150.0, "buy_date": "2025-06-01"},
                {"symbol": "300308", "cost": 120.0, "buy_date": "2026-01-05"},
            ], today="2026-09-05")
        assert m.call_count == 1                     # 同 symbol 只拉一次
        # cost 150 → -10% (loss)；cost 120 → +12.5% (gain)
        assert rows[0]["band"] == "loss" and rows[1]["band"] == "gain"

    def test_stale_price_gets_note(self):
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", return_value={
            "dimension": "kline",
            # 最新一根在 7 个自然日前（窗口内停牌模拟）
            "data": [{"trade_date": "2026-08-25", "close": 150.0},
                     {"trade_date": "2026-09-01", "close": 155.0}],
            "status": "available",
        }):
            rows = build_position_rows_from_holdings(
                [{"symbol": "300308", "cost": 150.0}], today="2026-09-06",
            )
        assert "现价截至" in rows[0]["note"]


class TestReview2Fixes:
    """code-review max round2：positions 消费端校验降级 + 港股码防错路由。"""

    def test_string_cost_row_degrades_not_crash(self):
        """review2 A-4：Excel 字符串 cost 行 → 降级行（band unknown + note），不崩。"""
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", side_effect=AssertionError("不应拉取")):
            rows = build_position_rows_from_holdings(
                [{"symbol": "300308", "cost": "150.0", "buy_date": "2025-06-01"}],
                today="2026-09-06",
            )
        assert rows[0]["band"] == "unknown" and rows[0]["pnl_pct"] is None
        assert "cost 非数值" in rows[0]["note"]

    def test_inf_cost_row_degrades(self):
        """review2 A-6：cost=1e309（Infinity）→ 降级，不渲染笃定深亏。"""
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", side_effect=AssertionError("不应拉取")):
            rows = build_position_rows_from_holdings(
                [{"symbol": "600176", "cost": 1e309}], today="2026-09-06",
            )
        assert rows[0]["band"] == "unknown"
        assert "Infinity" in rows[0]["note"]

    def test_hk_symbol_not_routed_to_a_share_kline(self):
        """review2 HK-3：港股 5 位码不得喂 A 股 get_kline（zfill 错路由 000700）——
        仅确认持仓事实 + note。"""
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", side_effect=AssertionError("港股码不得进 A 股采集")) as m:
            rows = build_position_rows_from_holdings(
                [{"symbol": "00700", "cost": 400.0}], today="2026-09-06",
            )
        m.assert_not_called()
        assert rows[0]["band"] == "unknown"
        assert "非 A 股 6 位代码" in rows[0]["note"]

    def test_sh_prefixed_symbol_accepted(self):
        """带 sh/sz 前缀的 A 股码是合法形态（剥前缀后按 6 位拉取）。"""
        from unittest.mock import patch
        from lib._invest_path import ensure_skills_lib_on_path
        ensure_skills_lib_on_path()
        from lib import collector as col
        from lib.positions import build_position_rows_from_holdings

        with patch.object(col, "collect_kline", return_value={
            "dimension": "kline",
            "data": [{"trade_date": "2026-09-04", "close": 20.0}],
            "status": "available",
        }) as m:
            rows = build_position_rows_from_holdings(
                [{"symbol": "sh600176", "cost": 20.0, "buy_date": "2026-01-05"}],
                today="2026-09-06",
            )
        assert m.call_count == 1
        assert rows[0]["band"] == "loss"     # 现价=成本 → 0% → loss 档？20/20-1=0 → loss


def test_paper_keys_survive_empty_symbol_holding():
    """v0.3.0 B1：holdings 中夹空 symbol 行不得让后续行的标识键错位。

    空 symbol 是 `load_holdings` 明确支持的形态（现金/占位/坏行）。旧实现
    `_carry_paper_keys(rows, holdings)` 按**下标** zip 未过滤的 holdings，而构造
    循环遇空 symbol 就 `continue` → 从该行起全体错位，真实持仓继承**上一行**的
    kind/account/tag，`disposition_hint` 随之输出「模拟/观察仓同样适用」的虚假陈述。
    """
    from unittest.mock import patch
    from lib._invest_path import ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from lib import collector as col
    from lib.positions import build_position_rows_from_holdings, disposition_hint

    holdings = [
        {"symbol": "", "name": "现金", "kind": "观察仓", "weight": 0.1},
        {"symbol": "600176", "name": "中国巨石", "weight": 0.6,
         "cost": 10.0, "buy_date": "2026-01-05"},
    ]
    with patch.object(col, "collect_kline", side_effect=RuntimeError("offline")):
        rows = build_position_rows_from_holdings(holdings, today="2026-09-05")

    assert [r["symbol"] for r in rows] == ["600176"], "空 symbol 行不应产出位置行"
    assert rows[0].get("kind") is None, "真实持仓不得继承上一行的 kind"
    hint = disposition_hint(rows)
    assert hint is not None and hint["paper"] is False, "真实持仓不得被判为模拟/观察仓"
    assert "stakes 低 ≠ 无偏差" not in hint["note"]
