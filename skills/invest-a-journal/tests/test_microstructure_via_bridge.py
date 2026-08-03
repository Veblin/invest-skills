"""v0.2.3: query_data._safe_collect_microstructure 走 data_bridge 缓存层。

验证消费侧接线（get_microstructure 透传），不触发真实网络/真实 snapshot。
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import query_data  # noqa: E402


def test_safe_collect_microstructure_goes_through_bridge(monkeypatch):
    """_safe_collect_microstructure 透传 get_microstructure 返回值。"""
    captured = {"called": False}

    def fake_get_microstructure():
        captured["called"] = True
        return {"date": "20260803", "ad_ratio": 1.2, "label_breadth": "正常"}

    monkeypatch.setattr(query_data, "get_microstructure", fake_get_microstructure)
    out = query_data._safe_collect_microstructure()
    assert captured["called"] is True
    assert out["date"] == "20260803"


def test_safe_collect_microstructure_passes_cache_flag(monkeypatch):
    """缓存命中（_from_cache）原样透传，不污染。"""
    monkeypatch.setattr(
        query_data, "get_microstructure",
        lambda: {"date": "20260803", "_from_cache": True},
    )
    out = query_data._safe_collect_microstructure()
    assert out["_from_cache"] is True


def test_safe_collect_microstructure_none_becomes_error(monkeypatch):
    """get_microstructure 返回 None（非 journal 上下文降级）→ 错误信封。"""
    monkeypatch.setattr(query_data, "get_microstructure", lambda: None)
    out = query_data._safe_collect_microstructure()
    assert out == {"_error": "microstructure unavailable"}


def test_safe_collect_microstructure_exception_captured(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(query_data, "get_microstructure", boom)
    out = query_data._safe_collect_microstructure()
    assert out["_error"] == "boom"
