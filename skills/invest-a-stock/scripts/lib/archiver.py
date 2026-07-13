"""原始数据存档模块。

将采集结果保存为时间戳命名的 JSON + WebSearch 附录，
支持 diff 子命令对比。
"""

from __future__ import annotations

import json
import os
import re
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


def archive_websearch(
    symbol: str,
    searches: list[dict[str, str]],
    raw_dir: str = DEFAULT_RAW_DIR,
) -> str | None:
    """追加 WebSearch 补充结果到同时间戳的 _websearch.md。

    Args:
        symbol: 股票代码
        searches: [{"query": "...", "summary": "..."}, ...]
        raw_dir: 存档目录

    Returns:
        存档文件路径
    """
    if not searches:
        return None
    try:
        base = _ensure_dir(raw_dir)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-{symbol}_websearch.md"
        filepath = base / filename

        lines = [f"# {symbol} WebSearch 附录", f"存档时间: {ts}", ""]
        for i, s in enumerate(searches, 1):
            lines.append(f"## 搜索 {i}: {s.get('query', '?')}")
            lines.append("")
            lines.append(s.get("summary", "无结果"))
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return str(filepath)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("archive_websearch failed: %s", exc)
        return None


def list_archives(symbol: str = "", raw_dir: str = DEFAULT_RAW_DIR) -> list[dict]:
    """列出存档文件。

    Returns:
        [{"symbol": "...", "timestamp": "...", "filepath": "..."}, ...]
    """
    base = Path(raw_dir)
    if not base.exists():
        return []

    archives = []
    pattern = f"*{symbol}*.json" if symbol else "*.json"
    _archive_re = re.compile(r"^(\d{8}-\d{6})-(.+)$")
    for f in sorted(base.glob(pattern), reverse=True):
        name = f.stem
        m = _archive_re.match(name)
        if m:
            ts, sym = m.group(1), m.group(2)
        else:
            ts, sym = "", name
        archives.append({
            "symbol": sym,
            "timestamp": ts,
            "filepath": str(f),
            "size_bytes": f.stat().st_size,
        })
    return archives
