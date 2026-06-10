"""
Web 搜索采集模块 web_research.py

将 Web 搜索提升为正式采集维度，提供：

- collect_web_research: 针对股票的组合搜索（产业链、舆情、机构覆盖）
- _deterministic_tags: 搜索结果可信度分级标签
- merge_with_api_data: Web 搜索结果与 API 数据的交叉引用标记

搜索可靠度分级（输出标注使用）：
    🔵 official   — 互动平台回复、公司公告原文、巨潮资讯网
    🟡 analyst    — 券商研报、郭明錤等知名分析师、财经媒体
    🔴 rumor      — 股吧、雪球帖子、未署名消息、微信群转发
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 搜索查询模板
# ─────────────────────────────────────────────────────────────

# 每个维度的搜索查询模板
QUERY_TEMPLATES = {
    "industry": [
        "{name} {code} 产业链 供应链 上下游 客户 供应商",
        "{name} 行业地位 竞争对手 市场份额 2025 2026",
    ],
    "sentiment": [
        "{name} 最新消息 公告 新闻 2026",
        "{name} {code} 舆情 互动易 投资者关系",
    ],
    "institutional": [
        "{name} {code} 券商 研报 评级 目标价 2025 2026",
        "{name} 机构持仓 基金重仓 北向资金",
    ],
    "catalysts": [
        "{name} 新产品 订单 量产 产能 2026",
        "{name} {code} 概念 热点 人形机器人 折叠屏 新能源",
    ],
}

# 对用户指定 topic 的补充搜索
TOPIC_QUERY_TEMPLATE = [
    "{name} {topic} 2025 2026",
    "{name} {code} {topic} 最新进展",
]


# ─────────────────────────────────────────────────────────────
# 可信度分级
# ─────────────────────────────────────────────────────────────

# 可信度来源关键词匹配
CREDIBILITY_PATTERNS = {
    "official": [
        # 公司官方渠道
        r"互动易", r"互动平台", r"深交所互动",
        r"cninfo\.com\.cn", r"巨潮资讯", r"公司公告",
        r"static\.cninfo\.com\.cn", r"finalpage",
        # 交易所
        r"深圳证券交易所", r"上海证券交易所",
        r"szse\.cn", r"sse\.com\.cn",
        # 官方回复关键词
        r"公司在.*回复", r"根据客户订单",
    ],
    "analyst": [
        # 知名分析机构/个人
        r"郭明錤", r"天风国际", r"天风证券",
        r"中信证券", r"中金公司", r"华泰证券",
        r"证券时报", r"财联社", r"经济观察",
        r"同花顺", r"格隆汇", r"新浪财经",
        r"证券之星", r"东方财富",
        r"研报", r"评级", r"目标价",
    ],
    "rumor": [
        # 低可信度来源
        r"雪球", r"股吧", r"帖子",
        r"网传", r"传闻", r"据传",
        r"微信群", r"微博", r"抖音",
        r"小道消息", r"知情人士",
        r"xueqiu\.com",
    ],
}


def classify_source_credibility(url: str, title: str) -> tuple[str, str]:
    """根据 URL 和标题判断信息源可信度。

    Returns:
        (tier, icon): ('official', '🔵') | ('analyst', '🟡') | ('rumor', '🔴') | ('unknown', '⚪')
    """
    combined = f"{url} {title}".lower()

    # 检查 rumor 模式（先检查，避免 false positive）
    for pattern in CREDIBILITY_PATTERNS["rumor"]:
        if re.search(pattern, combined, re.IGNORECASE):
            return ("rumor", "🔴")

    # 检查 official 模式
    for pattern in CREDIBILITY_PATTERNS["official"]:
        if re.search(pattern, combined, re.IGNORECASE):
            return ("official", "🔵")

    # 检查 analyst 模式
    for pattern in CREDIBILITY_PATTERNS["analyst"]:
        if re.search(pattern, combined, re.IGNORECASE):
            return ("analyst", "🟡")

    return ("unknown", "⚪")


# ─────────────────────────────────────────────────────────────
# 核心采集函数
# ─────────────────────────────────────────────────────────────

def collect_web_research(
    symbol: str,
    *,
    name: str = "",
    topic: str | None = None,
    dimensions: list[str] | None = None,
    queries: list[str] | None = None,
) -> dict[str, Any]:
    """Web 搜索维度采集。

    注：本函数返回搜索查询模板列表，实际搜索调用由 Skill 的 Step 2 阶段
    （LLM 分析阶段）通过 WebSearch tool 执行。这是因为 Claude Code 的
    WebSearch 只能在 Agent 上下文中调用，不能从 subprocess 调用。

    Args:
        symbol: 股票代码
        name: 公司名（用于构造查询）
        topic: 用户额外指定的主题（如 "苹果合作"）
        dimensions: 需要搜索的维度列表，默认所有
        queries: 直接指定查询列表，跳过模板生成

    Returns:
        dict: {
            "dimension": "web_research",
            "display": "Web 搜索（产业链/舆情/机构）",
            "data": {
                "queries": [{"query": "...", "dimension": "industry", "credibility_guide": "..."}],
                "source_credibility_tags": {"🔵": "official", ...},
                "cross_reference_hints": [...],
            },
            "_meta": {...},
        }
    """
    start = datetime.now(timezone.utc)
    search_name = name or f"股票{symbol}"

    # 生成查询列表
    if queries:
        query_list = [{"query": q, "dimension": "custom"} for q in queries]
    else:
        dims = dimensions or ["industry", "sentiment", "institutional", "catalysts"]
        query_list = []
        for dim in dims:
            if dim in QUERY_TEMPLATES:
                for tmpl in QUERY_TEMPLATES[dim]:
                    query_list.append({
                        "query": tmpl.format(name=search_name, code=symbol),
                        "dimension": dim,
                    })

        # 附加 topic 查询
        if topic:
            for tmpl in TOPIC_QUERY_TEMPLATE:
                query_list.append({
                    "query": tmpl.format(name=search_name, code=symbol, topic=topic),
                    "dimension": "topic",
                })

    data = {
        "queries": query_list,
        "source_credibility_tags": {
            "🔵 official": "互动平台回复、公司公告原文、巨潮资讯网",
            "🟡 analyst": "券商研报、郭明錤等知名分析师、财经媒体（证券时报/财联社）",
            "🔴 rumor": "股吧帖子、雪球、未署名消息、微信群转发",
            "⚪ unknown": "无法判断来源可信度",
        },
        "classification_rules": {
            "per_result": "对每条搜索结果，根据 URL/标题匹配可信度分类",
            "cross_check": "🔵 官方确认 > 🟡 分析师/媒体 > 🔴 市场传闻",
            "conflict_resolution": "当不同来源信息矛盾时，优先采信 🔵>🟡>🔴",
        },
        "cross_reference_hints": [
            "将搜索结果与 API 实时行情数据交叉验证（如：分析师预测的营收 vs 财务API的营收）",
            "将搜索结果与财务报告交叉验证（如：报道的订单规模 vs 财报的营收结构）",
            "官方互动平台回复 vs 媒体解读，以原文为准",
            "多篇独立来源重复同一信息 → 可信度 +1",
        ],
    }

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    return {
        "dimension": "web_research",
        "display": "Web 搜索（产业链/舆情/机构/催化剂）",
        "data": data,
        "_meta": {
            "source": "search_query_templates",
            "source_group": "search_ai",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "confidence": "medium",
            "latency_ms": round(latency, 1),
            "success": len(query_list) > 0,
            "note": "查询模板已生成，实际搜索由 LLM Analysis 阶段通过 WebSearch tool 执行",
        },
        "status": "available",
    }


def build_web_search_plan(
    symbol: str,
    name: str = "",
    topic: str | None = None,
) -> dict[str, Any]:
    """生成 Web 搜索采集计划（供 Step 0 展示）。

    Args:
        symbol: 股票代码
        name: 公司名
        topic: 额外主题

    Returns:
        采集计划摘要
    """
    queries = []
    search_name = name or f"股票{symbol}"

    for dim, templates in QUERY_TEMPLATES.items():
        for tmpl in templates:
            queries.append({
                "query": tmpl.format(name=search_name, code=symbol),
                "dimension": dim,
                "estimated_results": "3-5篇",
            })

    if topic:
        for tmpl in TOPIC_QUERY_TEMPLATE:
            queries.append({
                "query": tmpl.format(name=search_name, code=symbol, topic=topic),
                "dimension": "topic",
                "estimated_results": "3-5篇",
            })

    return {
        "phase": "web_research",
        "total_queries": len(queries),
        "by_dimension": {
            dim: sum(1 for q in queries if q["dimension"] == dim)
            for dim in set(q["dimension"] for q in queries)
        },
        "estimated_total_results": f"{len(queries) * 3}-{len(queries) * 5}条",
        "queries": queries,
    }


# ─────────────────────────────────────────────────────────────
# 结果后处理
# ─────────────────────────────────────────────────────────────

def annotate_search_results(
    results: list[dict[str, str]],
) -> list[dict[str, str]]:
    """对搜索结果加可信度标签。

    Args:
        results: [{"title": "...", "url": "..."}, ...]

    Returns:
        [{"title": "...", "url": "...", "credibility_tier": "official", "tag": "🔵"}, ...]
    """
    annotated = []
    for r in results:
        tier, icon = classify_source_credibility(r.get("url", ""), r.get("title", ""))
        annotated.append({**r, "credibility_tier": tier, "tag": icon})
    return annotated


def extract_key_claims(
    annotated_results: list[dict[str, str]],
) -> dict[str, list[str]]:
    """从标注结果中按可信度分组提取关键信息。

    Returns:
        {"official": ["claim1", "claim2"], "analyst": [...], "rumor": [...]}
    """
    # 此函数由 LLM 在分析阶段实现更合适
    # 这里提供一个结构化容器
    return {
        "official": [],
        "analyst": [],
        "rumor": [],
        "unknown": [],
    }
