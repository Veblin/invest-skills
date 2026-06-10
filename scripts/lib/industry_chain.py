"""
行业产业链模块（MVP）。

覆盖：
- get_industry_context: 行业名、申万分类、同业列表
- search_supply_chain: WebSearch 研报摘要

MVP 不做：ChainKnowledgeGraph 接入（Phase 6.2）
Phase 6 扩展：产业链拓扑图谱
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
        "confidence": "medium" if source else "low",
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": None if success else "empty",
    }


def _format_code(code: str) -> str:
    code = str(code).strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "").replace(".HK", "")
    return code.zfill(6)


# ------------------------------------------------------------------
# get_industry_context
# ------------------------------------------------------------------

def get_industry_context(code: str) -> dict[str, Any]:
    """获取行业背景：行业名、申万分类、同业列表。

    Args:
        code: 股票代码

    Returns:
        dict: {industry_name, shenwan_classification, peers, ...}
    """
    code = _format_code(code)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["akshare.stock_individual_info_em", "akshare.stock_board_industry_*"]

    try:
        import akshare as ak
        attempted_sources.append("akshare")

        # 1. 获取个股行业分类
        try:
            info_df = ak.stock_individual_info_em(symbol=code)
            if not info_df.empty:
                info_dict = dict(zip(info_df["item"], info_df["value"]))
                result["industry_name"] = str(info_dict.get("行业", ""))
                result["sector"] = _infer_sector(result.get("industry_name", ""))
        except Exception as e:
            logger.debug("akshare 个股行业获取失败: %s", e)

        # 2. 获取申万行业分类
        try:
            sw_df = ak.stock_board_industry_cons_em(symbol=result.get("industry_name", "白酒"))
            if sw_df is not None and not sw_df.empty:
                result["shenwan_classification"] = result.get("industry_name", "")
        except Exception:
            pass

        # 3. 获取同行业股票列表
        if result.get("industry_name"):
            try:
                peers_df = ak.stock_board_industry_cons_em(symbol=result["industry_name"])
                if peers_df is not None and not peers_df.empty:
                    peers = peers_df.head(20)[["代码", "名称"]].to_dict(orient="records")
                    result["peers"] = [p for p in peers if str(p.get("代码", "")) != code]
                    result["peer_count"] = len(result["peers"])
            except Exception:
                pass

        if result:
            source = "akshare"
            source_group = "eastmoney"

    except Exception as e:
        logger.warning("行业背景获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {"error": "行业背景数据不可得", "attempted_sources": attempted_sources}

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=fallback_chain,
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


def _infer_sector(industry: str) -> str:
    """从申万行业推断大板块。"""
    industry = str(industry)
    if any(k in industry for k in ["酒", "食品", "饮料", "乳", "肉", "调味"]):
        return "消费-食品饮料"
    if any(k in industry for k in ["医药", "生物", "医疗", "器械"]):
        return "医药生物"
    if any(k in industry for k in ["汽车", "新能源", "电池", "光伏"]):
        return "新能源/汽车"
    if any(k in industry for k in ["半导体", "芯片", "电子", "通信"]):
        return "TMT"
    if any(k in industry for k in ["银行", "保险", "券商", "房地产"]):
        return "金融地产"
    if any(k in industry for k in ["化工", "钢铁", "有色", "煤炭"]):
        return "周期"
    return "其他"


# ------------------------------------------------------------------
# search_supply_chain
# ------------------------------------------------------------------

def search_supply_chain(industry: str) -> dict[str, Any]:
    """搜索产业链上下游研报摘要。

    MVP 通过 WebSearch 获取研报标题/摘要。
    Phase 6 接入 ChainKnowledgeGraph 拓扑。

    Args:
        industry: 行业名称

    Returns:
        dict: {supply_chain_summary, ...}
    """
    attempted_sources: list[str] = []
    result: dict[str, Any] = {}
    source = ""
    start = datetime.now(timezone.utc)

    # 使用 news_search 的 Tavily/Bocha/WebSearch 链路
    try:
        from scripts.lib.news_search import search_industry_news
        news_result = search_industry_news(f"{industry} 产业链 上游 下游")
        articles = news_result.get("articles", [])
        if articles and articles[0].get("source") != "fallback_note":
            result = {
                "industry": industry,
                "supply_chain_articles": articles[:5],
            }
            source = news_result.get("_meta", {}).get("source", "")
            attempted_sources.append("news_search")
    except Exception as e:
        logger.warning("产业链搜索失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    if not result:
        result = {
            "industry": industry,
            "note": (
                "产业链详细分析将在 Phase 6 实现。"
                "MVP 提供申万行业分类和同业列表。"
                "Phase 6 计划接入 ChainKnowledgeGraph 拓扑结构（数据截至约2019，仅作结构参考）。"
            ),
            "attempted_sources": attempted_sources,
        }

    result["_meta"] = _build_meta(
        source=source or None,
        source_group="search_ai" if source else None,
        fallback_chain=["news_search → WebSearch"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== 行业背景: {code} ===")
    ctx = get_industry_context(code)
    for k, v in ctx.items():
        if k != "_meta":
            print(f"  {k}: {v}")

    if ctx.get("industry_name"):
        print(f"\n=== 产业链: {ctx['industry_name']} ===")
        sc = search_supply_chain(ctx["industry_name"])
        for k, v in sc.items():
            if k != "_meta":
                print(f"  {k}: {v}")
