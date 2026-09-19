"""pytest 配置：导入路径、隔离 store、共享常量。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from stock_testutil import FORBIDDEN_SIGNAL_WORDS, make_store_collection  # noqa: F401

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture
def isolated_store(tmp_path: Path) -> Iterator[Any]:
    """使用临时 SQLite，避免污染 ~/.local/share/investment/research.db。"""
    from lib import store as store_mod

    previous = store_mod._db_override
    store_mod._db_override = tmp_path / "test_research.db"
    try:
        store_mod.init_db()
        yield store_mod
    finally:
        store_mod._db_override = previous


@pytest.fixture(autouse=True)
def _reset_hsgt_run_cache(monkeypatch: Any) -> Iterator[None]:
    """hsgt_top10 run 级缓存：pytest 同进程同日跨测试污染 → 每测试重置。

    原为 test_v013_phase1 局部 fixture；test_r12h 的 collect_all 测试也会写入
    缓存（patch _q_tushare_hsgt_top10 后经 _hsgt_top10_cached 落缓存），同日
    同进程其他测试/文件会命中假行（code-review）→ 上收 conftest 全局生效。
    """
    from lib import collector

    monkeypatch.setattr(collector._orchestrate, "_hsgt_top10_cache", {})
    monkeypatch.setattr(collector._orchestrate, "_hsgt_top10_cache_day", "")
    yield


@pytest.fixture(autouse=True)
def _isolate_sw_member_cache(tmp_path: Path, monkeypatch: Any) -> Iterator[None]:
    """申万成分整表（index_member_all）缓存：默认落真实 ~/.local/share/investment/cache。

    不隔离则测试会读到**本机真实数据**——实测后果：本地跑过一次真实采集后，
    test_v013_phase2 的 mock 用例被缓存短路，industry_name 由 fixture 的
    「电气设备」变成真实申万名「玻纤制造」而失败。故每测试注入 tmp 缓存目录，
    保证 hermetic（与 _reset_hsgt_run_cache 同型）。
    """
    from lib import collector

    try:
        from lib.cache import DataCache  # type: ignore
    except ImportError:  # pragma: no cover - 共享 lib 以顶层 cache 暴露时
        from cache import DataCache  # type: ignore

    monkeypatch.setattr(
        collector._orchestrate, "_sw_cache",
        lambda: DataCache(cache_dir=tmp_path / "sw_member_cache"),
    )
    yield
