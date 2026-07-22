"""Unit tests for create_source() — mocked, no network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import kline_source


class _ProbeClient:
    """Stand-in for TushareClient used in auto-path availability probe."""

    instances: list["_ProbeClient"] = []
    is_available_calls = 0

    def __init__(self, token=None, **kwargs):
        self.token = token
        self.closed = False
        _ProbeClient.instances.append(self)

    def is_available(self) -> bool:
        _ProbeClient.is_available_calls += 1
        return self._available

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _ProbeClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()


@pytest.fixture(autouse=True)
def _reset_probe_counters():
    _ProbeClient.instances.clear()
    _ProbeClient.is_available_calls = 0
    yield


def _make_probe_client(available: bool) -> type[_ProbeClient]:
    class _Client(_ProbeClient):
        _available = available

    return _Client


@patch.object(kline_source, "BaostockSource")
@patch.object(kline_source, "TushareBulkSource")
@patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "tok"})
def test_auto_available_returns_bulk_once(mock_get_config, mock_bulk_cls, mock_baostock_cls):
    mock_bulk_cls.return_value = MagicMock(name="bulk")
    Client = _make_probe_client(available=True)

    with patch("lib.tushare_client.TushareClient", Client):
        result = kline_source.create_source("auto", ts_codes=["600176.SH"])

    assert result is mock_bulk_cls.return_value
    mock_bulk_cls.assert_called_once_with(skip_availability_check=True)
    mock_baostock_cls.assert_not_called()
    assert _ProbeClient.is_available_calls == 1
    assert len(_ProbeClient.instances) == 1
    assert _ProbeClient.instances[0].closed is True


@patch.object(kline_source, "BaostockSource")
@patch.object(kline_source, "TushareBulkSource")
@patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "tok"})
def test_auto_unavailable_falls_back_without_bulk(
    mock_get_config, mock_bulk_cls, mock_baostock_cls,
):
    mock_baostock_cls.return_value = MagicMock(name="baostock")
    Client = _make_probe_client(available=False)

    with patch("lib.tushare_client.TushareClient", Client):
        result = kline_source.create_source("auto", ts_codes=["600176.SH"])

    assert result is mock_baostock_cls.return_value
    mock_bulk_cls.assert_not_called()
    mock_baostock_cls.assert_called_once_with(ts_codes=["600176.SH"])
    assert _ProbeClient.is_available_calls == 1
    assert len(_ProbeClient.instances) == 1
    assert _ProbeClient.instances[0].closed is True


@patch.object(kline_source, "BaostockSource")
@patch.object(kline_source, "TushareBulkSource")
@patch("lib.env.get_config", return_value={})
def test_auto_no_token_skips_probe(mock_get_config, mock_bulk_cls, mock_baostock_cls):
    mock_baostock_cls.return_value = MagicMock(name="baostock")

    with patch("lib.tushare_client.TushareClient", _ProbeClient):
        result = kline_source.create_source("auto")

    assert result is mock_baostock_cls.return_value
    mock_bulk_cls.assert_not_called()
    assert len(_ProbeClient.instances) == 0
    assert _ProbeClient.is_available_calls == 0


@patch.object(kline_source, "TushareBulkSource")
def test_forced_tushare_unchanged(mock_bulk_cls):
    mock_bulk_cls.return_value = MagicMock(name="bulk")

    result = kline_source.create_source("tushare")

    assert result is mock_bulk_cls.return_value
    mock_bulk_cls.assert_called_once_with()


@patch.object(kline_source, "BaostockSource")
def test_forced_baostock_unchanged(mock_baostock_cls):
    mock_baostock_cls.return_value = MagicMock(name="baostock")
    codes = ["000001.SZ", "600176.SH"]

    result = kline_source.create_source("baostock", ts_codes=codes)

    assert result is mock_baostock_cls.return_value
    mock_baostock_cls.assert_called_once_with(ts_codes=codes)
