"""交易日历共享模块（C8 收敛 gap-scan scan._fetch_trade_cal 的交易日获取逻辑）。

两种语义：
- ``fetch_trade_cal(start, end) -> (list[str], bool)``：区间交易日 + is_estimated（gap-scan）
- ``last_trade_dates(n) -> list[str]``：最近 N 个交易日 YYYYMMDD 降序（复用 fetch_trade_cal）

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

    review 第三轮 #5：旧 tushare_enrich 路径有 is_tushare_available 探测 +
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


# 日历缓存（code-review #3：prev/next_trading_day 每次调用发一次 Tushare HTTP，
# R11b 事件对齐对每条事件调 2 次、重叠窗口反复重拉——单段扩展式缓存：
# 首次请求合并新旧范围，后续查询在缓存段内直接切片命中，零重复请求）。
# 请求范围对齐到整月（缓存段月度化）：相邻日期序列的 ±30 天窗口随日期逐日
# 漂移（end 每天 +1），不对齐则每次跨 1 天都扩展请求；对齐后同月内全部命中。
# 日历历史固定；未来日期随时间新增，查询范围超出缓存段时自动扩展刷新。
import calendar as _calendar
import threading as _threading

_TRADE_CAL_LOCK = _threading.Lock()
_TRADE_CAL_SEG: list[str] | None = None      # 升序交易日（已覆盖 [START, END]）
_TRADE_CAL_SEG_START: str | None = None      # YYYYMMDD
_TRADE_CAL_SEG_END: str | None = None
_TRADE_CAL_SEG_ESTIMATED: bool = True


def _clear_trade_cal_cache() -> None:
    """清空日历缓存（测试钩子：fixture 隔离估算/真实两路径）。"""
    global _TRADE_CAL_SEG, _TRADE_CAL_SEG_START, _TRADE_CAL_SEG_END
    global _TRADE_CAL_SEG_ESTIMATED
    with _TRADE_CAL_LOCK:
        _TRADE_CAL_SEG = None
        _TRADE_CAL_SEG_START = None
        _TRADE_CAL_SEG_END = None
        _TRADE_CAL_SEG_ESTIMATED = True


def _month_align_end(d: str) -> str:
    """YYYYMMDD → 所在月最后一天 YYYYMMDD（请求范围月末对齐）。"""
    y, m = int(d[:4]), int(d[4:6])
    return f"{y:04d}{m:02d}{_calendar.monthrange(y, m)[1]:02d}"


def _fetch_trade_cal_impl(start_date: str, end_date: str) -> tuple[list[str], bool]:
    """无缓存的取数实现（缓存命中由 fetch_trade_cal 拦截）。"""
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


def fetch_trade_cal(start_date: str, end_date: str) -> tuple[list[str], bool]:
    """获取交易日列表，返回 (trade_dates, is_estimated)。

    优先使用 Tushare trade_cal（SSE is_open=1）；无 token/不可用/失败/空
    → 自然日估算（is_estimated=True）。带单段扩展式缓存（见模块注释）。
    """
    global _TRADE_CAL_SEG, _TRADE_CAL_SEG_START, _TRADE_CAL_SEG_END, _TRADE_CAL_SEG_ESTIMATED
    with _TRADE_CAL_LOCK:
        if (_TRADE_CAL_SEG is not None and _TRADE_CAL_SEG_START is not None
                and _TRADE_CAL_SEG_START <= start_date <= end_date <= _TRADE_CAL_SEG_END):
            return ([d for d in _TRADE_CAL_SEG if start_date <= d <= end_date],
                    _TRADE_CAL_SEG_ESTIMATED)
        req_start = start_date
        req_end = end_date
        if _TRADE_CAL_SEG_START is not None and _TRADE_CAL_SEG_END is not None:
            req_start = min(start_date, _TRADE_CAL_SEG_START)
            req_end = max(end_date, _TRADE_CAL_SEG_END)
        # 月度对齐（见模块注释）：请求范围含整月，相邻日期序列 prev/next 同月命中
        req_start = req_start[:6] + "01"
        req_end = _month_align_end(req_end)
    # 网络/估算在锁外执行（避免持锁阻塞并发调用）
    dates, estimated = _fetch_trade_cal_impl(req_start, req_end)
    with _TRADE_CAL_LOCK:
        if dates:
            # req 范围 = 旧缓存段与本次窗口的并集 → 直接整体替换（单调扩展）
            _TRADE_CAL_SEG = dates
            _TRADE_CAL_SEG_START = req_start
            _TRADE_CAL_SEG_END = req_end
            _TRADE_CAL_SEG_ESTIMATED = estimated
        return ([d for d in dates if start_date <= d <= end_date], estimated)


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


# 30 天回溯/前瞻窗口：覆盖最长休市段（春节 8+ 天休市 + 调休），保证 ±1 交易日
# 语义在长假前后仍取到真实日历中的最近交易日
_CAL_WINDOW_DAYS = 30


def prev_trading_day(d: datetime.date) -> datetime.date:
    """前一交易日（有 token：SSE 真实日历，含节假日/调休；无 token：周末近似）。

    收敛 etf_timeline 的 holiday-blind 实现（C8 #12）：事件对齐窗口在
    节假日前后不再把休市日当作交易日。
    """
    start = (d - timedelta(days=_CAL_WINDOW_DAYS)).strftime("%Y%m%d")
    end = (d - timedelta(days=1)).strftime("%Y%m%d")
    dates, _ = fetch_trade_cal(start, end)
    if dates:  # 升序；估算路径与真实日历均以 dates[-1] 为「d 前最近交易日」
        return datetime.strptime(dates[-1], "%Y%m%d").date()
    x = d - timedelta(days=1)  # 兜底：周末跳过
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def next_trading_day(d: datetime.date) -> datetime.date:
    """后一交易日（有 token：SSE 真实日历；无 token：周末近似）。"""
    start = (d + timedelta(days=1)).strftime("%Y%m%d")
    end = (d + timedelta(days=_CAL_WINDOW_DAYS)).strftime("%Y%m%d")
    dates, _ = fetch_trade_cal(start, end)
    if dates:
        return datetime.strptime(dates[0], "%Y%m%d").date()
    x = d + timedelta(days=1)  # 兜底：周末跳过
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x
