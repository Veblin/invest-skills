"""News scanner — three-layer architecture (v0.1.9).

Layer 1: akshare notices (always)
Layer 2: declarative query pack (always, no network)
Layer 3: optional Tavily REST (TAVILY_API_KEY)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

QUERIES_TEMPLATE = {
    "zh_breaking": "{name} {symbol} 突发 紧急 最新",
    "zh_negative": "{name} {symbol} 利空 风险 监管 政策",
    "zh_positive": "{name} {symbol} 利好 突破 订单 中标",
    "en_policy": "{name_eng} policy regulation regulator US EU",
    "en_business": "{name_eng} contract order partnership expansion",
}


@dataclass
class NewsCard:
    source: str
    query: str
    title: str
    date: str
    url: str | None
    summary: str
    direction: str
    credibility: str
    credibility_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_news_query_pack(
    symbol: str,
    name: str,
    name_eng: str | None = None,
) -> list[dict[str, str]]:
    """Declarative queries for Claude WebSearch — no network."""
    eng = name_eng or name
    pack = []
    for key, template in QUERIES_TEMPLATE.items():
        pack.append({
            "id": key,
            "query": template.format(name=name, symbol=symbol, name_eng=eng),
            "channel": "websearch_pack",
            "purpose": key,
        })
    return pack


def classify_credibility(
    title: str,
    summary: str,
    url: str | None,
) -> tuple[str, float]:
    """Keyword-based credibility rating."""
    text = f"{title} {summary} {url or ''}".lower()
    rules: list[tuple[list[str], str, float]] = [
        (["交易所", "证监会", "上交所", "深交所", "公告", "cninfo"], "official", 0.95),
        (["证券时报", "中国证券报", "上海证券报", "财联社", "cls.cn"], "media_confirmed", 0.85),
        (["研报", "证券", "研究所", "目标价"], "media_confirmed", 0.75),
        (["雪球", "xueqiu", "互动易", "投资者关系"], "industry", 0.55),
        (["知情人士", "据传", "传闻", "小道"], "rumor", 0.25),
        (["逻辑", "分析", "预计", "有望"], "logical", 0.45),
    ]
    for keywords, label, score in rules:
        if any(kw in text for kw in keywords):
            return label, score
    if url and any(d in (url or "") for d in ("eastmoney", "stcn", "cs.com")):
        return "media_confirmed", 0.7
    return "media_leak", 0.5


def _infer_direction(title: str, summary: str) -> str:
    text = f"{title} {summary}"
    bear = ("利空", "下跌", "亏损", "减持", "调查", "处罚", "风险", "下调")
    bull = ("利好", "上涨", "增长", "中标", "增持", "突破", "上调", "盈利")
    b = sum(1 for w in bear if w in text)
    u = sum(1 for w in bull if w in text)
    if b > u:
        return "bearish"
    if u > b:
        return "bullish"
    return "neutral"


def _fetch_notice_cards(symbol: str, days: int) -> list[NewsCard]:
    from .events import _fetch_notice_events, _normalize_date

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cards: list[NewsCard] = []
    for ev in _fetch_notice_events(symbol):
        date_str = _normalize_date(str(ev.get("date") or ""))
        if not date_str or date_str < cutoff:
            continue
        title = ev.get("title") or ""
        summary = title[:200]
        cred, score = classify_credibility(title, summary, ev.get("url"))
        cards.append(NewsCard(
            source="notice",
            query="",
            title=title,
            date=date_str,
            url=ev.get("url"),
            summary=summary,
            direction=_infer_direction(title, summary),
            credibility=cred,
            credibility_score=score,
        ))
    return cards


def _fetch_tavily_cards(symbol: str, name: str, days: int) -> list[NewsCard]:
    from . import env

    config = env.get_config()
    key = config.get("TAVILY_API_KEY")
    if not key:
        return []

    import requests

    query = f"{name} {symbol} 最新 新闻"
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": 5,
                "search_depth": "basic",
            },
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return []

    cards: list[NewsCard] = []
    for item in results:
        title = str(item.get("title") or "")
        summary = str(item.get("content") or title)[:200]
        url = item.get("url")
        cred, score = classify_credibility(title, summary, url)
        cards.append(NewsCard(
            source="tavily",
            query=query,
            title=title,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            url=url,
            summary=summary,
            direction=_infer_direction(title, summary),
            credibility=cred,
            credibility_score=score,
        ))
    return cards


def collect_targeted_sites(name_eng: str) -> list[NewsCard]:
    """Channel B — v0.2.0."""
    raise NotImplementedError("Channel B: deferred to v0.3.0")


def collect_community_heat(symbol: str) -> list[NewsCard]:
    """Channel C — v0.2.0."""
    raise NotImplementedError("Channel C: deferred to v0.3.0")


def collect_news(
    symbol: str,
    name: str = "",
    days: int = 7,
    name_eng: str | None = None,
) -> dict[str, Any]:
    """Layer 1 + 2 + optional Layer 3."""
    attempted: dict[str, str] = {}
    cards: list[NewsCard] = []

    # Layer 1
    try:
        notice_cards = _fetch_notice_cards(symbol, days)
        cards.extend(notice_cards)
        attempted["notice"] = "success" if notice_cards else "empty"
    except Exception as exc:
        attempted["notice"] = f"error: {exc}"

    # Layer 2 — always produces 5 query entries (see QUERIES_TEMPLATE)
    query_pack = build_news_query_pack(symbol, name or symbol, name_eng)
    attempted["query_pack"] = "success"

    # Layer 3 — optional Tavily
    from . import env
    if env.is_tavily_available(env.get_config()):
        try:
            tavily_cards = _fetch_tavily_cards(symbol, name or symbol, days)
            cards.extend(tavily_cards)
            attempted["tavily"] = "success" if tavily_cards else "empty"
        except Exception as exc:
            attempted["tavily"] = f"error: {exc}"
    else:
        attempted["tavily"] = "skipped (no key)"

    # Channel B/C placeholders
    for ch_name, fn, arg in (
        ("channel_b", collect_targeted_sites, name_eng or name),
        ("channel_c", collect_community_heat, symbol),
    ):
        try:
            fn(arg)  # type: ignore[arg-type]
        except NotImplementedError:
            attempted[ch_name] = "deferred v0.2.0"
        except Exception as exc:
            attempted[ch_name] = f"error: {exc}"

    return {
        "cards": [c.to_dict() for c in cards],
        "query_pack": query_pack,
        "attempted_sources": attempted,
        "days": days,
    }
