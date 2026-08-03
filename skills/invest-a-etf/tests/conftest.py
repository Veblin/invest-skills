"""pytest 配置：scripts/lib 入 path（勿把 scripts/ 顶到前面，以免遮蔽 invest-a-stock 的 lib）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


@pytest.fixture(autouse=True)
def _redirect_data_bridge_cache(tmp_path, monkeypatch):
    """隔离 data_bridge 磁盘缓存到 tmp（query_* 已走 data_bridge 路径，防污染真实缓存目录）。

    v0.2.3：etf_data 经 _bridge_get → data_bridge 维度缓存；测试若直接走
    data_bridge 会读写 ~/.local/share/investment/cache，这里整体重定向。
    """
    _SKILLS_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
    if str(_SKILLS_LIB) not in sys.path:
        sys.path.insert(0, str(_SKILLS_LIB))
    try:
        import data_bridge  # noqa: PLC0415
        from cache import DataCache  # noqa: PLC0415
    except ImportError:
        return
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
