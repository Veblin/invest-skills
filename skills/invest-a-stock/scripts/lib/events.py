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
from datetime import datetime, timedelta

from .shared_dates import parse_date, shanghai_now as _shanghai_now

logger = logging.getLogger(__name__)

# ── 占位标记 ──
INDUSTRY_EVENTS_PLACEHOLDER: tuple[dict, ...] = ()
MARKET_EVENTS_PLACEHOLDER: tuple[dict, ...] = ()

PLACEHOLDER_NOTE_INDUSTRY = "⏭️ 来源缺口：暂无稳定 API"
PLACEHOLDER_NOTE_MARKET = "⏭️ 来源缺口：暂无稳定 API"


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

_NOTICE_TYPE_MAP: dict[str, str] = {
    # 回购
    "回购实施公告": "buyback", "回购报告书": "buyback",
    "回购进展情况": "buyback", "回购预案": "buyback",
    # 股权激励
    "股权激励计划": "equity_incentive", "股权激励计划摘要": "equity_incentive",
    "股权激励对象名单": "equity_incentive", "股权激励进展公告": "equity_incentive",
    "股权激励行权价（数量）调整": "equity_incentive",
    # 增发/募资
    "增发预案": "private_placement", "增发获准公告": "private_placement",
    "增发提示性公告": "private_placement", "增发发行结果公告": "private_placement",
    "其他增发事项公告": "private_placement", "增资扩股": "private_placement",
    "募集资金使用情况报告": "private_placement", "募集资金补充流动资金": "private_placement",
    "变更募集资金投资项目": "private_placement",
    # 首发≠增发：招股说明书是 IPO，套上 private_placement 会带出「非公开发行/定向增发」
    # 的现金流元数据与强化关系——用独立类型，避免把上市事件读成再融资。
    "首发招股说明书摘要": "ipo",
    # 并购
    "收购出售资产/股权": "mna",
    # 分红（「分配预案」不含「分红/利润分配」字样，正则漏判）
    "分配预案": "dividend", "分配方案实施": "dividend", "分配方案决议公告": "dividend",
    # 增持
    "股东/实际控制人股份增持": "holder_increase",
    # 重大合同（「签订协议」不含「合同/中标」字样，正则漏判）
    "签订协议": "major_contract",
    # 定期报告（一/三季报不含「季报/季度报告」字样，正则漏判）
    "年度报告全文": "earnings_report", "年度报告摘要": "earnings_report",
    "年度报告补充公告": "earnings_report",
    "半年度报告全文": "earnings_report", "半年度报告摘要": "earnings_report",
    "一季度报告全文": "earnings_report", "一季度报告正文": "earnings_report",
    "一季度报告更正公告": "earnings_report",
    "三季度报告全文": "earnings_report", "三季度报告正文": "earnings_report",
    "业绩预告": "earnings_guidance",
    # 监管风险
    "违法违规": "regulatory", "警示函公告": "regulatory",
    "上交所股票监管工作函": "regulatory",
    # 质押/冻结
    "股份质押、冻结": "pledge",
    # 供给事件
    "限售股份上市流通": "unlock",
    # 异动/停复牌
    "股票交易异常波动": "market_anomaly", "停牌公告": "market_anomaly",
    "复牌公告": "market_anomaly",
    # 对外投资
    "对外项目投资": "investment", "投资设立公司": "investment",
    "投资理财": "investment",
    # 担保（或有负债，实质风险项——不得归入程序性）
    "担保事项": "guarantee", "担保年度额度预计": "guarantee",
    "提供/对外担保公告": "guarantee",
    # 股权/权益变动（方向未知 → 中性类型，不给方向提示）
    "权益变动报告书": "holder_change", "股本变动": "holder_change",
    "股权转让": "holder_change",
    # 关联交易（利益输送风险，实质项）
    "关联交易": "related_party",
    # 源自带的兜底类型「其他」= **源没分类**，不是「程序性公告」。二者同属低信号桶
    # （_LOW_SIGNAL_TYPES，都不进信号榜，降噪效果一致），区别只在标签语义：
    # 把「源未分类」说成「程序性」是替源下结论，渲染层另有「未分类公告」文案。
    "其他": "other",
    # 程序性公告——显式归类以**降噪**：不进因子矩阵的信号榜，但仍留档可追溯
    "专项说明/独立意见": "procedural",
    "法律意见书": "procedural", "保荐/核查意见": "procedural",
    "股东大会资料": "procedural", "管理办法/制度": "procedural",
    "审计报告": "procedural", "审计机构变更": "procedural",
    "ESG公告": "procedural", "自查报告": "procedural",
    "内部控制报告": "procedural", "社会责任报告": "procedural",
    "受托管理事务定期报告": "procedural", "议事规则/实施细则": "procedural",
    "独立董事候选人声明": "procedural", "独立董事提名人声明": "procedural",
    "独立董事述职报告": "procedural", "监事会决议公告": "procedural",
    "董事会决议公告": "procedural", "召开股东大会通知": "procedural",
    "召开股东大会提示性公告": "procedural", "增加股东大会议案": "procedural",
    "变更股东大会地点": "procedural", "变更股东大会时间": "procedural",
    "股东大会决议公告": "procedural", "调研活动": "procedural",
    "高管人员任职变动": "procedural", "证券简称变更": "procedural",
    "股票": "procedural", "公司关联方基本资料变更": "procedural",
    "公司其他基本信息变更": "procedural", "公司办公地址变更": "procedural",
    "公司注册地址变更": "procedural", "公司章程": "procedural",
    "公司章程修正": "procedural", "公司章程修订": "procedural",
    "公司经营范围变更": "procedural", "月度经营情况": "procedural",
    "借贷": "procedural",
    "信用级别变动": "procedural", "一般付息公告": "procedural",
    "一般债券发行公告": "procedural", "一般债券发行结果": "procedural",
}

