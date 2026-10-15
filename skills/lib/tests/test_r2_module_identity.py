"""R2/A04：ETF canonical `etf_data.py` 运行单实例合同（任意导入顺序同实例）。

负责人-ETF实例边界.py 现场：CLI 的裸 ``import etf_data`` 与
``invest_path.load_invest_a_etf_module()``（经 data_bridge/journal shim）加载
**同一文件**却得到两个模块对象、各持一份 ``_SPOT_CACHE_LOCK`` 与进程内缓存。

本文件用**子进程**控制导入顺序（pytest 会话内的 sys.modules 状态不可控）：
① CLI 先导入 → loader 复用同一实例；
② loader 先加载 → 后来的裸 ``import etf_data`` 复用同一实例；
③ 分包 ``lib.etf_data`` 布局 → loader 复用同一实例；
④ journal shim（按文件路径加载转发 shim）→ 转发到同一实例；
⑤ 失败清理：加载失败不得留下残破模块（固定名与兼容名都不留）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ETF_LIB = REPO / "skills" / "invest-a-etf" / "scripts" / "lib"
ETF_SCRIPTS = REPO / "skills" / "invest-a-etf" / "scripts"
SKILLS_LIB = REPO / "skills" / "lib"
JOURNAL_LIB = REPO / "skills" / "invest-a-journal" / "scripts" / "lib"

_PRELUDE = f"""
import json, sys
from pathlib import Path
root = Path(r"{REPO}")
sys.path.insert(0, str(root / "skills" / "invest-a-stock" / "scripts"))
"""


def _run(code: str) -> dict:
    proc = subprocess.run([sys.executable, "-c", _PRELUDE + code],
                          capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert proc.returncode == 0, f"子进程失败 rc={proc.returncode}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_cli_bare_import_then_loader_same_instance():
    """① CLI 裸导入在前：loader 必须复用该实例（旧实现返回第二份拷贝）。"""
    out = _run(f"""
sys.path.insert(0, str(root / "skills" / "lib"))
sys.path.insert(0, str(root / "skills" / "invest-a-etf" / "scripts" / "lib"))
import etf_data                                  # CLI 路径（顶层名）
from invest_path import load_invest_a_etf_module
b = load_invest_a_etf_module()
print(json.dumps({{
  "same_module": b is etf_data,
  "same_lock": b._SPOT_CACHE_LOCK is etf_data._SPOT_CACHE_LOCK,
  "same_spot_cache": b._SPOT_CACHE_TS is etf_data._SPOT_CACHE_TS,
  "canonical_name_points_same": sys.modules.get("invest_a_etf_etf_data") is etf_data,
}}))
""")
    assert out["same_module"] is True, "同一文件不得有第二实例"
    assert out["same_lock"] is True, "_SPOT_CACHE_LOCK 不得分叉"
    assert out["same_spot_cache"] is True
    assert out["canonical_name_points_same"] is True


def test_loader_then_bare_import_same_instance():
    """② loader 先加载：其后裸 `import etf_data`（CLI 反向顺序）同实例。"""
    out = _run(f"""
sys.path.insert(0, str(root / "skills" / "lib"))
sys.path.insert(0, str(root / "skills" / "invest-a-etf" / "scripts" / "lib"))
from invest_path import load_invest_a_etf_module
b = load_invest_a_etf_module()
import etf_data                                  # 反向：loader 在前
print(json.dumps({{
  "same_module": etf_data is b,
  "same_lock": etf_data._SPOT_CACHE_LOCK is b._SPOT_CACHE_LOCK,
}}))
""")
    assert out["same_module"] is True, "loader 先注册兼容名后，裸导入必须复用"
    assert out["same_lock"] is True


def test_arbitrary_registered_name_same_instance():
    """③ 任意注册名（分包 ``lib.etf_data`` 类点号名）：loader 必须按文件识别复用。

    主仓库不具备"单份 lib 包"的分包布局（包内合并布局由构建器产出，另在
    分发形态检查中验证）；此处用显示的点号名把 canonical 文件注册进
    sys.modules，验证 loader 的**按文件识别**而非按固定名识别。
    """
    out = _run(f"""
import importlib.util
sys.path.insert(0, str(root / "skills" / "lib"))
sys.path.insert(0, str(root / "skills" / "invest-a-etf" / "scripts" / "lib"))
spec = importlib.util.spec_from_file_location(
    "lib.etf_data", root / "skills" / "invest-a-etf" / "scripts" / "lib" / "etf_data.py")
pkg_mod = importlib.util.module_from_spec(spec)
sys.modules["lib.etf_data"] = pkg_mod
spec.loader.exec_module(pkg_mod)
from invest_path import load_invest_a_etf_module
b = load_invest_a_etf_module()
print(json.dumps({{
  "same_module": b is pkg_mod,
  "same_lock": b._SPOT_CACHE_LOCK is pkg_mod._SPOT_CACHE_LOCK,
  "canonical_alias": sys.modules.get("invest_a_etf_etf_data") is pkg_mod,
}}))
""")
    assert out["same_module"] is True, "任意注册名的同文件模块必须被复用"
    assert out["same_lock"] is True
    assert out["canonical_alias"] is True


def test_journal_shim_forward_same_instance():
    """④ journal shim：裸导入在前时，shim 转发到同一实例（不再有第二份 canonical）。"""
    out = _run(f"""
import importlib.util
sys.path.insert(0, str(root / "skills" / "lib"))
sys.path.insert(0, str(root / "skills" / "invest-a-etf" / "scripts" / "lib"))
import etf_data                                  # 先占顶层名（CLI 顺序）
spec = importlib.util.spec_from_file_location(
    "journal_etf_shim_probe",
    root / "skills" / "invest-a-journal" / "scripts" / "lib" / "etf_data.py")
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)
print(json.dumps({{
  "canonical_same": sys.modules["invest_a_etf_etf_data"] is etf_data,
  "shim_map_same": shim.CSINDEX_MAP is etf_data.CSINDEX_MAP,
  "shim_callable_same": shim.query_etf_kline is etf_data.query_etf_kline,
}}))
""")
    assert out["canonical_same"] is True, "journal shim 不得加载第二份 canonical"
    assert out["shim_map_same"] is True and out["shim_callable_same"] is True


def test_failed_load_leaves_no_partial_module(tmp_path):
    """⑤ 失败清理：exec 异常时固定名与兼容名都不得残留残破模块。"""
    broken = tmp_path / "lib"
    broken.mkdir()
    (broken / "etf_data.py").write_text(
        "raise RuntimeError('boom-load')\n", encoding="utf-8")
    out = _run(f"""
sys.path.insert(0, str(root / "skills" / "lib"))
from invest_path import load_invest_a_etf_module
import invest_path
invest_path.invest_a_etf_lib_dir = lambda: Path(r"{broken}")
raised = None
try:
    load_invest_a_etf_module()
except RuntimeError as exc:
    raised = str(exc)
print(json.dumps({{
  "raised": raised,
  "canonical_left": "invest_a_etf_etf_data" in sys.modules,
  "toplevel_left": "etf_data" in sys.modules,
}}))
""")
    assert out["raised"] == "boom-load"
    assert out["canonical_left"] is False, "失败后固定名不得残留"
    assert out["toplevel_left"] is False, "失败后兼容名不得残留"
