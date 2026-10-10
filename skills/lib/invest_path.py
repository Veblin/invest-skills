"""Shared path bootstrap for cross-skill import of invest-a-stock scripts.

Canonical implementation (Batch D / X-02). Skill-local `_invest_path.py` files
are thin shims that re-export from here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

__all__ = [
    "invest_a_scripts_dir",
    "invest_a_etf_lib_dir",
    "journal_lib_dir",
    "ensure_invest_a_scripts_on_path",
    "ensure_shared_lib_on_path",
    "load_invest_a_etf_module",
    "load_journal_module",
]


def invest_a_scripts_dir() -> Path:
    """skills/invest-a-stock/scripts — resolved from skills/lib/."""
    return Path(__file__).resolve().parent.parent / "invest-a-stock" / "scripts"


def invest_a_etf_lib_dir() -> Path:
    """skills/invest-a-etf/scripts/lib — canonical ETF data module."""
    return Path(__file__).resolve().parent.parent / "invest-a-etf" / "scripts" / "lib"


def gap_scan_lib_dir() -> Path:
    """skills/invest-a-gap-scan/scripts/lib — canonical gap-scan data pipeline."""
    return Path(__file__).resolve().parent.parent / "invest-a-gap-scan" / "scripts" / "lib"


def journal_lib_dir() -> Path:
    """skills/invest-a-journal/scripts/lib — journal 数据管道目录（A03 起
    市场微观结构 canonical 在共享层；本目录仅保留 shim/领域模块）。"""
    return Path(__file__).resolve().parent.parent / "invest-a-journal" / "scripts" / "lib"


# 固定模块注册名：pattern-scan shim 与调用方共用同一 canonical 实例
_GAP_SCAN_KLINE_SOURCE_NAME = "gap_scan_kline_source"


def load_gap_scan_module(module_file: str = "kline_source"):
    """按显式路径加载 gap-scan canonical 模块（仿 load_invest_a_etf_module）。

    返回 sys.modules 缓存的模块实例；canonical 文件缺失抛 ImportError。
    module_file: "kline_source" 或 "universe"。
    """
    mod_name = f"{_GAP_SCAN_KLINE_SOURCE_NAME}_{module_file}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    lib = gap_scan_lib_dir()
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(mod_name, lib / f"{module_file}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"gap-scan canonical {module_file}.py missing at {lib}")
    mod = _ilu.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # v0.3.0 D4（D11 同族清扫）：exec 失败留下的残破模块会被 sys.modules 永久
        # 缓存，后续 load_gap_scan_module 短路返回它 → 真实 import 错误被掩盖成
        # 远处难懂的 AttributeError。范式见 collector/_orchestrate.py 的 F5 处理。
        sys.modules.pop(mod_name, None)
        raise
    return mod


def ensure_invest_a_scripts_on_path() -> Path:
    """Insert invest-a-stock/scripts on sys.path (idempotent). Returns the path."""
    scripts = invest_a_scripts_dir()
    s = str(scripts)
    if s not in sys.path:
        sys.path.insert(0, s)
    return scripts


def ensure_shared_lib_on_path() -> Path:
    """Promote skills/lib to the front of sys.path (idempotent).

    Note: chicken-and-egg — modules *outside* skills/lib must inline a
    bootstrap before importing this (see skill-local ``_invest_path.py``).
    """
    d = Path(__file__).resolve().parent
    s = str(d)
    # 已存在但排在 skill 私有 lib 后面时也须提升，否则身份 shim 会导入自身。
    while s in sys.path:
        sys.path.remove(s)
    sys.path.insert(0, s)
    return d


# 固定模块注册名：journal shim 与 data_bridge 共用同一 canonical 实例
_INVEST_A_ETF_MODULE_NAME = "invest_a_etf_etf_data"

#: 顶层裸名（CLI `from etf_data import …` 与 journal shim 的模块名）
_ETF_DATA_TOPLEVEL_NAME = "etf_data"


def _loaded_module_by_path(target: Path):
    """sys.modules 中 ``__file__`` resolve 等于 target 的已加载模块（任意注册名）。

    A04（R2）同实例合同：同一 canonical 文件可能已按不同注册名加载
    （CLI 裸名 ``etf_data``、loader 固定名 ``invest_a_etf_etf_data``、分包
    布局 ``lib.etf_data``）——按文件名比对可识别"同文件第二实例"，
    否则两个实例各持一份 ``_SPOT_CACHE_LOCK``/进程内缓存，monkeypatch 接缝
    分叉（负责人-ETF实例边界.py 实证）。
    """
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            if Path(f).resolve() == target:
                return mod
        except OSError:
            continue
    return None


def _load_shared_module(module_file: str, *, shim_name: str):
    """身份 shim 的裸名/包名入口共用 canonical 与标准 import 锁。"""
    target = (ensure_shared_lib_on_path() / f"{module_file}.py").resolve()
    existing = _loaded_module_by_path(target)
    if existing is not None:
        mod = importlib.import_module(existing.__name__)
    else:
        # 首次裸名导入时缓存里是正在执行的 shim，须让 import 真正加载 canonical。
        previous = sys.modules.pop(module_file, None) if shim_name == module_file else None
        try:
            mod = importlib.import_module(module_file)
        except Exception:
            if previous is not None:
                sys.modules[module_file] = previous
            raise
    if Path(mod.__file__).resolve() != target:
        raise ImportError(f"{module_file} 未解析到共享 canonical: {target}")
    sys.modules[module_file] = mod
    return mod


def load_invest_a_etf_module():
    """Load invest-a-etf's canonical ``scripts/lib/etf_data.py`` by explicit path.

    Deterministic replacement for a bare ``import etf_data``, which resolves
    via ``sys.path`` order and silently degrades to ``None`` (ImportError)
    whenever invest-a-etf's lib dir is not on the path (e.g. invest-a-stock
    context). This loader:

    1. **same-file reuse (R2/A04)**: if the canonical file is already in
       ``sys.modules`` under *any* name (CLI's bare ``etf_data``, package
       ``lib.etf_data``), return that instance — never a second copy of the
       same file (one ``_SPOT_CACHE_LOCK``/cache per file);
    2. ensures ``invest-a-etf/scripts/lib`` is on ``sys.path`` so the
       module's own top-level imports (``_invest_path`` → shared
       ``invest_path``) resolve the same way they do inside invest-a-etf;
    3. loads ``etf_data.py`` by explicit file path under the fixed module
       name ``invest_a_etf_etf_data``, cached in ``sys.modules`` so
       data_bridge and the journal shim share one instance; the compatible
       bare name ``etf_data`` is registered when free (reverse import order:
       loader first, later ``import etf_data`` reuses this instance).

    Raises :exc:`ImportError` when the canonical file is missing.
    """
    lib = invest_a_etf_lib_dir()
    target = (lib / "etf_data.py").resolve()
    existing = _loaded_module_by_path(target)
    if existing is not None:
        sys.modules.setdefault(_INVEST_A_ETF_MODULE_NAME, existing)
        sys.modules.setdefault(_ETF_DATA_TOPLEVEL_NAME, existing)
        return existing
    mod = sys.modules.get(_INVEST_A_ETF_MODULE_NAME)
    if mod is not None:
        sys.modules.setdefault(_ETF_DATA_TOPLEVEL_NAME, mod)
        return mod
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    path = lib / "etf_data.py"
    spec = importlib.util.spec_from_file_location(_INVEST_A_ETF_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load invest-a-etf etf_data from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_INVEST_A_ETF_MODULE_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # v0.3.0 D4（D11 同族清扫）：残破模块不得留在 sys.modules；
        # 失败清理同样覆盖兼容名（下方 setdefault 只在成功后执行）
        sys.modules.pop(_INVEST_A_ETF_MODULE_NAME, None)
        raise
    # 顶层名空缺时注册：后续裸 `import etf_data` 复用同一实例（反向顺序同实例）。
    # 已有同名模块（如 journal shim，另一文件）时不覆盖。
    sys.modules.setdefault(_ETF_DATA_TOPLEVEL_NAME, mod)
    return mod


# 固定模块注册名：data_bridge / journal shim 与（经顶层名导入的）消费方共用同一实例
# （A03 起 canonical 在 skills/lib，注册名去 journal 化；函数名保留兼容）
_SHARED_MARKET_MODULE_PREFIX = "shared_market_canonical"


def load_journal_module(module_file: str = "market_microstructure"):
    """加载**公共**市场微观结构 canonical 模块（skills/lib，A03 起）。

    历史名保留（A04 引入时为 journal canonical loader；A03 owner 迁移后解析
    公共实例，兼容既有调用面：data_bridge / journal shim / 测试 / 分发构建）。

    1. 若同进程已按顶层名导入**同一 canonical 文件**（``__file__`` resolve
       相等）→ 复用该实例（monkeypatch 接缝与模块状态不分叉：测试 patch
       ``market_microstructure._fetch_*`` 后 bridge 必须调到同一份）；
    2. 否则按文件路径加载（skills/lib），并在顶层名空缺时注册该实例
       （后续顶层 import 复用同一份，不再出现第二实例）。

    provider 不依赖 journal 源码：canonical 文件缺失（部署不完整）抛
    ImportError，由 data_bridge 降级 None + warning（语义与旧实现一致）。
    """
    lib = Path(__file__).resolve().parent
    target = (lib / f"{module_file}.py").resolve()

    # 同文件已加载（任意注册名：顶层名 / 分包 lib.X / 固定名）→ 复用同一实例
    existing = _loaded_module_by_path(target)
    if existing is not None:
        sys.modules.setdefault(module_file, existing)
        return existing

    mod_name = f"{_SHARED_MARKET_MODULE_PREFIX}_{module_file}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    spec = importlib.util.spec_from_file_location(mod_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"shared canonical {module_file}.py missing at {lib}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # 残破模块不得留在 sys.modules（同 load_gap_scan_module 范式）
        sys.modules.pop(mod_name, None)
        raise
    # 顶层名空缺时注册：后续 `import market_microstructure` 复用同一实例
    sys.modules.setdefault(module_file, mod)
    return mod
