"""Tests for invest.py cmd_peer ranking."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestCmdPeerRanking:
    def test_target_ranked_by_sort_metric(self, capsys):
        import invest

        target = {
            "symbol": "600176",
            "name": "中国巨石",
            "total_mv": 100.0,
            "pe_ttm": 15.0,
            "pb": 2.0,
            "roe": 10.0,
            "revenue_yoy": 5.0,
        }
        peers = [
            {
                "symbol": "002080",
                "name": "中材科技",
                "total_mv": 300.0,
                "pe_ttm": 20.0,
                "pb": 3.0,
                "roe": 12.0,
                "revenue_yoy": 8.0,
            },
            {
                "symbol": "300196",
                "name": "长海股份",
                "total_mv": 200.0,
                "pe_ttm": 18.0,
                "pb": 2.5,
                "roe": 11.0,
                "revenue_yoy": 6.0,
            },
        ]
        mock_result = {
            "peers": peers,
            "target": target,
            "industry_name": "玻纤",
            "peer_source": "akshare_fallback",
            "sort_by": "market_cap",
        }

        with patch.object(invest.collector, "collect_peer_comparison", return_value=mock_result):
            rc = invest.cmd_peer(
                Namespace(symbol="600176", top=10, sort_by="market_cap"),
            )

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            ln for ln in out.splitlines()
            if ln.startswith("| ") and not ln.startswith("| 排名") and not ln.startswith("|--")
        ]
        assert len(lines) == 3
        assert lines[0].startswith("| 1 | 002080")
        assert lines[1].startswith("| 2 | 300196")
        assert lines[2].startswith("| 3 | **600176**")
        assert "共 3 行（含标的）" in out
