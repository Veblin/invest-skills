"""_compute_labels_v2 分位标签（今日双计防回归，review #6 第二轮）。

背景：snapshot → _auto_persist（写今日行）→ load_history(60) 已含今日 →
分位计算若不剔除今日行，今日值被双计（抬高约 2/(N+1) 个分位点），
在 10/30/70/90 边界可翻转极冷/偏冷/偏暖/极热标签。
cd5e7a4 已修杠杆分位与 _compute_tier2，广度分位漏网（本测试防回归）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()


class TestBreadthLabelTodayExclusion:
    def test_breadth_percentile_excludes_today_row(self):
        """边界样本：20 个历史日（1 个 ad=0.5 + 19 个 ad=3.0）+ 今日行 0.5。

        剔除今日 → 2/21 = 9.52% → 极冷（<10%）；双计 → 3/22 = 13.64% → 偏冷。
        """
        from market_microstructure import _compute_labels_v2

        snap = {"date": "20260807", "ad_ratio": 0.5, "ad_ratio_5d_ma": None}
        history = [{"date": "20260701", "ad_ratio": 0.5}] + [
            {"date": f"202607{i:02d}", "ad_ratio": 3.0} for i in range(2, 21)
        ]  # 20 个历史日
        history.append({"date": "20260807", "ad_ratio": 0.5})  # 今日已持久化行

        _compute_labels_v2(snap, history)
        label = snap["label_breadth"]
        assert "极冷" in label        # 2/21 = 9.52% < 10%
        assert "偏冷" not in label    # 双计 3/22 = 13.64% 会误标偏冷

    def test_breadth_label_uses_20_day_window(self):
        """剔除今日后不足 20 个历史日 → 走阈值表分支而非分位。"""
        from market_microstructure import _compute_labels_v2

        snap = {"date": "20260807", "ad_ratio": 0.5, "ad_ratio_5d_ma": None}
        history = [{"date": f"202607{i:02d}", "ad_ratio": 1.0} for i in range(1, 19)]
        history.append({"date": "20260807", "ad_ratio": 0.5})  # 今日行
        _compute_labels_v2(snap, history)
        # 剔除今日后仅 18 个 → 阈值表：ad 0.5 < 0.6 → 极冷
        assert "极冷" in snap["label_breadth"]
