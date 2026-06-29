"""公告事件采集模块。从 akshare 多源采集公告事件并分类。

设计原则：
  - 所有 akshare 调用均包装在 try/except 中，单源失败不阻塞其他源
  - 事件卡片按日期降序排列
  - 同一来源内按 (date, normalized_title) 去重
  - 行业/市场事件占位槽预先分配

使用方式:
    collection = attach_events(collection, "600176", days=30)
    # collection["events"] 包含事件卡片列表
    # collection["_meta"]["events_summary"] 包含汇总
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, date
from typing import Any

logger = logging.getLogger(__name__)

# ── 占位标记 ──
INDUSTRY_EVENTS_PLACEHOLDER: list[dict] = []
MARKET_EVENTS_PLACEHOLDER: list[dict] = []

PLACEHOLDER_NOTE_INDUSTRY = "⏭️ 待补来源：暂无稳定 API"
PLACEHOLDER_NOTE_MARKET = "⏭️ 待补来源：暂无稳定 API"


# ── 分类关键词映射 ──

_CLASSIFICATION_RULES: list[tuple[re.Pattern, str, str, str]] = [
    # (pattern, event_type, impact_dimension, duration)
    (re.compile(r"回购"), "buyback", "估值", "中长期变量"),
    (re.compile(r"股权激励|限制性股票|股票期权"), "equity_incentive", "治理", "中长期变量"),
    (re.compile(r"增发|非公开发行|募集资金"), "private_placement", "现金流", "中长期变量"),
    (re.compile(r"并购|重组|收购|合并|资产注入"), "mna", "收入", "中长期变量"),
    (re.compile(r"分红|派息|送股|转增|利润分配"), "dividend", "估值", "短期扰动"),
    (re.compile(r"减持"), "holder_decrease", "估值", "短期扰动"),
    (re.compile(r"增持"), "holder_increase", "估值", "短期扰动"),
    (re.compile(r"合同|中标|协议"), "major_contract", "收入", "中长期变量"),
    (re.compile(r"诉讼|仲裁"), "litigation", "治理", "短期扰动"),
    (re.compile(r"ST|退市|风险警示"), "st_risk", "治理", "结构性质变"),
    (re.compile(r"年报|年度报告|annual report", re.IGNORECASE),
     "earnings_report", "收入", "短期扰动"),
    (re.compile(r"半年报|半年度报告|semi-annual", re.IGNORECASE),
     "earnings_report", "收入", "短期扰动"),
    (re.compile(r"季报|季度报告|quarterly", re.IGNORECASE),
     "earnings_report", "收入", "短期扰动"),
    (re.compile(r"业绩预告|业绩修正|盈利预测"), "earnings_guidance", "收入", "短期扰动"),
    (re.compile(r"业绩快报"), "earnings_preview", "收入", "短期扰动"),
]

# 逻辑关系映射（基于事件类型 + impact_dimension 的默认值）
_LOGIC_RELATION_MAP: dict[str, str] = {
    "buyback": "强化",
    "equity_incentive": "强化",
    "private_placement": "强化",
    "mna": "强化",
    "dividend": "强化",
    "holder_decrease": "削弱",
    "holder_increase": "强化",
    "major_contract": "强化",
    "litigation": "削弱",
    "st_risk": "削弱",
    "earnings_report": "不改变",
    "earnings_guidance": "不改变",
    "earnings_preview": "不改变",
}

# 分红数据的事件类型映射
_DIVIDEND_TYPE_MAP: dict[str, str] = {
    "dividend": "dividend",
    "分红": "dividend",
    "bonus": "dividend",
    "送转": "dividend",
    "配送": "dividend",
}

# 股东变动事件类型映射
_SHAREHOLDER_CHANGE_MAP: dict[str, str] = {
    "增加": "holder_increase",
    "增持": "holder_increase",
    "减少": "holder_decrease",
    "减持": "holder_decrease",
}


# ── 主入口 ──


def attach_events(collection: dict, symbol: str, days: int = 30) -> dict:
    """采集公告事件并挂载到 collection。

    Args:
        collection: 采集结果字典（会原地修改）
        symbol: 股票代码，如 "600176"
        days: 时间窗口天数，默认 30。

    Returns:
        修改后的 collection。
    """
    all_events: list[dict] = []

    # 1. 公告通知（主要来源）
    try:
        notice_events = _fetch_notice_events(symbol)
        if notice_events:
            all_events.extend(notice_events)
            logger.info("events: %d notices from stock_individual_notice_report", len(notice_events))
    except Exception as exc:
        logger.warning("events: notice report failed for %s: %s", symbol, exc)

    # 2. 分红方案（历史明细）
    try:
        dividend_events = _fetch_dividend_events(symbol)
        if dividend_events:
            all_events.extend(dividend_events)
            logger.info("events: %d dividend events from stock_history_dividend_detail", len(dividend_events))
    except Exception as exc:
        logger.warning("events: dividend detail failed for %s: %s", symbol, exc)

    # 3. 股东变动（辅助来源，补充 keyword-filtered notice_report）
    try:
        shareholder_events = _fetch_shareholder_events(symbol)
        if shareholder_events:
            all_events.extend(shareholder_events)
            logger.info("events: %d shareholder changes from stock_shareholder_change_ths", len(shareholder_events))
    except Exception as exc:
        logger.warning("events: shareholder change failed for %s: %s", symbol, exc)

    # 4. 去重
    all_events = _dedup_events(all_events)

    # 5. 时间窗口过滤
    all_events = _filter_by_days(all_events, days)

    # 6. 按日期降序排列
    all_events.sort(key=lambda e: str(e.get("date", "")), reverse=True)

    # 7. 写入 collection
    collection["events"] = all_events
    collection["industry_events"] = list(INDUSTRY_EVENTS_PLACEHOLDER)
    collection["market_events"] = list(MARKET_EVENTS_PLACEHOLDER)

    # 8. 写入 meta
    meta = collection.setdefault("_meta", {})
    meta["events_window_days"] = days
    meta["events_summary"] = _build_summary(all_events, days)

    # 9. 占位槽说明
    meta["industry_events_note"] = PLACEHOLDER_NOTE_INDUSTRY
    meta["market_events_note"] = PLACEHOLDER_NOTE_MARKET

    return collection


# ── 数据源采集 ──


def _fetch_notice_events(symbol: str) -> list[dict]:
    """从 akshare stock_individual_notice_report 采集公告事件。

    API 返回列: 代码/名称/公告标题/公告类型/公告日期/网址
    """
    import akshare as ak

    try:
        df = ak.stock_individual_notice_report(security=symbol)
    except Exception as exc:
        logger.warning("events: stock_individual_notice_report failed for %s: %s", symbol, exc)
        return []

    if df is None or df.empty:
        logger.info("events: no notice data for %s", symbol)
        return []

    records = df.to_dict("records") if hasattr(df, "to_dict") else []
    if not records:
        return []

    events: list[dict] = []
    for rec in records:
        raw_title = str(rec.get("公告标题", ""))
        raw_date = str(rec.get("公告日期", ""))
        # 清理标题中的前后空格和 URL 编码
        title = _clean_title(raw_title)
        if not title:
            continue
        date_str = _normalize_date(raw_date)
        if not date_str:
            continue

        classified = _classify_event({
            "title": title,
            "raw_type": str(rec.get("公告类型", "")),
            "raw_date": raw_date,
        })
        card = {
            "date": date_str,
            "type": classified["event_type"],
            "title": title,
            "impact_dimension": classified["impact_dimension"],
            "duration": classified["duration"],
            "logic_relation": _get_logic_relation(classified["event_type"]),
            "source": "akshare stock_individual_notice_report",
            "url": str(rec.get("网址", "")),
        }
        events.append(card)
    return events


def _fetch_dividend_events(symbol: str) -> list[dict]:
    """从 akshare stock_history_dividend_detail 采集分红事件。

    优先使用 stock_history_dividend_detail；若失败则回退到 stock_dividend_cninfo。
    """
    import akshare as ak

    events: list[dict] = []

    # 主源
    try:
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is not None and not df.empty:
            records = df.to_dict("records") if hasattr(df, "to_dict") else []
            for rec in records:
                date_str = _normalize_date(str(rec.get("股权登记日", "")))
                if not date_str:
                    continue
                plan = str(rec.get("方案说明", "") or rec.get("送转比例", "") or "")
                title = f"分红方案：{plan}" if plan else "分红方案公告"
                events.append({
                    "date": date_str,
                    "type": "dividend",
                    "title": title,
                    "impact_dimension": "估值",
                    "duration": "短期扰动",
                    "logic_relation": "强化",
                    "source": "akshare stock_history_dividend_detail",
                    "url": "",
                })
            if events:
                return events
    except Exception as exc:
        logger.debug("events: stock_history_dividend_detail failed for %s, trying cninfo: %s", symbol, exc)

    # 回退源
    try:
        df = ak.stock_dividend_cninfo(symbol=symbol)
        if df is not None and not df.empty:
            records = df.to_dict("records") if hasattr(df, "to_dict") else []
            for rec in records:
                date_str = _normalize_date(str(rec.get("股权登记日", "") or rec.get("公告日期", "")))
                if not date_str:
                    continue
                desc = str(rec.get("分红说明", "") or rec.get("方案", "") or "分红方案")
                title = f"分红方案：{desc}"
                events.append({
                    "date": date_str,
                    "type": "dividend",
                    "title": title,
                    "impact_dimension": "估值",
                    "duration": "短期扰动",
                    "logic_relation": "强化",
                    "source": "akshare stock_dividend_cninfo",
                    "url": "",
                })
    except Exception as exc:
        logger.debug("events: stock_dividend_cninfo failed for %s: %s", symbol, exc)

    return events


def _fetch_shareholder_events(symbol: str) -> list[dict]:
    """从 akshare stock_shareholder_change_ths 采集股东变动事件。

    数据通常较旧（16 条），作为 notice_report 的辅助补充。
    """
    import akshare as ak

    try:
        df = ak.stock_shareholder_change_ths(symbol=symbol)
        if df is None or df.empty:
            return []

        records = df.to_dict("records") if hasattr(df, "to_dict") else []
        events: list[dict] = []
        for rec in records:
            date_str = _normalize_date(str(rec.get("变动日期", "") or rec.get("公告日期", "")))
            if not date_str:
                continue
            holder = str(rec.get("股东名称", "") or "")
            change_type = str(rec.get("变动类型", "") or rec.get("方向", "") or "")
            change_vol = str(rec.get("变动数量", "") or "")
            event_type = "other"
            for keyword, etype in _SHAREHOLDER_CHANGE_MAP.items():
                if keyword in change_type:
                    event_type = etype
                    break

            title_parts = [f"股东变动"]
            if holder:
                title_parts.append(holder)
            if change_type:
                title_parts.append(change_type)
            if change_vol:
                title_parts.append(change_vol)
            title = " ".join(title_parts)

            events.append({
                "date": date_str,
                "type": event_type,
                "title": title,
                "impact_dimension": "估值",
                "duration": "短期扰动",
                "logic_relation": _get_logic_relation(event_type),
                "source": "akshare stock_shareholder_change_ths",
                "url": "",
            })
        return events
    except Exception as exc:
        logger.debug("events: shareholder change failed for %s: %s", symbol, exc)
        return []


# ── 事件分类 ──


def _classify_event(record: dict) -> dict:
    """将单条公告记录分类为事件卡片。

    Args:
        record: 包含 title, raw_type 的字典。

    Returns:
        包含 event_type, impact_dimension, duration 的字典。
    """
    title = str(record.get("title", ""))
    raw_type = str(record.get("raw_type", ""))

    for pattern, etype, impact, duration in _CLASSIFICATION_RULES:
        if pattern.search(title) or pattern.search(raw_type):
            return {
                "event_type": etype,
                "impact_dimension": impact,
                "duration": duration,
            }

    return {
        "event_type": "other",
        "impact_dimension": "治理",
        "duration": "短期扰动",
    }


def _get_logic_relation(event_type: str) -> str:
    """根据事件类型返回默认逻辑关系。"""
    return _LOGIC_RELATION_MAP.get(event_type, "不改变")


# ── 辅助函数 ──


def _clean_title(raw: str) -> str:
    """清理公告标题：去除空白、前后修饰词。"""
    # 去除前后空白
    title = raw.strip()
    # 去除常见中英文空格和特殊空白
    title = re.sub(r'\s+', ' ', title)
    # 去除 URL 编码
    title = re.sub(r'%[0-9a-fA-F]{2}', '', title)
    return title.strip()


def _normalize_date(raw: str) -> str | None:
    """将多种日期格式标准化为 YYYY-MM-DD。

    支持: 2026-06-15, 20260615, 2026/06/15, 2026年06月15日
    """
    if not raw or raw in ("--", "N/A", "", "—"):
        return None

    raw = raw.strip()
    # 已为标准格式
    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw):
        return raw

    # YYYYMMDD
    if re.match(r'^\d{8}$', raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

    # YYYY/MM/DD
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # YYYY年MM月DD日
    m = re.match(r'^(\d{4})年(\d{1,2})月(\d{1,2})日$', raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 如果包含日期模式但长度不对，尝试提取
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    logger.debug("events: could not parse date '%s'", raw)
    return None


def _normalize_title_for_dedup(title: str) -> str:
    """标准化标题用于去重匹配。

    去除常见前缀词和空格，保留核心内容。
    """
    t = title.strip()
    # 去除常见前缀
    for prefix in ["关于", "公告", "审议", "通过", "召开", "提示性", "说明"]:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    # 去除尾部常见词
    for suffix in ["公告", "提示性公告", "的公告", "书", "函", "通知"]:
        if t.endswith(suffix):
            t = t[:-len(suffix)].strip()
    # 压缩空格
    t = re.sub(r'\s+', '', t)
    return t[:80]  # 截断以避免超长比较


def _filter_by_days(events: list[dict], days: int) -> list[dict]:
    """按时间窗口过滤事件。

    仅保留在 days 天内（含当日）的事件。
    日期为空或解析失败的事件默认保留。

    Args:
        events: 事件卡片列表
        days: 时间窗口天数

    Returns:
        过滤后的事件列表。
    """
    if days <= 0:
        return []

    cutoff_date = date.today() - timedelta(days=days)
    out: list[dict] = []
    for e in events:
        date_str = str(e.get("date", ""))
        if not date_str:
            out.append(e)
            continue
        try:
            event_d = datetime.strptime(date_str, "%Y-%m-%d").date()
            if event_d >= cutoff_date:
                out.append(e)
        except (ValueError, TypeError):
            out.append(e)
    return out


def _dedup_events(events: list[dict]) -> list[dict]:
    """对事件列表去重。

    去重规则：同一来源内，按 (date, normalized_title) 去重。
    多个来源的事件若日期和标准化标题相同，保留第一个。

    Args:
        events: 事件卡片列表

    Returns:
        去重后的事件列表。
    """
    seen: set[tuple[str, str, str]] = set()  # (source, date, norm_title)
    out: list[dict] = []
    for e in events:
        source = str(e.get("source", ""))
        date_str = str(e.get("date", ""))
        title = str(e.get("title", ""))
        norm = _normalize_title_for_dedup(title)
        key = (source, date_str, norm)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _build_summary(events: list[dict], days: int) -> dict:
    """构建事件汇总统计。

    Args:
        events: 事件卡片列表
        days: 时间窗口天数

    Returns:
        汇总字典。
    """
    count_30d = len(events)

    # 最新日期
    dates = [str(e.get("date", "")) for e in events if e.get("date")]
    latest_date = max(dates) if dates else None

    # 按类型统计
    type_counts: dict[str, int] = {}
    for e in events:
        t = str(e.get("type", "other"))
        type_counts[t] = type_counts.get(t, 0) + 1

    # 按数量降序排列
    top_types = sorted(type_counts.items(), key=lambda x: -x[1])

    return {
        "count_30d": count_30d,
        "window_days": days,
        "latest_date": latest_date,
        "top_types": [{"type": t, "count": c} for t, c in top_types[:5]],
    }
