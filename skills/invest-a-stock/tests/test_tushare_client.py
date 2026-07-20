"""Tushare 轻量客户端测试。"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pandas as pd


class TestTusharePermissionDenied:
    def test_permission_denied_logged_at_debug_and_cached(self, caplog):
        from lib.tushare_client import TushareClient

        client = TushareClient(token="a" * 32)
        responses = [
            {"code": 40203, "msg": "抱歉，您没有接口(sw_daily)访问权限"},
            {"code": 0, "data": {"fields": ["close"], "items": [[1.0]]}},
        ]

        def _fake_post(*_a, **_kw):
            class _R:
                def raise_for_status(self):
                    return None

                def json(self):
                    return responses.pop(0) if responses else {"code": 0, "data": {}}

            return _R()

        client._session.post = _fake_post  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG, logger="lib.tushare_client"):
            first = client.query("sw_daily", ts_code="851024.SI")
            second = client.query("sw_daily", ts_code="851024.SI")

        assert first.empty
        assert second.empty
        assert "sw_daily" in client._permission_denied_apis
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_sw_index_availability_labels_akshare_fallback(self):
        from lib.collector import _ms_sw_index_availability_label

        label = _ms_sw_index_availability_label({"source": "akshare.index_hist_sw"})
        assert "5000" in label
        assert "akshare fallback" in label
        assert _ms_sw_index_availability_label({"source": "tushare.sw_daily"}) == "available"

    def test_sw_index_falls_back_to_akshare_when_tushare_empty(self, monkeypatch):
        from lib import collector

        mock_tc = MagicMock()
        mock_tc.query.side_effect = lambda api, **kw: (
            pd.DataFrame({"industry_name": ["通信设备"], "index_code": ["851024.SI"]})
            if api == "index_classify"
            else pd.DataFrame()
        )

        fake_sw = {
            "index_code": "851024.SI",
            "industry": "通信设备",
            "return_20d_pct": 5.0,
            "source": "akshare.index_hist_sw",
        }

        with patch.object(collector.env, "is_tushare_available", return_value=True), patch.object(
            collector.env, "get_config", return_value={"TUSHARE_TOKEN": "x" * 32},
        ), patch.object(collector, "_tushare_client", return_value=mock_tc), patch.object(
            collector, "_ms_fetch_sw_index_akshare", return_value=fake_sw,
        ):
            result = collector._ms_fetch_sw_index(mock_tc, "300308", "通信设备")

        assert result is not None
        assert result["source"] == "akshare.index_hist_sw"

    def test_sw_index_akshare_prefers_industry_lookup_over_tushare_code(self, monkeypatch):
        from lib import collector

        monkeypatch.setattr(
            collector, "_ms_lookup_akshare_sw_code", lambda industry: "801093",
        )
        monkeypatch.setattr(
            collector, "_akshare_closes_from_hist_sw",
            lambda code, **kw: [100.0, 105.0] if code == "801093" else [],
        )
        monkeypatch.setattr(
            collector, "_akshare_hs300_closes", lambda **kw: [3000.0, 3010.0],
        )

        result = collector._ms_fetch_sw_index_akshare(
            "300308", "通信设备", index_code="851024.SI", tc=None,
        )
        assert result is not None
        assert result["index_code"] == "801093"
        assert result["source"] == "akshare.index_hist_sw"
