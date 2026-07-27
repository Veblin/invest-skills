"""ETF 专属数据查询 — 指数 PE、折溢价、规模、跟踪误差、对冲覆盖。

Canonical owner: invest-a-etf（自 journal v0.2.1 迁出）。
invest-a-journal 经 scripts/lib/etf_data.py shim 复用本模块。
硬编码对冲映射表 + akshare 直调。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.nums import safe_float  # noqa: E402
from lib.proxy import akshare_direct_session  # noqa: E402
from lib.technical import rsi_series  # noqa: E402

logger = logging.getLogger(__name__)

# fund_etf_spot_em 全表缓存（短 TTL，去重同请求内多次查询）
_SPOT_CACHE_LOCK = threading.Lock()
_SPOT_CACHE_DF: Any = None
_SPOT_CACHE_TS: float = 0.0
_SPOT_CACHE_TTL_SEC = 30.0


# ---------------------------------------------------------------------------
# 对冲工具覆盖映射表
# ---------------------------------------------------------------------------

# 运行时 canonical 源；人类可读副本见 references/etf-hedge-map.md（改映射请先改此处再同步文档）
ETF_HEDGE_MAP: dict[str, dict[str, str | None]] = {
    "510050": {"index": "上证50", "futures": "上证50股指期货(IH)", "options": "上证50ETF期权", "coverage": "high"},
    "510300": {"index": "沪深300", "futures": "沪深300股指期货(IF)", "options": "沪深300ETF期权", "coverage": "high"},
    "510500": {"index": "中证500", "futures": "中证500股指期货(IC)", "options": "中证500ETF期权", "coverage": "high"},
    "512100": {"index": "中证1000", "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "159845": {"index": "中证1000", "futures": "中证1000股指期货(IM)", "options": "中证1000ETF期权(部分)", "coverage": "partial"},
    "588000": {"index": "科创50", "futures": "科创50期货(2025上线)", "options": "科创50ETF期权", "coverage": "high"},
    "159915": {"index": "创业板指", "futures": None, "options": "创业板ETF期权", "coverage": "partial"},
    "159949": {"index": "创业板50", "futures": None, "options": "创业板50ETF期权", "coverage": "partial"},
    "563300": {"index": "中证2000", "futures": None, "options": None, "coverage": "none"},
    "510880": {"index": "红利指数", "futures": None, "options": None, "coverage": "none"},
    "511880": {"index": "银华日利", "futures": None, "options": None, "coverage": "none"},
    "513100": {"index": "纳指100", "futures": None, "options": None, "coverage": "low"},
    "513500": {"index": "标普500", "futures": None, "options": None, "coverage": "low"},
    "515790": {"index": "光伏产业", "futures": None, "options": None, "coverage": "none"},
    "516970": {"index": "基建工程", "futures": None, "options": None, "coverage": "none"},
    "518880": {"index": "黄金9999", "futures": "黄金期货(AU)", "options": None, "coverage": "low"},
}

# csindex 符号映射（ETF 代码 → csindex 指数代码）
CSINDEX_MAP: dict[str, str] = {
    "510050": "000016",   # 上证50
    "510300": "000300",   # 沪深300
    "510500": "000905",   # 中证500
    "512100": "000852",   # 中证1000
    "159845": "000852",   # 中证1000 ETF（深市，同 512100）
    "563300": "932000",   # 中证2000
    "588000": "000688",   # 科创50
    "159915": "399006",   # 创业板指
    "159949": "399673",   # 创业板50
}


# ---------------------------------------------------------------------------
# ETF 类型分类映射（G1）
# ---------------------------------------------------------------------------

# ETF 代码 → 类型标签。已覆盖 HEDGE_MAP 所有条目 + 主流行业/主题 ETF。
# 未列出的 ETF 通过 fund_etf_category_sina 动态查询或名称关键词推断。
_ETF_CATEGORY_MAP: dict[str, str] = {
    # 宽基
    "510050": "broad_market", "510300": "broad_market", "510500": "broad_market",
    "512100": "broad_market", "159845": "broad_market", "563300": "broad_market",
    "588000": "broad_market", "159915": "broad_market", "159949": "broad_market",
    # 行业
    "515790": "sector", "516970": "sector", "512480": "sector", "512690": "sector",
    "512010": "sector", "512760": "sector", "516160": "sector", "159995": "sector",
    "512800": "sector", "512200": "sector", "515050": "sector", "515880": "sector",
    "512660": "sector", "512710": "sector",
    # 跨境
    "513100": "cross_border", "513500": "cross_border",
    # 商品
    "518880": "commodity",
    # 债券/货币
    "511880": "money_market", "510880": "bond",  # 红利ETF归类为bond策略
}

_CATEGORY_LABELS: dict[str, str] = {
    "broad_market": "宽基ETF", "sector": "行业ETF", "thematic": "主题ETF",
    "cross_border": "跨境ETF", "bond": "债券ETF", "commodity": "商品ETF",
    "money_market": "货币ETF",
}


# ---------------------------------------------------------------------------
# ETF → 申万一级行业映射（G4）
# ---------------------------------------------------------------------------

ETF_TO_SW_INDUSTRY: dict[str, dict[str, str]] = {
    # 科技/TMT
    "515050": {"sw_code": "801770", "sw_name": "通信",       "sub": "5G"},
    "515880": {"sw_code": "801770", "sw_name": "通信",       "sub": "通信设备"},
    "512480": {"sw_code": "801080", "sw_name": "电子",       "sub": "半导体"},
    "159995": {"sw_code": "801080", "sw_name": "电子",       "sub": "芯片"},
    "512330": {"sw_code": "801750", "sw_name": "计算机",     "sub": "信息技术"},
    "515230": {"sw_code": "801750", "sw_name": "计算机",     "sub": "软件"},
    # 新能源/高端制造
    "515790": {"sw_code": "801730", "sw_name": "电力设备",   "sub": "光伏"},
    "516160": {"sw_code": "801730", "sw_name": "电力设备",   "sub": "新能源"},
    "512760": {"sw_code": "801740", "sw_name": "国防军工",   "sub": "军工"},
    "512660": {"sw_code": "801740", "sw_name": "国防军工",   "sub": "军工"},
    "516970": {"sw_code": "801720", "sw_name": "建筑装饰",   "sub": "基建"},
    # 消费/医药
    "512690": {"sw_code": "801120", "sw_name": "食品饮料",   "sub": "白酒"},
    "512010": {"sw_code": "801150", "sw_name": "医药生物",   "sub": "医药"},
    "512200": {"sw_code": "801180", "sw_name": "房地产",     "sub": "地产"},
    # 金融
    "512800": {"sw_code": "801780", "sw_name": "银行",       "sub": "银行"},
    "512070": {"sw_code": "801790", "sw_name": "非银金融",   "sub": "券商"},
    # 资源/周期
    "512400": {"sw_code": "801050", "sw_name": "有色金属",   "sub": "有色"},
    "512710": {"sw_code": "801150", "sw_name": "医药生物",   "sub": "生物医药"},
}


# ---------------------------------------------------------------------------
# 申万行业 → 推荐估值指标（G9）
# ---------------------------------------------------------------------------

SECTOR_VALUATION_MAP: dict[str, dict] = {
    # 金融地产 — PB 为主要指标（利润受拨备/周期扭曲）
    "银行":       {"primary": "PB",  "secondary": "ROE",        "pe_timing": False, "reason": "利润受拨备周期扭曲，PB+ROE 更可靠"},
    "非银金融":    {"primary": "PB",  "secondary": "ROE",        "pe_timing": False, "reason": "强周期，保险看 EV"},
    "房地产":      {"primary": "PB",  "secondary": "净负债率",    "pe_timing": False, "reason": "政策驱动 > 估值驱动"},
    # 周期行业 — EV/EBITDA 或 PB
    "石油石化":    {"primary": "EV/EBITDA", "secondary": "PB",  "pe_timing": False, "reason": "油价驱动，PE 失真"},
    "煤炭":       {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "盈利高位时注意 PE 虚低"},
    "有色金属":    {"primary": "EV/EBITDA", "secondary": "PB",  "pe_timing": False, "reason": "商品价格驱动"},
    "基础化工":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业均值回归"},
    "钢铁":       {"primary": "PB",  "secondary": "股息率",      "pe_timing": False, "reason": "强周期，PE 高位=买点"},
    "建筑材料":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "均值回归特征强"},
    # 制造业
    "电力设备":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "新能源子行业不适用传统 PE（需结合 PEG/PS）"},
    "机械设备":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业均值回归"},
    "国防军工":    {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "盈利不稳定，主题/政策驱动"},
    "汽车":       {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "强周期，需结合月度销量"},
    "家用电器":    {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "消费属性强，均值回归"},
    # 消费行业 — 盈利增长消化高 PE
    "食品饮料":    {"primary": "PE",  "secondary": "ROE",        "pe_timing": False, "reason": "品牌壁垒使高盈利持续，高 PE 不意味必然回调"},
    "纺织服装":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "品牌/渠道分化大"},
    "轻工制造":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "子行业差异大"},
    "商贸零售":    {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "转型期，盈利不稳定"},
    "社会服务":    {"primary": "PE",  "secondary": "PS",         "pe_timing": False, "reason": "恢复期"},
    # TMT — 部分适用 PE，部分需 PS
    "电子":       {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "PE 时机选择在 A 股电子行业有效；半导体早期看 PS"},
    "计算机":      {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "亏损占比高，主题/政策驱动"},
    "通信":       {"primary": "PE",  "secondary": "EV/EBITDA",  "pe_timing": False, "reason": "高增长消化高 PE"},
    "传媒":       {"primary": "PS",  "secondary": "PE",         "pe_timing": False, "reason": "主题/爆款驱动"},
    # 公用事业与基础设施
    "公用事业":    {"primary": "PE",  "secondary": "股息率",      "pe_timing": True,  "reason": "A 股 PE 时机选择最有效的行业之一，盈利稳定，均值回归"},
    "交通运输":    {"primary": "PE",  "secondary": "PB",         "pe_timing": True,  "reason": "部分子行业有效"},
    "建筑装饰":    {"primary": "PB",  "secondary": "PE",         "pe_timing": False, "reason": "政策驱动"},
    "环保":       {"primary": "PB",  "secondary": "PE",         "pe_timing": False, "reason": "政策驱动"},
    # 其他
    "农林牧渔":    {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "猪周期驱动，PE 波动极大"},
    "医药生物":    {"primary": "PE",  "secondary": "研发费用率",   "pe_timing": False, "reason": "创新药 vs 仿制药差异极大"},
    "美容护理":    {"primary": "PE",  "secondary": "PS",         "pe_timing": False, "reason": "高增长"},
    "综合":       {"primary": "PE",  "secondary": "PB",         "pe_timing": False, "reason": "多元化企业，逐案分析"},
}


# ---------------------------------------------------------------------------
# 主查询函数
# ---------------------------------------------------------------------------

def query_etf_data(
    symbol: str,
    fund_code: str = "",
    *,
    spot_row: Any = None,
) -> dict[str, Any]:
    """查询 ETF 专属数据。

    Parameters
    ----------
    symbol : str
        ETF 代码（如 "563300"）。
    fund_code : str
        对应指数代码（csindex 格式，如 "932000"）。为空时从 CSINDEX_MAP 查找。
    spot_row : optional
        已解析的 fund_etf_spot_em 行（Series），避免重复拉全表。

    Returns
    -------
    dict
        {index_pe, index_pe_status, premium_discount, aum, tracking_error,
         hedge_coverage, flags, data_quality}
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "category": query_etf_category(symbol),
        "index_pe": None,
        "index_pe_status": "unknown_etf",
        "industry_pe": None,
        "industry_pe_note": None,
        "valuation_guide": None,
        "industry_allocation": None,
        "premium_discount": None,
        "aum": None,
        "tracking_error": None,
        "tracking_error_note": (
            "跟踪误差需 ETF 净值与指数点位序列对比，当前引擎未实现；"
            "请勿填写估算数字"
        ),
        "hedge_coverage": _lookup_hedge(symbol),
        "flags": [],
        "_errors": [],
        "data_quality": {},
    }

    idx_code = fund_code or CSINDEX_MAP.get(symbol, "")
    _set_index_pe_status(result, symbol, idx_code)
    if idx_code:
        _fetch_csindex_pe(result, idx_code)

    # G4: 行业 ETF — 从 industry_weekly 查行业 PE 作为估值参考
    _attach_industry_pe(result, symbol)

    # G6: ETF 行业配置比例（akshare fund_portfolio_industry_allocation_em）
    _attach_industry_allocation(result, symbol)

    # G9: 附加行业特定估值指标指引
    _attach_valuation_guide(result)

    if spot_row is not None:
        _apply_spot_row_to_profile(result, spot_row, symbol)
    else:
        row, err = _lookup_etf_spot_row(symbol)
        if err:
            result["_errors"].append(err)
        elif row is not None:
            _apply_spot_row_to_profile(result, row, symbol)

    _auto_flags(result, result["category"].get("category", ""))
    result["data_quality"] = _summarize_etf_data_quality(result)
    return result


