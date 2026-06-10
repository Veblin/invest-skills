"""
A 股实时行情数据模块。

提供当前价格、涨跌幅、成交量比、换手率、PE/PB 等实时指标。
与 historical K 线分离——实时行情需要独立的高频、低延迟 fallback 链。

Fallback 链（DSA 验证）：
  腾讯财经(tencent) → akshare(新浪) → efinance → akshare(东财) → 最近K线收盘价
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from typing import Any

import requests
import pandas as pd

logger = logging.getLogger(__name__)


def get_realtime_quote(code: str) -> dict[str, Any]:
    """获取 A 股实时行情快照。

    Args:
        code: 纯数字代码（如 "600519"）

    Returns:
        dict: {
            price, change_pct, volume_ratio, turnover_rate,
            pe_ratio, pb_ratio, total_mv, circ_mv, amplitude,
            source, fetched_at, is_intraday, _meta
        }
    """
    code = str(code).strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "").zfill(6)
    attempted_sources: list[str] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = [
        "腾讯财经(tencent)",
        "akshare(新浪)",
        "efinance/akshare(东财)",
        "最近K线收盘价",
    ]

    result: dict[str, Any] = {}

    # 1. 腾讯财经（DSA 推荐首选，单股查询最稳定）
    try:
        attempted_sources.append("tencent")
        market = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"http://qt.gtimg.cn/q={market}{code}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200 and "~" in resp.text:
            parts = resp.text.split("~")
            if len(parts) > 40:
                result = {
                    "price": _safe_float(parts[3]),
                    "change_pct": _safe_float(parts[32]),
                    "volume_ratio": _safe_float(parts[49]),
                    "turnover_rate": _safe_float(parts[38]),
                    "pe_ratio": _safe_float(parts[39]),
                    "pb_ratio": _safe_float(parts[46]),
                    "total_mv": _safe_float(parts[45]),
                    "circ_mv": _safe_float(parts[44]),
                    "amplitude": _safe_float(parts[43]),
                }
                source = "tencent_finance"
                source_group = "sina"
    except Exception as e:
        logger.debug("腾讯财经实时行情失败: %s", e)

    # 2. akshare 新浪接口
    if not result:
        try:
            import akshare as ak
            attempted_sources.append("akshare_sina")
            df = ak.stock_zh_a_spot()
            if not df.empty:
                match = df[df["代码"] == code]
                if not match.empty:
                    row = match.iloc[0]
                    result = {
                        "price": _safe_float(row.get("最新价")),
                        "change_pct": _safe_float(row.get("涨跌幅")),
                        "volume_ratio": _safe_float(row.get("量比")),
                        "turnover_rate": _safe_float(row.get("换手率")),
                        "pe_ratio": _safe_float(row.get("市盈率-动态")),
                        "pb_ratio": _safe_float(row.get("市净率")),
                        "total_mv": _safe_float(row.get("总市值")),
                        "circ_mv": _safe_float(row.get("流通市值")),
                        "amplitude": _safe_float(row.get("振幅")),
                    }
                    source = "akshare.stock_zh_a_spot"
                    source_group = "sina"
        except Exception as e:
            logger.debug("akshare 新浪实时行情失败: %s", e)

    # 3. efinance（批量拉取，单股也可）
    if not result:
        try:
            import efinance as ef
            attempted_sources.append("efinance")
            spot_df = ef.stock.get_realtime_quotes()
            if not spot_df.empty:
                match = spot_df[spot_df["股票代码"] == code]
                if not match.empty:
                    row = match.iloc[0]
                    result = {
                        "price": _safe_float(row.get("最新价")),
                        "change_pct": _safe_float(row.get("涨跌幅")),
                        "volume_ratio": _safe_float(row.get("量比")),
                        "turnover_rate": _safe_float(row.get("换手率")),
                        "pe_ratio": _safe_float(row.get("动态市盈率")),
                        "pb_ratio": _safe_float(row.get("市净率")),
                        "total_mv": _safe_float(row.get("总市值")),
                        "circ_mv": _safe_float(row.get("流通市值")),
                        "amplitude": _safe_float(row.get("振幅")),
                    }
                    source = "efinance.stock.get_realtime_quotes"
                    source_group = "eastmoney"
        except Exception as e:
            logger.debug("efinance 实时行情失败: %s", e)

    # 4. Fallback: 最近 K 线收盘价
    if not result:
        try:
            from scripts.lib.a_share_data import get_daily_kline
            attempted_sources.append("kline_fallback")
            kline_result = get_daily_kline(code)
            kline_df = kline_result.get("data", pd.DataFrame())
            if not kline_df.empty:
                last_close = _safe_float(kline_df.iloc[-1].get("close", kline_df.iloc[-1].get("收盘")))
                result = {
                    "price": last_close,
                    "change_pct": None,
                    "volume_ratio": None,
                    "turnover_rate": None,
                    "pe_ratio": None,
                    "pb_ratio": None,
                    "total_mv": None,
                    "circ_mv": None,
                    "amplitude": None,
                    "warning": f"非实时，基于 {kline_df.iloc[-1].get('date', kline_df.iloc[-1].get('日期', 'unknown'))} 收盘价",
                }
                source = "kline_close"
                source_group = "historical"
        except Exception as e:
            logger.warning("K线收盘价 fallback 失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {
            "price": None,
            "error": "实时行情不可得",
            "attempted_sources": attempted_sources,
        }

    # 判断盘中/盘后
    now = datetime.now()
    is_intraday = (
        now.weekday() < 5
        and (9, 30) <= (now.hour, now.minute) <= (15, 0)
    )

    result["is_intraday"] = is_intraday
    result["source"] = source or "none"
    result["fetched_at"] = now.isoformat()

    result["_meta"] = {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": _confidence_from_source(source),
        "latency_ms": round(latency, 1),
        "success": bool(source) and source != "none",
        "error_type": None if source else "empty",
        **({"warning": result.get("warning")} if result.get("warning") else {}),
    }

    return result


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _confidence_from_source(source: str | None) -> str:
    if not source or source == "none":
        return "low"
    if source == "tencent_finance":
        return "high"
    if source and ("akshare" in source or "efinance" in source):
        return "medium"
    if source == "kline_close":
        return "low"
    return "low"


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    quote = get_realtime_quote(code)
    print(json.dumps({k: v for k, v in quote.items() if k != "_meta"}, indent=2, ensure_ascii=False, default=str))
    print(f"\n_meta: {quote.get('_meta')}")