# 低信号公告不计入「信号榜」（因子矩阵的 top_types），避免淹没实质事件。
# 两个成员语义不同，**不得合并展示**：procedural = 源显式标注的程序性类型；
# other = 源未分类（含源兜底类型「其他」）。下游判据统一用本集合，勿写死单个成员。
_LOW_SIGNAL_TYPES = frozenset({"procedural", "other"})


def is_low_signal(event_type: str) -> bool:
    """该事件类型是否属低信号桶（不占信号榜）。

    跨模块判据的**唯一入口**：凡需要「剔除低信号」的地方（如 store 的跨版本 diff）
    都走本函数，不要各写一份字面量集合——集合一旦增员，散落的副本不会一起变。
    """
    return str(event_type) in _LOW_SIGNAL_TYPES

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
    "regulatory": "削弱",
    "pledge": "削弱",
    "unlock": "不改变",  # 解禁本身不代表减持
    "market_anomaly": "不改变",
    "investment": "不改变",  # 投资效果须结合公告内容判断
    "guarantee": "削弱",
    "holder_change": "不改变",  # 方向未知：增持/减持都可能，不给方向
    "related_party": "削弱",
    "procedural": "不改变",
}

# 股东变动事件类型映射
_SHAREHOLDER_CHANGE_MAP: dict[str, str] = {
    "增加": "holder_increase",
    "增持": "holder_increase",
    "减少": "holder_decrease",
    "减持": "holder_decrease",
}


# ── 主入口 ──


def _take_leg(
    legs: dict[str, str], name: str, label: str, fetcher, symbol: str,
) -> list[dict]:
    """跑一条事件来源腿，三态写入 ``legs[name]``。

    三态取值：``ok``（取到数据）/ ``empty``（接口正常、窗口内无数据）/ ``failed``
    （接口异常）。**区分 empty 与 failed 是本函数的全部意义**：历史实现两者都返回
    ``[]``，下游只能按「没有事件」处理，于是把采集缺陷读成事实。fetcher 以 ``None``
    表示失败，抛异常同样计为失败（兼容 mock 与实现变更）。
    """
    try:
        events = fetcher(symbol)
    except Exception as exc:
        logger.warning("events: %s failed for %s: %s", name, symbol, exc)
        legs[name] = "failed"
        return []
    if events is None:
        logger.warning("events: %s returned no result for %s", name, symbol)
        legs[name] = "failed"
        return []
    if events:
        logger.info("events: %d from %s", len(events), label)
    legs[name] = "ok" if events else "empty"
    return events