# ---------------------------------------------------------------------------
# ETF spot 缓存与查询
# ---------------------------------------------------------------------------

def prefetch_etf_spot() -> bool:
    """预拉 fund_etf_spot_em 全表并写入短 TTL 缓存。返回是否成功。"""
    return _get_etf_spot_df(force=True) is not None


def clear_etf_spot_cache() -> None:
    """清空 spot 缓存（测试用）。"""
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS
    with _SPOT_CACHE_LOCK:
        _SPOT_CACHE_DF = None
        _SPOT_CACHE_TS = 0.0


def _get_etf_spot_df(*, force: bool = False) -> Any:
    """带锁 + TTL 的 fund_etf_spot_em 全表缓存。"""
    global _SPOT_CACHE_DF, _SPOT_CACHE_TS
    now = time.monotonic()
    with _SPOT_CACHE_LOCK:
        if (
            not force
            and _SPOT_CACHE_DF is not None
            and (now - _SPOT_CACHE_TS) < _SPOT_CACHE_TTL_SEC
        ):
            return _SPOT_CACHE_DF.copy()
        try:
            import akshare as ak

            with akshare_direct_session():
                df = ak.fund_etf_spot_em()
        except Exception as exc:
            logger.warning("fund_etf_spot_em failed: %s", exc)
            return None
        if df is None or df.empty:
            return None
        _SPOT_CACHE_DF = df
        _SPOT_CACHE_TS = now
        return df


