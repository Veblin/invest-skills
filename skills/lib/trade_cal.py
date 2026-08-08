"""交易日历共享模块（C8 收敛 gap-scan scan._fetch_trade_cal + limit-up tushare_enrich.get_trade_dates）。

两种语义：
- ``fetch_trade_cal(start, end) -> (list[str], bool)``：区间交易日 + is_estimated（gap-scan）
- ``last_trade_dates(n) -> list[str]``：最近 N 个交易日 YYYYMMDD 降序（limit-up，复用 fetch_trade_cal）

Tushare trade_cal 优先；无 token/不可用/失败 → 自然日去周末估算（节假日无法由
日期推断，属已知近似——估算路径显式标注 is_estimated / 不静默）。

stock _orchestrate.py 的 PCR 窗口保持内联（复用既有 tc、无兜底语义，与
本模块估算语义不同，收敛有行为风险——见 review 第三轮 #12 说明）。

TushareClient / env 惰性导入（经各 skill _invest_path shim 可达）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# import 探测模块级一次完成（review 第三轮 #6：不得在裸 except Exception 内
# 吞 import 失败——引导失败应显式走估算路径而非伪装成数据降级）
try:
    from lib import env
    from lib.tushare_client import TushareClient
except ImportError:  # skills/lib 裸环境（无 invest-a-stock 挂载）
    env = None  # type: ignore[assignment]
    TushareClient = None  # type: ignore[assignment]

_CLIENT: Any | None = None  # 模块级缓存（对齐旧 tushare_enrich._get_client probe-once 语义）


def _client() -> Any | None:
    """获取可用的 TushareClient；无 token/不可用 → None（零网络快路径）。

    review 第三轮 #5：旧 limit-up 路径有 is_tushare_available 探测 +
    模块级缓存——无 token 部署零网络。重构后必须保持该快路径。
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if env is None:
        return None
    try:
        config = env.get_config()
        if not env.is_tushare_available(config):
            return None
        client = TushareClient(token=config.get("TUSHARE_TOKEN"), timeout=15)
        if not client.is_available():
            return None
        _CLIENT = client
    except Exception as exc:
        logger.warning("TushareClient 初始化失败，静默降级: %s", exc)
        _CLIENT = None
    return _CLIENT


def _shanghai_today() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y%m%d")


def _shanghai_days_ago(n: int) -> str:
    return (datetime.now(_SHANGHAI) - timedelta(days=n)).strftime("%Y%m%d")


def _estimate_trade_dates(start_date: str, end_date: str) -> list[str]:
    """粗略估算交易日（自然日去周末，仅兜底；节假日混入属已知近似）。"""
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    dates: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _fallback_weekdays(n: int) -> list[str]:
    """无 token 兜底：自然日去周末，返回恰好 n 个工作日（YYYYMMDD 降序）。

    工作日约占自然日 5/7 → 用 1.6 倍 + 余量窗口采样，保证取满 n 个。
    """
    span = max(int(n * 1.6) + 3, 10)
    out: list[str] = []
    for i in range(span):
        d = _shanghai_days_ago(i)
        if datetime.strptime(d, "%Y%m%d").weekday() >= 5:
            continue
        out.append(d)
        if len(out) >= n:
            break
    return out


def fetch_trade_cal(start_date: str, end_date: str) -> tuple[list[str], bool]:
    """获取交易日列表，返回 (trade_dates, is_estimated)。

    优先使用 Tushare trade_cal（SSE is_open=1）；无 token/不可用/失败/空
    → 自然日估算（is_estimated=True）。
    """
    client = _client()
    if client is None:
        # 无 token/不可用 → 零网络估算（review 第三轮 #5：不构造 client 发请求）
        return _estimate_trade_dates(start_date, end_date), True
    try:
        cal = client.query(
            "trade_cal", exchange="SSE", is_open="1",
            start_date=start_date, end_date=end_date,
        )
    except Exception as exc:
        logger.warning("Tushare trade_cal 请求失败: %s", exc)
        return _estimate_trade_dates(start_date, end_date), True
    if cal is None or cal.empty:
        logger.warning("Tushare trade_cal 返回空，使用自然日估算")
        return _estimate_trade_dates(start_date, end_date), True
    date_col = "cal_date" if "cal_date" in cal.columns else "trade_date"
    return sorted(cal[date_col].astype(str).tolist()), False


def last_trade_dates(n: int) -> list[str]:
    """获取最近 N 个交易日（YYYYMMDD 降序）。

    review 第三轮 #7：复用 fetch_trade_cal（服务端已过滤 is_open=1，
    客户端 is_open 过滤冗余）——消除双份查询 body 的漂移面。
    """
    end = _shanghai_today()
    start = _shanghai_days_ago(max(n * 2, 14))
    dates, _ = fetch_trade_cal(start, end)
    if not dates:
        return _fallback_weekdays(n)
    return sorted(dates, reverse=True)[:n]
