"""A02 owner 迁移契约：env/proxy/tushare_client 共享 canonical + stock 身份 shim。

验证（v0.3.2 A基础）：
1. 同一模块实例：``lib.env is env``（两种导入顺序；子进程验证，避免
   pytest 会话既成导入顺序掩盖顺序依赖）；
2. monkeypatch 接缝：``lib.env.get_config`` 的 patch 对 TushareClient
   惰性 token 解析生效（D13：patch 目标 = 消费方查找点）；
3. 全局限流共享：``lib.proxy`` 与顶层 ``proxy`` 同 ``__dict__``（状态单一）；
4. D11 同族扫描：skills/lib 生产模块不得对 stock 私有基础模块做**首选路径**
   反向 import（仅允许 ImportError 兜底臂与 stock-owned **公开 provider**
   ``lib.market_daily`` / ``lib.store`` / ``lib.macro``；基础模块
   env/proxy/tushare_client/nums/stats 等必须走 skills/lib canonical）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_SKILLS = Path(__file__).resolve().parents[2]
_SKILLS_LIB = _SKILLS / "lib"
_STOCK_SCRIPTS = _SKILLS / "invest-a-stock" / "scripts"
_REPO_ROOT = _SKILLS.parent

for _p in (str(_SKILLS_LIB), str(_STOCK_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _subprocess_identity(first: str, second: str) -> str:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(_STOCK_SCRIPTS)!r})\n"
        f"sys.path.insert(0, {str(_SKILLS_LIB)!r})\n"
        f"import {first} as a\n"
        f"import {second} as b\n"
        "assert a is b, (a, b)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.parametrize("top,lib", [
    ("lib.env", "env"),
    ("env", "lib.env"),
    ("lib.proxy", "proxy"),
    ("proxy", "lib.proxy"),
    ("lib.tushare_client", "tushare_client"),
    ("tushare_client", "lib.tushare_client"),
])
def test_same_module_instance_both_orders(top: str, lib: str) -> None:
    """两种导入顺序都得到同一模块对象（shim 的 sys.modules 替换契约）。"""
    assert _subprocess_identity(top, lib) == "ok"


@pytest.fixture
def stock_lib_layout():
    """把 ``lib`` 解析固定到 stock scripts/lib（生产布局）并在结束后恢复。

    背景（同 test_data_bridge 既有先例注释）：pytest 在 skills/lib/tests
    目录下会因共享 ``skills/lib`` 有 ``__init__.py`` 而把 ``skills/`` 置于
    path 前位——此时 ``import lib`` 会绑到共享包（其下 env.py 即 canonical
    文件本身，不是 shim），这不是生产布局。生产语句经各 ``_invest_path``
    shim 把 stock scripts / skills/lib 入 path，``lib`` 恒为 stock 包。
    """
    saved_path = list(sys.path)
    saved_lib = {k: sys.modules.pop(k) for k in list(sys.modules)
                 if k == "lib" or k.startswith("lib.")}
    sys.path.insert(0, str(_STOCK_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = saved_path
        sys.modules.update(saved_lib)


def test_identity_in_current_process(stock_lib_layout) -> None:
    import env
    import lib.env as lib_env
    import lib.proxy as lib_proxy
    import lib.tushare_client as lib_tc
    import proxy
    import tushare_client

    assert lib_env is env
    assert lib_proxy is proxy
    assert lib_tc is tushare_client
    # 同 __dict__ ⇒ 模块级状态（限流计数/_em_now monkeypatch 等）只有一份
    assert lib_proxy.__dict__ is proxy.__dict__
    assert lib_env.__dict__ is env.__dict__


def test_env_patch_seam_reaches_tushare_client(monkeypatch, stock_lib_layout) -> None:
    """D13：patch lib.env.get_config → 裸 TushareClient() 惰性 token 解析命中。

    断言只用布尔中间量：失败信息不得回显真实 token（含 .env 回退值）。
    """
    import lib.env as lib_env
    from lib.tushare_client import TushareClient

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    sentinel = "f" * 32
    monkeypatch.setattr(
        lib_env, "get_config",
        lambda: {"TUSHARE_TOKEN": sentinel, "TUSHARE_DAILY_CALL_LIMIT": None},
    )
    client = TushareClient()
    seam_ok = client._token == sentinel and client._token is not None
    assert seam_ok, "TushareClient 未采用 patched lib.env.get_config（接缝断裂；值不打印）"


def test_proxy_throttle_state_shared(monkeypatch, stock_lib_layout) -> None:
    """全局限流 state 共享：顶层 proxy 的 monkeypatch 接缝命中 lib.proxy 调用。"""
    import lib.proxy as lib_proxy
    import proxy

    sleeps: list[float] = []
    monkeypatch.setattr(proxy, "_em_now", lambda: 100.0)
    monkeypatch.setattr(proxy.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(proxy, "EM_REQUEST_INTERVAL_SEC", 1.0)
    monkeypatch.setattr(lib_proxy, "_em_last_call", 99.5)  # 距读数 0.5s < 1.0s
    lib_proxy.throttle_eastmoney()
    assert sleeps == [0.5], "共享 _em_now/_em_last_call 接缝应触发等待（state 未分叉）"


# ── D11 同族扫描：skills/lib 不得反向首选 import stock 私有基础模块 ──────────

# stock 自有 canonical（允许 import：数据层 provider 归 stock）
#   lib.market_daily — 全市场横截面（stock owner）
#   lib.store        — 研究库路径/连接/DDL owner（公开 `open_connection`/
#                      `get_db_path`/`init_db`；A02 公开接口）
#   lib.macro        — 宏观 provider（公开 `fetch_fred_series`，A04）
# A03 起 skills/lib/market_microstructure.py（公共市场服务）为这三个 provider 的
# 显式消费者；A02 迁移的 env/proxy/tushare_client/nums/stats/dates/cache/
# trade_cal 等**基础模块**仍须走 skills/lib canonical（不得加回本集合）。
_STOCK_OWNED_ALLOW = {"lib.market_daily", "lib.store", "lib.macro"}


def _iter_shared_modules() -> list[Path]:
    return sorted(
        p for p in _SKILLS_LIB.glob("*.py") if p.name != "__init__.py"
    )


def _import_error_handler_nodes(tree: ast.AST) -> set[int]:
    """收集处于 except ImportError 处理块内的语句行号（兜底臂豁免）。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            catches = (
                handler.type is None
                or (isinstance(handler.type, ast.Name) and handler.type.id == "ImportError")
                or (
                    isinstance(handler.type, ast.Tuple)
                    and any(
                        isinstance(e, ast.Name) and e.id == "ImportError"
                        for e in handler.type.elts
                    )
                )
            )
            if catches:
                for stmt in handler.body:
                    for sub in ast.walk(stmt):
                        lines.add(getattr(sub, "lineno", 0))
    return lines


