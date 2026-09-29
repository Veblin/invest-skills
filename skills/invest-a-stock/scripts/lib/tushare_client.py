"""
Tushare HTTP 轻量客户端。

不依赖官方 tushare SDK，直接通过 HTTP JSON 调用 Tushare Pro API。
.env 加载由 lib/env.py 统一处理（本模块不重复加载）。

设计原则：
- Token 无效 → is_available() 返回 False，不抛异常
- Token 有效但配额耗尽 → 静默降级
- Tushare 作为主数据源，与腾讯行情（兜底）配合使用
"""

from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import threading

import requests
import pandas as pd

from .shared_dates import shanghai_days_ago, shanghai_today

logger = logging.getLogger(__name__)

TUSHARE_API_URL = "https://api.tushare.pro"

# Tushare 接口配额限制
DAILY_CALL_LIMIT = 500
RATE_LIMIT_PER_MINUTE = 80

# 官方「积分与频次权限对应表」（https://tushare.pro/document/1?doc_id=290）：
#   120 分 → 50 次/分钟；2000 分以上 → 200；5000 分以上 → 500；10000 分以上 → 500（特色数据 300）
# 用于把「接口要求的最低积分」机械映射成该接口的默认频次预算。
#
# ⚠️ 口径诚实标注：官方页**未明说**限额是按接口还是按账号。故本映射取官方值的
#    1/4 作保守下限（CONSERVATIVE_TIER_DIVISOR），并且**以实例默认值为地板**——
#    即只有「能访问高积分接口 ⇒ 账号必然在高档位」的接口才会放宽，其余一字不动。
#    若观察到 403/限流响应，用环境变量 TUSHARE_RATE_LIMIT_PER_MINUTE 全局下调。
OFFICIAL_TIER_RPM: list[tuple[int, int]] = [  # (所需积分下限, 官方每分钟频次)
    (5000, 500),
    (2000, 200),
    (120, 50),
]
CONSERVATIVE_TIER_DIVISOR = 4

# 官方最低积分（以各接口文档为准；用于降级提示）
# sw_daily: https://tushare.pro/document/2?doc_id=327
TUSHARE_API_MIN_POINTS: dict[str, int] = {
    "stock_basic": 120,
    "daily": 120,
    "fina_indicator": 2000,
    "top10_floatholders": 2000,
    "daily_basic": 2000,
    "moneyflow": 2000,
    "margin_detail": 2000,
    "hsgt_top10": 2000,
    "index_classify": 2000,
    "index_member_all": 2000,  # 申万成分整表（CONFIGURATION.md 同载 2000 档）
    "index_daily": 2000,
    "index_dailybasic": 4000,
    "sw_daily": 5000,
    "opt_daily": 5000,
    "forecast": 2000,   # 业绩预告（接口文档标注；_q_tushare_forecast docstring 同载）
}


def api_min_points(api_name: str) -> int | None:
    """返回接口文档标注的最低积分，未知则 None。"""
    return TUSHARE_API_MIN_POINTS.get(api_name)


def official_tier_rpm(points: int) -> int | None:
    """按账号积分档位返回官方每分钟频次；低于最低档返回 None。"""
    for min_points, rpm in OFFICIAL_TIER_RPM:
        if points >= min_points:
            return rpm
    return None


def _env_rate_limit() -> int | None:
    """TUSHARE_RATE_LIMIT_PER_MINUTE 覆盖（非正整数/缺省 → None，用默认值）。"""
    raw = os.environ.get("TUSHARE_RATE_LIMIT_PER_MINUTE")
    if raw and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return None


