"""Unit tests for ETF spot cache deduplication (no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from etf_data import (  # noqa: E402
    clear_etf_spot_cache,
    query_etf_data,
    query_etf_quote,
)


@pytest.fixture(autouse=True)
def _reset_spot_cache():
    clear_etf_spot_cache()
    yield
    clear_etf_spot_cache()


def _fake_spot_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "代码": "515790",
            "最新价": 4.5,
            "涨跌幅": 0.1,
            "成交量": 1000,
            "成交额": 4500,
            "基金折价率": 0.2,
            "最新份额": 2e10,
        }]
    )


@patch("etf_data.akshare_direct_session")
def test_spot_fetched_once_for_data_and_quote(mock_session, monkeypatch):
    mock_session.return_value.__enter__ = MagicMock(return_value=None)
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    fake_df = _fake_spot_df()
    call_count = {"n": 0}

    def _fake_spot_em():
        call_count["n"] += 1
        return fake_df

    ak = MagicMock()
    ak.fund_etf_spot_em = _fake_spot_em
    monkeypatch.setitem(sys.modules, "akshare", ak)

    profile = query_etf_data("515790")
    quote = query_etf_quote("515790")

    assert call_count["n"] == 1
    assert profile["premium_discount"] == pytest.approx(-0.2)
    assert quote["status"] == "available"
    assert quote["price"] == pytest.approx(4.5)