def attach_events(collection: dict, symbol: str, days: int = 30) -> dict:
    """采集公告事件并挂载到 collection。

    Args:
        collection: 采集结果字典（会原地修改）
        symbol: 股票代码，如 "600176"
        days: 时间窗口天数，默认 30。

    Returns:
        修改后的 collection。

    写入 ``_meta`` 的字段：``events_window_days`` / ``events_summary`` /
    ``events_legs``（逐来源腿三态，见 ``_take_leg``）。
    """
    # Priority: collection._meta.events_window_days over days parameter
    meta_days = collection.get("_meta", {}).get("events_window_days")
    if meta_days is not None:
        days = meta_days

    all_events: list[dict] = []
    legs: dict[str, str] = {}

    # 1. 公告通知（主要来源）
    all_events.extend(_take_leg(
        legs, "notice", "stock_individual_notice_report", _fetch_notice_events, symbol))
    # 2. 分红方案（历史明细）
    all_events.extend(_take_leg(
        legs, "dividend", "stock_history_dividend_detail", _fetch_dividend_events, symbol))
    # 3. 股东变动（辅助来源，补充 keyword-filtered notice_report）
    all_events.extend(_take_leg(
        legs, "holder_change", "stock_shareholder_change_ths", _fetch_shareholder_events, symbol))

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
    # 逐腿状态：渲染层据此区分「公告腿未取到」与「窗口内无公告」，
    # 不得由 events 是否为空反推（空既可能是失败，也可能是事实）。
    meta["events_legs"] = legs

    # 9. 占位槽说明
    meta["industry_events_note"] = PLACEHOLDER_NOTE_INDUSTRY
    meta["market_events_note"] = PLACEHOLDER_NOTE_MARKET

    return collection


# ── 数据源采集 ──


def _fetch_notice_events(symbol: str) -> list[dict] | None:
    """从 akshare stock_individual_notice_report 采集公告事件。

    API 返回列: 代码/名称/公告标题/公告类型/公告日期/网址

    返回 ``None`` 表示**取数失败**（接口异常），``[]`` 表示**接口正常但无数据**。
    调用方须区分二者（见 ``_take_leg``）。

    ⚠️ ``lib.catalyst`` 也会调同一接口，但用途不同（前瞻日历 vs 历史分类），
    且二者从不在同一进程——**不是重复实现**，见 ``catalyst.py`` 模块 docstring 的分工表。
    """
    from .proxy import akshare_direct_session

    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.stock_individual_notice_report(security=symbol)
    except Exception as exc:
        logger.debug("events: stock_individual_notice_report failed for %s: %s", symbol, exc)
        return None

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


def _fetch_dividend_events(symbol: str) -> list[dict] | None:
    """从 akshare stock_history_dividend_detail 采集分红事件。

    优先使用 stock_history_dividend_detail；若失败则回退到 stock_dividend_cninfo。

    返回 ``None`` 表示**两个源都失败**；``[]`` 表示至少一个源正常应答但无数据。
    """
    from .proxy import akshare_direct_session

    events: list[dict] = []
    failed = 0

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
        failed += 1
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
        failed += 1
        logger.debug("events: stock_dividend_cninfo failed for %s: %s", symbol, exc)

    if not events and failed == 2:
        return None
    return events


def _fetch_shareholder_events(symbol: str) -> list[dict] | None:
    """从 akshare stock_shareholder_change_ths 采集股东变动事件。

    数据通常较旧（16 条），作为 notice_report 的辅助补充。

    返回 ``None`` 表示**取数失败**，``[]`` 表示接口正常但无数据。
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
        return None


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

    # 1) **标题正则优先**：标题是公司对本次公告的自述，比平台的粗分类更具体。
    #    实测冲突例：标题「协议转让…暨减持计划」+ 类型「股权转让」——按类型会判成 mna，
    #    丢掉减持的「削弱」方向；标题「筹划重大资产重组停牌公告」+ 类型「停牌公告」同理。
    for pattern, etype in _CLASSIFICATION_RULES:
        if pattern.search(title):
            return {
                "event_type": etype,
                "impact_dimension": _event_dimension(etype),
                "duration": _event_duration(etype),
            }

    # 2) 标题无实质关键词 → 用源提供的「公告类型」映射。这是本次新增能力覆盖的场景：
    #    「签订协议」（标题不含「合同」）、「股份质押、冻结」「限售股份上市流通」
    #    「警示函公告」「股票交易异常波动」「对外项目投资」等标题中性但类型明确的事件。
    mapped = _NOTICE_TYPE_MAP.get(raw_type.strip())
    if mapped and mapped not in _LOW_SIGNAL_TYPES:
        return {
            "event_type": mapped,
            "impact_dimension": _event_dimension(mapped),
            "duration": _event_duration(mapped),
        }

    # 3) 标题正则再看原类型文本（源类型未收录时的最后一条线索）
    for pattern, etype in _CLASSIFICATION_RULES:
        if pattern.search(raw_type):
            return {
                "event_type": etype,
                "impact_dimension": _event_dimension(etype),
                "duration": _event_duration(etype),
            }

    # 4) 兜底：源明确标了低信号类型 → 尊重其分桶；否则才是未分类。
    #    判据与步骤 2 同用 _LOW_SIGNAL_TYPES（此前这里是 `mapped == "procedural"`，
    #    与步骤 2 不等价：往集合里加第三个成员就会两边分桶不一致且无断言报警）。
    fallback = mapped if mapped in _LOW_SIGNAL_TYPES else "other"
    return {
        "event_type": fallback,
        "impact_dimension": _event_dimension(fallback),
        "duration": _event_duration(fallback),
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
    """将多种日期格式标准化为 YYYY-MM-DD（委托 shared_dates.parse_date）。"""
    d = parse_date(raw)
    return d.isoformat() if d else None


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
    - ``events == []`` 且**公告腿未给出结论**（failed 或缺失）：应重试
    - ``events == []`` 且无 ``events_summary``：采集未完成或失败，应重试
    - ``events == []`` 且公告腿已应答、``events_summary`` 存在：窗口内确实无事件，不重试

    **判据落在公告腿本身，而不是「所有腿都失败」**：分红明细与股东变动只覆盖很窄的
    公告类型，它们为空**不能**替代公告源得出「没有公告」的结论（C4：一手优先，
    二手只作线索、不取代一手核验）。按「全腿失败」判会把「公告腿挂掉 + 两条辅助腿
    空表」读成「窗口内无公告」——把采集缺陷说成事实。
    """
    events = collection.get("events")
    if events is None:
        return True
    if isinstance(events, list) and len(events) == 0:
        meta = collection.get("_meta") or {}
        legs = meta.get("events_legs")
        if isinstance(legs, dict) and legs.get("notice") not in ("ok", "empty"):
            return True
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

    cutoff_date = _shanghai_now().date() - timedelta(days=days)
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


