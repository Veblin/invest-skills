"""Universe module smoke — ts_code mapping covered by skills/lib/tests/test_codes.py."""

from __future__ import annotations

import universe


def test_universe_module_imports_shared_codes():
    """Gap-scan universe delegates symbol/board helpers to skills/lib/codes."""
    assert hasattr(universe, "symbol_to_ts_code")
    assert hasattr(universe, "classify_board")
    assert universe.symbol_to_ts_code("600176") == "600176.SH"
    assert universe.classify_board("688001.SH") == "科创板"
