"""
新闻搜索模块。

覆盖：
- search_stock_news: 个股新闻
- search_industry_news: 行业新闻
- search_policy_news: 政策新闻
- search_analyst_reports: 研报摘要

Fallback 链：
  Tavily → Bocha（中文搜索）→ WebSearch（Claude 内置）
  Tavily 无 API Key 时静默降级到 WebSearch，不报错。
"""

from __future__ import annotations

import os
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
    rows_fetched: int | None = None,
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
        **({"rows_fetched": rows_fetched} if rows_fetched is not None else {}),
    }


def _get_tavily_client():
    """惰性获取 Tavily 客户端。"""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return None
    try:
        # Tavily Python SDK
        from tavily import TavilyClient
        return TavilyClient(api_key=api_key)
    except ImportError:
        logger.debug("tavily-python SDK 未安装，跳过")
        return None
    except Exception:
        return None


def _search_tavily(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """通过 Tavily API 搜索。"""
    client = _get_tavily_client()
    if client is None:
        return []
    try:
        response = client.search(query=query, max_results=max_results)
        results = response.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:300],
                "source": "tavily",
            }
            for r in results
        ]
    except Exception as e:
        logger.warning("Tavily 搜索失败: %s", e)
        return []


def _search_bocha(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """通过 Bocha 中文搜索 API。"""
    api_key = os.environ.get("BOCHA_API_KEY", "")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.get(
            "https://api.bocha.cn/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("snippet", "")[:300],
                    "source": "bocha",
                }
                for item in items
            ]
    except Exception as e:
        logger.warning("Bocha 搜索失败: %s", e)
    return []


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def search_stock_news(code: str, days: int = 30) -> dict[str, Any]:
    """搜索个股相关新闻。

    Args:
        code: 股票代码
        days: 搜索天数范围

    Returns:
        dict: {articles: [...], _meta}
    """
    attempted_sources: list[str] = []
    articles: list[dict] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    fallback_chain = ["Tavily", "Bocha", "WebSearch(Claude内置)"]

    # 1. Tavily
    tavily_results = _search_tavily(f"A股 {code} 新闻 最近{days}天")
    if tavily_results:
        articles.extend(tavily_results)
        attempted_sources.append("tavily")
        source = "tavily"
        source_group = "search_ai"

    # 2. Bocha
    if not articles:
        bocha_results = _search_bocha(f"{code} 股票新闻 公告")
        if bocha_results:
            articles.extend(bocha_results)
            attempted_sources.append("bocha")
            source = "bocha"
            source_group = "search_ai"

    # 3. 若两者都无，标注 fallback 到 WebSearch
    if not articles:
        attempted_sources.append("websearch_pending")
        articles = [{
            "title": "新闻搜索未配置 API Key",
            "content": (
                "Tavily 和 Bocha 均未配置 API Key。"
                "请在 SKILL 流程中使用 Claude 内置 WebSearch 工具替代。"
                "配置 TAVILY_API_KEY 或 BOCHA_API_KEY 环境变量即可启用自动搜索。"
            ),
            "url": "",
            "source": "fallback_note",
        }]

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "articles": articles,
        "search_query": f"{code} 个股新闻",
        "_meta": _build_meta(
            source=source or None,
            source_group=source_group or None,
            fallback_chain=fallback_chain,
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
            rows_fetched=len(articles) if articles else None,
        ),
    }


def search_industry_news(industry: str) -> dict[str, Any]:
    """搜索行业新闻。

    Args:
        industry: 行业名称（如 "白酒"、"新能源汽车"）

    Returns:
        dict: {articles: [...], _meta}
    """
    attempted_sources: list[str] = []
    articles: list[dict] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    tavily_results = _search_tavily(f"{industry} 行业 最新动态 2026")
    if tavily_results:
        articles.extend(tavily_results)
        attempted_sources.append("tavily")
        source = "tavily"
        source_group = "search_ai"

    if not articles:
        bocha_results = _search_bocha(f"{industry}行业 新闻 政策")
        if bocha_results:
            articles.extend(bocha_results)
            attempted_sources.append("bocha")
            source = "bocha"
            source_group = "search_ai"

    if not articles:
        attempted_sources.append("websearch_pending")
        articles = [{"title": "行业新闻搜索未配置 API Key", "source": "fallback_note"}]

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "articles": articles,
        "search_query": f"{industry} 行业新闻",
        "_meta": _build_meta(
            source=source or None,
            source_group=source_group or None,
            fallback_chain=["Tavily", "Bocha", "WebSearch(Claude内置)"],
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
            rows_fetched=len(articles) if articles else None,
        ),
    }


def search_policy_news(keywords: list[str]) -> dict[str, Any]:
    """搜索政策相关新闻。

    Args:
        keywords: 关键词列表（如 ["白酒", "消费税", "政策"]）

    Returns:
        dict: {articles: [...], _meta}
    """
    query = " ".join(keywords) + " 政策 监管"
    attempted_sources: list[str] = []
    articles: list[dict] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    tavily_results = _search_tavily(query)
    if tavily_results:
        articles.extend(tavily_results)
        attempted_sources.append("tavily")
        source = "tavily"
        source_group = "search_ai"

    if not articles:
        bocha_results = _search_bocha(query)
        if bocha_results:
            articles.extend(bocha_results)
            attempted_sources.append("bocha")
            source = "bocha"
            source_group = "search_ai"

    if not articles:
        attempted_sources.append("websearch_pending")
        articles = [{"title": "政策新闻搜索未配置 API Key", "source": "fallback_note"}]

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "articles": articles,
        "search_query": query,
        "_meta": _build_meta(
            source=source or None,
            source_group=source_group or None,
            fallback_chain=["Tavily", "Bocha", "WebSearch(Claude内置)"],
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
            rows_fetched=len(articles) if articles else None,
        ),
    }


def search_analyst_reports(company: str) -> dict[str, Any]:
    """搜索分析师研报标题/摘要。

    Args:
        company: 公司名称（如 "贵州茅台"）

    Returns:
        dict: {reports: [...], _meta}
    """
    attempted_sources: list[str] = []
    reports: list[dict] = []
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    query = f"{company} 研报 评级 目标"
    tavily_results = _search_tavily(query)
    if tavily_results:
        reports.extend(tavily_results)
        attempted_sources.append("tavily")
        source = "tavily"
        source_group = "search_ai"

    if not reports:
        bocha_results = _search_bocha(query)
        if bocha_results:
            reports.extend(bocha_results)
            attempted_sources.append("bocha")
            source = "bocha"
            source_group = "search_ai"

    if not reports:
        attempted_sources.append("websearch_pending")
        reports = [{"title": "研报搜索未配置 API Key", "source": "fallback_note"}]

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "reports": reports,
        "search_query": query,
        "_meta": _build_meta(
            source=source or None,
            source_group=source_group or None,
            fallback_chain=["Tavily", "Bocha", "WebSearch(Claude内置)"],
            attempted_sources=attempted_sources,
            latency_ms=latency,
            success=bool(source),
            rows_fetched=len(reports) if reports else None,
        ),
    }


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== 个股新闻 ===")
    news = search_stock_news("600519")
    print(f"  来源: {news['_meta']['source']}")
    print(f"  articles: {len(news.get('articles', []))}")

    print("\n=== 行业新闻 ===")
    ind = search_industry_news("白酒")
    print(f"  来源: {ind['_meta']['source']}")
    print(f"  articles: {len(ind.get('articles', []))}")