def test_no_reverse_private_imports_in_shared_lib() -> None:
    """skills/lib 生产模块对 lib.* 的 import 仅限豁免集（首选路径不得反向）。"""
    offenders: list[str] = []
    for path in _iter_shared_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        fallback_lines = _import_error_handler_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            mod = node.module
            if mod != "lib" and not mod.startswith("lib."):
                continue
            # `from lib import store as _store`：按别名展开为 lib.store 再判豁免
            targets = ([f"lib.{a.name}" for a in node.names]
                       if mod == "lib" else [mod])
            if all(t in _STOCK_OWNED_ALLOW for t in targets):
                continue
            if node.lineno in fallback_lines:
                continue  # ImportError 兜底臂（历史布局兼容），非首选路径
            offenders.append(f"{path.name}:{node.lineno}: from {mod} import ...")
    assert not offenders, "skills/lib 反向 import stock 私有基础模块:\n" + "\n".join(offenders)


def test_canonical_files_and_shims_shape() -> None:
    """canonical 在 skills/lib；stock 侧为 sys.modules 替换型 shim（非空壳/非双实现）。"""
    for name in ("env", "proxy", "tushare_client"):
        canonical = _SKILLS_LIB / f"{name}.py"
        shim = _STOCK_SCRIPTS / "lib" / f"{name}.py"
        assert canonical.is_file(), canonical
        text = shim.read_text(encoding="utf-8")
        assert "sys.modules[__name__]" in text, f"{shim} 未做身份替换（会产生双实例）"
        assert len(text.splitlines()) < 40, f"{shim} 不应承载实现体"


@pytest.mark.parametrize("module", ["env", "proxy", "tushare_client", "trade_cal"])
def test_stock_shims_promote_already_present_shared_path(module):
    code = f'''
import sys, importlib
from pathlib import Path
sys.path[:0] = [{str(_STOCK_SCRIPTS / 'lib')!r}, {str(_STOCK_SCRIPTS)!r}, {str(_SKILLS_LIB)!r}]
a = importlib.import_module("lib.{module}")
b = importlib.import_module("{module}")
assert a is b
assert Path(a.__file__).resolve() == Path({str(_SKILLS_LIB / (module + '.py'))!r}).resolve()
import market_microstructure
assert Path(market_microstructure.__file__).resolve() == Path({str(_SKILLS_LIB / 'market_microstructure.py')!r}).resolve()
print("ok")
'''
    proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                          text=True, cwd=str(_REPO_ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.parametrize("module", ["env", "proxy", "tushare_client", "trade_cal"])
def test_bare_shim_first_import_reuses_canonical(module):
    code = f'''
import sys, importlib
from pathlib import Path
sys.path[:0] = [{str(_STOCK_SCRIPTS / 'lib')!r}, {str(_STOCK_SCRIPTS)!r}, {str(_SKILLS_LIB)!r}]
a = importlib.import_module("{module}")
b = importlib.import_module("lib.{module}")
assert a is b
assert Path(a.__file__).resolve() == Path({str(_SKILLS_LIB / (module + '.py'))!r}).resolve()
print("ok")
'''
    proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                          text=True, cwd=str(_REPO_ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
