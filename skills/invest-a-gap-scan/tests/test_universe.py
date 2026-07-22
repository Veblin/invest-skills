"""Tests for universe helpers — pure mapping, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from universe import _convert_to_ts_code  # noqa: E402


class TestConvertToTsCode:
    def test_shanghai_main(self):
        assert _convert_to_ts_code("600176") == "600176.SH"

    def test_shanghai_star(self):
        assert _convert_to_ts_code("688001") == "688001.SH"

    def test_shenzhen_main(self):
        assert _convert_to_ts_code("000001") == "000001.SZ"

    def test_shenzhen_chinext(self):
        assert _convert_to_ts_code("300750") == "300750.SZ"

    def test_beijing_8xxx(self):
        """北交所 8xxxx → .BJ."""
        assert _convert_to_ts_code("830799") == "830799.BJ"
        assert _convert_to_ts_code("872925") == "872925.BJ"

    def test_beijing_4xxx(self):
        """北交所 4xxxx → .BJ."""
        assert _convert_to_ts_code("430047") == "430047.BJ"

    def test_zfill_short_code(self):
        assert _convert_to_ts_code("1") == "000001.SZ"
