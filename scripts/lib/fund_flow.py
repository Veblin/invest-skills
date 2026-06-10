"""
资金流数据模块。

覆盖：
- 北向资金（沪股通/深股通）流向
- 南向资金（港股通）流向
- ETF 份额变动（Phase 6 实现，MVP 返回占位）

数据来源：akshare（东方财富资金流接口）→ Tushare（有 Token 时并列）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 北向资金
# ------------------------------------------------------------------

def get_northbound_flow(code: str | None = None) -> dict[str, Any]:
    """获取北向资金（沪股通/深股通）流向。

    Args:
        code: 可选个股代码，为 None 时返回市场整体数据

    Returns:
        dict 含 data + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    try:
        import akshare as ak
        attempted_sources.append("akshare")

        # 北向资金历史流向（沪股通+深股通合计）
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if not df.empty:
            latest = df.iloc[-1]
            result = {
                "latest_date": str(latest.get("日期", "")),
                "net_flow_rmb": float(latest.get("净流入", 0) or 0),
                "cumulative_flow": float(latest.get("累计净流入", 0) or 0),
                "recent_20_days": _summarize_recent_flow(df, days=20),
            }
            source = "akshare.stock_hsgt_hist_em"
            source_group = "eastmoney"
    except Exception as e:
        logger.warning("北向资金 akshare 获取失败: %s", e)

    # Fallback: Tushare
    if not result:
        try:
            from scripts.lib.tushare_client import TushareClient
            client = TushareClient()
            if client.is_available():
                attempted_sources.append("tushare")
                df = client.query("moneyflow_hsgt")
                if not df.empty:
                    result = {
                        "latest_date": str(df.iloc[0].get("trade_date", "")),
                        "source": "tushare",
                    }
                    source = "tushare.moneyflow_hsgt"
                    source_group = "official"
                client.close()
        except Exception as e:
            logger.warning("北向资金 Tushare 获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {
            "error": "北向资金数据不可得",
            "attempted_sources": attempted_sources,
        }

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.stock_hsgt_hist_em", "tushare.moneyflow_hsgt"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


def _summarize_recent_flow(df: pd.DataFrame, days: int = 20) -> dict[str, Any]:
    """汇总近 N 日流向。"""
    recent = df.tail(days)
    net_flows = pd.to_numeric(recent.get("净流入", pd.Series([0])), errors="coerce")
    return {
        "net_total": float(net_flows.sum()),
        "net_avg_daily": float(net_flows.mean()) if len(net_flows) > 0 else 0.0,
        "positive_days": int((net_flows > 0).sum()),
        "total_days": len(net_flows),
    }


# ------------------------------------------------------------------
# 南向资金
# ------------------------------------------------------------------

def get_southbound_flow(code: str | None = None) -> dict[str, Any]:
    """获取南向资金（港股通）流向。

    Args:
        code: 可选个股代码

    Returns:
        dict 含 data + _meta
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    try:
        import akshare as ak
        attempted_sources.append("akshare")

        df = ak.stock_hsgt_hist_em(symbol="南向资金")
        if not df.empty:
            latest = df.iloc[-1]
            result = {
                "latest_date": str(latest.get("日期", "")),
                "net_flow_hkd": float(latest.get("净流入", 0) or 0),
                "recent_20_days": _summarize_recent_flow(df, days=20),
            }
            source = "akshare.stock_hsgt_hist_em"
            source_group = "eastmoney"
    except Exception as e:
        logger.warning("南向资金 akshare 获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {
            "error": "南向资金数据不可得",
            "attempted_sources": attempted_sources,
        }

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.stock_hsgt_hist_em"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# ETF 份额变动（Phase 6，MVP 占位）
# ------------------------------------------------------------------

def get_etf_share_change(etf_code: str) -> dict[str, Any]:
    """获取 ETF 份额变动。

    Phase 6 实现完整逻辑。MVP 返回占位信息。
    """
    return {
        "etf_code": etf_code,
        "status": "Phase 6 实现",
        "note": "ETF 份额变动数据将在 Phase 6 接入（akshare 东方财富 ETF 中心）",
        "_meta": _build_meta(
            source=None,
            source_group=None,
            fallback_chain=[],
            attempted_sources=[],
            latency_ms=0,
            success=False,
        ),
    }


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
    warning: str | None = None,
    rows_fetched: int | None = None,
) -> dict[str, Any]:
    """构建 _meta 字段（符合架构关系 §2.2 契约）。"""
    meta: dict[str, Any] = {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": _confidence_from_result(source, success),
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": None if success else ("empty" if attempted_sources else "network"),
    }
    if warning:
        meta["warning"] = warning
    if rows_fetched is not None:
        meta["rows_fetched"] = rows_fetched
    return meta


def _confidence_from_result(source: str | None, success: bool) -> str:
    """根据数据源和结果自评可信度。"""
    if not success:
        return "low"
    if source and ("tushare" in source or "fred" in source):
        return "high"
    if source and "akshare" in source:
        return "medium"
    return "medium"


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== 北向资金 ===")
    nb = get_northbound_flow()
    print(nb.get("_meta", {}))
    if "error" not in nb:
        print(f"最新净流入: {nb.get('net_flow_rmb')} 亿元")
        recent = nb.get("recent_20_days", {})
        print(f"近20日: 净{recent.get('net_total', 0):+.1f}亿, "
              f"正流入 {recent.get('positive_days', 0)}/{recent.get('total_days', 0)} 天")

    print("\n=== 南向资金 ===")
    sb = get_southbound_flow()
    print(sb.get("_meta", {}))
    if "error" not in sb:
        print(f"最新净流入: {sb.get('net_flow_hkd')} 亿元")
