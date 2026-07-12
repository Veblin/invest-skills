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

logger = logging.getLogger(__name__)

# ── 占位标记 ──
INDUSTRY_EVENTS_PLACEHOLDER: list[dict] = []
MARKET_EVENTS_PLACEHOLDER: list[dict] = []

PLACEHOLDER_NOTE_INDUSTRY = "⏭️ 待补来源：暂无稳定 API"
PLACEHOLDER_NOTE_MARKET = "⏭️ 待补来源：暂无稳定 API"


# ── 事件类型元数据（从 event_type_taxonomy.yaml 加载，共享 analysis_templates 的缓存）──

from .analysis_templates import load_event_taxonomy


_EVENT_META_DEFAULTS: dict[str, dict[str, str]] = {
    "earnings_report": {"impact_dimension": "收入", "default_duration_hint": "短期扰动"},
    "earnings_guidance": {"impact_dimension": "收入", "default_duration_hint": "短期扰动"},
    "earnings_preview": {"impact_dimension": "收入", "default_duration_hint": "短期扰动"},
    "buyback": {"impact_dimension": "估值", "default_duration_hint": "中长期变量"},
    "equity_incentive": {"impact_dimension": "治理", "default_duration_hint": "中长期变量"},
    "private_placement": {"impact_dimension": "现金流", "default_duration_hint": "中长期变量"},
    "mna": {"impact_dimension": "收入", "default_duration_hint": "中长期变量"},
    "dividend": {"impact_dimension": "估值", "default_duration_hint": "短期扰动"},
    "holder_decrease": {"impact_dimension": "估值", "default_duration_hint": "短期扰动"},
    "holder_increase": {"impact_dimension": "估值", "default_duration_hint": "短期扰动"},
    "major_contract": {"impact_dimension": "收入", "default_duration_hint": "中长期变量"},
    "litigation": {"impact_dimension": "治理", "default_duration_hint": "短期扰动"},
    "st_risk": {"impact_dimension": "治理", "default_duration_hint": "结构性质变"},
    "other": {"impact_dimension": "治理", "default_duration_hint": "短期扰动"},
}


def _event_meta(event_type: str) -> dict:
    """从 YAML 加载的事件类型元数据（label, impact_dimension, default_duration_hint）。"""
    taxonomy = load_event_taxonomy()
    event_types = taxonomy.get("event_types", {})
    if event_type in event_types:
        return event_types[event_type]
    return _EVENT_META_DEFAULTS.get(event_type, _EVENT_META_DEFAULTS["other"])


def _event_dimension(event_type: str) -> str:
    """事件类型 → impact_dimension（来源: event_type_taxonomy.yaml）。"""
    return _event_meta(event_type).get("impact_dimension", "治理")


def _event_duration(event_type: str) -> str:
    """事件类型 → default_duration_hint（来源: event_type_taxonomy.yaml）。"""
    return _event_meta(event_type).get("default_duration_hint", "短期扰动")


# ── 分类关键词映射（正则规则在代码中，元数据字段委托 YAML 加载）──