def _lookup_etf_spot_row(symbol: str) -> tuple[Any | None, str | None]:
    """从缓存/网络查找单只 ETF spot 行。返回 (row, error)。"""
    df = _get_etf_spot_df()
    if df is None:
        return None, "etf_spot: empty response"
    row_df = df[df["代码"] == symbol]
    if row_df.empty:
        return None, f"etf_spot: {symbol} not found"
    return row_df.iloc[0], None


def _apply_spot_row_to_profile(result: dict, row: Any, symbol: str) -> None:
    """将 spot 行写入 profile（折溢价 / AUM）。"""
    result["premium_discount"] = _em_to_premium_discount(row.get("基金折价率"))
    shares = safe_float(row.get("最新份额"))
    price = safe_float(row.get("最新价"))
    if shares is not None and price is not None:
        result["aum"] = round(shares * price / 1e8, 2)


def _spot_row_to_quote(symbol: str, row: Any) -> dict[str, Any]:
    """将 spot 行转为 quote 结构。"""
    return {
        "symbol": symbol,
        "price": safe_float(row.get("最新价")),
        "change_pct": safe_float(row.get("涨跌幅")),
        "volume": safe_float(row.get("成交量")),
        "amount": safe_float(row.get("成交额")),
        "premium_discount": _em_to_premium_discount(row.get("基金折价率")),
        "status": "available",
        "_error": None,
    }


# ---------------------------------------------------------------------------
# 子查询
# ---------------------------------------------------------------------------