def summarize_event_types(events: list[dict]) -> dict:
    """按类型现场聚合事件计数（含低信号分桶），供 summary 与渲染层共用。

    **渲染层必须用本函数而不是快照里的 ``events_summary.top_types``**：
    后者只存信号榜前 5（低信号被剔），与「总数」并列会出现括号内数字对不上总数的
    行；旧快照的 top_types 还是历史口径（含 ``other``），重放会原样打出来。

    低信号两桶**分开展示**、不得合并成一句：``procedural`` 是源显式标注的程序性
    类型，``other`` 是源未分类（含源兜底类型「其他」），后者不是程序性公告。
    排序按数量降序、同数量保持出现顺序（与 ``_build_summary`` 历史行为一致）。
    """
    counts: dict[str, int] = {}
    for e in events:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "other"))
        counts[t] = counts.get(t, 0) + 1

    substantive = sorted(
        ((t, c) for t, c in counts.items() if t not in _LOW_SIGNAL_TYPES),
        key=lambda x: -x[1],
    )
    procedural_count = counts.get("procedural", 0)
    unclassified_count = counts.get("other", 0)
    return {
        "total": len(events),
        "counts": counts,
        "substantive": substantive,
        "substantive_count": sum(c for _, c in substantive),
        "procedural_count": procedural_count,
        "unclassified_count": unclassified_count,
        "low_signal_count": procedural_count + unclassified_count,
    }


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

    # 类型计数委托 summarize_event_types（单一实现；信号榜剔除低信号，
    # 避免程序性/未分类公告占多数时把真实事件挤出因子矩阵那行只取的前 3 个）
    agg = summarize_event_types(events)

    return {
        f"count_{days}d": count,
        "event_count": count,
        "window_days": days,
        "latest_date": latest_date,
        "top_types": [{"type": t, "count": c} for t, c in agg["substantive"][:5]],
        "procedural_count": agg["procedural_count"],
        "unclassified_count": agg["unclassified_count"],
        "low_signal_count": agg["low_signal_count"],
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

    # Scale-aware floor: absolute 0.01 fails for tiny per-share EPS inputs
    _eps = max(1e-9, 1e-6 * max(abs(v_true), abs(v_false), abs(post_price), 1.0))
    warn = None
    if abs(spread) < _eps:
        ratio = 0.5
        warn = f"|V_真 - V_假| < {_eps:.2e}，ratio 默认 0.5"
    else:
        ratio = (post_price - v_false) / spread
        # 场景钳位仅对真实价差生效；零价差回退 0.5 不被钳位覆盖
        if scenario == "bearish" and post_price >= pre_price:
            ratio = 0.0
        elif scenario == "bullish" and post_price <= pre_price:
            ratio = 0.0
    ratio = max(0.0, min(1.0, ratio))

    def _ratio_at_pe(pe: float) -> float:
        vf = eps_base * pe
        local_spread = v_true - vf
        local_eps = max(1e-9, 1e-6 * max(abs(v_true), abs(vf), abs(post_price), 1.0))
        if abs(local_spread) < local_eps:
            r = 0.5
        else:
            r = (post_price - vf) / local_spread
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
