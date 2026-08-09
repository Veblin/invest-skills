"""原始数据存档模块。

将采集结果保存为时间戳命名的 JSON + WebSearch 附录，
支持 diff 子命令对比。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .json_util import dumps_json


DEFAULT_RAW_DIR = os.path.expanduser("~/.local/share/investment/raw/")


def _ensure_dir(path: str) -> Path:
    """确保目录存在。"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_collection(
    symbol: str,
    result: dict[str, Any],
    raw_dir: str = DEFAULT_RAW_DIR,
) -> str | None:
    """保存原始采集 JSON 到时间戳命名文件。

    Args:
        symbol: 股票代码
        result: collect_all 的输出
        raw_dir: 存档目录

    Returns:
        存档文件路径，失败返回 None
    """
    try:
        base = _ensure_dir(raw_dir)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-{symbol}.json"
        filepath = base / filename

        # 序列化为 JSON（处理 datetime 等不可序列化类型）
        payload = dumps_json(result)
        filepath.write_text(payload, encoding="utf-8")

        return str(filepath)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("archive_collection failed: %s", exc)
        return None


