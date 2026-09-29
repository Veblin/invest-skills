"""pytest 配置：scripts/lib 入 path（勿把 scripts/ 顶到前面，以免遮蔽 invest-a-stock 的 lib）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


@pytest.fixture(autouse=True)
def _isolate_store_db(tmp_path):
    """隔离 store SQLite 到 tmp：任何 ETF 用例都不得碰 ~/.local/share/investment/research.db。

    必要性（2026-09-29 实测复现）：`futures_basis.query_futures_basis` 在查询前先调
    `store.init_db()`，而用例只 monkeypatch 了 `load_futures_daily`。DB 路径可写时用例
    通过——**但仍会打开/建表用户的真实研究库**；DB 不可写时 `init_db()` 抛出被折成
    「futures_daily 读取失败」，mock 数据根本到不了，3 个用例失败（复现方式：HOME 指向
    只读 research.db，`.venv/bin/python -m pytest skills/invest-a-etf/tests/test_futures_basis.py`
    → 3 failed，签名 `'历史数据不足' not in 'futures_daily 读取失败'`）。

    故隔离方式与 `invest-a-stock/tests/conftest.py::isolated_store`、
    `invest-a-etf/tests/test_r1_staleness.py` 同型（同一 `_db_override` 机制，不新增接口）。
    此处设为 autouse：缺陷不是某一个用例写错，而是「任何遍历到 store 的 ETF 用例都会外溢」。
    """
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


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
