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


# ======================================================================
# 评审回归（2026-09-23）：attempted_sources 只记**实际尝试过**的源
# ======================================================================


@patch.object(kline_source, "TushareBulkSource")
@patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "tok"})
def test_auto_available_records_single_attempt(mock_get_config, mock_bulk_cls):
    """首选源可用：attempted = ("tushare",)，且理由由采集层给出。"""
    mock_bulk_cls.return_value = MagicMock(name="bulk")
    Client = _make_probe_client(available=True)

    with patch("lib.tushare_client.TushareClient", Client):
        result = kline_source.create_source("auto", ts_codes=["600176.SH"])

    assert result.attempted_sources == ("tushare",)
    assert "未降级" in result.source_selection_note


@patch.object(kline_source, "BaostockSource")
@patch("lib.env.get_config", return_value={"TUSHARE_TOKEN": "tok"})
def test_auto_unavailable_records_true_degradation(mock_get_config, mock_baostock_cls):
    """有 token 但检验失败：确为「尝试过 Tushare 后降级」。"""
    mock_baostock_cls.return_value = MagicMock(name="baostock")
    Client = _make_probe_client(available=False)

    with patch("lib.tushare_client.TushareClient", Client):
        result = kline_source.create_source("auto", ts_codes=["600176.SH"])

    assert result.attempted_sources == ("tushare", "baostock")
    assert "检验失败" in result.source_selection_note and "降级" in result.source_selection_note


@patch.object(kline_source, "BaostockSource")
@patch("lib.env.get_config", return_value={})
def test_auto_no_token_does_not_claim_tushare_attempted(
    mock_get_config, mock_baostock_cls,
):
    """F4 回归：无 token 时 Tushare 从未被调用，不得记为「尝试过」。

    修复前回退分支无条件写 ("tushare", "baostock")，报告据此标「发生降级」，
    与「实际尝试过」的定义不符（未配置 ≠ 尝试后失败）。
    """
    mock_baostock_cls.return_value = MagicMock(name="baostock")

    with patch("lib.tushare_client.TushareClient", _ProbeClient):
        result = kline_source.create_source("auto")

    assert result.attempted_sources == ("baostock",)
    assert "未配置 TUSHARE_TOKEN" in result.source_selection_note
    assert _ProbeClient.is_available_calls == 0  # 确未发起可用性检验


@patch.object(kline_source, "TushareBulkSource")
def test_forced_tushare_records_explicit_choice(mock_bulk_cls):
    mock_bulk_cls.return_value = MagicMock(name="bulk")
    result = kline_source.create_source("tushare")
    assert result.attempted_sources == ("tushare",)
    assert "显式指定" in result.source_selection_note


@patch.object(kline_source, "BaostockSource")
def test_forced_baostock_records_explicit_choice(mock_baostock_cls):
    mock_baostock_cls.return_value = MagicMock(name="baostock")
    result = kline_source.create_source("baostock", ts_codes=["000001.SZ"])
    assert result.attempted_sources == ("baostock",)
    assert "显式指定" in result.source_selection_note
