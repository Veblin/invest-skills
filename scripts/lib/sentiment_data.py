"""
情绪数据模块。

覆盖：
- get_eastmoney_sentiment: 东方财富股吧热度
- get_search_trends: 搜索趋势（可选，pytrends / 百度指数）

不做：
- 雪球爬虫（ToS 风险，README 说明原因）
- Reddit 情绪（纯 A 股场景覆盖不到）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
) -> dict[str, Any]:
    return {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": "low",
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": None if success else "empty",
    }


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# get_eastmoney_sentiment
# ------------------------------------------------------------------

def get_eastmoney_sentiment(code: str) -> dict[str, Any]:
    """获取东方财富股吧热度和情绪指标。

    Args:
        code: 股票代码

    Returns:
        dict: {heat, post_count, recent_sentiment, ...}
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    # 尝试 akshare 东财股吧接口
    try:
        import akshare as ak
        attempted_sources.append("akshare")

        # 股吧人气榜
        try:
            gb_df = ak.stock_guba_hot_rank()
            if not gb_df.empty:
                code_str = str(code).zfill(6)
                match = gb_df[gb_df["代码"] == code_str]
                if not match.empty:
                    row = match.iloc[0]
                    result = {
                        "heat_rank": _safe_int(row.get("排名")),
                        "heat_score": _safe_int(row.get("热度")),
                        "post_count": _safe_int(row.get("帖子数")),
                        "source": "akshare.stock_guba_hot_rank",
                    }
                    source = "akshare.stock_guba_hot_rank"
                    source_group = "sentiment"
        except Exception:
            pass

        # 如果热榜没数据，尝试个股吧最近帖子
        if not result:
            try:
                gb_detail = ak.stock_guba_em(symbol=code)
                if gb_detail is not None and not (hasattr(gb_detail, 'empty') and gb_detail.empty):
                    result = {
                        "recent_posts": len(gb_detail) if hasattr(gb_detail, '__len__') else 1,
                        "source": "akshare.stock_guba_em",
                    }
                    source = "akshare.stock_guba_em"
                    source_group = "sentiment"
            except Exception:
                pass

    except Exception as e:
        logger.warning("东方财富股吧数据获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "情绪数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.stock_guba_hot_rank", "akshare.stock_guba_em"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# get_search_trends
# ------------------------------------------------------------------

def get_search_trends(keyword: str) -> dict[str, Any]:
    """获取搜索趋势（可选，Google Trends / 百度指数）。

    Args:
        keyword: 搜索关键词

    Returns:
        dict: {trend_data, ...}
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    start = datetime.now(timezone.utc)

    # pytrends
    try:
        from pytrends.request import TrendReq
        attempted_sources.append("pytrends")
        pytrends = TrendReq(hl="zh-CN", tz=480)
        pytrends.build_payload([keyword], timeframe="today 12-m")
        interest = pytrends.interest_over_time()
        if not interest.empty:
            result = {
                "keyword": keyword,
                "latest_interest": int(interest[keyword].iloc[-1]),
                "avg_interest": round(float(interest[keyword].mean()), 1),
                "source": "pytrends(Google Trends)",
            }
            source = "pytrends"
            source_group = "sentiment"
    except ImportError:
        logger.debug("pytrends 未安装，跳过")
    except Exception as e:
        logger.warning("pytrends 获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "搜索趋势数据不可得（pytrends 未安装或 Google 接口不可用）",
                  "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group="sentiment" if source else None,
        fallback_chain=["pytrends(Google Trends)"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== 东方财富股吧情绪 ===")
    sent = get_eastmoney_sentiment("600519")
    print(sent)

    print("\n=== 搜索趋势 ===")
    trend = get_search_trends("茅台")
    print(trend)
