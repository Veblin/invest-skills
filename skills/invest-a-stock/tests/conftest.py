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