def _set_index_pe_status(result: dict, symbol: str, idx_code: str) -> None:
    if idx_code:
        result["index_pe_status"] = "mapped"
        return
    if symbol in ETF_HEDGE_MAP:
        result["index_pe_status"] = "not_mapped"
        result["index_pe_note"] = (
            "该 ETF 在对冲映射表中，但尚无 csindex 指数代码映射，"
            "无法自动获取指数 PE（常见于行业/主题 ETF）"
        )
        return
    result["index_pe_status"] = "unknown_etf"
    result["index_pe_note"] = "不在已知映射表中，请手动核实跟踪指数"


def _fetch_csindex_pe(result: dict, idx_code: str) -> None:
    """指数 PE（csindex，仅 20 条历史，不足以计算可靠分位）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_zh_index_value_csindex(symbol=idx_code)
        if df is None or df.empty:
            result["_errors"].append("csindex_pe: empty response")
            return
        latest = df.iloc[-1]
        pe1 = safe_float(latest.get("市盈率1"))
        pe2 = safe_float(latest.get("市盈率2"))
        result["index_pe"] = pe1 if pe1 is not None else pe2
        result["index_pe_note"] = (
            f"来源: csindex {idx_code}，仅 {len(df)} 条历史，"
            "无可靠分位；市盈率1=股本加权，市盈率2=流通加权"
        )
    except Exception as exc:
        logger.warning("csindex_pe(%s) failed: %s", idx_code, exc)
        result["_errors"].append(f"csindex_pe: {exc}")


def _summarize_etf_data_quality(result: dict) -> dict[str, str]:
    """8 态简版 data_quality（与 journal 语义对齐）。"""
    dq: dict[str, str] = {}

    status = result.get("index_pe_status")
    if result.get("index_pe") is not None:
        dq["index_pe"] = "available"
    elif status == "not_mapped":
        dq["index_pe"] = "not_applicable"
    elif status == "mapped":
        dq["index_pe"] = "missing"
    else:
        dq["index_pe"] = "not_applicable"

    if result.get("premium_discount") is not None or result.get("aum") is not None:
        dq["spot"] = "available"
    elif any(e.startswith("etf_spot") for e in result.get("_errors", [])):
        dq["spot"] = "missing"
    else:
        dq["spot"] = "missing"

    hc = result.get("hedge_coverage") or {}
    cov = hc.get("coverage", "unknown")
    dq["hedge"] = "available" if cov != "unknown" else "unknown"

    return dq


# ---------------------------------------------------------------------------
# 自动标记
# ---------------------------------------------------------------------------

# ETF 类型 → 自动标记阈值（默认值适用于 broad_market / sector / thematic）
_TYPE_THRESHOLDS: dict[str, dict[str, float]] = {
    "cross_border": {"premium_warn": 5.0, "discount_warn": -5.0, "aum_min": 1.0},
    "bond":          {"premium_warn": 2.0, "discount_warn": -2.0, "aum_min": 2.0},
    "commodity":     {"premium_warn": 3.0, "discount_warn": -3.0, "aum_min": 1.0},
}
_DEFAULT_THRESHOLDS: dict[str, float] = {"premium_warn": 2.0, "discount_warn": -2.0, "aum_min": 2.0}


def _auto_flags(result: dict, etf_category: str = "") -> None:
    """基于阈值自动生成 flags。etf_category 为空时使用默认阈值。"""
    thresholds = _TYPE_THRESHOLDS.get(etf_category, _DEFAULT_THRESHOLDS)
    flags: list[str] = []

    aum = result.get("aum")
    if aum is not None and aum < thresholds["aum_min"]:
        flags.append(f"❌ AUM < {thresholds['aum_min']} 亿，存在清盘/流动性风险")

    pd_val = result.get("premium_discount")
    if pd_val is not None:
        if not math.isfinite(pd_val):
            flags.append("⚠️ 折溢价数据异常")
        elif pd_val > thresholds["premium_warn"]:
            flags.append(f"⚠️ 溢价 {pd_val:.1f}%，买入成本偏高")
        elif pd_val < thresholds["discount_warn"]:
            flags.append(f"⚠️ 折价 {abs(pd_val):.1f}%，可能存在流动性或结构问题")

    hc = result.get("hedge_coverage", {})
    cov = hc.get("coverage", "unknown")
    if cov == "none":
        flags.append("⚠️ 该 ETF 无可用的期货/期权对冲工具")
    elif cov == "low":
        flags.append("⚠️ 对冲工具覆盖有限")

    result["flags"] = flags


# ---------------------------------------------------------------------------
# 对冲覆盖查询
# ---------------------------------------------------------------------------

def _lookup_hedge(symbol: str) -> dict:
    """查找 ETF 对冲工具覆盖。未知 ETF 返回 unknown。"""
    entry = ETF_HEDGE_MAP.get(symbol)
    if entry:
        return dict(entry)
    return {"index": "未知", "futures": None, "options": None, "coverage": "unknown",
            "note": "未在已知对冲工具映射表中，请手动核实"}


# ---------------------------------------------------------------------------
# ETF 行情 + K 线（净值序列）
# ---------------------------------------------------------------------------

def query_etf_quote(symbol: str, *, spot_row: Any = None) -> dict[str, Any]:
    """ETF 当前行情：价格、涨跌幅、折溢价（从 fund_etf_spot_em）。"""
    result: dict[str, Any] = {
        "symbol": symbol,
        "price": None,
        "change_pct": None,
        "volume": None,
        "amount": None,
        "premium_discount": None,
        "status": "missing",
        "_error": None,
    }
    try:
        if spot_row is not None:
            return _spot_row_to_quote(symbol, spot_row)
        row, err = _lookup_etf_spot_row(symbol)
        if err:
            result["_error"] = err.replace("etf_spot: ", "", 1)
            return result
        if row is None:
            result["_error"] = "empty response"
            return result
        return _spot_row_to_quote(symbol, row)
    except Exception as exc:
        logger.warning("etf_quote(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)
    return result


def query_etf_kline(symbol: str, days: int = 60) -> dict[str, Any]:
    """ETF 净值序列 + 年化波动率计算。

    通过 fund_etf_fund_info_em 获取历史单位净值，计算日收益率
    的年化标准差。同时返回 MA20/MA60 基于净值。

    Args:
        days: Number of **trading bars** needed (not calendar days).
            Calendar lookback uses ``int(days * 365 / 250) + 15`` so MA60
            has enough history after weekends/holidays.
    """
    from datetime import date, timedelta

    result: dict[str, Any] = {
        "symbol": symbol,
        "nav_rows": 0,
        "latest_nav": None,
        "volatility_annualized": None,
        "adj_applied": False,
        "adj_note": None,
        "rsi": None,
        "rsi_period": None,
        "rsi_note": "Wilder RSI on NAV closes，默认周期 24（ETF NAV 波动低于个股价格，较标准 14 周期更平滑；数据不足时降级为 14），非交易信号",
        "ma20": None,
        "ma60": None,
        "index_ma20": None,
        "index_ma60": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "nav_history": [],
        "status": "missing",
        "_error": None,
    }

    try:
        import akshare as ak

        end_date = date.today().strftime("%Y%m%d")
        calendar_days = int(days * 365 / 250) + 15
        start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y%m%d")

        # 主链路：fund_etf_fund_info_em（字段更丰富）
        # fallback：fund_open_fund_info_em — akshare 偶尔变更主接口列数时报
        #   "Length mismatch: Expected axis has 14 elements, new values have 13 elements"
        df = None
        source = "fund_etf_fund_info_em"
        try:
            with akshare_direct_session():
                df = ak.fund_etf_fund_info_em(fund=symbol, start_date=start_date, end_date=end_date)
        except Exception as exc:
            msg = str(exc)
            if "Length mismatch" in msg or "Expected axis has" in msg:
                logger.info(
                    "fund_etf_fund_info_em column mismatch, falling back to fund_open_fund_info_em: %s",
                    exc,
                )
                try:
                    with akshare_direct_session():
                        df = ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势")
                    source = "fund_open_fund_info_em"
                except Exception as fb_exc:
                    logger.warning("fund_open_fund_info_em fallback also failed: %s", fb_exc)
                    result["_error"] = f"fund_etf_fund_info_em: {exc}; fallback: {fb_exc}"
                    return result
            else:
                raise

        if df is None or df.empty:
            result["_error"] = f"{source}: empty response"
            return result

        result["nav_rows"] = len(df)

        # 尝试 Tushare fund_adj 复权（消除分红/拆分断点）
        adj_map = _fetch_fund_adj_factor(symbol)
        if adj_map:
            result["adj_applied"] = True
            result["adj_note"] = (
                "NAV 序列已通过 Tushare fund_adj 前复权（消除分红/拆分造成的断点），"
                "MA/波动率/RSI/BOLL 基于复权后序列计算"
            )

        navs, returns = _aligned_nav_returns(df, source=source, adj_map=adj_map)
        if navs:
            result["latest_nav"] = navs[-1]

        if len(returns) < 5:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns)} daily returns"
            return result

        # 固定 60 日窗口，不同 ETF 间可比；不足 60 日则用全部可用数据
        returns_window = returns[-60:] if len(returns) >= 60 else returns
        if len(returns_window) < 5:
            result["status"] = "insufficient"
            result["_error"] = f"only {len(returns_window)} daily returns"
            return result
        mean_ret = sum(returns_window) / len(returns_window)
        variance = sum((r - mean_ret) ** 2 for r in returns_window) / (len(returns_window) - 1)
        daily_vol = math.sqrt(variance)
        result["volatility_annualized"] = round(daily_vol * math.sqrt(252) * 100, 2)
        result["volatility_window"] = len(returns_window)

        if len(navs) >= 20:
            result["ma20"] = round(sum(navs[-20:]) / 20, 4)
        if len(navs) >= 60:
            result["ma60"] = round(sum(navs[-60:]) / 60, 4)

        # D4: 指数价格 MA（底层指数的趋势结构，与 NAV MA 互补）
        idx_code = CSINDEX_MAP.get(symbol, "")
        if idx_code and len(navs) >= 60:
            try:
                _fetch_index_ma(result, idx_code)
            except Exception as exc:
                logger.info("index_ma(%s/%s) skipped: %s", symbol, idx_code, exc)

        # D8: BOLL 布林带（基于 NAV 序列，SMA(20) ± 2×std，用于波动率区间判断）
        if len(navs) >= 20:
            try:
                window = navs[-20:]
                mid = sum(window) / 20
                variance_boll = sum((x - mid) ** 2 for x in window) / (20 - 1)
                std_boll = math.sqrt(variance_boll)
                result["boll_mid"] = round(mid, 4)
                result["boll_upper"] = round(mid + 2.0 * std_boll, 4)
                result["boll_lower"] = round(mid - 2.0 * std_boll, 4)
            except Exception as exc:
                logger.info("boll(%s) skipped: %s", symbol, exc)

        period = 24 if len(navs) >= 25 else (14 if len(navs) >= 15 else None)
        if period is not None:
            rsi_val = _latest_rsi(navs, period)
            result["rsi"] = rsi_val
            result["rsi_period"] = period

        result["nav_history"] = [
            {"date": str(r.get("净值日期", "")), "nav": safe_float(r.get("单位净值")),
             "change_pct": safe_float(r.get("日增长率"))}
            for _, r in df.iterrows()
        ]
        result["status"] = "available"

    except Exception as exc:
        logger.warning("etf_kline(%s) failed: %s", symbol, exc)
        result["_error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _aligned_nav_returns(df: Any, *, source: str = "", adj_map: dict[str, float] | None = None) -> tuple[list[float], list[float]]:
    """从净值表构建对齐的 navs / returns（同一行样本）。

    Parameters
    ----------
    source : str
        数据源标识（仅用于日志/调试，不改变字段名解析逻辑）。
    adj_map : dict[str, float] | None
        Tushare fund_adj 复权因子映射 {date_str: adj_factor}。
        不为 None 时，对历史 NAV 做前复权（forward-adjust），
        使分红/拆分前后的 NAV 连续可比。
    """
    latest_adj = 1.0
    if adj_map:
        sorted_dates = sorted(adj_map.keys())
        latest_adj = adj_map[sorted_dates[-1]] if sorted_dates else 1.0

    navs: list[float] = []
    returns: list[float] = []
    prev_nav: float | None = None
    for _, row_data in df.iterrows():
        nav = safe_float(row_data.get("单位净值"))
        if nav is None:
            continue
        # 前复权：adjusted = raw * adj(d) / adj(latest)
        if adj_map:
            date_str = str(row_data.get("净值日期", ""))
            # Tushare fund_adj 日期格式为 "20260724"，akshare 为 "2026-07-24"
            # 统一为无分隔符格式做匹配
            date_key = date_str.replace("-", "")
            adj_d = adj_map.get(date_key)
            if adj_d is None:
                adj_d = adj_map.get(date_str)
            if adj_d and latest_adj > 0:
                nav = nav * adj_d / latest_adj
        chg = safe_float(row_data.get("日增长率"))
        if chg is not None:
            ret = chg / 100.0
            navs.append(nav)
            returns.append(ret)
            prev_nav = nav
        elif prev_nav is not None and prev_nav > 0:
            ret = (nav / prev_nav) - 1.0
            navs.append(nav)
            returns.append(ret)
            prev_nav = nav
        else:
            # 首行有效 NAV 但无日增长率 — 保留为锚点，不丢失数据
            prev_nav = nav
            navs.append(nav)
    return navs, returns


def _latest_rsi(navs: list[float], period: int) -> float | None:
    """Latest Wilder RSI from NAV close series (aligned with lib.technical)."""
    if len(navs) < period + 1:
        return None
    series = rsi_series(navs, period)
    for val in reversed(series):
        if val is not None:
            return round(val, 2)
    return None


def rollup_etf_quality_status(etf: dict) -> str:
    """Roll up etf_data bundle to available / partial / missing (journal query_data)."""
    errors = list(etf.get("_errors") or [])
    if etf.get("_error") and not errors:
        errors = [str(etf["_error"])]
    has_data = any(
        etf.get(k) is not None
        for k in ("index_pe", "premium_discount", "aum", "tracking_error")
    ) or bool(etf.get("hedge_coverage"))
    if errors and has_data:
        return "partial"
    if errors or not has_data:
        return "missing"
    return "available"


def _fetch_fund_adj_factor(symbol: str) -> dict[str, float] | None:
    """从 Tushare ``fund_adj`` 拉取 ETF 复权因子。

    用于前复权 NAV 序列，消除分红/拆分造成的断点。

    Returns
    -------
    dict or None
        {date_str: adj_factor}，Tushare 不可用时返回 None。
    """
    try:
        from lib.env import get_config

        config = get_config()
        token = config.get("TUSHARE_TOKEN")
        if not token:
            logger.info("fund_adj(%s): no TUSHARE_TOKEN, skipping", symbol)
            return None

        # ETF 代码 → Tushare ts_code（内联，避免跨包导入 lib.codes）
        s = symbol.strip()
        if s.startswith(("5", "6")):
            ts_code = f"{s}.SH"
        elif s.startswith(("0", "1", "3")):
            ts_code = f"{s}.SZ"
        elif s.startswith(("4", "8")):
            ts_code = f"{s}.BJ"
        else:
            return None

        import tushare as ts

        pro = ts.pro_api(token)
        df = pro.fund_adj(ts_code=ts_code, fields="trade_date,adj_factor")
        if df is None or df.empty:
            return None

        adj_map: dict[str, float] = {}
        for _, row in df.iterrows():
            d = str(row.get("trade_date", ""))
            f = safe_float(row.get("adj_factor"))
            if d and f is not None:
                adj_map[d] = f
        logger.info("fund_adj(%s): %d rows loaded, latest adj=%.4f", symbol, len(adj_map),
                     adj_map.get(sorted(adj_map.keys())[-1], 1.0) if adj_map else 1.0)
        return adj_map if adj_map else None
    except Exception as exc:
        logger.info("fund_adj(%s) unavailable: %s", symbol, exc)
        return None


def _fetch_index_ma(result: dict, idx_code: str) -> None:
    """从 akshare 拉取指数日 K 线，计算 index_ma20/index_ma60。

    降级策略：akshare 不可用或数据不足时静默跳过，不阻塞主流程。
    """
    try:
        import akshare as ak

        # csindex 代码 → akshare 行情代码
        # 中证(0/9开头)在上交所发布→sh；深证/创业板(3开头)在深交所→sz
        ticker = f"sh{idx_code}" if idx_code.startswith(("0", "9")) else f"sz{idx_code}"
        with akshare_direct_session():
            df = ak.stock_zh_index_daily(symbol=ticker)
        if df is None or df.empty:
            return
        closes = [safe_float(r.get("close")) for _, r in df.iterrows()]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 20:
            result["index_ma20"] = round(sum(closes[-20:]) / 20, 2)
        if len(closes) >= 60:
            result["index_ma60"] = round(sum(closes[-60:]) / 60, 2)
    except Exception:
        pass  # 非关键数据，静默降级


def _em_to_premium_discount(em_raw: object) -> float | None:
    """EM 基金折价率（+ = 折价）→ premium_discount（+ = 溢价）。"""
    em = safe_float(em_raw)
    return None if em is None else -em


# ---------------------------------------------------------------------------
# ETF 类型分类（G1）
# ---------------------------------------------------------------------------

def query_etf_category(symbol: str) -> dict[str, str]:
    """查询 ETF 类型标签。

    来源优先级：硬编码映射 > fund_etf_category_sina > 名称关键词推断。

    Returns
    -------
    dict
        {"category": "sector", "label": "行业ETF", "source": "builtin_map"}
    """
    cat = _ETF_CATEGORY_MAP.get(symbol)
    if cat:
        return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "builtin_map"}

    # 动态查询 akshare 分类（兜底）
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_etf_category_sina()
        row = df[df["代码"] == symbol] if df is not None else None
        if row is not None and not row.empty:
            name = str(row.iloc[0].get("名称", ""))
            cat = _infer_category_from_name(name)
            return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "sina_dynamic"}
    except Exception:
        pass

    # 名称关键词回退
    try:
        row2, _ = _lookup_etf_spot_row(symbol)  # type: ignore[arg-type]
        if row2 is not None:
            name = str(row2.get("名称", ""))
            cat = _infer_category_from_name(name)
            return {"category": cat, "label": _CATEGORY_LABELS.get(cat, cat), "source": "name_fallback"}
    except Exception:
        pass

    return {"category": "unknown", "label": "未知", "source": "none"}


def _infer_category_from_name(name: str) -> str:
    """从 ETF 名称关键词推断类型。"""
    n = name.lower()
    if any(k in n for k in ["qdii", "纳指", "标普", "恒生", "日经", "德国"]):
        return "cross_border"
    if any(k in n for k in ["黄金", "豆粕", "原油", "商品期货", "期货"]):
        return "commodity"
    if any(k in n for k in ["货币", "日利", "保证金"]):
        return "money_market"
    if any(k in n for k in ["债", "国债", "信用债", "可转债"]):
        return "bond"
    if any(k in n for k in ["沪深300", "中证500", "中证1000", "中证2000",
                              "上证50", "科创50", "创业板", "深证"]):
        return "broad_market"
    return "sector"


# ---------------------------------------------------------------------------
# 行业 PE 附加 + 估值指引（G4 + G9）
# ---------------------------------------------------------------------------

def _attach_industry_pe(result: dict, symbol: str) -> None:
    """对行业/主题 ETF，从 industry_weekly SQLite 表查行业 PE 注入 result。"""
    sw_info = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw_info:
        return
    # 仅当无 csindex PE 时才附加行业 PE（行业 ETF 通常 not_mapped 或 unknown_etf）
    if result.get("index_pe") is not None:
        return
    try:
        import sqlite3
        from lib.store import _conn, _safe_close
        c = _conn()
        try:
            row = c.execute(
                "SELECT pe, pb, date FROM industry_weekly "
                "WHERE index_code = ? ORDER BY date DESC LIMIT 1",
                (sw_info["sw_code"],),
            ).fetchone()
        except sqlite3.OperationalError:
            return
        finally:
            _safe_close(c)
        if row:
            pe_val = row["pe"]
            if pe_val is None:
                return  # pe 字段为 NULL（如行业整体亏损），静默跳过
            result["industry_pe"] = pe_val
            result["industry_pe_note"] = (
                f"申万{sw_info['sw_name']}({sw_info['sw_code']})行业 PE={pe_val:.2f}，"
                f"数据日期 {row['date']}；"
                f"此为行业层面估值参考，非 ETF 精确 PE"
            )
    except Exception:
        pass  # 行业 PE 为非关键附加数据，降级不阻塞主流程


def _attach_industry_allocation(result: dict, symbol: str) -> None:
    """从 akshare ``fund_portfolio_industry_allocation_em`` 获取 ETF 行业配置比例（G6）。

    降级策略：接口不可用或数据为空时静默跳过。
    取最新报告期数据，去重，过滤占比为 0 的行业。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_portfolio_industry_allocation_em(symbol=symbol, date="2026")
        if df is None or df.empty:
            return
        # 取最新一期数据
        latest_date = str(sorted(df["截止时间"].unique())[-1])
        latest = df[df["截止时间"] == latest_date]
        alloc: list[dict] = []
        for _, row in latest.iterrows():
            industry = str(row.get("行业类别", ""))
            pct = safe_float(row.get("占净值比例"))
            if industry and pct is not None and pct > 0:
                alloc.append({"industry": industry, "pct": round(pct, 2)})
        if alloc:
            alloc.sort(key=lambda x: x["pct"], reverse=True)
            result["industry_allocation"] = alloc
            result["industry_allocation_date"] = latest_date
    except Exception:
        pass  # 非关键数据，降级不阻塞