_CLASSIFICATION_RULES: list[tuple[re.Pattern, str]] = [
    # (pattern, event_type)  — impact_dimension/duration 委托 _event_dimension/_event_duration
    (re.compile(r"回购"), "buyback"),
    (re.compile(r"股权激励|限制性股票|股票期权"), "equity_incentive"),
    (re.compile(r"增发|非公开发行|募集资金"), "private_placement"),
    (re.compile(r"并购|重组|收购|合并|资产注入"), "mna"),
    (re.compile(r"分红|派息|送股|转增|利润分配"), "dividend"),
    (re.compile(r"减持"), "holder_decrease"),
    (re.compile(r"增持"), "holder_increase"),
    (re.compile(r"合同|中标"), "major_contract"),
    (re.compile(r"诉讼|仲裁"), "litigation"),
    (re.compile(r"(?<![A-Za-z])ST(?![A-Za-z])|退市|风险警示"), "st_risk"),
    (re.compile(r"年报|年度报告|annual report", re.IGNORECASE), "earnings_report"),
    (re.compile(r"半年报|半年度报告|semi-annual", re.IGNORECASE), "earnings_report"),
    (re.compile(r"季报|季度报告|quarterly", re.IGNORECASE), "earnings_report"),
    (re.compile(r"业绩预告|业绩修正|盈利预测"), "earnings_guidance"),
    (re.compile(r"业绩快报"), "earnings_preview"),
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
    # Priority: collection._meta.events_window_days over days parameter
    meta_days = collection.get("_meta", {}).get("events_window_days")
    if meta_days is not None:
        days = meta_days

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

    # 4. 时间窗口过滤（先过滤减少去重计算量）
    all_events = _filter_by_days(all_events, days)

    # 5. 去重
    all_events = _dedup_events(all_events)

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
    from .proxy import akshare_direct_session

    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.stock_individual_notice_report(security=symbol)
    except Exception as exc:
        logger.debug("events: stock_individual_notice_report failed for %s: %s", symbol, exc)
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
    from .proxy import akshare_direct_session

    events: list[dict] = []

    # 主源
    try:
        with akshare_direct_session():
            import akshare as ak
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
                    "impact_dimension": _event_dimension("dividend"),
                    "duration": _event_duration("dividend"),
                    "logic_relation": _get_logic_relation("dividend"),
                    "source": "akshare stock_history_dividend_detail",
                    "url": "",
                })
            if events:
                return events
    except Exception as exc:
        logger.debug("events: stock_history_dividend_detail failed for %s, trying cninfo: %s", symbol, exc)

    # 回退源
    try:
        with akshare_direct_session():
            import akshare as ak
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
                    "impact_dimension": _event_dimension("dividend"),
                    "duration": _event_duration("dividend"),
                    "logic_relation": _get_logic_relation("dividend"),
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
    from .proxy import akshare_direct_session

    try:
        with akshare_direct_session():
            import akshare as ak
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
                "impact_dimension": _event_dimension(event_type),
                "duration": _event_duration(event_type),
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

    for pattern, etype in _CLASSIFICATION_RULES:
        if pattern.search(title) or pattern.search(raw_type):
            return {
                "event_type": etype,
                "impact_dimension": _event_dimension(etype),
                "duration": _event_duration(etype),
            }

    return {
        "event_type": "other",
        "impact_dimension": _event_dimension("other"),
        "duration": _event_duration("other"),
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


def needs_events_backfill(collection: dict) -> bool:
    """判断 collection 是否需要重新采集 events。

    - ``events`` 缺失：从未挂载
    - ``events == []`` 且无 ``events_summary``：采集未完成或失败，应重试
    - ``events == []`` 且有 ``events_summary``：窗口内确实无事件，不重试
    """
    events = collection.get("events")
    if events is None:
        return True
    if isinstance(events, list) and len(events) == 0:
        meta = collection.get("_meta") or {}
        return "events_summary" not in meta
    return False


def _filter_by_days(events: list[dict], days: int) -> list[dict]:
    """按时间窗口过滤事件。

    仅保留在 days 天内（含当日）的事件。
    日期为空的事件保留；无法解析的日期丢弃（避免窗口过滤失效）。

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
            logger.warning(
                "events: unparseable date '%s' in event %s — dropped",
                date_str, e.get("title", ""),
            )
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
    count = len(events)

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
        f"count_{days}d": count,
        "event_count": count,
        "window_days": days,
        "latest_date": latest_date,
        "top_types": [{"type": t, "count": c} for t, c in top_types[:5]],
    }


def calc_price_impact_interpolation(
    pre_price: float,
    post_price: float,
    eps_base: float,
    eps_hit: float,
    pe_normal: float,
    pe_stressed: float,
    scenario: str | None = None,
) -> dict:
    """Linear interpolation ratio (not risk-neutral probability).

    ratio = (P_current - V_false) / (V_true - V_false), clamped [0, 1]
  """
    v_true = eps_hit * pe_normal
    v_false = eps_base * pe_stressed
    spread = v_true - v_false

    if scenario is None:
        scenario = "bearish" if v_true < v_false else "bullish"

    warn = None
    if abs(spread) < 0.01:
        ratio = 0.5
        warn = "|V_真 - V_假| < 0.01，ratio 默认 0.5"
    else:
        ratio = (post_price - v_false) / spread
        if scenario == "bearish" and post_price >= pre_price:
            ratio = 0.0
        elif scenario == "bullish" and post_price <= pre_price:
            ratio = 0.0
        ratio = max(0.0, min(1.0, ratio))

    def _ratio_at_pe(pe: float) -> float:
        vf = eps_base * pe
        if abs(v_true - vf) < 0.01:
            return 0.5
        r = (post_price - vf) / (v_true - vf)
        return max(0.0, min(1.0, r))

    pe_lo = max(pe_stressed - 2, 0.1)
    pe_hi = pe_stressed + 2
    p_range = [round(_ratio_at_pe(pe_lo), 4), round(_ratio_at_pe(pe_hi), 4)]

    return {
        "ratio": round(ratio, 4),
        "p_range": p_range,
        "scenario": scenario,
        "v_true": round(v_true, 2),
        "v_false": round(v_false, 2),
        "pre_price": pre_price,
        "post_price": post_price,
        "warn": warn,
        "disclaimer": (
            "价格冲击插值比例：反映当前价格在两个假设估值之间的线性位置，"
            "不具备风险中性理论基础，不应用于概率判断。仅供参考，不构成投资建议。"
        ),
    }