def rate_limit_for_api(
    api_name: str, floor: int = RATE_LIMIT_PER_MINUTE, *, ceiling: int | None = None,
) -> int:
    """该接口的每分钟调用预算；显式降速时用 ceiling 封顶。

    能访问某接口 ⇒ 账号积分 ≥ 该接口的最低积分 ⇒ 官方频次至少是该档位值。
    取 1/4 作保守下限（官方页未明说限额按接口还是按账号），并**以 floor 为地板**：
    只有 5000 档接口放宽，其余保持默认 80/min 不变。

    实测效果：opt_daily(5000) → max(80, 125) = 125；daily_basic(2000) → max(80, 50) = 80；
    daily(120) → max(80, 12) = 80；未知接口 → 80。

    例：`opt_daily` 原受 80/min 自节流，~121 次调用的理论最短窗口 ≈ 90.8s；
    按 125/min 降至 ≈ 58.1s（理论值，非实测墙钟）。
    """
    points = TUSHARE_API_MIN_POINTS.get(api_name)
    rpm = official_tier_rpm(points) if points is not None else None
    budget = max(floor, rpm // CONSERVATIVE_TIER_DIVISOR) if rpm is not None else floor
    return min(budget, ceiling) if ceiling is not None else budget

# 积分/权限不足（预期降级，非异常）
_PERMISSION_DENIED_CODES = frozenset({40203})


def _is_permission_denied(code: int, msg: str) -> bool:
    if code in _PERMISSION_DENIED_CODES:
        return True
    return "访问权限" in msg or "没有接口" in msg


_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _next_beijing_midnight_reset_at(now: float | None = None) -> float:
    """Return Unix timestamp of the next Asia/Shanghai (UTC+8) midnight."""
    if now is None:
        now = time.time()
    dt = datetime.fromtimestamp(now, tz=_BEIJING_TZ)
    next_day = dt.date() + timedelta(days=1)
    next_midnight = datetime.combine(next_day, datetime.min.time(), tzinfo=_BEIJING_TZ)
    return next_midnight.timestamp()


class TushareClient:
    """Tushare Pro HTTP 轻量客户端。

    不依赖官方 SDK，直接 HTTP POST JSON 调用。
    借鉴 daily_stock_analysis/data_provider/tushare_fetcher.py 的生产实践。
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: int = 30,
        rate_limit_per_minute: int | None = None,
        daily_call_limit: int | None = None,
    ):
        # 三级降级：显式 token → 环境变量 → .env 文件（env.get_config 惰性加载）。
        # .env 不会自动注入 os.environ，裸 TushareClient() 此前在该场景静默
        # 缺 token、is_available 恒 False（universe.py 3342bf6 同型修复下沉到此处）。
        self._token = token or os.environ.get("TUSHARE_TOKEN")
        if self._token is None:
            try:
                from lib import env
                self._token = env.get_config().get("TUSHARE_TOKEN")
            except Exception:
                self._token = None
        self._timeout = timeout
        # 显式参数 > 环境变量 TUSHARE_RATE_LIMIT_PER_MINUTE > 默认 80。
        # env 统一在此解析，避免各调用方（market_daily / futures_data）各写一份。
        # 显式值是账号总量及各接口的上限；默认值用于低积分接口的地板。
        self._explicit_rate_limit = rate_limit_per_minute is not None
        if rate_limit_per_minute is None:
            rate_limit_per_minute = _env_rate_limit()
            self._explicit_rate_limit = rate_limit_per_minute is not None
        self._rate_limit_per_minute = (
            RATE_LIMIT_PER_MINUTE if rate_limit_per_minute is None else rate_limit_per_minute
        )
        self._daily_call_limit = (
            DAILY_CALL_LIMIT if daily_call_limit is None else daily_call_limit
        )
        self._session = requests.Session()
        self._session.trust_env = False
        # 按**接口名分桶**的 60s 滑动窗口。分桶是必需的：若仍用单一全局窗口，
        # 即便 rate_limit_for_api 放开了 opt_daily 的预算，全局计数照样会把它卡在 80。
        self._call_timestamps: dict[str, list[float]] = {}
        self._total_call_timestamps: list[float] = []
        self._daily_calls = 0
        # 限流计数器并发保护：_map_parallel 的 PCR/创新高面板 fan-out
        # （默认 8 线程）共享同一实例并发 query，append+重绑定的复合操作
        # 在无锁下丢失更新会低估调用数 → 80/min 自节流失效
        self._lock = threading.Lock()
        # 当日结束时重置计数器（Tushare 日配额按北京时间 UTC+8 零点重置）
        self._daily_reset_at = _next_beijing_midnight_reset_at(time.time())
        self._permission_denied_apis: set[str] = set()
        # 账号积分档位的**已证明下限**：仅当某个 ≥该档的接口实际返回成功才抬升。
        # 「能调用某接口 ⇒ 账号积分 ≥ 该接口门槛」只对该接口成立，不能推广到账号
        # 总量——未证明前总桶保持地板，避免只用低档接口的账号被机械放宽（评审 P2）。
        self._proven_points: int = 0
        # 最近一次 query 的失败原因（None=成功，含合法空结果）。
        # 失败路径一律返回空 DataFrame 不抛异常（本类契约）→ 调用方若要区分
        # 「真空窗」与「取数失败」必须读此信号（R1 审查 F1：forecast 曾假设
        # query 会抛/返回 None，导致失败检测永不触发）。
        self.last_error: str | None = None
        # 在初始化时捕获代理设置，供显式传入 Session（trust_env=False）
        self._proxies: dict[str, str] = {}
        for key in ("http", "https"):
            val = os.environ.get(f"{key}_proxy") or os.environ.get(f"{key.upper()}_PROXY")
            if val:
                self._proxies[key] = val

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """检测 Token 是否有效且可连接。

        返回 False 而非抛异常，调用方据此决定降级策略。
        """
        if not self._token:
            logger.info("Tushare: 未配置 TUSHARE_TOKEN，跳过")
            return False
        try:
            result = self.query(
                "stock_basic",
                ts_code="600519.SH",
                fields="ts_code,name",
            )
            return result is not None and not result.empty
        except Exception as e:
            logger.warning("Tushare: 连接测试失败 — %s", e)
            return False

    def remaining_calls_today(self) -> int:
        """今日剩余配额（估估值）。"""
        self._reset_daily_counter_if_needed()
        return max(0, self._daily_call_limit - self._daily_calls)

    def is_permission_denied(self, api_name: str) -> bool:
        """该接口是否已被判定为**权限不足**（本会话内）。

        调用方用它产出**用户可读的权限提示**（如「需 N 积分」），而不是把
        「空数据 / 超时 / 权限不足」压成同一句文案。积分门槛见
        ``api_min_points(api_name)``（TUSHARE_API_MIN_POINTS）。
        """
        return api_name in self._permission_denied_apis

    def query(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        """统一查询入口。

        Args:
            api_name: Tushare 接口名，如 "daily"、"stock_basic"
            fields: 逗号分隔的字段列表，空字符串表示全部字段
            **kwargs: 接口参数（如 ts_code="600519.SH"）

        Returns:
            pd.DataFrame，失败时返回空 DataFrame（并置 ``last_error`` 说明原因；
            合法空结果时 ``last_error`` 为 None）
        """
        self.last_error = None
        if not self._token:
            logger.debug("Tushare: 无 Token，跳过 query(%s)", api_name)
            self.last_error = "未配置 TUSHARE_TOKEN"
            return pd.DataFrame()

        if api_name in self._permission_denied_apis:
            logger.debug("Tushare: 跳过 %s（本会话已确认无接口权限）", api_name)
            self.last_error = f"无接口权限（本会话已确认）: {api_name}"
            return pd.DataFrame()

        self._reset_daily_counter_if_needed()
        self._wait_for_rate_limit(api_name, reserve=True)

        payload: dict[str, Any] = {
            "api_name": api_name,
            "token": self._token,
            "params": kwargs,
        }
        if fields:
            payload["fields"] = fields

        try:
            resp = self._session.post(
                TUSHARE_API_URL,
                json=payload,
                timeout=self._timeout,
                proxies=self._proxies if self._proxies else None,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                code = data.get("code", -1)
                msg = str(data.get("msg", ""))
                if code == -2002:
                    logger.error("Tushare: Token 无效 (%s)", api_name)
                elif code == -2001:
                    logger.warning("Tushare: 配额已用完 (%s)", api_name)
                elif _is_permission_denied(code, msg):
                    self._permission_denied_apis.add(api_name)
                    min_pts = api_min_points(api_name)
                    if min_pts:
                        logger.debug(
                            "Tushare: %s 无接口权限 code=%s（文档最低 %s 积分，已降级）",
                            api_name, code, min_pts,
                        )
                    else:
                        logger.debug(
                            "Tushare: %s 无接口权限 code=%s（已降级）",
                            api_name, code,
                        )
                else:
                    logger.warning(
                        "Tushare: %s 返回错误 code=%s msg=%s",
                        api_name, code, msg,
                    )
                self.last_error = f"code={code} {msg[:80]}"
                return pd.DataFrame()

            # code==0 即证明账号可访问该接口 ⇒ 积分 ≥ 该接口门槛（供账号总桶预算用）。
            # 空结果同样算证明：非交易日返回空帧也是「有权限」的合法响应。
            _pts = api_min_points(api_name)
            if _pts is not None:
                with self._lock:
                    if _pts > self._proven_points:
                        self._proven_points = _pts

            data_obj = data.get("data", {})
            if not data_obj:
                return pd.DataFrame()

            items = data_obj.get("items", [])
            if not items:
                return pd.DataFrame()

            fields_list = data_obj.get("fields", [])
            if not fields_list:
                # 从请求中推断
                if fields:
                    fields_list = fields.split(",")
                else:
                    return pd.DataFrame()

            df = pd.DataFrame(items, columns=fields_list)
            return df

        except requests.RequestException as e:
            logger.warning("Tushare: 网络请求失败 %s — %s", api_name, e)
            self.last_error = f"{type(e).__name__}: {str(e)[:80]}"
            return pd.DataFrame()
        except Exception as e:
            logger.warning("Tushare: 查询 %s 异常 — %s", api_name, e)
            self.last_error = f"{type(e).__name__}: {str(e)[:80]}"
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _record_call(self, api_name: str) -> None:
        with self._lock:
            now = time.time()
            bucket = self._call_timestamps.setdefault(api_name, [])
            bucket.append(now)
            self._total_call_timestamps.append(now)
            self._daily_calls += 1
            # 只保留最近 60 秒的记录
            cutoff = now - 60
            self._call_timestamps[api_name] = [t for t in bucket if t > cutoff]
            self._total_call_timestamps = [t for t in self._total_call_timestamps if t > cutoff]

    def _wait_for_rate_limit(self, api_name: str, *, reserve: bool = False) -> None:
        """遵守接口与账号总量的 60 秒窗口；query 在放行时预占名额。

        预算由 `rate_limit_for_api(api_name, floor=self._rate_limit_per_minute)` 给出：
        地板保证不会比旧行为更激进，档位映射只对高积分接口放宽。
        账号总桶同理：未证明更高档位前保持地板（见 `_proven_points`）。

        预占在锁内完成，并发请求不能同时看到同一剩余额度。
        """
        limit = rate_limit_for_api(
            api_name, floor=self._rate_limit_per_minute,
            ceiling=self._rate_limit_per_minute if self._explicit_rate_limit else None,
        )
        # 账号总桶：未证明更高档位前一律用地板。旧写法无条件取档位表首项推导值
        # （5000 档 → 125），使只用低档接口的账号也被放宽，与「不得机械提高并发」
        # 相悖（评审 P2）。档位由 _proven_points 决定：成功调用过 opt_daily/sw_daily
        # 等 5000 档接口后才抬到 125；2000 档推导值 50 < 地板，仍保持地板。
        total_limit = (
            self._rate_limit_per_minute if self._explicit_rate_limit
            else max(RATE_LIMIT_PER_MINUTE,
                     (official_tier_rpm(self._proven_points) or 0) // CONSERVATIVE_TIER_DIVISOR)
        )
        while True:
            with self._lock:
                now = time.time()
                cutoff = now - 60
                bucket = [t for t in self._call_timestamps.get(api_name, []) if t > cutoff]
                total = [t for t in self._total_call_timestamps if t > cutoff]
                self._call_timestamps[api_name] = bucket
                self._total_call_timestamps = total
                waits = []
                if len(bucket) >= limit:
                    waits.append(bucket[0] + 60 - now)
                if len(total) >= total_limit:
                    waits.append(total[0] + 60 - now)
                if not waits:
                    if reserve:
                        bucket.append(now)
                        total.append(now)
                        self._daily_calls += 1
                    return
                wait = max(waits)
            logger.debug("Tushare: 频率限制 (%s)，等待 %.1fs", api_name, wait)
            time.sleep(max(wait, 0.001))

    def _reset_daily_counter_if_needed(self) -> None:
        with self._lock:
            now = time.time()
            if now >= self._daily_reset_at:
                self._daily_calls = 0
                self._daily_reset_at = _next_beijing_midnight_reset_at(now)

    # ------------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path
    _d = Path(__file__).parent.parent
    sys.path.insert(0, str(_d))
    from lib.env import ensure_env_loaded
    ensure_env_loaded()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = TushareClient()
    available = client.is_available()
    print(f"Tushare available: {available}")
    if available:
        end = shanghai_today()
        start = shanghai_days_ago(5)
        df = client.query("daily", ts_code="600519.SH",
                          start_date=start, end_date=end,
                          fields="trade_date,open,high,low,close")
        print(df)
        print(f"今日剩余配额: {client.remaining_calls_today()}")
    else:
        print("Tushare 不可用（无 Token 或网络不通），这是正常的降级状态。")
    client.close()