def _attach_valuation_guide(result: dict) -> None:
    """附加该 ETF 对应行业的推荐估值指标指引。"""
    symbol = result.get("symbol", "")
    sw_info = ETF_TO_SW_INDUSTRY.get(symbol)
    if not sw_info:
        return
    guide = SECTOR_VALUATION_MAP.get(sw_info["sw_name"])
    if guide:
        result["valuation_guide"] = {
            "industry": sw_info["sw_name"],
            "sub_sector": sw_info.get("sub", ""),
            "primary": guide["primary"],
            "secondary": guide["secondary"],
            "pe_timing": guide["pe_timing"],
            "reason": guide["reason"],
        }


def query_sector_valuation_guide(sw_name: str) -> dict | None:
    """查询申万行业的推荐估值指标。

    Parameters
    ----------
    sw_name : str
        申万一级行业名称（如 "电子"、"银行"）。

    Returns
    -------
    dict or None
        {primary, secondary, pe_timing, reason}
    """
    return SECTOR_VALUATION_MAP.get(sw_name)


# ---------------------------------------------------------------------------
# ETF 份额历史序列（v0.2.2 P0）
# ---------------------------------------------------------------------------

def save_etf_share_snapshot(symbol: str) -> dict | None:
    """采集当日 ETF 份额快照并写入 etf_share_snapshots 表。

    数据源：akshare ``fund_etf_spot_em`` 的 ``最新份额`` 列。

    Parameters
    ----------
    symbol : str
        6 位 ETF 代码（如 588000）。

    Returns
    -------
    dict or None
        写入的快照字典；非交易日（价格/份额为 NaN）返回 None。
    """
    import sqlite3
    from datetime import date

    from lib.store import _conn, _safe_close, init_db

    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.fund_etf_spot_em()
    except Exception as exc:
        logger.warning("etf share snapshot: fund_etf_spot_em failed: %s", exc)
        return None

    if df is None or df.empty:
        return None

    row = df[df["代码"] == symbol]
    if row.empty:
        logger.warning("etf share snapshot: symbol %s not found in spot data", symbol)
        return None

    row = row.iloc[0]
    shares = safe_float(row.get("最新份额"))
    price = safe_float(row.get("最新价"))

    # 非交易日检测
    if shares is None or price is None:
        logger.info("etf share snapshot: %s 疑似非交易日（份额/价格缺失），跳过", symbol)
        return None

    aum = round(shares * price / 1e8, 2)
    today = date.today().strftime("%Y%m%d")

    snap = {
        "date": today,
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "aum": aum,
    }

    init_db()
    c = _conn()
    try:
        c.execute(
            "INSERT OR REPLACE INTO etf_share_snapshots (date, symbol, shares, price, aum) "
            "VALUES (?, ?, ?, ?, ?)",
            (today, symbol, shares, price, aum),
        )
        c.commit()
        logger.info("etf share snapshot %s/%s saved: %.0f 份, AUM %.2f 亿", today, symbol, shares, aum)
        return snap
    except Exception as exc:
        logger.warning("etf share snapshot save failed: %s", exc)
        c.rollback()
        return None
    finally:
        _safe_close(c)


