"""
Tushare HTTP 轻量客户端。

不依赖官方 tushare SDK，直接通过 HTTP JSON 调用 Tushare Pro API。
借鉴 daily_stock_analysis 的 _TushareHttpClient 设计。

设计原则：
- Token 无效 → is_available() 返回 False，不抛异常
- Token 有效但配额耗尽 → 静默降级，在 _meta 中标注
- 与 efinance 并列并行（先到先用），不作为前置拦截器
"""

from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# .env 自动加载（无外部依赖）
# 在模块导入时自动加载项目根目录的 .env 文件到 os.environ
# ------------------------------------------------------------------

def _load_dotenv() -> None:
    """从项目根目录加载 .env 文件到环境变量。"""
    # 查找 .env：优先当前工作目录，再搜索父目录
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",  # scripts/../.env
    ]
    env_path = None
    for p in candidates:
        if p.exists():
            env_path = p
            break

    if env_path is None:
        return

    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 只处理 KEY=VALUE 格式
            match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if match:
                key = match.group(1)
                value = match.group(2).strip().strip('"').strip("'")
                # 不覆盖已存在的环境变量（允许用户手动 export 覆盖）
                if key not in os.environ:
                    os.environ[key] = value
        logger.debug("已加载 .env: %s", env_path)
    except Exception as e:
        logger.debug("加载 .env 失败: %s", e)

_load_dotenv()

TUSHARE_API_URL = "http://api.tushare.pro"

# Tushare 接口配额限制（免费用户）
DAILY_CALL_LIMIT = 500
RATE_LIMIT_PER_MINUTE = 80


class TushareClient:
    """Tushare Pro HTTP 轻量客户端。

    不依赖官方 SDK，直接 HTTP POST JSON 调用。
    借鉴 daily_stock_analysis/data_provider/tushare_fetcher.py 的生产实践。
    """

    def __init__(self, token: str | None = None, timeout: int = 30):
        self._token = token or os.environ.get("TUSHARE_TOKEN")
        self._timeout = timeout
        self._session = requests.Session()
        self._call_timestamps: list[float] = []
        self._daily_calls = 0
        self._daily_reset_at = time.time() + 86400 * 7  # far future until first call

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
        return max(0, DAILY_CALL_LIMIT - self._daily_calls)

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
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                logger.warning(
                    "Tushare: %s 返回错误 code=%s msg=%s",
                    api_name,
                    data.get("code"),
                    data.get("msg", ""),
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
        """遵守 80 次/分钟 的频率限制。"""
        cutoff = time.time() - 60
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        recent_calls = len(self._call_timestamps)
        if recent_calls >= RATE_LIMIT_PER_MINUTE:
            wait = 1.0 + (recent_calls - RATE_LIMIT_PER_MINUTE + 1) * 0.75
            logger.debug("Tushare: 频率限制，等待 %.1fs", wait)
            time.sleep(min(wait, 5.0))

    def _reset_daily_counter_if_needed(self) -> None:
        now = time.time()
        if now > self._daily_reset_at:
            self._daily_calls = 0
            self._daily_reset_at = now + 86400

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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = TushareClient()
    available = client.is_available()
    print(f"Tushare available: {available}")
    if available:
        df = client.query("daily", ts_code="600519.SH", limit=5)
        print(df)
        print(f"今日剩余配额: {client.remaining_calls_today()}")
    else:
        print("Tushare 不可用（无 Token 或网络不通），这是正常的降级状态。")
    client.close()
