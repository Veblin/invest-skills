"""开发模式日志。INVEST_DEV=1 → stderr INFO（gap-scan 格式）+ 轮转文件；
release（默认）→ 不做任何事（root lastResort 仅 WARNING，零文件 I/O，维持现状）。"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from . import env

_LOG_DIR = env.STORE_DIR / "logs"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 7
_setup_done = False


def setup_logging(dev: bool | None = None) -> bool:
    """启用开发模式日志；返回是否实际启用。幂等（重复调用不重复挂 handler）。

    dev: None 时读 INVEST_DEV=='1'。
    显式 handler 构建而非 basicConfig：root 已有 handler 时 basicConfig 会 no-op。
    release 分支直接返回，不碰 root logger（lastResort 行为原样保留）。
    """
    global _setup_done
    if dev is None:
        dev = os.environ.get("INVEST_DEV") == "1"
    if not dev or _setup_done:
        return _setup_done

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            _LOG_DIR / f"invest_{datetime.now():%Y%m%d}.log",
            maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass  # 目录不可写不致命，退化为仅 stderr
    _setup_done = True
    return True


__all__ = ["setup_logging"]
