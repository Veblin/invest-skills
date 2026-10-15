"""Shim: canonical implementation at skills/lib/market_microstructure.py (A03 owner 迁移, v0.3.2).

身份保持 shim（非逐名转发）：经共享 invest_path.load_journal_module() 解析
**公共 canonical**（skills/lib）后，把本模块在 sys.modules 中**替换为 canonical
模块对象** —— ``import market_microstructure``（journal 上下文）与
``load_journal_module()`` 是同一实例：

- monkeypatch 接缝不变（``patch(market_microstructure, "snapshot")`` 命中 canonical，
  data_bridge.get_microstructure 经 loader 复用同一份）；
- 导出面 = canonical 全表面（不逐名转发，新增名字自动可见）；
- 公共 provider 不依赖 journal 源码：canonical 在 skills/lib 独立可用，
  本 shim 只作旧路径（``cd scripts/lib``）兼容；独立包内由构建器以
  canonical 文件取代本 shim（包内单实例）。

背景：市场微观结构/快照/两融/涨停（含 zt_industry_flow / zt_seesaw /
zt_rotation_bundle）是公共市场服务，owner 已自 journal 私有目录迁至共享层。
"""

from __future__ import annotations

import sys
from pathlib import Path

_skills_lib = Path(__file__).resolve().parents[3] / "lib"
if str(_skills_lib) not in sys.path:
    sys.path.insert(0, str(_skills_lib))

from invest_path import load_journal_module  # noqa: E402

_canonical = load_journal_module()

sys.modules[__name__] = _canonical
