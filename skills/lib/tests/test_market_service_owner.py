"""A03 公共市场服务 owner 契约：canonical 位置 / 单实例 / 旧路径退出。

验证（v0.3.2 A03，配合 skills/lib/references/market-service-owner.md）：
1. canonical 在 skills/lib，journal 侧为身份 shim（顶层名解析到同一文件）；
2. 两种 sys.path 顺序（journal lib 先 / skills/lib 先）都是**单实例**
   （独立 subprocess，避免 pytest 会话既成导入顺序掩盖顺序依赖）；
3. `load_journal_module()` 在 journal 源码不在 path 时仍可用（provider 不依赖 journal）；
4. 旧路径退出：pulse SKILL.md 不再 cd journal 私有目录、stock invest.py 不再注入
   journal 路径；journal 自身命令仍可用（shim 存在）。
"""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_SKILLS = Path(__file__).resolve().parents[2]
_SKILLS_LIB = _SKILLS / "lib"
_JOURNAL_LIB = _SKILLS / "invest-a-journal" / "scripts" / "lib"
_STOCK_SCRIPTS = _SKILLS / "invest-a-stock" / "scripts"
_REPO_ROOT = _SKILLS.parent

for _p in (str(_SKILLS_LIB), str(_STOCK_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@lru_cache(maxsize=None)
def _run_order(journal_first: bool) -> str:
    """子进程按指定顺序 import market_microstructure 与 load_journal_module。"""
    paths = [str(_JOURNAL_LIB), str(_SKILLS_LIB)] if journal_first else [
        str(_SKILLS_LIB), str(_JOURNAL_LIB)]
    code = (
        "import sys\n"
        f"sys.path[:0] = {paths!r}\n"
        f"sys.path.append({str(_STOCK_SCRIPTS)!r})\n"
        "import market_microstructure as m\n"
        "from invest_path import load_journal_module\n"
        "loaded = load_journal_module()\n"
        "assert loaded is m, (loaded, m)\n"
        "assert m.__file__.endswith('skills/lib/market_microstructure.py'), m.__file__\n"
        "assert callable(m.snapshot) and callable(m.zt_rotation_bundle)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(_REPO_ROOT), timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_single_instance_journal_lib_first():
    assert _run_order(True) == "ok"


def test_single_instance_shared_lib_first():
    assert _run_order(False) == "ok"


def test_provider_independent_of_journal_source():
    """journal 目录不在 path：公共 canonical 与 loader 均可独立加载。"""
    code = (
        "import sys\n"
        f"sys.path[:0] = [{str(_SKILLS_LIB)!r}]\n"
        f"sys.path.append({str(_STOCK_SCRIPTS)!r})\n"
        "from invest_path import load_journal_module\n"
        "m = load_journal_module()\n"
        "assert m.__file__.endswith('skills/lib/market_microstructure.py'), m.__file__\n"
        "assert 'invest-a-journal' not in getattr(m, '__file__', '')\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(_REPO_ROOT), timeout=180,
    )
    assert proc.returncode == 0, proc.stderr


def test_journal_shim_is_identity_not_second_implementation():
    """journal shim 不做逐名转发实现（体积/关键字锁定防第二实现）。"""
    shim = (_JOURNAL_LIB / "market_microstructure.py").read_text(encoding="utf-8")
    assert "sys.modules[__name__] = _canonical" in shim
    assert "def snapshot(" not in shim
    assert "def zt_rotation_bundle(" not in shim


def test_pulse_skill_no_journal_private_dir():
    """应失败反例：pulse SKILL.md 出现 journal 私有目录 cd → 红。"""
    text = (_SKILLS / "invest-a-pulse" / "SKILL.md").read_text(encoding="utf-8")
    assert "invest-a-journal/scripts/lib" not in text
    # 采集命令走公共 skills/lib；双窗口一次调用
    assert "/skills/lib\"" in text
    assert "zt_rotation_bundle" in text


def test_stock_invest_no_journal_path_injection():
    text = (_STOCK_SCRIPTS / "invest.py").read_text(encoding="utf-8")
    assert "invest-a-journal" not in text
    assert "ensure_skills_lib_on_path" in text
    assert "load_invest_a_etf_module" in text


def test_owner_doc_and_d11_allowlist_documented():
    """owner 文档存在；D11 豁免集含 store/macro（stock-owned provider）。"""
    doc = _SKILLS_LIB / "references" / "market-service-owner.md"
    assert doc.is_file()
    d11 = (_SKILLS_LIB / "tests" / "test_shared_owner_migration.py").read_text(encoding="utf-8")
    for name in ('"lib.store"', '"lib.macro"', '"lib.market_daily"'):
        assert name in d11
