"""pytest 配置：skills/lib + scripts/lib（shim 测 invest_path；勿遮蔽 invest-a-stock lib）。

另含 issue #33 的 DB 隔离（autouse）：任何 journal 用例都不得触碰生产
`~/.local/share/investment/research.db`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_LIB = _SKILL_ROOT / "scripts" / "lib"
_SKILLS_LIB = _SKILL_ROOT.parent / "lib"

for p in (_SKILLS_LIB, _LIB):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _main_db_file(conn) -> Path:
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file).resolve()
    raise AssertionError("连接未暴露 main 数据库")


def make_connect_guard(real_connect, allowed_root: Path):
    """连接边界守卫工厂（issue #33 P2，独立验收 2026-10-01）。

    返回一个包装函数：**先**核验传给底层 `connect_db` 的路径解析后位于
    `allowed_root` 之内，**再**放行真实连接。任何越界目标在建立连接/创建目录/
    建表之前即抛 AssertionError —— 单靠「连接后 PRAGMA 回读」不足以防零接触
    （`connect_db` 自身会 mkdir + 建文件），故守卫必须在这一层。
    """

    def _guarded(path):
        target = Path(path).resolve()
        if not target.is_relative_to(allowed_root):
            raise AssertionError(
                f"journal 测试连接边界守卫：目标 {target} 不在允许的临时目录 "
                f"{allowed_root} 内 —— 拒绝在隔离区之外建立连接/建表 "
                "（issue #33 防回退；若为误报请检查 _db_override 与 db.py 的路径解析）"
            )
        return real_connect(path)

    return _guarded


@pytest.fixture(autouse=True)
def _isolate_journal_db(tmp_path, monkeypatch):
    """隔离研究库到 tmp：任何 journal 用例都不得碰生产 research.db（issue #33）。

    必要性（2026-09-30 离线复现）：`test_snapshot_status.py` 曾调真实 `snapshot()`，
    其 `_auto_persist()` 落库；无隔离时 fixture 值（ad_ratio=1.2）经 merge=COALESCE
    覆盖生产库当日行并刷新 collected_at，且写入异常被 `_auto_persist` 吞掉 → 测试恒绿。
    历史污染 29 行（20260804–20260930，已于 2026-10-01 清理）。

    隔离走 `store._db_override` 单一真源（与 invest-a-stock / invest-a-etf 同机制）：
    journal `db.py` 的写入连接自 #33 起也经 `store.get_db_path()` 解析，因此只 patch
    `_db_override` 即可同时覆盖「journal 连接」与「store 建表 / futures 读」两条路径。
    **不要**改回只 patch journal `db.DB_PATH`——会造成「建表在 A、连接写 B」的双路径
    错位，写入异常被吞掉，比不修更隐蔽。

    防回退的顺序契约（P2）：一切连接/建表之前依序完成
    ① 纯路径不变量（零 I/O）；② **连接边界守卫**（越界目标直接抛错，零接触）；
    ③ 落点回读（此时连接已保证在 tmp 内）；之后才允许 `init_db()`。
    """

    from _invest_path import ensure_invest_a_scripts_on_path

    ensure_invest_a_scripts_on_path()
    from lib import store as store_mod
    import db as db_mod

    allowed = tmp_path.resolve()
    test_db = allowed / "test_research.db"

    # ① 纯路径不变量：先于一切连接与建表
    monkeypatch.setattr(store_mod, "_db_override", test_db)
    assert Path(store_mod.get_db_path()).resolve() == test_db, (
        "store._db_override 未生效：拒绝在任何库上继续（issue #33 回归）"
    )

    # ② 连接边界守卫：journal 与 store 两条入口（含各自的底层 db_util 模块）全部覆盖
    targets: dict[int, tuple[object, object]] = {}
    for mod in (db_mod, store_mod):
        real = getattr(mod, "connect_db", None)
        if real is None:
            continue
        targets[id(mod)] = (mod, real)
        defining = sys.modules.get(getattr(real, "__module__", "") or "")
        if defining is not None and id(defining) not in targets:
            targets[id(defining)] = (defining, getattr(defining, "connect_db", real))
    for mod, real in targets.values():
        monkeypatch.setattr(mod, "connect_db", make_connect_guard(real, allowed))

    # ③ 落点回读（连接已在允许根内，属零风险自证）
    c = db_mod._conn()
    try:
        landed = _main_db_file(c)
    finally:
        db_mod._safe_close(c)
    assert landed == test_db, (
        f"journal 连接落点 {landed} ≠ 隔离文件 {test_db}"
        "（会污染生产库/静默失败，issue #33）"
    )

    # ④ 允许建表
    store_mod.init_db()  # market_snapshots / macro_snapshots 等
    db_mod.init_db()     # trade_journals
    yield test_db


@pytest.fixture
def guarded_connect_factory(tmp_path):
    """暴露连接边界守卫工厂：`factory(real_connect) -> 受保护连接函数`（允许根=本测试 tmp_path）。

    供负控制测试直接驱动守卫（不依赖 autouse fixture 的内部装配）。
    """
    return lambda real_connect: make_connect_guard(real_connect, tmp_path.resolve())


@pytest.fixture(autouse=True)
def _redirect_data_bridge_cache(tmp_path, monkeypatch):
    """隔离 data_bridge 磁盘缓存到 tmp（同 invest-a-etf/tests/conftest.py）。

    单进程整套运行时，journal 用例若经 data_bridge 读写 L2，会碰真实
    ~/.local/share/investment/cache。
    """
    try:
        import data_bridge
        from cache import DataCache
    except ImportError:
        return
    monkeypatch.setattr(data_bridge, "_cache", DataCache(cache_dir=tmp_path / "cache"))
