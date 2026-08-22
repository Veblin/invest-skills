"""v0.2.4：报告内 diff 提示——有历史快照即显示「相对上次调研变化」块。

改动核心：_v3.py 条件从 `key_diff and key_diff.get("categories")` 放宽为
`key_diff`（非 None 即有历史），无显著变化时显示「关键字段无显著变化」
状态行（format_key_diff_markdown_lines 已处理空 categories）。
"""

from __future__ import annotations

import sys
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from test_v013_phase4 import _phase4_collection  # noqa: E402


def _render(collection, symbol: str, mode: str) -> str:
    from lib.render_markdown._concise import render_report_v3

    return render_report_v3(collection, symbol, mode=mode)


class TestReportDiffHistory:
    def test_diff_block_shown_when_no_significant_change(self, isolated_store):
        """有历史快照但关键字段无显著变化 → 仍显示块 + 状态行（本次改动核心）。"""
        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z")
        s2 = _phase4_collection("600176", "2026-08-07T00:00:00Z")
        isolated_store.save_collection(s1)

        md = _render(s2, "600176", "full")
        assert "相对上次调研变化" in md
        assert "关键字段无显著变化" in md

    def test_diff_block_lists_changes(self, isolated_store):
        """关键字段有变化 → 块内列出变化行（回归 v0.1.3 Phase4 行为）。"""
        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z", latest_roe=18.0)
        s2 = _phase4_collection("600176", "2026-08-07T00:00:00Z", latest_roe=22.0)
        isolated_store.save_collection(s1)

        md = _render(s2, "600176", "full")
        assert "相对上次调研变化" in md
        assert "roe" in md

    def test_no_diff_block_without_history(self, isolated_store):
        """无历史快照 → 不显示 diff 块（load_key_diff_vs_stored 返回 None）。"""
        c = _phase4_collection("600176", "2026-08-07T00:00:00Z")
        md = _render(c, "600176", "full")
        assert "相对上次调研变化" not in md

    def test_diff_block_in_brief_mode(self, isolated_store):
        """brief 模式与 full 共用 _section_snapshot，块同样生效。"""
        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z")
        s2 = _phase4_collection("600176", "2026-08-07T00:00:00Z")
        isolated_store.save_collection(s1)

        md = _render(s2, "600176", "brief")
        assert "相对上次调研变化" in md

    def test_concise_mode_unaffected(self, isolated_store):
        """concise 模式走 _concise_positioning，不渲染 diff 块（现状保持）。"""
        s1 = _phase4_collection("600176", "2026-08-01T00:00:00Z")
        s2 = _phase4_collection("600176", "2026-08-07T00:00:00Z")
        isolated_store.save_collection(s1)

        md = _render(s2, "600176", "concise")
        assert "相对上次调研变化" not in md
