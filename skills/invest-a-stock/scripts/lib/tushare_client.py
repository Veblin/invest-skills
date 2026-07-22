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

import requests
import pandas as pd

logger = logging.getLogger(__name__)

TUSHARE_API_URL = "http://api.tushare.pro"

# Tushare 接口配额限制
DAILY_CALL_LIMIT = 500
RATE_LIMIT_PER_MINUTE = 80

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
    "index_daily": 2000,
    "index_dailybasic": 4000,
    "sw_daily": 5000,
    "opt_daily": 5000,
}


def api_min_points(api_name: str) -> int | None:
    """返回接口文档标注的最低积分，未知则 None。"""
    return TUSHARE_API_MIN_POINTS.get(api_name)

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
        self._token = token or os.environ.get("TUSHARE_TOKEN")
        self._timeout = timeout
        self._rate_limit_per_minute = (
            RATE_LIMIT_PER_MINUTE if rate_limit_per_minute is None else rate_limit_per_minute
        )
        self._daily_call_limit = (
            DAILY_CALL_LIMIT if daily_call_limit is None else daily_call_limit
        )
        self._session = requests.Session()
        self._session.trust_env = False
        self._call_timestamps: list[float] = []
        self._daily_calls = 0
        # 当日结束时重置计数器（Tushare 日配额按北京时间 UTC+8 零点重置）
        self._daily_reset_at = _next_beijing_midnight_reset_at(time.time())
        self._permission_denied_apis: set[str] = set()
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

    def query(self, api_name: str, fields: str = "", **kwargs: Any) -> pd.DataFrame:
        """统一查询入口。

        Args:
            api_name: Tushare 接口名，如 "daily"、"stock_basic"
            fields: 逗号分隔的字段列表，空字符串表示全部字段
            **kwargs: 接口参数（如 ts_code="600519.SH"）

        Returns:
            pd.DataFrame，失败时返回空 DataFrame
        """
        if not self._token:
            logger.debug("Tushare: 无 Token，跳过 query(%s)", api_name)
            return pd.DataFrame()

        if api_name in self._permission_denied_apis:
            logger.debug("Tushare: 跳过 %s（本会话已确认无接口权限）", api_name)
            return pd.DataFrame()

        self._reset_daily_counter_if_needed()
        self._wait_for_rate_limit()

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
                return pd.DataFrame()

            self._record_call()

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
            return pd.DataFrame()
        except Exception as e:
            logger.warning("Tushare: 查询 %s 异常 — %s", api_name, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _record_call(self) -> None:
        now = time.time()
        self._call_timestamps.append(now)
        self._daily_calls += 1
        # 只保留最近 60 秒的记录
        cutoff = now - 60
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]

    def _wait_for_rate_limit(self) -> None:
        """遵守实例级频率限制（默认 80 次/分钟）。"""
        cutoff = time.time() - 60
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        recent_calls = len(self._call_timestamps)
        limit = self._rate_limit_per_minute
        if recent_calls >= limit:
            wait = 1.0 + (recent_calls - limit + 1) * 0.75
            logger.debug("Tushare: 频率限制，等待 %.1fs", wait)
            time.sleep(min(wait, 5.0))

    def _reset_daily_counter_if_needed(self) -> None:
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
    import datetime, sys
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
        end = datetime.date.today().strftime("%Y%m%d")
        start = (datetime.date.today() - datetime.timedelta(days=5)).strftime("%Y%m%d")
        df = client.query("daily", ts_code="600519.SH",
                          start_date=start, end_date=end,
                          fields="trade_date,open,high,low,close")
        print(df)
        print(f"今日剩余配额: {client.remaining_calls_today()}")
    else:
        print("Tushare 不可用（无 Token 或网络不通），这是正常的降级状态。")
    client.close()