def etf_share_flow(symbol: str, days: int = 60) -> dict:
    """读取 ETF 份额历史序列，计算份额变动和估算资金流。

    Parameters
    ----------
    symbol : str
        6 位 ETF 代码。
    days : int
        回溯行数（默认 60 行，对应约 60 个交易日；注意非自然日语义，依赖每日 snapshot 采集频率）。

    Returns
    -------
    dict
        {symbol, date, shares_current, aum_current,
         share_change_5d/20d/60d (份),
         flow_est_5d/20d/60d (亿元),
         history_count}
    """
    import sqlite3

    from lib.store import _conn, _safe_close

    c = _conn()
    try:
        rows = c.execute(
            "SELECT date, shares, price, aum FROM etf_share_snapshots "
            "WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol, days),
        ).fetchall()
        rows = list(reversed(rows))  # 恢复为 ASC 顺序
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {"symbol": symbol, "history_count": 0, "note": "无历史数据（etf_share_snapshots 表不存在）"}
        raise
    finally:
        _safe_close(c)

    if not rows:
        return {"symbol": symbol, "history_count": 0, "note": "无历史数据"}

    history = [dict(r) for r in rows]
    latest = history[-1]

    def _change(window: int) -> dict:
        if len(history) < window + 1:
            return {"share_change": None, "flow_est": None}
        prev = history[-(window + 1)]
        d_shares = latest["shares"] - prev["shares"]
        avg_price = (latest["price"] + prev["price"]) / 2
        flow_est = round(d_shares * avg_price / 1e8, 2) if avg_price > 0 else None
        return {
            "share_change": d_shares,
            "flow_est": flow_est,
        }

    return {
        "symbol": symbol,
        "date": latest["date"],
        "shares_current": latest["shares"],
        "aum_current": latest["aum"],
        "share_change_5d": _change(5)["share_change"],
        "flow_est_5d": _change(5)["flow_est"],
        "share_change_20d": _change(20)["share_change"],
        "flow_est_20d": _change(20)["flow_est"],
        "share_change_60d": _change(60)["share_change"],
        "flow_est_60d": _change(60)["flow_est"],
        "history_count": len(history),
    }
