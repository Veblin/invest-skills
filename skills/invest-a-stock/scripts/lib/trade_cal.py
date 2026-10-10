"""Shim: canonical implementation at skills/lib/trade_cal.py (A02 同族, v0.3.2).

身份保持 shim（非逐名转发）：加载顶层 canonical 后把本模块在 sys.modules
中**替换为 canonical 模块对象** —— ``lib.trade_cal`` 与顶层 ``trade_cal``
是同一实例。

动机（A02 同族扫描的直接证据）：共享层消费者（如 ``freshness.trading_day_lag``）
已按顶层 canonical 导入；若本 shim 仍逐名转发，则 ``patch("lib.trade_cal.
fetch_trade_cal")`` 只改 shim 属性、canonical 消费者看不到（seam 分叉）。
身份替换后两个名字是同一对象，patch 任一名都命中消费方查找点（D13）。
独立包内由构建器以 canonical 文件取代本 shim（包内单实例）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_shared_lib = str(Path(__file__).resolve().parents[3] / "lib")
if _shared_lib not in sys.path:
    sys.path.insert(0, _shared_lib)

from invest_path import _load_shared_module  # noqa: E402

_canonical = _load_shared_module("trade_cal", shim_name=__name__)

sys.modules[__name__] = _canonical
