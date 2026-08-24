"""Collection orchestration — dimension collectors, market structure, industry peers."""
from __future__ import annotations
import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from lib.nums import (  # review 二轮 R-13：dict 行末列兜底（可单测）
    ONE_PER_WAN,
    ONE_PER_YI,
    WAN_PER_YI,
    coalesce_field,
    row_value_or_last,
    safe_float,
)

# code-review #9：弃用 dir()-copy 隐式再导出，消费名全部显式导入
from .. import env
from ..proxy import akshare_direct_session, akshare_push2_available
from ..schema import DimensionResult, SourceResult
from ..shared_dates import (
    shanghai_days_ago as _days_ago,
    shanghai_today as _today,
    yyyymmdd_to_iso as _to_iso_date,
)

from ._base import (
    _annotate_query_params,
    _fred_date,
    _latest_quarter_end,
    _map_parallel,
    _proxy_bypass,
    _run_in_thread,
    _run_one_source,
    _run_sources_cascade,
    _run_sources_parallel,
    _ts_code,
)
from ._sources import (
    _apply_qfq,
    _flow_amount_yuan,
    _q_akshare_basic,
    _q_akshare_financials,
    _q_akshare_industry_board,
    _q_akshare_industry_pe,
    _q_akshare_kline,
    _q_akshare_northbound,
    _q_akshare_shareholders,
    _q_baostock_kline,
    _q_tencent_quote,
    _q_tickflow_kline,
    _q_tushare_adj_factor,
    _q_tushare_basic,
    _q_tushare_daily,
    _q_tushare_daily_qfq,
    _q_tushare_financials,
    _q_tushare_hsgt_top10,
    _q_tushare_moneyflow,
    _q_tushare_shareholders,
    _qp_akshare,
    _qp_baostock,
    _qp_tencent,
    _qp_tickflow,
    _qp_tushare,
    _require_tushare,
    _tushare_client,
)


logger = logging.getLogger(__name__)

# ---- 各维度采集（并行 fan-out）----

def _collect_dimension(
    dimension: str,
    tasks: list[tuple[str, Callable]],
    *,
    query_params: dict[str, str] | Callable[[list], dict[str, str]] | None = None,
    postprocess: Callable[[dict, list], dict] | None = None,
    empty_result: dict | None = None,
    cascade: bool = False,
    cascade_always: set[str] | None = None,
) -> dict:
    """采集样板（R12h：cascade=True 时首选源单发、失败按序降级；L2 维度保持并行双源先到先用）。

    tasks → _run_sources_parallel | _run_sources_cascade → annotate → legacy → postprocess。
    cascade_always：cascade 模式下不随降级链跳过的源名集合（如 quote 的腾讯实时快照）。
    """
    if not tasks and empty_result is not None:
        return empty_result
    results = (_run_sources_cascade(tasks, dimension, always_attempt=cascade_always)
               if cascade else _run_sources_parallel(tasks, dimension))
    if query_params is not None:
        qp = query_params(results) if callable(query_params) else query_params
        _annotate_query_params({r.source: r for r in results}, qp)
    legacy = DimensionResult(dimension, results).to_legacy_dict()
    return postprocess(legacy, results) if postprocess else legacy


def collect_basic_info(symbol: str) -> dict:
    """基本信息。cascade 首选单发（R12h）：Tushare 失败 → akshare 按序降级。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.stock_basic", lambda: _q_tushare_basic(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_individual_info_em",
                      lambda: _q_akshare_basic(symbol)))
    return _collect_dimension(
        "basic_info", tasks,
        query_params={
            "tushare.stock_basic": _qp_tushare("stock_basic", symbol),
            "akshare.stock_individual_info_em": _qp_akshare("stock_individual_info_em", symbol),
        },
        # R12h: 非 L2 维度首选源单发（tushare 失败 → akshare 降级）
        cascade=True,
    )


def collect_financials(symbol: str) -> dict:
    """财务报告。并行双源（L2 保持 _run_sources_parallel）：Tushare + akshare 先到先用。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.fina_indicator", lambda: _q_tushare_financials(symbol)))
    if env.is_akshare_available():
        tasks.append(("akshare.stock_financial_abstract_ths",
                      lambda: _q_akshare_financials(symbol)))

    def _attach_dcf(legacy: dict, _results: list) -> dict:
        try:
            from ..valuation import attach_dcf_preprocess
            attach_dcf_preprocess(legacy)
        except Exception as exc:
            logger.warning("dcf_preprocess failed for %s: %s", symbol, exc)
        return legacy

    return _collect_dimension(
        "financials", tasks,
        query_params={
            "tushare.fina_indicator": _qp_tushare(
                "fina_indicator", symbol,
                start_date=_days_ago(1460), end_date=_today()),  # ≥3 年报（R1 需）
            "akshare.stock_financial_abstract_ths": _qp_akshare(
                "stock_financial_abstract_ths", symbol, indicator="按报告期"),
        },
        postprocess=_attach_dcf,
    )


def collect_shareholders(symbol: str) -> dict:
    """十大股东。并行：Tushare + akshare。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.top10_floatholders", lambda: _q_tushare_shareholders(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_gdfx_top_10_em",
                      lambda: _q_akshare_shareholders(symbol)))
    return _collect_dimension(
        "shareholders", tasks,
        query_params={
            "tushare.top10_floatholders": _qp_tushare(
                "top10_floatholders", symbol, period=_latest_quarter_end()),
            "akshare.stock_gdfx_top_10_em": _qp_akshare("stock_gdfx_top_10_em", symbol),
        },
        cascade=True,  # R12h: tushare 首选，失败 → akshare
    )


def _apply_qfq_with_newest_raw_fallback(
    rows: list[dict], factors: dict[str, float],
) -> list[dict] | None:
    """盘中 adj_factor 未发布时的 qfq 兜底：去掉最新日重试，尾部按 raw 保留。

    Tushare 当日 adj_factor 盘后才发布：_apply_qfq 对"最新日缺因子"整体
    拒绝 → 调用方此前整串回退 raw，与 akshare qfq 历史混串，多日消费者
    （10 日趋势等）看到未复权收盘。这里去掉最新日重试 _apply_qfq，成功
    则最新日按 raw 价格原样保留。

    近似说明：最新日在自身锚定下 qfq==raw；若当日恰为除权日，锚点由 D
    平移至 D-1，raw 值与真 qfq 值相差 f_D/f_{D-1} 量级——D-1(qfq) → D(raw)
    边界会出现假价格跳变（如 10% 分红看似跌 10%）。因此 fallback 路径把
    最新日标记 ``has_qfq_gap: True``：该行是 raw 而非与历史同标度的 qfq，
    需要连续性的消费者（MA20 偏离、10 日趋势等）应排除/跳过标记行，不得
    让假跳变进入 data[-1] 的连续性计算。重试仍失败（因子整体缺失/中间日
    缺失）→ 返回 None，调用方沿用 raw 整串回退。
    """
    adjusted = _apply_qfq(rows, factors)
    if adjusted is not None:
        return adjusted
    if not factors or len(rows) < 2:
        return None
    newest_td = max(str(r.get("trade_date") or "") for r in rows)
    rest = [r for r in rows if str(r.get("trade_date") or "") != newest_td]
    if not rest:
        return None
    adjusted_rest = _apply_qfq(rest, factors)
    if adjusted_rest is None:
        return None
    # 最新日 raw 与历史 qfq 段不同标度：若当日为除权日，D-1(qfq)→D(raw) 边界
    # 产生假跳变。标记 has_qfq_gap 而非静默混入，让连续性消费者显式排除。
    newest_rows = [
        dict(r, has_qfq_gap=True)
        for r in rows if str(r.get("trade_date") or "") == newest_td
    ]
    return adjusted_rest + newest_rows


def _quote_tushare_rows(symbol: str) -> list[dict] | None:
    """quote 维度的 Tushare 日线：前复权 + 升序。

    升序使 data[-1] = 最新日，与 akshare 升序及共享消费者
    （schema._extract_scalar 等 data[-1]-is-newest 约定）对齐——
    tushare 降序时 data[-1] 是最旧 bar，逐份报告产出"最新收盘"错值。
    盘中 adj_factor 未发布（常态）时经 _apply_qfq_with_newest_raw_fallback
    去掉最新日重试、最新日按 raw 保留：历史段保持 qfq 连续，最新日标量
    不变（qfq==raw 于自身锚定）；因子整体缺失才整串回退 raw。
    """
    from lib.technical import sort_kline_asc

    # 单次 daily 取数：_q_tushare_daily_qfq 内部先取 daily 再取 adj_factor，
    # 盘中 adj_factor 未发布时返回 None → 再调 _q_tushare_daily 会重复取同一
    # daily 序列（3 次 Tushare 往返）；这里组合拆开，raw 兜底复用已取的行。
    raw_rows = _q_tushare_daily(symbol, start_date=_days_ago(10), end_date=_today())
    if not raw_rows:
        return None
    rows = _apply_qfq_with_newest_raw_fallback(
        raw_rows,
        _q_tushare_adj_factor(symbol, start_date=_days_ago(10), end_date=_today()),
    )
    # 升序排序委托 lib.technical.sort_kline_asc（混合日期格式归一化 +
    # 无日期行置尾的共享约定，而非本地字符串排序）
    return sort_kline_asc(rows if rows is not None else raw_rows)


def collect_quote(symbol: str) -> dict:
    """实时行情。R12h：Tushare 首选单发 → akshare（L3 行情类，EM 最后）；腾讯实时快照始终并行尝试。

    腾讯实时快照（change_pct/turnover_rate/pe_ratio/total_mv）是 quote 维度最关键的
    实时字段，不依赖 tushare 成功：无论链内是否已成功都独立尝试（失败不影响 tushare
    结果）；成功后实时字段并入维度数据（主数据为 K 线 list 时 → 合并为 dict，K 线保留
    在 kline 字段），tushare 健康时报告不再丢失实时涨跌/换手/市值。
    """
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        # 统一前复权语义（与 akshare qfq 对齐，跨源校验不再因 raw/qfq 错配产生 divergence 噪音）
        tasks.append(("tushare.daily", lambda: _quote_tushare_rows(symbol)))
    tasks.append(("tencent_finance", lambda: _q_tencent_quote(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_zh_a_hist",
                      lambda: _q_akshare_kline(symbol, start_date=_days_ago(10), end_date=_today())))

    def _merge_realtime(legacy: dict, results: list) -> dict:
        """腾讯实时快照并入维度主数据（仅当快照含有效字段，全空快照不覆盖 K 线）。"""
        tencent = next(
            (r for r in results if r.source == "tencent_finance"), None)
        if tencent is None or not isinstance(tencent.data, dict):
            return legacy
        if not any(v is not None for v in tencent.data.values()):
            return legacy  # 休市/停牌全空快照：保留主数据原状
        data = legacy.get("data")
        if isinstance(data, list):
            merged = dict(tencent.data)
            merged["kline"] = data  # 保留 10 日 K 线（消费方按 dict 读实时字段）
            legacy["data"] = merged
        return legacy

    return _collect_dimension(
        "quote", tasks,
        query_params={
            "tushare.daily": _qp_tushare("daily", symbol,
                                         start_date=_days_ago(10), end_date=_today())
                                         + " (qfq via adj_factor, asc, raw-fallback)",
            "akshare.stock_zh_a_hist": _qp_akshare(
                "stock_zh_a_hist", symbol, period="daily",
                start_date=_days_ago(10), end_date=_today()),
            "tencent_finance": _qp_tencent(symbol),
        },
        cascade=True,           # kline 冗余链：tushare → akshare(EM 最后)
        cascade_always={"tencent_finance"},  # 实时快照始终并行尝试
        postprocess=_merge_realtime,
    )


def collect_northbound(symbol: str) -> dict:
    """北向资金。并行：Tushare hsgt_top10 + akshare 个股持股变动。"""
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.hsgt_top10", lambda: _hsgt_top10_cached(symbol)))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_hsgt_individual_em",
                      lambda: _q_akshare_northbound(symbol)))
    return _collect_dimension(
        "northbound", tasks,
        query_params={
            "tushare.hsgt_top10": _qp_tushare(
                "hsgt_top10", symbol, start_date=_days_ago(30), end_date=_today()),
            "akshare.stock_hsgt_individual_em": _qp_akshare(
                "stock_hsgt_individual_em", symbol),
        },
        cascade=True,  # R12h: tushare 首选，失败 → akshare
    )


def _sort_kline_asc_postprocess(legacy: dict, _results: list) -> dict:
    """P2-3 v0.2.7: kline 数据统一升序落库（data[-1]=最新，canonical 约定
    skills/lib/technical.py sort_kline_asc）。Tushare daily 返回降序，此前
    原序进 store raw_json——现有消费方显式排序未出错，但未排序消费方将
    静默算错窗口（batch-review P2-3，复检脚本实证「近 20 日 -27.71%」误算）。
    存量数据不回溯：读侧仍须显式排序或按日期取 max。"""
    data = legacy.get("data")
    if isinstance(data, list) and data:
        from lib.technical import sort_kline_asc
        legacy["data"] = sort_kline_asc([r for r in data if isinstance(r, dict)])
    return legacy


def collect_kline(symbol: str, start_date: str = "", end_date: str = "") -> dict:
    """日K线。并行：Tushare + akshare + baostock(兜底) [+ tickflow(可选)]。

    复权语义：全部源统一前复权（tushare adj_factor 自算 / akshare adjust=qfq /
    baostock adjustflag=2 / tickflow forward）——技术指标（MA/BOLL/RSI）在不复权
    价格上会被除权日跳变污染，前复权使历史价格连续，且跨源校验不再因语义
    错配产生 divergence 噪音。

    默认窗口 400 自然日，覆盖 MA250（需 ≥250 个交易日缓冲）。
    --deep 模式通过 invest.py 传入 start_date=_days_ago(730)。
    同日重复采集命中 _kline_cache（TTL 1 天，INVEST_KLINE_CACHE=0 禁用）；
    quote 维度（近实时行情）有意不进缓存。
    源开关：baostock 默认 auto（仅无 Tushare token 时启用，INVEST_ENABLE_BAOSTOCK
    可覆盖）；tickflow 默认关闭（INVEST_ENABLE_TICKFLOW=1 启用）。
    """
    sd = start_date or _days_ago(400)
    ed = end_date or _today()

    from . import _kline_cache  # 惰性导入，避免包初始化开销

    def _cached(source: str, fetch: Callable[[], list | None],
                qfq: bool = True) -> list | None:
        return _kline_cache.load_or_fetch(symbol, source, sd, ed, fetch, qfq=qfq)

    # R12h（L3 行情类：tushare → baostock → 腾讯类 → EM 最后）——cascade 首选源单发
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.daily", lambda: _cached("tushare.daily",
                      lambda: _q_tushare_daily_qfq(symbol, start_date=sd, end_date=ed))))
    if env.baostock_kline_enabled():
        tasks.append(("baostock.kline", lambda: _cached("baostock.kline",
                      lambda: _q_baostock_kline(symbol, start_date=sd, end_date=ed))))
    if env.tickflow_kline_enabled():
        tasks.append(("tickflow.kline", lambda: _cached("tickflow.kline",
                      lambda: _q_tickflow_kline(symbol, start_date=sd, end_date=ed))))
    if env.is_akshare_available() and akshare_push2_available():
        tasks.append(("akshare.stock_zh_a_hist", lambda: _cached("akshare.stock_zh_a_hist",
                      lambda: _q_akshare_kline(symbol, start_date=sd, end_date=ed))))

    def _kline_qp(_results: list) -> dict[str, str]:
        qp_map: dict[str, str] = {
            "tushare.daily": _qp_tushare("daily", symbol, start_date=sd, end_date=ed)
            + " + adj_factor(前复权自算)",
            "akshare.stock_zh_a_hist": _qp_akshare(
                "stock_zh_a_hist", symbol, period="daily",
                start_date=sd, end_date=ed, adjust="qfq"),
        }
        if env.is_baostock_available():
            qp_map["baostock.kline"] = _qp_baostock(symbol, sd, ed)
        if env.is_tickflow_available():
            qp_map["tickflow.kline"] = _qp_tickflow(symbol, sd, ed)
        return qp_map

    return _collect_dimension(
        "kline", tasks, query_params=_kline_qp, cascade=True,
        postprocess=_sort_kline_asc_postprocess,
    )


# ---- 估值维度 ----

def _q_tushare_daily_basic(symbol: str) -> list[dict] | None:
    """Tushare daily_basic 接口：获取每日 PE/PB/PS 历史序列。

    API: pro.daily_basic(ts_code, start_date, end_date, fields=...)
    配额: 每股 1 次调用。

    归一化（跨源校验 C5 缺陷修复）：
    - 单位：total_mv 原始为万元 → 亿元（与腾讯快照 total_mv 亿元口径一致；
      此前无换算 → 跨源校验恒差 ~200%，每份报告误标 divergence）。
    - 行序：Tushare 返回最新在前（降序）→ 显式按 trade_date 升序，
      data[-1] = 最新（共享 data[-1]-is-newest 约定，见 _quote_tushare_rows）。
    """
    config, tc = _require_tushare()
    df = tc.query("daily_basic", ts_code=_ts_code(symbol),
                  fields="trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv",
                  start_date=_days_ago(1825), end_date=_today())
    if df is not None and not df.empty:
        records = df.to_dict("records")
        for rec in records:
            mv = safe_float(rec.get("total_mv"))
            if mv is not None:
                rec["total_mv"] = mv / WAN_PER_YI  # 万元 → 亿元
        from lib.technical import sort_kline_asc

        return sort_kline_asc(records)
    return None


def _q_tencent_valuation_snapshot(symbol: str) -> dict | None:
    """腾讯行情估值快照：当前 PE。作为 Tushare 不可用时的降级源。"""
    quote = _q_tencent_quote(symbol)
    if quote is None:
        return None
    result: dict[str, Any] = {}
    if quote.get("pe_ratio") is not None:
        result["pe_ttm"] = quote["pe_ratio"]
    if quote.get("total_mv") is not None:
        result["total_mv"] = quote["total_mv"]
    result["history_available"] = False  # 腾讯仅为快照，无历史序列
    return result if result else None


def _qp_tushare_daily_basic(symbol: str) -> str:
    return _qp_tushare("daily_basic", symbol,
                       start_date=_days_ago(1825), end_date=_today(),
                       fields="trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv")


def collect_valuation(symbol: str) -> dict:
    """估值分析。并行：Tushare daily_basic（历史序列） + 腾讯快照。

    有 Tushare Token: 获取 5 年历史序列 + 分位
    无 Tushare Token: 仅腾讯当前 PE 快照，标注"历史分位不可得"
    """
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.daily_basic", lambda: _q_tushare_daily_basic(symbol)))
    tasks.append(("tencent_finance", lambda: _q_tencent_valuation_snapshot(symbol)))

    def _valuation_qp(results: list) -> dict[str, str]:
        qp_map: dict[str, str] = {"tencent_finance": _qp_tencent(symbol)}
        sources = {r.source for r in results}
        if "tushare.daily_basic" in sources:
            qp_map["tushare.daily_basic"] = _qp_tushare_daily_basic(symbol)
        return qp_map

    return _collect_dimension("valuation", tasks, query_params=_valuation_qp)


# ---- 机构研报与盈利预测采集 ----

def _q_tushare_report_rc(symbol: str) -> list[dict] | None:
    """Tushare report_rc：机构研报盈利预测（含评级/目标价）。

    权限：特色大数据，需 10000+积分 或单独购买券商研报库（500元/年）。
    120 积分可试用（日 10 次）。
    字段：rating, max_price, min_price, eps, pe, np, org_name, report_date

    Returns:
        list[dict] | None — 研报记录列表，权限不足或失败返回 None
    """
    config, tc = _require_tushare()
    ts = _ts_code(symbol)
    try:
        df = tc.query("report_rc", ts_code=ts,
                      start_date=_days_ago(180), end_date=_today(),
                      fields="ts_code,name,report_date,report_title,"
                             "org_name,rating,max_price,min_price,"
                             "eps,pe,np,op_rt,roe,classify,quarter")
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("report_rc: %d records for %s", len(out), ts)
            return out
        return None
    except Exception as exc:
        err = str(exc)
        if "权限" in err or "40203" in err or "无权限" in err:
            logger.info("report_rc 权限不足（需 10000+积分），降级: %s", err)
            return None
        logger.warning("report_rc query failed for %s: %s", ts, err)
        return None


def _q_tushare_forecast(symbol: str) -> list[dict] | None:
    """Tushare forecast：业绩预告（上市公司自行披露的盈利预测）。

    权限：2000 积分可用。
    字段：end_date, type, p_change_min, p_change_max, profit_min, profit_max

    Returns:
        list[dict] | None — 业绩预告记录，权限不足或无数据返回 None
    """
    config, tc = _require_tushare()
    ts = _ts_code(symbol)
    try:
        df = tc.query("forecast", ts_code=ts,
                      start_date=_days_ago(365), end_date=_today(),
                      fields="ts_code,end_date,type,p_change_min,p_change_max,"
                             "profit_min,profit_max,last_parent_net")
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("forecast: %d records for %s", len(out), ts)
            return out
        return None
    except Exception as exc:
        err = str(exc)
        if "权限" in err or "40203" in err or "无权限" in err:
            logger.info("forecast 权限不足（需 2000+积分），降级: %s", err)
            return None
        logger.warning("forecast query failed for %s: %s", ts, err)
        return None


def _q_akshare_research(symbol: str) -> list[dict] | None:
    """akshare：东方财富个股研报（免注册，但依赖东方财富接口）。

    注意：当前代理环境可能阻断东方财富 push2 接口。
    当 akshare_push2_available() 为 False 时跳过。
    """
    if not env.is_akshare_available():
        return None
    if not akshare_push2_available():
        logger.info("akshare_research: 东方财富 push2 不可达，跳过")
        return None
    try:
        import akshare as ak
        # stock_research_report_em 接口仅支持 symbol 参数；东财需直连
        with akshare_direct_session():
            df = ak.stock_research_report_em(symbol=symbol)
        if df is not None and not df.empty:
            out = df.to_dict("records")
            logger.info("akshare_research: %d records for %s", len(out), symbol)
            return out
        return None
    except Exception as exc:
        logger.warning("akshare_research failed for %s: %s", symbol, exc)
        return None


def _aggregate_sellside_price_range(
    prices: list[tuple[Any, Any]],
) -> dict[str, float] | None:
    """聚合卖方预期价位；单侧仅有 max 或 min 时也输出区间。"""
    valid_max: list[float] = []
    valid_min: list[float] = []
    for mx, mn in prices:
        fm = safe_float(mx)
        fn = safe_float(mn)
        if fm is not None:
            valid_max.append(fm)
        if fn is not None:
            valid_min.append(fn)
    if not valid_max and not valid_min:
        return None
    low = min(valid_min) if valid_min else min(valid_max)
    high = max(valid_max) if valid_max else max(valid_min)
    out: dict[str, float] = {
        "min": round(low, 2),
        "max": round(high, 2),
    }
    if out["min"] > out["max"]:
        out["min"], out["max"] = out["max"], out["min"]
    if valid_max:
        out["avg_upper"] = round(sum(valid_max) / len(valid_max), 2)
    if valid_min:
        out["avg_lower"] = round(sum(valid_min) / len(valid_min), 2)
    return out


def _summarize_research(tushare_rc: list[dict] | None,
                        tushare_fc: list[dict] | None,
                        akshare_rc: list[dict] | None) -> dict:
    """将多源研报数据汇总为统一结构。

    优先使用 Tushare report_rc（含评级和目标价），
    其次使用 Tushare forecast（仅业绩预告），
    最后使用 akshare。
    """
    summary = {
        "latest_ratings": [],       # 最新卖方评级
        "target_price_range": None, # {min, max} 目标价区间
        "eps_forecasts": [],        # EPS预测列表
        "profit_forecasts": [],     # 净利润预测
        "company_guidance": None,   # 公司自身业绩预告
        "source": None,
        "status": "no_data",
        "summary_text": "",
    }

    # Tier 1: Tushare report_rc (含评级+目标价)
    if tushare_rc:
        # 取最新（按 report_date 排序）
        latest = sorted(tushare_rc, key=lambda r: r.get("report_date", ""), reverse=True)
        # 提取评级
        ratings = [
            {"org": r.get("org_name"), "rating": r.get("rating"),
             "report_date": r.get("report_date")}
            for r in latest if r.get("rating")
        ]
        summary["latest_ratings"] = ratings[:10]  # 取前10条

        # 提取卖方预期价位
        prices = [
            (r.get("max_price"), r.get("min_price"))
            for r in latest
            if r.get("max_price") is not None or r.get("min_price") is not None
        ]
        if prices:
            summary["target_price_range"] = _aggregate_sellside_price_range(prices)

        # 提取 EPS 预测（按报告期聚合）
        eps_by_quarter = {}
        for r in latest:
            q = r.get("quarter") or r.get("report_type", "") or "unknown"
            eps = r.get("eps")
            if eps is not None:
                if q not in eps_by_quarter:
                    eps_by_quarter[q] = []
                eps_by_quarter[q].append(eps)
        summary["eps_forecasts"] = [
            {"quarter": q, "avg_eps": round(sum(vs) / len(vs), 4),
             "n_analysts": len(vs)}
            for q, vs in sorted(eps_by_quarter.items())
        ]

        # 提取净利润预测（NP 字段，万元→亿元）
        np_by_quarter = {}
        for r in latest:
            q = r.get("quarter") or "unknown"
            np_val = r.get("np")
            if np_val is not None:
                if q not in np_by_quarter:
                    np_by_quarter[q] = []
                np_by_quarter[q].append(np_val)
        summary["profit_forecasts"] = [
            {"quarter": q, "avg_np_100m": round(sum(vs) / len(vs) / WAN_PER_YI, 2),
             "n_analysts": len(vs)}
            for q, vs in sorted(np_by_quarter.items())
        ]

        summary["source"] = "tushare.report_rc"
        summary["status"] = "ok"

        # 生成摘要文本（LAW 6：禁「买入」「目标价」字面）
        n_ratings = len(ratings)
        bullish = sum(
            1 for r in ratings
            if "买" in str(r.get("rating", "")) and "卖" not in str(r.get("rating", ""))
        )
        if summary["target_price_range"]:
            tp = summary["target_price_range"]
            summary["summary_text"] = (
                f"近半年 {n_ratings} 条机构评级（{bullish} 条偏多），"
                f"卖方预期价位 {tp['min']}–{tp['max']} 元"
            )
        else:
            summary["summary_text"] = (
                f"近半年 {n_ratings} 条机构评级（{bullish} 条偏多），无公开价位预期"
            )
        return summary

    # Tier 2: Tushare forecast (业绩预告)
    if tushare_fc:
        latest_fc = sorted(tushare_fc, key=lambda r: r.get("end_date", ""), reverse=True)
        if latest_fc:
            rec = latest_fc[0]
            p_min = safe_float(rec.get("p_change_min"))
            p_max = safe_float(rec.get("p_change_max"))
            last_net = safe_float(rec.get("last_parent_net"))  # 上年同期归母净利（万元）
            profit_min = safe_float(rec.get("profit_min"))
            profit_max = safe_float(rec.get("profit_max"))
            guidance = {
                "end_date": rec.get("end_date"),
                "type": rec.get("type"),
                "pct_change_min": p_min,
                "pct_change_max": p_max,
            }
            if profit_min is not None and profit_max is not None:
                guidance["profit_min_100m"] = round(profit_min / WAN_PER_YI, 2)
                guidance["profit_max_100m"] = round(profit_max / WAN_PER_YI, 2)
            elif last_net is not None and p_min is not None and p_max is not None:
                guidance["profit_min_100m"] = round(last_net * (1 + p_min / 100) / WAN_PER_YI, 2)
                guidance["profit_max_100m"] = round(last_net * (1 + p_max / 100) / WAN_PER_YI, 2)
                guidance["last_parent_net_100m"] = round(last_net / WAN_PER_YI, 2)
            summary["company_guidance"] = guidance
            summary["source"] = "tushare.forecast"
            summary["status"] = "ok_guidance_only"
            if guidance.get("profit_min_100m") is not None:
                if p_min is not None and p_max is not None:
                    summary["summary_text"] = (
                        f"公司业绩预告（{rec.get('type', '')}）：净利润 "
                        f"{guidance['profit_min_100m']}–{guidance['profit_max_100m']} 亿元 "
                        f"（同比 {p_min}%–{p_max}%）"
                    )
                else:
                    summary["summary_text"] = (
                        f"公司业绩预告（{rec.get('type', '')}）：净利润 "
                        f"{guidance['profit_min_100m']}–{guidance['profit_max_100m']} 亿元"
                    )
            elif p_min is not None and p_max is not None:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：同比 {p_min}%–{p_max}%"
                )
            elif p_min is not None:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：同比变动约 {p_min}% 起"
                )
            else:
                summary["summary_text"] = (
                    f"公司业绩预告（{rec.get('type', '')}）：变动区间数据不足"
                )
        return summary

    # Tier 3: akshare
    if akshare_rc:
        summary["source"] = "akshare.research"
        summary["status"] = "ok_limited"
        n_records = len(akshare_rc)
        summary["summary_text"] = f"东方财富 {n_records} 条研报记录（无结构化评级摘要）"
        summary["raw_records"] = akshare_rc[:10]
        return summary

    # 全部失败
    summary["status"] = "no_data"
    summary["summary_text"] = "未获取到机构研报/评级数据"
    return summary


def collect_research(symbol: str) -> dict:
    """采集机构研报、评级与盈利预测数据。

    三层降级策略（按 Tushare 积分体系）：
      1️⃣ Tushare report_rc（10000+积分/特色大数据）→ 含评级+目标价+盈利预测
      2️⃣ Tushare forecast（2000+积分）→ 仅公司业绩预告
      3️⃣ akshare 东方财富个股研报（免注册，可能被代理阻断）
      4️⃣ 全部失败 → 标注不可得

    Returns:
        dict: 标准 DimensionResult 格式
    """
    results: list[SourceResult] = []
    dim_val = "research"
    ts = _ts_code(symbol)

    # Tier 1 → 2 → 3 顺序降级；高阶成功则跳过低阶 API 调用
    rc_data: list[dict] | None = None
    try:
        rc_data = _q_tushare_report_rc(symbol)
    except RuntimeError:
        pass
    except Exception as exc:
        logger.warning("collect_research/report_rc: %s", exc)
    if rc_data:
        results.append(SourceResult(
            source="tushare.report_rc",
            data=rc_data,
            dimension=dim_val,
            query_params=f"pro.report_rc(ts_code='{ts}', start_date='{_days_ago(180)}')",
        ))
    else:
        results.append(SourceResult(
            source="tushare.report_rc",
            data=None,
            dimension=dim_val,
            error="权限不足或无数据返回（需 Tushare 10000+积分）",
            query_params=f"pro.report_rc(ts_code='{ts}')",
        ))

    fc_data: list[dict] | None = None
    if not rc_data:
        try:
            fc_data = _q_tushare_forecast(symbol)
        except RuntimeError:
            pass
        except Exception as exc:
            logger.warning("collect_research/forecast: %s", exc)
        if fc_data:
            results.append(SourceResult(
                source="tushare.forecast",
                data=fc_data,
                dimension=dim_val,
                query_params=f"pro.forecast(ts_code='{ts}')",
            ))
        else:
            results.append(SourceResult(
                source="tushare.forecast",
                data=None,
                dimension=dim_val,
                error="权限不足或无数据（需 Tushare 2000+积分）",
                query_params=f"pro.forecast(ts_code='{ts}')",
            ))

    ak_data: list[dict] | None = None
    if not rc_data and not fc_data:
        try:
            ak_data = _q_akshare_research(symbol)
        except Exception as exc:
            logger.warning("collect_research/akshare: %s", exc)
        if ak_data:
            results.append(SourceResult(
                source="akshare.research",
                data=ak_data,
                dimension=dim_val,
                query_params=f"ak.stock_research_report_em(symbol='{symbol}')",
            ))
        else:
            results.append(SourceResult(
                source="akshare.research",
                data=None,
                dimension=dim_val,
                error="东方财富 push2 不可达或接口异常",
                query_params=f"ak.stock_research_report_em(symbol='{symbol}')",
            ))

    # 汇总
    tushare_rc = next((r.data for r in results if r.source == "tushare.report_rc" and r.success), None)
    tushare_fc = next((r.data for r in results if r.source == "tushare.forecast" and r.success), None)
    akshare_rc = next((r.data for r in results if r.source == "akshare.research" and r.success), None)
    summary = _summarize_research(tushare_rc, tushare_fc, akshare_rc)

    dim = DimensionResult("research", results)
    dim_dict = dim.to_legacy_dict()
    dim_dict["research_summary"] = summary
    return dim_dict


def collect_industry(symbol: str) -> dict:
    """行业级数据采集：行业指数、行业PE、行业资金流向。

    依赖 akshare 东方财富接口。akshare 不可用时返回 missing。
    """
    dim_val = "industry"
    tasks: list[tuple[str, Callable]] = []

    industry_name = ""
    if env.is_akshare_available() and akshare_push2_available():
        info = _q_akshare_basic(symbol)
        if info:
            industry_name = info.get("行业") or info.get("industry", "") or ""

    if env.is_akshare_available() and akshare_push2_available():
        ind = industry_name
        tasks.append(("akshare.stock_board_industry_hist_em",
                      lambda i=ind: _q_akshare_industry_board(symbol, industry_name=i)))
        tasks.append(("akshare.stock_board_industry_pe_ratio_cninfo",
                      lambda i=ind: _q_akshare_industry_pe(symbol, industry_name=i)))

    empty = {
        "dimension": dim_val,
        "display": "行业数据",
        "data": None,
        "status": "missing",
        "error": "无可用行业数据源（需 akshare + 东方财富 push2 可用）",
        "_meta": {"source": "none", "success": False,
                  "all_sources": [], "multi_source": False,
                  "source_count": 0},
    }

    def _merge_industry(dim_dict: dict, results: list) -> dict:
        merged: dict = {}
        sources_ok: list[str] = []
        for r in results:
            if r.data and isinstance(r.data, dict):
                merged.update(r.data)
                sources_ok.append(r.source)
        if merged:
            dim_dict["data"] = merged
            if len(sources_ok) > 1:
                meta = dim_dict.setdefault("_meta", {})
                meta["source"] = "merged:" + "+".join(sources_ok)
                meta["merged_sources"] = sources_ok
                meta["multi_source"] = True
        return dim_dict

    return _collect_dimension(
        dim_val, tasks, empty_result=empty, postprocess=_merge_industry,
        # 注：本维度两个任务为互补数据（板块行情 + 行业 PE），非冗余备份——保持并行
    )


# ---- 股东增减持采集（P0-2 holder_changes） ----

_HOLDER_SOURCE_RANK: dict[str, int] = {
    "tushare.stk_holdertrade": 0,
    "akshare.stock_hold_management_detail_cninfo": 1,
    "akshare.stock_shareholder_change_ths": 2,
}


def _first_present(row: dict, *keys: str):
    """取 dict 中第一个非 None 原始值（保留 0；不做数值转换）。"""
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _holder_avg_price(row: dict, *keys: str):
    """成交均价：数值优先于列顺序；无法数值化时回退原文。

    - ``coalesce_field`` 跳过非纯数字串，可能落到靠后的 key
      （例：成交均价=\"12.50元\" + 交易均价=9.8 → 9.8）
    - 全部无法数值化时回退 ``_first_present`` 原文（如单独的「12.50元」）
    """
    raw = _first_present(row, *keys)
    num = coalesce_field(row, *keys)
    return num if num is not None else raw


def _infer_holder_direction(
    row: dict,
    change_vol_raw,
    parsed_vol: float | None = None,
) -> str:
    """推断增减持方向：变动类型列 > 文本关键词 > 数量符号。"""
    for key in ("变动类型", "方向", "变动方向"):
        typ = str(row.get(key) or "")
        if "增持" in typ:
            return "增持"
        if "减持" in typ:
            return "减持"
    raw_s = str(change_vol_raw or "")
    if "增持" in raw_s:
        return "增持"
    if "减持" in raw_s:
        return "减持"
    vol = parsed_vol if parsed_vol is not None else _parse_holder_change_vol(change_vol_raw)
    if vol is not None:
        if vol > 0:
            return "增持"
        if vol < 0:
            return "减持"
    return "未知"


def _source_has_data(data) -> bool:
    """源结果是否含有效数据（空列表不算）——统一口径在 lib.data_util.has_data。"""
    from lib.data_util import has_data

    return has_data(data)


def _build_summary(dimensions: list) -> dict:
    """维度汇总：与 merge_collections 共用 has_data 口径（空 list/dict 不计
    available）。missing 用 not _source_has_data（而非 is None）：status='available'
    但 data=[]（非交易日 quote）此前不计入任何计数器 → available+partial+missing
    < total，all_partial 失真，invest.py 的 available==0 中止误触发。
    sources_responded：status ∈ {available, partial} 的维度数——"数据源有响应"
    语义；响应的源返回空数据（节假日）不算失败，报告照常渲染无数据区块。
    """
    has_data = sum(
        1 for d in dimensions
        if d and _source_has_data(d.get("data"))
        and d.get("status") in ("available", "partial")
    )
    partial = sum(1 for d in dimensions if d and d.get("status") == "partial")
    missing = sum(
        1 for d in dimensions
        if d and not _source_has_data(d.get("data")) and d.get("status") != "partial"
    )
    sources_responded = sum(
        1 for d in dimensions if d and d.get("status") in ("available", "partial")
    )
    # 全部"有数据"维度均为 partial 才算 all_partial（partial==has_data 在
    # 存在无数据的 partial 维度时会误判为 True）
    all_partial = (
        has_data > 0
        and missing == 0
        and all(
            d.get("status") == "partial"
            for d in dimensions if d and _source_has_data(d.get("data"))
        )
    )
    return {
        "total": len(dimensions),
        "available": has_data,
        "degraded": partial,
        "missing": missing,
        "all_partial": all_partial,
        "sources_responded": sources_responded,
    }


def _parse_holder_change_vol(raw) -> float | None:
    """解析 '增持58.11万' / 5901992.0 → 统一为股数（float）。"""
    import re
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        f = float(raw)
        if f != f:  # NaN
            return None
        return f
    s = str(raw).strip().replace(",", "")
    m = re.search(r"(-?[\d.]+)\s*(万|亿)?", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2) or ""
    if unit == "万":
        val *= ONE_PER_WAN
    elif unit == "亿":
        val *= ONE_PER_YI
    return val


def _holder_vol_key(raw) -> str:
    """变动数量归一化 key（万股精度，供跨源匹配）。"""
    v = _parse_holder_change_vol(raw)
    if v is None:
        return str(raw or "")
    return f"{round(v / ONE_PER_WAN, 2)}"


def _normalize_holder_name(name: str) -> str:
    """股东名称归一化（仅用于 transaction key，不修改展示字段）。"""
    s = str(name or "").strip()
    for suffix in ("股份有限公司", "有限公司"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    return s


def _holder_transaction_key(r: dict) -> tuple:
    return (
        _normalize_holder_name(str(r.get("holder_name", ""))),
        str(r.get("ann_date", ""))[:10],
        str(r.get("direction", "")),
        _holder_vol_key(r.get("change_vol")),
    )


def _norm_date(raw: str) -> str:
    """日期归一化：尝试 YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD → YYYYMMDD。

    委托 lib.financials.normalize_end_date 处理；无法解析时先尝试中文
    「X年X月X日」格式（cninfo 公告日期），再回退保留原始串（截断至 10
    字符），避免不同日期的记录塌缩到同一空 key（跨源误折叠）。
    """
    import re
    from lib.financials import normalize_end_date
    norm = normalize_end_date(raw)
    if norm:
        return norm
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(raw))
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return str(raw).strip()[:10]


def _q_tushare_holdertrade(symbol: str) -> list[dict] | None:
    """Tushare stk_holdertrade — 股东增减持（主源）。"""
    config, tc = _require_tushare()
    ts = _ts_code(symbol)
    df = tc.query(
        "stk_holdertrade", ts_code=ts,
        start_date=_days_ago(730), end_date=_today(),
        fields="ts_code,ann_date,holder_name,holder_type,in_de,"
               "change_vol,change_ratio,avg_price,after_share,after_ratio",
    )
    if df is None or df.empty:
        return None
    records = df.to_dict("records")
    for rec in records:
        # 三态：in_de 缺失/NaN 标"未知"（None/NaN 恒不等于字符串，默认"减持"会把缺失误判为看空信号）
        in_de = rec.get("in_de")
        rec["direction"] = "增持" if in_de == "IN" else ("减持" if in_de == "DE" else "未知")
        rec["source"] = "Tushare stk_holdertrade"
    return records


def _run_with_timeout(fn: Callable[[], Any], timeout_sec: float, label: str) -> Any:
    """在 daemon 线程中执行阻塞调用，超时/异常返回 None（不 join 挂起线程）。

    用于 cninfo 全市场扫描等慢接口：超时后立即返回，挂起线程在解释器
    退出时被杀（边界由 env.configure_socket_timeout 兜底）。
    """
    data, err = _run_in_thread(fn, timeout_sec, label)
    if err is not None:
        if isinstance(err, TimeoutError):
            logger.warning("%s timed out after %.0fs, skipping", label, timeout_sec)
        else:
            logger.warning("%s failed: %s", label, err)
        return None
    return data


# run 级缓存：cninfo 高管增减持是全市场接口（每次「增持/减持」各
# ~9500/17300 行、单方向超时 45s），此前每符号重取 —— N 标的
# watchlist/compare/portfolio 最多 ~7.5 分钟。缓存原始 DataFrame 按方向
# 存一份，同 run（同日）内所有 symbol 复用；跨自然日自动失效重建。
_cninfo_hold_cache: dict[str, object] = {}  # {direction: df|None}
_cninfo_hold_cache_day: str = ""            # 缓存所属日期 YYYY-MM-DD


def _cninfo_hold_cache_today() -> str:
    # 上海时区（_today = shanghai_today，全模块统一口径）——date.today() 是
    # 本机时区，非 UTC+8 主机上缓存键错位会跨日误复用（review #14 第二轮）
    return _today()


def _q_akshare_management_hold(symbol: str) -> list[dict] | None:
    """akshare stock_hold_management_detail_cninfo — 高管增减持（副源）。

    该接口按「增持/减持」分类返回全市场数据（~9500+17300 行），
    需按 symbol 过滤。⚠️ 内部使用 JS 引擎，不可在并行采集线程池中执行。
    单次请求超时见 env.CNINFO_HOLDER_TIMEOUT_SEC，超时则跳过该方向。
    """
    if not env.is_akshare_available():
        return None
    import akshare as ak

    global _cninfo_hold_cache_day
    # 按日失效：跨日清空重建，同 run（同日）内只取一次全市场数据
    day = _cninfo_hold_cache_today()
    if _cninfo_hold_cache_day != day:
        _cninfo_hold_cache.clear()
        _cninfo_hold_cache_day = day

    sym = symbol.strip().zfill(6)
    records: list[dict] = []
    timeout_sec = float(env.CNINFO_HOLDER_TIMEOUT_SEC)

    # 仅拉取未缓存的方向（缓存命中不再请求网络，也不再进入东财限流闸口）
    missing = [d for d in ("增持", "减持") if d not in _cninfo_hold_cache]
    if missing:
        with akshare_direct_session():
            for direction in missing:
                df = _run_with_timeout(
                    lambda d=direction: ak.stock_hold_management_detail_cninfo(symbol=d),
                    timeout_sec,
                    f"akshare cninfo({direction})",
                )
                if df is None:
                    # 超时/异常不落缓存——否则整个 run 其余 symbol 都复用
                    # None（该方向数据全缺失且不重试，review #14 第二轮）
                    logger.warning(
                        "akshare cninfo(%s) 超时/失败，本 symbol 跳过，"
                        "后续 symbol 将独立重试", direction,
                    )
                    continue
                _cninfo_hold_cache[direction] = df

    for direction in ("增持", "减持"):
        df = _cninfo_hold_cache.get(direction)
        if df is None or getattr(df, "empty", True):
            continue
        # 过滤当前标的
        code_col = "证券代码" if "证券代码" in df.columns else (
            "股票代码" if "股票代码" in df.columns else None)
        if code_col is None:
            logger.warning(
                "akshare cninfo(%s): missing symbol column (%s), skip",
                direction, list(df.columns),
            )
            continue
        df = df[df[code_col].astype(str).str.contains(sym, na=False)]
        if df.empty:
            continue
        for row in df.to_dict("records"):
            change_vol_raw = _first_present(row, "变动数量", "变动股数")
            parsed_vol = _parse_holder_change_vol(change_vol_raw)
            records.append({
                "ann_date": _norm_date(
                    row.get("公告日期") or row.get("变动日期") or ""),
                "holder_name": row.get("董监高姓名") or row.get("高管姓名") or row.get("变动人") or "",
                "position": row.get("董监高职务") or row.get("职务") or "",
                "direction": direction,
                "change_vol": parsed_vol if parsed_vol is not None else change_vol_raw,
                "change_vol_raw": change_vol_raw,
                "avg_price": _holder_avg_price(row, "成交均价", "交易均价"),
                "reason": row.get("持股变动原因") or row.get("变动原因") or "",
                "source": "akshare cninfo",
            })
    return records or None


def _q_akshare_shareholder_change_ths(symbol: str) -> list[dict] | None:
    """akshare stock_shareholder_change_ths — 同花顺股东变动（降级备选）。"""
    if not env.is_akshare_available():
        return None
    import akshare as ak
    with _proxy_bypass():
        df = ak.stock_shareholder_change_ths(symbol=symbol.strip().zfill(6))
    if df is None or df.empty:
        return None
    records = []
    for row in df.to_dict("records"):
        change_vol_raw = row.get("变动数量")
        parsed_vol = _parse_holder_change_vol(change_vol_raw)
        direction = _infer_holder_direction(row, change_vol_raw, parsed_vol)
        records.append({
            "ann_date": _norm_date(str(row.get("公告日期") or row.get("变动期间") or "")),
            "holder_name": row.get("变动股东") or "",
            "change_vol": parsed_vol if parsed_vol is not None else change_vol_raw,
            "change_vol_raw": change_vol_raw,
            "avg_price": _holder_avg_price(row, "交易均价"),
            "remain_vol": row.get("剩余股份总数"),
            "direction": direction,
            "source": "akshare ths",
        })
    return records or None


def _merge_holder_records(results: list) -> list[dict]:
    """合并多源增减持记录，按 transaction_key 分组并标注 cross_check。

    同源同日多笔交易（不同变动数量）全部保留；仅跨源命中同一笔时折叠为 1 条。
    去重优先级：Tushare > akshare cninfo > akshare ths。
    """
    pending: list[dict] = []
    for sr in results:
        if not sr.success or sr.data is None:
            continue
        records = sr.data if isinstance(sr.data, list) else [sr.data]
        for r in records:
            if not isinstance(r, dict):
                continue
            rec = dict(r)
            rec["_source_api_key"] = sr.source
            rec["_source_rank"] = _HOLDER_SOURCE_RANK.get(sr.source, 99)
            pending.append(rec)

    if not pending:
        return []

    groups: dict[tuple, list[dict]] = {}
    for r in pending:
        groups.setdefault(_holder_transaction_key(r), []).append(r)

    merged: list[dict] = []
    for group in groups.values():
        sources = {r["_source_api_key"] for r in group}
        if len(sources) > 1:
            group.sort(key=lambda r: r.get("_source_rank", 99))
            best = group[0]
            best["cross_check"] = len(sources)
            best.pop("_source_api_key", None)
            best.pop("_source_rank", None)
            merged.append(best)
        else:
            for r in group:
                r["cross_check"] = 1
                r.pop("_source_api_key", None)
                r.pop("_source_rank", None)
                merged.append(r)

    merged.sort(key=lambda r: str(r.get("ann_date", "")), reverse=True)
    return merged


def _needs_cninfo_holder_fallback(results: list) -> bool:
    """并行源（Tushare + ths）均有数据时跳过慢速 cninfo；否则顺序补采。"""
    has_tushare = any(
        r.source == "tushare.stk_holdertrade" and _source_has_data(r.data)
        for r in results
    )
    has_ths = any(
        r.source == "akshare.stock_shareholder_change_ths" and _source_has_data(r.data)
        for r in results
    )
    return not (has_tushare and has_ths)


def collect_holder_changes(symbol: str) -> dict:
    """股东增减持动向 — 三源并行 + 交叉验证（与 collect_financials 同构）。

    cninfo 源因内部使用 JS 引擎（mini_racer），不在线程池中执行。
    当 Tushare 与 ths 并行源均有数据时跳过 cninfo；否则顺序补采高管维度数据。
    """
    # 线程安全的源（Tushare + ths）
    tasks: list[tuple[str, Callable]] = []
    if env.is_tushare_available(env.get_config()):
        tasks.append(("tushare.stk_holdertrade", lambda: _q_tushare_holdertrade(symbol)))
    if env.is_akshare_available():
        tasks.append(("akshare.stock_shareholder_change_ths",
                      lambda: _q_akshare_shareholder_change_ths(symbol)))

    # 注：tushare/ths 两源数据合并去重（互补），cninfo 兜底决策依赖双源数据——
    # 保持并行（R12h 单源化仅适用于冗余同义源，此处不适用）
    results = _run_sources_parallel(tasks, "holder_changes")

    # cninfo 源（不可在线程中运行 — 内部使用 JS 引擎，且全市场数据极慢）
    if env.is_akshare_available() and _needs_cninfo_holder_fallback(results):
        results.append(_run_one_source(
            "akshare.stock_hold_management_detail_cninfo",
            lambda: _q_akshare_management_hold(symbol),
            "holder_changes",
        ))

    result_map = {r.source: r for r in results}
    _annotate_query_params(result_map, {
        "tushare.stk_holdertrade": _qp_tushare("stk_holdertrade", symbol,
                                               start_date=_days_ago(730), end_date=_today()),
        "akshare.stock_hold_management_detail_cninfo": _qp_akshare(
            "stock_hold_management_detail_cninfo", symbol),
        "akshare.stock_shareholder_change_ths": _qp_akshare(
            "stock_shareholder_change_ths", symbol),
    })

    dim = DimensionResult("holder_changes", results)
    legacy = dim.to_legacy_dict()
    # 合并多源记录做去重 + 交叉验证标注
    merged = _merge_holder_records(results)
    legacy["data"] = merged if merged else legacy.get("data")
    return legacy


# ---- 行业定价采集（P1-2 + P1-3 industry_pricing） ----

def _calc_futures_trend_from_spot(spot_old, spot_new, code: str, code_col: str) -> str:
    """对比两日期货现货价，计算近 30 日趋势（2 次 API，不按品种循环）。"""
    if spot_old is None or spot_old.empty or spot_new is None or spot_new.empty:
        return "数据不足"
    row_old = spot_old[spot_old[code_col] == code]
    row_new = spot_new[spot_new[code_col] == code]
    if row_old.empty or row_new.empty:
        return "数据不足"
    try:
        old_p = safe_float(row_old.iloc[-1].get("spot_price") or row_old.iloc[-1].get("sp"))
        new_p = safe_float(row_new.iloc[-1].get("spot_price") or row_new.iloc[-1].get("sp"))
        if old_p is None or new_p is None or old_p == 0:
            return "数据不足"
        pct = (new_p - old_p) / abs(old_p) * 100
        arrow = "↗" if pct > 0 else "↘" if pct < 0 else "→"
        return f"{arrow} {pct:+.1f}%"
    except (TypeError, ValueError, IndexError):
        return "数据不足"


def _q_akshare_futures_spot(symbol: str, industry: str) -> dict | None:
    """期货现货价格 + 近月合约价格 + 基差率。"""
    from ..chain import get_futures_for_industry

    futures_list = get_futures_for_industry(industry)
    if not futures_list:
        return None

    if not env.is_akshare_available():
        return None
    import akshare as ak

    codes = [code for _, code in futures_list]
    results = {}
    # 尝试昨天 → 前天（期货数据通常 T+1 更新，跳过今天）
    spot = None
    for day_offset in (1, 2, 3):
        try:
            spot = ak.futures_spot_price(date=_days_ago(day_offset), vars_list=codes)
            if spot is not None and not spot.empty:
                break
        except Exception:
            continue

    if spot is None or spot.empty:
        return None

    spot_old = None
    try:
        spot_old = ak.futures_spot_price(date=_days_ago(30), vars_list=codes)
    except Exception:
        pass

    code_col = "symbol" if "symbol" in spot.columns else "var"
    for name, code in futures_list:
        row = spot[spot[code_col] == code]
        if row.empty:
            results[name] = {"code": code, "error": "无数据"}
            continue
        r = row.iloc[-1]
        trend = _calc_futures_trend_from_spot(spot_old, spot, code, code_col)
        results[name] = {
            "code": code,
            "spot_price": r.get("spot_price") or r.get("sp"),
            "near_month_price": r.get("near_contract_price") or r.get("near_price"),
            "dom_price": r.get("dominant_contract_price") or r.get("dom_price"),
            "near_basis_rate": r.get("near_basis_rate"),
            "dom_basis_rate": r.get("dom_basis_rate"),
            "trend_30d": trend,
        }

    return results or None


def _q_akshare_company_news_price(symbol: str) -> dict | None:
    """公司新闻涨价信号检测。正则匹配: 涨价|提价|上调|调价|价格上涨。"""
    import re
    from datetime import datetime, timedelta

    if not env.is_akshare_available():
        return None
    import akshare as ak
    with akshare_direct_session():
        df = ak.stock_news_em(symbol=symbol.strip().zfill(6))
    if df is None or df.empty:
        return None

    pattern = re.compile(r"涨价|提价|上调|调价|价格上涨")
    cutoff = datetime.now() - timedelta(days=30)
    matches = []
    for row in df.to_dict("records"):
        title = str(row.get("新闻标题", ""))
        content = str(row.get("新闻内容", ""))
        if not (pattern.search(title) or pattern.search(content)):
            continue
        date_raw = str(row.get("发布时间", ""))
        if not _news_date_within(date_raw, cutoff):
            continue
        matches.append({
            "date": date_raw,
            "title": title,
            "content_snippet": content[:200],
        })

    signal = "确认" if len(matches) >= 2 else ("单条" if len(matches) == 1 else "无")
    return {
        "matches": matches,
        "signal": signal,
        "signal_detail": f"近 30 日 {len(matches)} 条涨价相关新闻",
    }


def _parse_news_datetime(date_raw: str):
    """解析 akshare stock_news_em 发布时间。"""
    from datetime import datetime

    raw = str(date_raw).strip()
    if not raw:
        return None
    for fmt, maxlen in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
    ):
        try:
            return datetime.strptime(raw[:maxlen], fmt)
        except ValueError:
            continue
    return None


def _news_date_within(date_raw: str, cutoff) -> bool:
    """判断新闻是否在 cutoff 之后（近 30 日窗口）。"""
    dt = _parse_news_datetime(date_raw)
    return dt is not None and dt >= cutoff


def _resolve_industry_for_pricing(
    symbol: str,
    dim_results: dict[str, dict] | None = None,
) -> str:
    """解析行业名（优先已采集 basic_info，否则补采）。"""
    if dim_results:
        basic = dim_results.get("basic_info")
        if basic:
            industry = extract_industry_from_basic_info(basic.get("data"))
            if industry:
                return industry
    try:
        basic = collect_basic_info(symbol)
        return extract_industry_from_basic_info(basic.get("data")) or ""
    except Exception as exc:
        logger.warning(
            "industry_pricing: industry lookup failed for %s: %s", symbol, exc,
        )
        return ""


def collect_industry_pricing_dim(symbol: str) -> dict:
    """COLLECTORS 入口：采集前解析行业（期货映射依赖 industry）。"""
    return collect_industry_pricing(
        symbol, _resolve_industry_for_pricing(symbol),
    )


def collect_industry_pricing(symbol: str, industry: str = "") -> dict:
    """行业产品定价追踪（与 collect_financials 同构）。"""
    from ..chain import get_futures_for_industry

    tasks: list[tuple[str, Callable]] = []
    if env.is_akshare_available():
        tasks.append(
            ("akshare.futures_spot_price",
             lambda: _q_akshare_futures_spot(symbol, industry)),
        )
        tasks.append(
            ("akshare.stock_news_em",
             lambda: _q_akshare_company_news_price(symbol)),
        )

    def _inject_meta(legacy: dict, _results: list) -> dict:
        legacy.setdefault("data", {})
        if isinstance(legacy["data"], dict):
            legacy["data"]["industry"] = industry
            legacy["data"]["has_futures"] = bool(get_futures_for_industry(industry))
        return legacy

    return _collect_dimension(
        "industry_pricing", tasks, postprocess=_inject_meta,
    )


# ---- 全维度采集 ----

COLLECTORS = {
    "basic_info": ("基本信息", collect_basic_info),
    "financials": ("财务报告", collect_financials),
    "quote": ("实时行情", collect_quote),
    "shareholders": ("十大股东", collect_shareholders),
    "northbound": ("北向资金", collect_northbound),
    "kline": ("日K线", collect_kline),
    "valuation": ("估值分析", collect_valuation),
    "research": ("机构研报", collect_research),
    "industry": ("行业数据", collect_industry),  # R-11: NEW
    "holder_changes": ("股东增减持", collect_holder_changes),  # P0-2
    "industry_pricing": ("行业定价", collect_industry_pricing_dim),  # P1-2
}

_DEFAULT_DIMS = ["basic_info", "financials", "quote", "shareholders",
                 "northbound", "valuation", "kline", "holder_changes"]


# ---- E1 板块同步性引擎（v0.2.7） ----

_SECTOR_SYNC_MODULE = "lib_sector_sync"
# 与 skills/lib/sector_sync.SECTOR_SYNC_FIELDS 同步（仅作模块加载失败时的
# 字面 fallback；正常路径骨架 fields 取 mod.SECTOR_SYNC_FIELDS）
_SECTOR_SYNC_FIELD_NAMES = (
    "sector_beta_60d",
    "sector_r2_60d",
    "idio_var_share",
    "sector_dispersion",
    "csad_gamma2",
    "downside_corr_gap",
)


def _sector_sync_skeleton(symbol: str, *, reason: str,
                          industry: str | None = None,
                          n_constituents: int = 0,
                          n_constituents_with_kline: int = 0,
                          error: str = "") -> dict:
    """板块同步性不可得骨架（统一 13 键 schema，fields 全 None）。

    akshare 不可用 / 冷缓存跳过 / probe 无锚定 / collect_all 异常兜底四条
    路径共用同一形状——存储/渲染消费者按统一 schema 读取不 KeyError。
    fields 键名取已加载模块的 SECTOR_SYNC_FIELDS（模块加载失败时退回内联
    副本，即 _SECTOR_SYNC_FIELD_NAMES）。``error`` 仅异常兜底路径使用
    （额外键，供排障）。
    """
    mod = sys.modules.get(_SECTOR_SYNC_MODULE)
    field_names = getattr(mod, "SECTOR_SYNC_FIELDS", None) or _SECTOR_SYNC_FIELD_NAMES
    out = {
        "symbol": symbol,
        "available": False,
        "provider": None,
        "industry": industry,
        "index_code": None,
        "n_constituents": n_constituents,
        "n_constituents_with_kline": n_constituents_with_kline,
        "window_days": 0,
        "window_start": None,
        "window_end": None,
        "fields": {f: None for f in field_names},
        "meta": {},
        "reasons": {"_all": reason},
    }
    if error:
        out["error"] = error
    return out


def _load_sector_sync_module():
    """加载 skills/lib/sector_sync.py（显式路径 + 固定模块名）。

    scripts/lib 无 sector_sync shim（sector_sync 为 v0.2.7 新增，落 skills/lib），
    直接 ``from lib.sector_sync import ...`` 在 scripts/lib 包内会 ImportError——
    仿 invest_path.load_invest_a_etf_module：按文件路径加载并注册固定模块名，
    测试按同名 patch（D13：patch 目标 = 定义模块命名空间）。
    """
    mod = sys.modules.get(_SECTOR_SYNC_MODULE)
    if mod is not None:
        return mod
    try:
        from lib._invest_path import ensure_skills_lib_on_path  # scripts/lib shim
    except ImportError:  # pragma: no cover — skills/lib 独立上下文
        from invest_path import ensure_shared_lib_on_path as ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from invest_path import invest_a_scripts_dir
    import importlib.util as _ilu

    skills_lib = Path(invest_a_scripts_dir()).parent.parent / "lib"
    spec = _ilu.spec_from_file_location(_SECTOR_SYNC_MODULE, skills_lib / "sector_sync.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load skills/lib/sector_sync.py from {skills_lib}")
    mod = _ilu.module_from_spec(spec)
    sys.modules[_SECTOR_SYNC_MODULE] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # F5：exec 失败会留下残破的部分模块，被 sys.modules 永久缓存——后续
        # 每次 collect_all 都短路返回它（AttributeError 恒现）。清理后重抛。
        sys.modules.pop(_SECTOR_SYNC_MODULE, None)
        raise
    return mod


def _attach_sector_sync(collection: dict, symbol: str, dim_results: dict,
                        *, force: bool = False) -> None:
    """E1：板块同步性 6 字段 — 计算并写入 collection['sector_sync'] + kline derived。

    - industry_hint 取自 basic_info 的「行业」字段（缺失 → sector_sync 内
      fail loud「行业分类缺失」，不静默）。
    - kline 已采集数据直接复用（不再二次抓取个股日线）。
    - F1 冷缓存门控：成分股日线逐只抓取 5-10 分钟，默认采集不得被阻塞——
      锚定板块成分股缓存缺口 ≤ 预算才实际计算，否则标注「缓存未预热」跳过；
      force=True（CLI --force-sector-sync）绕过门控强制计算（首次预热路径）。
    - probe → compute 锚定共享：probe 解析出的锚定板块经 anchor_override 直接
      复用，compute 不二次解析（数据源可达性在两次解析间翻转时不再退化为
      全量冷抓）；probe 无锚定（全解析失败/行业分类缺失）→ 跳过 compute
      fail-fast，下一次采集自然重试。
    - F4 derived 合并：仅 available=True 时并入——部分失败时 1-5 个有效字段
      保留在 collection['sector_sync']，kline.derived 不写入，两视图对同一
      快照的解读一致（部分数据可区分于「无数据」）。
    """
    basic = dim_results.get("basic_info") or {}
    basic_data = basic.get("data") if isinstance(basic, dict) else None
    industry_hint = ""
    if isinstance(basic_data, dict):
        industry_hint = str(basic_data.get("行业") or basic_data.get("industry") or "")
    kline_dim = dim_results.get("kline") or {}
    kline_bars = kline_dim.get("data") if isinstance(kline_dim, dict) else None
    if not isinstance(kline_bars, list):
        kline_bars = None

    # 先加载模块（exec 不依赖 akshare——akshare 为函数内懒导入）：骨架 fields
    # 取模块常量，无 akshare 环境同样得到 13 键统一 schema（不含内联副本漂移）
    try:
        mod = _load_sector_sync_module()
    except Exception as exc:
        logger.warning("sector_sync module load failed for %s: %s", symbol, exc)
        collection["sector_sync"] = _sector_sync_skeleton(
            symbol, reason=f"sector_sync 模块加载失败: {exc}", error=str(exc))
        return

    # sector_sync 全部数据源均经 akshare（东财 BK / 申万 / sina）——无 akshare
    # 环境直接标注不可得（fail loud），避免空转与无谓网络尝试。
    if not env.is_akshare_available():
        collection["sector_sync"] = _sector_sync_skeleton(
            symbol, reason="akshare 数据源不可用，板块同步性不可得")
        return

    # F1 冷缓存门控：成分股日线全量抓取 5-10 分钟，默认采集跳过冷缓存计算。
    if not force:
        probe = mod.probe_sector_cache_warmth(industry_hint)
        anchor = probe.get("anchor")
        if not probe.get("warm"):
            collection["sector_sync"] = _sector_sync_skeleton(
                symbol, reason=probe.get("reason") or "板块同步性缓存未预热",
                industry=industry_hint or None,
                n_constituents=int(probe.get("total", 0)),
                n_constituents_with_kline=int(probe.get("valid", 0)))
            return
        # probe 无锚定（全解析失败 / 行业分类缺失）：fail-fast 跳过 compute——
        # 不给二次解析全量冷抓的机会（probe 与 compute 之间数据源可达性翻转
        # 时，compute 会现场抓取 185 只成分股）；下一次采集自然重试
        if not anchor:
            collection["sector_sync"] = _sector_sync_skeleton(
                symbol, reason=probe.get("reason") or "板块指数不可得",
                industry=industry_hint or None,
                n_constituents=int(probe.get("total", 0)))
            return
        ss = mod.compute_sector_sync(
            symbol, industry_hint=industry_hint, stock_kline=kline_bars,
            anchor_override=anchor)
    else:
        ss = mod.compute_sector_sync(
            symbol, industry_hint=industry_hint, stock_kline=kline_bars)
    collection["sector_sync"] = ss

    # F4：字段并入 kline derived 以 available=True 为门槛（None 不写入）
    if ss.get("available") and isinstance(kline_dim, dict):
        derived = dict(kline_dim.get("derived") or {})
        merged = False
        for f in mod.SECTOR_SYNC_FIELDS:
            v = (ss.get("fields") or {}).get(f)
            if v is not None:
                derived[f] = v
                merged = True
        if merged:
            kline_dim["derived"] = derived


def _dimension_missing_skeleton(dim: str, exc: Exception, *,
                                display: str | None = None) -> dict:
    """维度采集失败骨架（status=missing 统一形状，review #8）。

    fanout 失败维度与 _collect_industry_pricing_block 共用；display 缺省取
    COLLECTORS[dim][0]，未知维度回退 dim 自身。
    """
    return {
        "dimension": dim,
        "display": display if display is not None
        else (COLLECTORS[dim][0] if dim in COLLECTORS else dim),
        "data": None,
        "status": "missing",
        "error": f"维度采集失败: {exc}",
        "_meta": {"source": "none", "success": False,
                  "all_sources": [], "multi_source": False,
                  "source_count": 0, "error": str(exc)},
    }


def _collect_dims_fanout(symbol: str, dims: list[str], kline_kwargs: dict) -> dict[str, dict]:
    """跨维度并行扇出。失败维度写 status=missing 骨架。

    收敛共享 _base._map_parallel fan-out 样板（review #7）：worker 公式
    max(1, min(n, _env_max_workers())) 尊重 INVEST_MAX_WORKERS 且空任务
    提前返回（dims=[] 不再 max_workers=0 崩溃，review #2）。
    """
    tasks: list[tuple[str, Callable[[], Any]]] = []
    for dim in dims:
        if dim not in COLLECTORS:
            logger.warning("忽略未知维度 '%s'（有效维度: %s）", dim, list(COLLECTORS.keys()))
            continue
        if dim == "industry_pricing":
            # industry_pricing 依赖 industry 解析结果，在并行扇出后单独采集
            continue
        _, fn = COLLECTORS[dim]
        if dim == "kline" and kline_kwargs:
            # lambda 默认参数绑定，防循环变量晚绑定（fn/kw 逐维不同）
            tasks.append((dim, lambda fn=fn, kw=kline_kwargs: fn(symbol, **kw)))
        else:
            tasks.append((dim, lambda fn=fn: fn(symbol)))

    dim_results: dict[str, dict] = {}
    dim_start = {dim: time.time() for dim, _ in tasks}

    def _on_error(item: tuple[str, Callable], exc: Exception) -> None:
        dim_results[item[0]] = _dimension_missing_skeleton(item[0], exc)

    for (dim, _fn), value in _map_parallel(tasks, lambda item: item[1](),
                                           on_error=_on_error):
        if value is not None:
            dim_results[dim] = value
            logger.info("dimension=%s done in %.1fs", dim,
                        time.time() - dim_start[dim])
    return dim_results


def _collect_industry_pricing_block(symbol: str, dims: list[str],
                                    dim_results: dict) -> dict | None:
    """industry_pricing 依赖 industry 解析结果，扇出后顺序采集。

    与兄弟 helper（_fuse_dimensions/_score_credibility 等）对齐：返回结果、
    调用方写入 dim_results；未请求该维度时返回 None。
    """
    if "industry_pricing" not in dims:
        return None
    try:
        industry = _resolve_industry_for_pricing(symbol, dim_results)
        return collect_industry_pricing(symbol, industry)
    except Exception as exc:
        return _dimension_missing_skeleton("industry_pricing", exc)


def _order_dimensions(dims: list[str], dim_results: dict) -> list:
    """按输入顺序排列维度结果。"""
    return [dim_results.get(d) for d in dims if d in COLLECTORS]


def _fuse_dimensions(dimensions: list, symbol: str) -> dict[str, Any]:
    """R-08: RRF 多源融合。"""
    fusion_results: dict[str, Any] = {}
    try:
        from ..fusion import (
            dimension_results_from_legacy,
            fuse_from_legacy_dicts,
            fuse_from_source_results,
            fusion_results_to_dict,
        )
        dim_result_map = dimension_results_from_legacy(dimensions)
        if dim_result_map:
            fusion_raw = fuse_from_source_results(dim_result_map)
        else:
            fusion_raw = fuse_from_legacy_dicts(dimensions)
        fusion_results = fusion_results_to_dict(fusion_raw)
        if fusion_results:
            logger.info(
                "fusion: %d dimensions fused for %s",
                len(fusion_results), symbol,
            )
    except Exception as exc:
        logger.warning("fusion failed for %s: %s", symbol, exc)
    return fusion_results


def _score_credibility(dimensions: list, symbol: str) -> dict[str, float]:
    """R-09: 证据可信度评分。"""
    credibility_scores: dict[str, float] = {}
    try:
        from ..rerank import score_all_dimensions
        credibility_scores = score_all_dimensions(dimensions)
    except Exception as exc:
        logger.warning("rerank scoring failed for %s: %s", symbol, exc)
    return credibility_scores


def _collect_macro_context_block(symbol: str, with_macro: bool) -> dict[str, Any]:
    """R-12: 宏观数据采集（层5，opt-in）。"""
    macro_context: dict[str, Any] = {}
    if with_macro:
        try:
            from ..macro import collect_macro_context
            macro_context = collect_macro_context(symbol)
        except Exception as exc:
            logger.warning("macro context collection failed for %s: %s", symbol, exc)
            macro_context = {"status": "error", "error": str(exc)}
    return macro_context


def _collect_chain_context_block(symbol: str, with_chain: bool, dim_results: dict) -> dict[str, Any]:
    """R-12: 产业链数据（层3+4，opt-in，复用已采集的 basic_info）。"""
    chain_context: dict[str, Any] = {}
    if with_chain:
        try:
            from ..chain import collect_chain_context
            basic_dim = dim_results.get("basic_info") or {}
            basic_data = basic_dim.get("data") if isinstance(basic_dim, dict) else None
            industry = ""
            if isinstance(basic_data, dict):
                industry = basic_data.get("industry", "") or basic_data.get("行业", "")
            chain_context = collect_chain_context(
                symbol, industry=industry, basic_data=basic_data,
            )
        except Exception as exc:
            logger.warning("chain context collection failed for %s: %s", symbol, exc)
            chain_context = {"status": "error", "error": str(exc)}
    return chain_context


def _assemble_result(symbol: str, dimensions: list, fusion_results: dict,
                     credibility_scores: dict, macro_context: dict,
                     chain_context: dict) -> dict[str, Any]:
    """装配采集结果主 dict。"""
    return {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions or [],
        "fusion": fusion_results,  # R-08: RRF 多源融合
        "credibility": credibility_scores,  # R-09: 证据可信度评分
        "macro_context": macro_context,  # R-12
        "chain_context": chain_context,  # R-12
        "summary": _build_summary(dimensions),
    }


def _attach_sector_sync_block(result: dict, symbol: str, dim_results: dict, force: bool) -> None:
    """E1: 板块同步性引擎（v0.2.7）— 6 个 derived 字段 + collection['sector_sync'] 详情。

    依赖 kline + basic_info；板块指数/成分股不可得时内部 fail loud（输出「不可得」，
    不给默认值）。F1 冷缓存门控：成分股逐只首跑 5-10 分钟，默认冷缓存跳过；
    --force-sector-sync 强制预热，之后经 DataCache 缓存同板块多标的秒级复用。
    """
    try:
        _attach_sector_sync(result, symbol, dim_results, force=force)
    except Exception as exc:
        logger.warning("sector_sync attach failed for %s: %s", symbol, exc)
        # 异常兜底同样走 13 键统一骨架（含 fields/meta），消费者按统一 schema
        # 读取不 KeyError；error 键供排障
        result["sector_sync"] = _sector_sync_skeleton(
            symbol, reason=f"sector_sync 采集异常: {exc}", error=str(exc))


def _attach_phase2_block(result: dict, symbol: str) -> None:
    """Phase 2 同行采集（异常兜底行业骨架）。"""
    try:
        attach_phase2_extras(result, symbol)
    except Exception as exc:
        logger.warning("attach_phase2_extras failed for %s: %s", symbol, exc)
        result.setdefault("phase2_extras_errors", []).append(str(exc))
        if not result.get("industry_peers"):
            result["industry_peers"] = {
                "peers": [],
                "target": None,
                "rankings": {},
                "industry_name": None,
                "sufficient": False,
                "error": f"Phase 2 同行采集异常: {exc}",
            }


def _attach_events_block(result: dict, symbol: str, deep: bool) -> None:
    """Attach events (not a default dim, always runs)。

    import 与调用失败均 non-fatal（code-review 2026-08-22 #1）：lib.events
    附属组件链断裂时跳过 events、保留已采集结果，不让数分钟采集成果整锅丢弃。
    report_qc 对 collect_all 是泛化 except（report_qc.py:463-480 任意异常 →
    两层 fail），不依赖此处 raise；quality/rigor 层不消费 events。
    meta["deep"] 在 import 之前绑定，失败时亦保证落盘。
    """
    meta = result.setdefault("_meta", {})
    meta["deep"] = deep
    event_days = 90 if deep else 30
    try:
        from lib.events import attach_events
        attach_events(result, symbol, days=event_days)
    except Exception as e:
        logger.warning("attach_events failed (non-fatal): %s", e)


def _attach_analysis_cards_block(result: dict) -> None:
    """Build analysis cards (Template A/B/C)。"""
    try:
        from lib.analysis_templates import build_analysis_cards
        build_analysis_cards(result)
    except Exception as e:
        logger.warning("build_analysis_cards failed (non-fatal): %s", e)


def _attach_manifest_block(result: dict) -> None:
    """Generate collection manifest (Task 9, P1)。

    生成失败 non-fatal：manifest=None（正常路径行为不变）。
    events/analysis 块均为 non-fatal，meta 由各块 setdefault 自绑定。
    """
    try:
        from lib.manifest import generate_manifest
        meta = result.setdefault("_meta", {})
        meta["manifest"] = generate_manifest(result)
    except Exception as e:
        logger.warning("manifest generation failed (non-fatal): %s", e)
        result.setdefault("_meta", {})["manifest"] = None


def _attach_news_pack_block(result: dict, symbol: str, with_news_pack: bool) -> None:
    """新闻包（公告 + 查询包 + 可选 Tavily，opt-in）。"""
    if with_news_pack:
        try:
            attach_news_pack(result, symbol)
        except Exception as exc:
            logger.warning("attach_news_pack failed for %s: %s", symbol, exc)
            result["news"] = {
                "cards": [],
                "query_pack": [],
                "attempted_sources": {"error": str(exc)},
            }


def collect_all(symbol: str, dims: list[str] | None = None,
                deep: bool = False,
                with_macro: bool = False,
                with_chain: bool = False,
                with_news_pack: bool = False,
                force_sector_sync: bool = False) -> dict[str, Any]:
    """全维度采集。

    last30days 模式扩展：维度之间也并行执行（跨维度 fan-out）。
    每个维度内部已在 collect_* 中并行查源。

    Args:
        symbol: 股票代码
        dims: 维度列表，None 使用默认（含 valuation + kline）
        deep: 深度模式，kline 扩大到 730 自然日
        with_macro: 采集中国宏观指标（PMI/CPI/PPI/LPR）
        with_chain: 采集产业链上下文（复用已采集的 basic_info）
        with_news_pack: 采集新闻包（公告 + 查询包 + 可选 Tavily）
        force_sector_sync: 绕过 F1 冷缓存门控强制计算板块同步性（首次预热，
            成分股日线全量抓取约 5-10 分钟；默认冷缓存时跳过并标注原因）
    """
    # 空列表（如 CLI --dims "" 解析结果）视同 None 填默认维度；
    # 显式语义 + 日志提示（review #2：不静默跑全量）
    if not dims:
        logger.warning("dims 为空（如 --dims \"\"），按默认维度采集: %s", list(_DEFAULT_DIMS))
        dims = list(_DEFAULT_DIMS)

    start_all = time.time()

    # 深度模式：kline 用更长窗口
    kline_kwargs = {}
    if deep:
        kline_kwargs["start_date"] = _days_ago(730)

    dim_results = _collect_dims_fanout(symbol, dims, kline_kwargs)
    industry_pricing = _collect_industry_pricing_block(symbol, dims, dim_results)
    if industry_pricing is not None:
        dim_results["industry_pricing"] = industry_pricing

    # 按输入顺序排列
    dimensions = _order_dimensions(dims, dim_results)

    fusion_results = _fuse_dimensions(dimensions, symbol)
    credibility_scores = _score_credibility(dimensions, symbol)
    macro_context = _collect_macro_context_block(symbol, with_macro)
    chain_context = _collect_chain_context_block(symbol, with_chain, dim_results)

    result = _assemble_result(symbol, dimensions, fusion_results,
                              credibility_scores, macro_context, chain_context)
    _attach_sector_sync_block(result, symbol, dim_results, force_sector_sync)
    _attach_phase2_block(result, symbol)
    _attach_events_block(result, symbol, deep)
    _attach_analysis_cards_block(result)
    _attach_manifest_block(result)
    _attach_news_pack_block(result, symbol, with_news_pack)

    logger.info("collect_all total=%.1fs symbol=%s dims=%d",
                time.time() - start_all, symbol, len(dims))
    return result


def attach_news_pack(result: dict[str, Any], symbol: str, days: int = 7) -> dict[str, Any]:
    """v0.1.9: attach news cards + query pack to collection."""
    from ..news_scanner import collect_news
    from ..json_util import dumps_json

    name = ""
    basic_dim = next(
        (d for d in result.get("dimensions", []) if d.get("dimension") == "basic_info"),
        None,
    )
    if basic_dim and isinstance(basic_dim.get("data"), dict):
        name = basic_dim["data"].get("name") or basic_dim["data"].get("股票简称") or ""

    news = collect_news(symbol, name=name, days=days)
    result["news"] = news

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pack_path = env.STORE_DIR / f"news_query_pack_{symbol}_{ts}.json"
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(
        dumps_json({"symbol": symbol, "name": name, "query_pack": news.get("query_pack", [])}),
        encoding="utf-8",
    )
    result.setdefault("_meta", {})["news_query_pack_path"] = str(pack_path)
    return news


# ---- 市场结构采集（v0.1.3 Phase 1） ----

_HS300_CODE = "000300.SH"
_ERP_MIN_ALIGNED_DAYS = 60
_ERP_DGS10_LOOKBACK_DAYS = 5


def _ms_set_unavailable(availability: dict[str, str], key: str, reason: str) -> None:
    availability[key] = f"unavailable: {reason}"


def _ms_lookup_sw_index_code_at_level(
    tc: Any, industry: str, level: str,
) -> str | None:
    """在指定申万层级（L1/L2）中按行业名精确匹配申万指数代码。"""
    df = tc.query("index_classify", level=level, src="SW2021")
    if df is None or df.empty:
        return None
    name = industry.strip()
    for _, row in df.iterrows():
        idx_name = str(row.get("industry_name") or row.get("name") or "").strip()
        if not idx_name:
            continue
        code = str(row.get("index_code", "")).strip()
        if name == idx_name and code:
            return code
    return None


def _ms_lookup_sw_index_code(tc: Any, industry: str | None) -> str | None:
    """按申万行业名称匹配指数代码（L3 → L2 → L1）。"""
    if not industry:
        return None
    for level in ("L3", "L2", "L1"):
        code = _ms_lookup_sw_index_code_at_level(tc, industry, level)
        if code:
            return code
    return None


def _resolve_sw_industry_name(
    tc: Any, symbol: str, industry_hint: str | None = None,
) -> str | None:
    """解析申万行业名：优先 collection 提示，再 stock_basic，再申万分类模糊匹配。"""
    candidates: list[str] = []
    if industry_hint and industry_hint.strip():
        candidates.append(industry_hint.strip())
    basic_df = tc.query("stock_basic", ts_code=_ts_code(symbol),
                        fields="ts_code,name,industry")
    if basic_df is not None and not basic_df.empty:
        bi = str(basic_df.iloc[0].get("industry", "")).strip()
        if bi and bi not in candidates:
            candidates.append(bi)
    for name in candidates:
        if _ms_lookup_sw_index_code(tc, name):
            return name
    for name in candidates:
        for level in ("L3", "L2", "L1"):
            df = tc.query("index_classify", level=level, src="SW2021")
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                idx_name = str(row.get("industry_name") or row.get("name") or "").strip()
                if idx_name and (name in idx_name or idx_name in name):
                    return idx_name
    return candidates[0] if candidates else None


def _ms_fetch_pmi() -> dict[str, Any] | None:
    """中国制造业 PMI（akshare），供 A-① 行业景气度补充。"""
    if not env.is_akshare_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.macro_china_pmi()
        if df is None or df.empty:
            return None
        # F0-4: akshare 序列最新在前，iloc[-1] 会取到 2008 年最旧行；
        # 按「月份」列取最新期行。
        from ..shared_dates import latest_month_row as _latest_month_row

        row = _latest_month_row(df.to_dict("records"))
        raw_month = row.get("月份")
        if raw_month is not None:
            month = str(raw_month)
        elif hasattr(row, "name") and row.name is not None:
            month = str(row.name)
        else:
            month = ""
        pmi = row_value_or_last(row, "制造业-指数", "制造业")
        if pmi is None:
            return None
        return {
            "month": month,
            "manufacturing_pmi": round(pmi, 2),
            "signal": "扩张" if pmi >= 50 else "收缩",
            "source": "akshare.macro_china_pmi",
        }
    except Exception as exc:
        logger.debug("PMI fetch failed: %s", exc)
        return None


def _ms_return_pct(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    start, end = closes[0], closes[-1]
    if not start:
        return None
    return (end - start) / start * 100


def _ms_sw_numeric_code(index_code: str) -> str:
    """851024.SI → 851024（akshare index_hist_sw 格式）。"""
    return index_code.split(".")[0]


def _ms_lookup_akshare_sw_code(industry: str) -> str | None:
    """按申万行业名在 akshare 行业列表中匹配指数代码（L3→L2→L1）。

    逐表拉取并立即 exact 扫描，命中即短路返回（常见第一表命中 1 次 API
    调用；修复前先拉全 3 表，精确命中场景 1 次调用退化为 3 次）。exact
    全 miss 再对已拉表做 substring 扫描（不重拉，最坏 3 次调用）。单表
    拉取失败跳过继续——其余表已找到的匹配不因限流整体丢失（修复前
    blanket except 让表 1 已命中的结果也返回 None）。
    """
    if not env.is_akshare_available():
        return None
    name = industry.strip()
    if not name:
        return None
    try:
        import akshare as ak
        loaders = (ak.sw_index_third_info, ak.sw_index_second_info, ak.sw_index_first_info)
        tables: list = []
        with akshare_direct_session():
            for loader in loaders:
                try:
                    df = loader()
                except Exception as exc:
                    logger.debug("akshare sw index loader failed: %s", exc)
                    continue
                if df is None or df.empty:
                    continue
                tables.append(df)
                for _, row in df.iterrows():  # exact 扫描：命中即返回
                    idx_name = str(row.get("行业名称", "")).strip()
                    code = str(row.get("行业代码", "")).strip()
                    if name == idx_name and code:
                        return code
            for df in tables:  # substring 扫描：复用已拉表
                for _, row in df.iterrows():
                    idx_name = str(row.get("行业名称", "")).strip()
                    code = str(row.get("行业代码", "")).strip()
                    if idx_name and code and (name in idx_name or idx_name in name):
                        return code
    except Exception as exc:
        logger.debug("akshare sw index lookup failed: %s", exc)
    return None


def _akshare_closes_from_hist_sw(index_code: str, *, days: int = 70) -> list[float]:
    """akshare 申万指数日线收盘价序列（升序）。"""
    import akshare as ak

    sym = _ms_sw_numeric_code(index_code)
    with akshare_direct_session():
        df = ak.index_hist_sw(symbol=sym, period="day")
    if df is None or df.empty:
        return []
    col = "收盘" if "收盘" in df.columns else "close"
    tail = df.sort_values("日期" if "日期" in df.columns else "trade_date").tail(days + 5)
    return [float(v) for v in tail[col].tolist() if v is not None]


def _akshare_hs300_dated_closes(*, days: int = 70) -> list[tuple[str, float]]:
    """沪深300 日线 (trade_date YYYYMMDD, close) 升序。"""
    import akshare as ak

    sd = _days_ago(days + 10)
    ed = _today()
    sd_fmt = _to_iso_date(sd)
    ed_fmt = _to_iso_date(ed)
    with akshare_direct_session():
        df = ak.stock_zh_index_daily_em(
            symbol="sh000300", start_date=sd_fmt, end_date=ed_fmt,
        )
    if df is None or df.empty:
        return []
    col = "收盘" if "收盘" in df.columns else "close"
    date_col = "日期" if "日期" in df.columns else "date"
    sorted_df = df.sort_values(date_col)
    out: list[tuple[str, float]] = []
    for _, row in sorted_df.iterrows():
        raw = str(row.get(date_col) or "")
        td = raw.replace("-", "").replace("/", "")[:8]
        v = row.get(col)
        if len(td) == 8 and td.isdigit() and v is not None:
            try:
                out.append((td, float(v)))
            except (TypeError, ValueError):
                continue
    return out


def _akshare_hs300_closes(*, days: int = 70) -> list[float]:
    """沪深300 日线收盘价（akshare / 东方财富）。"""
    return [c for _, c in _akshare_hs300_dated_closes(days=days)]


def _ms_build_sw_index_result(
    *,
    index_code: str,
    industry: str | None,
    sw_closes: list[float],
    bench_closes: list[float] | None,
    stock_closes: list[float] | None,
    source: str,
) -> dict | None:
    if len(sw_closes) < 2:
        return None
    ret_20 = _ms_return_pct(sw_closes[-21:]) if len(sw_closes) >= 2 else None
    bench_ret = (
        _ms_return_pct(bench_closes[-21:])
        if bench_closes and len(bench_closes) >= 2 else None
    )
    stock_ret = (
        _ms_return_pct(stock_closes[-21:])
        if stock_closes and len(stock_closes) >= 2 else None
    )
    rel_vs_bench = (ret_20 - bench_ret) if ret_20 is not None and bench_ret is not None else None
    rel_stock_vs_ind = (
        (stock_ret - ret_20) if stock_ret is not None and ret_20 is not None else None
    )
    return {
        "index_code": index_code,
        "industry": industry,
        "return_20d_pct": round(ret_20, 2) if ret_20 is not None else None,
        "benchmark_return_20d_pct": round(bench_ret, 2) if bench_ret is not None else None,
        "stock_return_20d_pct": round(stock_ret, 2) if stock_ret is not None else None,
        "relative_vs_benchmark_pct": round(rel_vs_bench, 2) if rel_vs_bench is not None else None,
        "stock_vs_industry_pct": round(rel_stock_vs_ind, 2) if rel_stock_vs_ind is not None else None,
        "source": source,
    }


def _ms_fetch_sw_index_akshare(
    symbol: str,
    industry: str | None,
    index_code: str | None = None,
    tc: Any | None = None,
) -> dict | None:
    """Tushare sw_daily 不可用时的 akshare 申万指数回退。"""
    if not env.is_akshare_available():
        return None
    code: str | None = None
    if industry:
        code = _ms_lookup_akshare_sw_code(industry)
    if not code and index_code:
        code = _ms_sw_numeric_code(index_code)
    if not code:
        return None
    try:
        sw_closes = _akshare_closes_from_hist_sw(code)
    except Exception as exc:
        logger.debug("akshare index_hist_sw failed: %s", exc)
        return None
    if len(sw_closes) < 2:
        return None

    bench_closes: list[float] | None = None
    if tc is not None:
        df_hs = tc.query("index_daily", ts_code=_HS300_CODE,
                         start_date=_days_ago(70), end_date=_today())
        if df_hs is not None and not df_hs.empty:
            hs = df_hs.sort_values("trade_date")
            bench_closes = [float(v) for v in hs["close"].tolist() if v is not None]
    if not bench_closes:
        try:
            bench_closes = _akshare_hs300_closes()
        except Exception as exc:
            logger.debug("akshare HS300 for sw_index failed: %s", exc)
            bench_closes = None

    stock_closes: list[float] | None = None
    if tc is not None:
        df_stk = tc.query("daily", ts_code=_ts_code(symbol),
                          start_date=_days_ago(70), end_date=_today(),
                          fields="trade_date,close")
        if df_stk is not None and not df_stk.empty:
            stk = df_stk.sort_values("trade_date")
            stock_closes = [float(v) for v in stk["close"].tolist() if v is not None]
    if not stock_closes:
        try:
            rows = _q_akshare_kline(symbol, start_date=_days_ago(70), end_date=_today())
            if rows:
                ordered = sorted(rows, key=lambda r: str(r.get("trade_date", "")))
                stock_closes = [
                    float(r["close"]) for r in ordered
                    if r.get("close") is not None
                ]
        except Exception as exc:
            logger.debug("akshare stock kline for sw_index failed: %s", exc)

    return _ms_build_sw_index_result(
        index_code=code,
        industry=industry,
        sw_closes=sw_closes,
        bench_closes=bench_closes,
        stock_closes=stock_closes,
        source="akshare.index_hist_sw",
    )


def _ms_sw_index_availability_label(value: dict) -> str:
    """sw_index 可用性标注（区分 Tushare 原生 vs akshare 回退）。"""
    if value.get("source") == "akshare.index_hist_sw":
        return (
            "available (akshare fallback; Tushare sw_daily 需 5000 积分，见 "
            "https://tushare.pro/document/2?doc_id=327)"
        )
    return "available"


def _ms_fetch_sw_index(tc: Any, symbol: str, industry: str | None) -> dict | None:
    resolved = industry or _resolve_sw_industry_name(tc, symbol, industry)
    index_code = _ms_lookup_sw_index_code(tc, resolved) if resolved else None
    if index_code:
        df_sw = tc.query("sw_daily", ts_code=index_code,
                         start_date=_days_ago(70), end_date=_today())
        if df_sw is not None and not df_sw.empty:
            sw = df_sw.sort_values("trade_date")
            sw_closes = [float(v) for v in sw["close"].tolist() if v is not None]
            df_hs = tc.query("index_daily", ts_code=_HS300_CODE,
                             start_date=_days_ago(70), end_date=_today())
            bench_closes: list[float] | None = None
            if df_hs is not None and not df_hs.empty:
                hs = df_hs.sort_values("trade_date")
                bench_closes = [float(v) for v in hs["close"].tolist() if v is not None]
            stock_closes: list[float] | None = None
            df_stk = tc.query("daily", ts_code=_ts_code(symbol),
                              start_date=_days_ago(70), end_date=_today(),
                              fields="trade_date,close")
            if df_stk is not None and not df_stk.empty:
                stk = df_stk.sort_values("trade_date")
                stock_closes = [float(v) for v in stk["close"].tolist() if v is not None]
            built = _ms_build_sw_index_result(
                index_code=index_code,
                industry=resolved,
                sw_closes=sw_closes,
                bench_closes=bench_closes,
                stock_closes=stock_closes,
                source="tushare.sw_daily",
            )
            if built is not None:
                return built
    return _ms_fetch_sw_index_akshare(symbol, resolved, index_code, tc=tc)


def _recent_flow_records(records: list[dict], *, limit: int) -> list[dict]:
    return sorted(
        records, key=lambda r: str(r.get("trade_date", "")), reverse=True,
    )[:limit]


_MIN_NORTHBOUND_DAYS = 5

# P0-1：北向个股披露源停更容忍窗口。最新有值记录距今超过该天数 → 净额
# 视为陈旧（2024-08 起北向个股披露规则变更，源可能停在两年前）。
_NORTHBOUND_STALE_DAYS = 90

# 同 run 按日缓存（仿 _cninfo_hold_cache 模式）：collect_northbound 与
# _ms_fetch_northbound_stock 以相同参数（30 日窗口）重复拉取 hsgt_top10。
# 缓存原始 rows（稀疏判定在消费方 _MIN_NORTHBOUND_DAYS，语义不变）；
# None 不落缓存；跨日自动失效。
_hsgt_top10_cache: dict[str, list[dict]] = {}
_hsgt_top10_cache_day: str = ""
_hsgt_top10_cache_lock = threading.Lock()  # D8：多线程 + 可变状态必须加锁（对齐 _run_kline_quote_cache）


def _hsgt_top10_cached(symbol: str) -> list[dict] | None:
    global _hsgt_top10_cache_day
    day = _today()
    with _hsgt_top10_cache_lock:
        if _hsgt_top10_cache_day != day:
            _hsgt_top10_cache.clear()
            _hsgt_top10_cache_day = day
        if symbol in _hsgt_top10_cache:
            # 副本：调用方 mutate 不污染缓存对象（对齐 _run_kline_quote_cache）
            return [dict(r) for r in _hsgt_top10_cache[symbol]]
        rows = _q_tushare_hsgt_top10(symbol)  # 模块全局名：测试 patch 命名空间仍可拦截
        if rows:
            _hsgt_top10_cache[symbol] = [dict(r) for r in rows]
        return rows


def _norm_trade_date(raw: Any) -> str | None:
    """把多种形态的 trade_date 归一化为 'YYYYMMDD'；无法识别返回 None。

    akshare 个股持股变动为 'YYYY-MM-DD'（实测 2026-08-24 live 复现 600176
    最新 '2024-08-16'），tushare 为 'YYYYMMDD'，TimeStamp 字符串形态
    'YYYY-MM-DD HH:MM:SS' 截前 8 位数字；NaN/None/'--'/'nan'/'2024-8-16'
    等无法归一化返回 None——调用方须逐行剔除坏值，不能让单条坏行污染
    max(dates) 使整个 P0-1 时效守卫静默失效（第五轮）。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None
    return digits


def _ms_fetch_northbound_stock(tc: Any, symbol: str) -> dict | None:
    """个股北向近 10 个交易日净额（元）。

    Tushare hsgt_top10（仅上榜日有 net_amount）→ akshare 个股持股变动回退。
    hsgt_top10 上榜日过少时回退 akshare，避免稀疏序列误导汇总。
    不使用 moneyflow（主力）或 moneyflow_hsgt（市场级汇总）。

    P0-1 时效守卫：2024-08 起北向个股披露规则变更，hsgt_top10/个股持股源
    停更——records 可能停留在两年前（如最新 2024-08-16）。若最新有值记录
    距今超过 _NORTHBOUND_STALE_DAYS（90 天），net_sum_10d 置 None 并附
    staleness_note，渲染层自动降级为「数据不足」，禁止把陈旧净额标为
    「近 10 日」参与 CV-4 印证。
    """
    def _guard_staleness(result: dict | None) -> dict | None:
        """对已聚合结果做时效校验；过期 → 净额置 None + 标注（保留 records 追溯）。"""
        if not result:
            return result
        recent = result.get("records") or []
        # 逐记录归一化后取 max：词法最大对坏行（'nan'/'--' 等垃圾字符串 > '2024...'）
        # 会把 latest 选成垃圾值、strptime ValueError 静默跳过守卫——单条坏行即令
        # 整个 P0-1 失效（第五轮）。归一化后全为 8 位数字，词法序 == 时间序。
        normed: list[tuple[str, Any]] = []
        for r in recent:
            d = _norm_trade_date(r.get("trade_date"))
            if d is not None:
                normed.append((d, r.get("trade_date")))
        if not normed:
            # 时效不可确认：宁降级为「不可用」，不得以不可判断的日期冒充新鲜
            # 数据（fail loud：日志可查，渲染层走数据不足降级）
            logger.warning(
                "northbound staleness guard(%s): %d 条记录 trade_date 均无法归一化，"
                "净额时效不可确认 → 置 None", symbol, len(recent))
            result["net_sum_10d"] = None
            result["stale"] = True
            result["staleness_note"] = (
                "北向个股披露记录期无法解析，净额时效不可确认（源异常）"
            )
            return result
        latest, latest_raw = max(normed)
        latest_dt = datetime.strptime(latest, "%Y%m%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - latest_dt).days
        if age_days <= _NORTHBOUND_STALE_DAYS:
            return result
        result["net_sum_10d"] = None
        result["stale"] = True
        result["latest_trade_date"] = str(latest_raw) if latest_raw is not None else latest
        result["staleness_note"] = (
            f"北向个股披露源已停更：最新记录 {latest_raw}，距今约 {age_days // 30} 个月，"
            "净额不可用（2024-08 起北向个股披露规则变更）"
        )
        return result

    try:
        records = _hsgt_top10_cached(symbol)
        if records:
            recent = _recent_flow_records(records, limit=10)
            valued = [r for r in recent if _flow_amount_yuan(r) is not None]
            if len(valued) >= _MIN_NORTHBOUND_DAYS:
                net_sum = sum(v for v in (_flow_amount_yuan(r) for r in valued))
                return _guard_staleness({
                    "records": recent,
                    "net_sum_10d": net_sum,
                    "days": len(valued),
                    "source": "tushare.hsgt_top10",
                })
    except Exception as exc:
        logger.debug("tushare hsgt_top10 failed for %s: %s", symbol, exc)

    records = _q_akshare_northbound(symbol)
    if not records:
        return None
    recent = _recent_flow_records(records, limit=10)
    # 与 tushare 分支同守卫：只数有值行（无值行计入会让 N 日合计被误标为
    # 10 日合计——akshare 映射不预滤无值行，code-review）
    valued = [r for r in recent if _flow_amount_yuan(r) is not None]
    if len(valued) < _MIN_NORTHBOUND_DAYS:
        return None
    net_sum = sum(v for v in (_flow_amount_yuan(r) for r in valued))
    # P0-1：akshare 分支同样走时效守卫（源停更时降级而非误标「近 10 日」）
    return _guard_staleness({
        "records": recent,
        "net_sum_10d": net_sum,
        "days": len(valued),
        "source": "akshare.stock_hsgt_individual_em",
    })


def _ms_fetch_margin(tc: Any, symbol: str) -> dict | None:
    """个股融资余额变化（margin_detail，非交易所汇总 margin）。

    按 LAW 16 分离三类余额变化：
      - change_pct: 融资余额（rzye）增速
      - rqye_change_pct: 融券余额增速
      - rzrqye_change_pct: 融资融券合计余额增速（如有）

    Tushare margin_detail 不可用时降级到 akshare stock_margin_account_info
    （全市场汇总粒度，非个股粒度；source 标记为 "akshare.margin_account"）。
    """
    # 主线：Tushare margin_detail（个股粒度）
    df = tc.query("margin_detail", ts_code=_ts_code(symbol),
                  start_date=_days_ago(15), end_date=_today())
    if df is not None and not df.empty:
        records = df.sort_values("trade_date").to_dict("records")
        if len(records) >= 2:
            first, last = records[0], records[-1]

            def _pct_chg(key: str) -> float | None:
                fv = first.get(key)
                lv = last.get(key)
                if fv is None or lv is None:
                    return None
                f = float(fv)
                l = float(lv)
                if abs(f) < 1e-9:
                    return None
                return (l - f) / f * 100

            change_pct = _pct_chg("rzye")
            rqye_change_pct = _pct_chg("rqye")
            rzrqye_change_pct = _pct_chg("rzrqye")

            result: dict[str, Any] = {
                "records": records[-10:],
                "source": "tushare.margin_detail",
            }
            if change_pct is not None:
                result["change_pct"] = round(change_pct, 2)
            if rqye_change_pct is not None:
                result["rqye_change_pct"] = round(rqye_change_pct, 2)
            if rzrqye_change_pct is not None:
                result["rzrqye_change_pct"] = round(rzrqye_change_pct, 2)
            if result.get("change_pct") is not None or \
               result.get("rqye_change_pct") is not None or \
               result.get("rzrqye_change_pct") is not None:
                # 至少有一个变化字段有效即返回（rzye 可能因首期近零而为 None，
                # 但 rqye/rzrqye 仍有效，不应丢弃导致降级到全市场聚合数据）
                return result

    # 降级：akshare 全市场汇总（有损：非个股粒度，仅提供方向性参考）
    try:
        from lib.market_pulse import fetch_margin_account_info  # 惰性导入

        df_ak = fetch_margin_account_info()
        if df_ak is None or df_ak.empty:
            return None
        # 尝试已知日期列名，降级到首列（标注警告）
        date_col = None
        for candidate in ("交易日期", "日期", "date", "trade_date"):
            if candidate in df_ak.columns:
                date_col = candidate
                break
        if date_col is None:
            date_col = df_ak.columns[0]
            logger.warning("margin akshare fallback: no known date column, using %s", date_col)
        records_ak = df_ak.sort_values(date_col).to_dict("records")
        if len(records_ak) < 2:
            return None

        # 窗口口径与 Tushare 主路径一致：change_pct 取最近 15 个交易日两端，
        # 而非全历史（约 2 年）首尾——同字段两种窗口语义会让主/降级路径的
        # 增速不可比（降级路径的增速被 2 年窗口摊薄）。
        window_ak = records_ak[-15:]
        first_a = safe_float(window_ak[0].get("融资余额"))
        last_a = safe_float(window_ak[-1].get("融资余额"))
        if first_a is None or last_a is None or abs(first_a) < 1e-9:
            return None

        change_pct_ak = round((last_a - first_a) / first_a * 100, 2)
        return {
            "records": records_ak[-10:],
            "source": "akshare.margin_account",
            "change_pct": change_pct_ak,
            "note": "全市场汇总，非个股数据；15 日窗口（与主路径同口径）；仅供方向性参考",
        }
    except Exception as exc:
        logger.warning("margin akshare fallback failed: %s", exc)

    return None


def _ms_fetch_moneyflow(tc: Any, symbol: str) -> dict | None:
    records = _q_tushare_moneyflow(symbol)
    if not records:
        return None
    recent = _recent_flow_records(records, limit=10)
    net_sum = sum(v for v in (_flow_amount_yuan(r) for r in recent[:5]) if v is not None)
    return {
        "records": recent,
        "net_sum_5d": net_sum,
        "source": "tushare.moneyflow",
    }


def _ms_fetch_turnover(tc: Any, symbol: str) -> dict | None:
    from lib.stats import percentile_rank

    df = tc.query("daily_basic", ts_code=_ts_code(symbol),
                  fields="trade_date,turnover_rate",
                  start_date=_days_ago(90), end_date=_today())
    if df is None or df.empty:
        return None
    rows = df.sort_values("trade_date")
    rates = [float(v) for v in rows["turnover_rate"].tolist()
             if v is not None and float(v) > 0]
    if not rates:
        return None
    avg_5 = sum(rates[-5:]) / min(5, len(rates[-5:]))
    avg_60 = sum(rates[-60:]) / min(60, len(rates[-60:]))
    current = rates[-1]
    pct = percentile_rank(rates[-60:], current) if len(rates) >= 5 else None
    return {
        "avg_5d": round(avg_5, 4),
        "avg_60d": round(avg_60, 4),
        "current": round(current, 4),
        "ratio_5_60": round(avg_5 / avg_60, 3) if avg_60 else None,
        "percentile_60d": round(pct, 1) if pct is not None else None,
        "source": "tushare.daily_basic",
    }


def _akshare_date_to_iso(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip().replace("/", "-")
    return s[:10] if len(s) >= 10 else s


def _ms_fetch_akshare_cn10y_series() -> list[tuple[str, float]]:
    """中国 10Y 国债收益率日序列（date, yield%）。FRED 不可用时的 ERP 回退。"""
    if not env.is_akshare_available():
        return []
    import akshare as ak
    try:
        with _proxy_bypass():
            df = ak.bond_zh_us_rate(start_date=_days_ago(1825))
    except Exception as exc:
        logger.warning("akshare bond_zh_us_rate failed: %s", exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    col = "中国国债收益率10年"
    if col not in df.columns:
        return []
    out: list[tuple[str, float]] = []
    for _, row in df.iterrows():
        dt = _akshare_date_to_iso(row.get("日期"))
        val = row.get(col)
        if not dt or val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if fval != fval:  # NaN
            continue
        out.append((dt, fval))
    out.sort(key=lambda x: x[0])
    return out


def _ms_fetch_y10_series(config: dict) -> tuple[list[tuple[str, float]], str]:
    """10Y 国债收益率序列：FRED DGS10 优先，akshare 中国 10Y 回退。"""
    fred = _ms_fetch_fred_dgs10_series(config)
    if fred:
        return fred, "FRED.DGS10"
    cn = _ms_fetch_akshare_cn10y_series()
    if cn:
        return cn, "akshare.bond_zh_us_rate"
    return [], ""


def _ms_fetch_fred_dgs10_series(config: dict) -> list[tuple[str, float]]:
    """FRED DGS10 日序列（date, yield%）。"""
    if not env.is_fred_available(config):
        return []
    import json
    import urllib.parse
    import urllib.request

    key = config.get("FRED_API_KEY", "")
    params = urllib.parse.urlencode({
        "series_id": "DGS10",
        "api_key": key,
        "file_type": "json",
        "observation_start": _fred_date(_days_ago(1825)),
        "observation_end": _fred_date(_today()),
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("FRED DGS10 fetch failed: %s", exc)
        return []
    out: list[tuple[str, float]] = []
    for obs in payload.get("observations", []):
        val = obs.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((obs.get("date", ""), float(val)))
        except (TypeError, ValueError):
            continue
    return out


def _dgs10_for_trade_date(
    dgs10_by_date: dict[str, float],
    trade_date_fmt: str,
    *,
    lookback_days: int = _ERP_DGS10_LOOKBACK_DAYS,
) -> float | None:
    """取交易日对应 DGS10；若当日无数据则向前最多 lookback_days 个自然日。"""
    try:
        d = datetime.strptime(trade_date_fmt, "%Y-%m-%d")
    except ValueError:
        return dgs10_by_date.get(trade_date_fmt)
    for i in range(lookback_days + 1):
        key = (d - timedelta(days=i)).strftime("%Y-%m-%d")
        if key in dgs10_by_date:
            return dgs10_by_date[key]
    return None


def _ms_fetch_erp(tc: Any, config: dict) -> dict | None:
    from lib.stats import percentile_rank

    df = tc.query("index_dailybasic", ts_code=_HS300_CODE,
                  fields="trade_date,pe_ttm",
                  start_date=_days_ago(1825), end_date=_today())
    if df is None or df.empty:
        return None
    dgs10_series, y10_source = _ms_fetch_y10_series(config)
    dgs10_by_date = {d: v for d, v in dgs10_series}
    latest_dgs10 = dgs10_series[-1][1] if dgs10_series else None

    rows = df.sort_values("trade_date").to_dict("records")
    erp_hist: list[float] = []
    for r in rows:
        pe = r.get("pe_ttm")
        if pe is None or float(pe) <= 0:
            continue
        td = str(r.get("trade_date", ""))
        d_fmt = _to_iso_date(td) if len(td) == 8 else td
        y10 = _dgs10_for_trade_date(dgs10_by_date, d_fmt)
        if y10 is None:
            continue
        ep = 100.0 / float(pe)
        erp_hist.append(ep - y10)

    if not erp_hist:
        return None
    current = erp_hist[-1]
    pct_5y = percentile_rank(erp_hist, current)
    erp_days = len(erp_hist)
    partial = erp_days < _ERP_MIN_ALIGNED_DAYS
    return {
        "raw": round(current, 3),
        "percentile_5y": round(pct_5y, 1) if pct_5y is not None else None,
        "dgs10": round(latest_dgs10, 3) if latest_dgs10 is not None else None,
        "erp_days": erp_days,
        "partial": partial,
        "index": _HS300_CODE,
        "source": f"tushare.index_dailybasic+{y10_source}" if y10_source else "tushare.index_dailybasic",
    }


_50ETF_UNDERLYING = "510050.SH"
_ETF_300_CODE = "510300.SH"
_NEW_HIGH_SAMPLE = 30
_PCR_HISTORY_5Y_CAL_DAYS = 1825
_PCR_HISTORY_60D = 60
# 5 年 PCR 历史分位：均匀降采样上限，避免逐日 opt_daily 风暴
_PCR_MAX_DAILY_QUERIES = 80


def _ms_50etf_option_codes(tc: Any) -> tuple[list[str], list[str]]:
    """SSE 50ETF 期权合约代码（认沽/认购）。"""
    df = tc.query("opt_basic", exchange="SSE", fields="ts_code,call_put,name")
    if df is None or df.empty:
        return [], []
    puts, calls = [], []
    for _, row in df.iterrows():
        name = str(row.get("name") or "")
        if "50ETF" not in name and "510050" not in name:
            continue
        code = str(row.get("ts_code") or "").strip()
        cp = str(row.get("call_put") or "").upper()
        if not code:
            continue
        if cp == "P":
            puts.append(code)
        elif cp == "C":
            calls.append(code)
    return puts, calls


def _ms_subsample_trade_dates(dates: list[str], max_points: int) -> list[str]:
    """均匀降采样交易日列表，始终保留最后一日。"""
    if len(dates) <= max_points:
        return dates
    step = max(1, len(dates) // max_points)
    sampled = list(dates[::step])
    if dates[-1] not in sampled:
        sampled.append(dates[-1])
    return sorted(set(sampled))


def _ms_pcr_from_df(df: Any, put_codes: set[str], call_codes: set[str]) -> float | None:
    """从单日 opt_daily DataFrame 计算 PCR（_ms_pcr_on_date 与探针共用）。"""
    if df is None or getattr(df, "empty", True):
        return None
    put_vol = call_vol = 0.0
    for _, row in df.iterrows():
        code = str(row.get("ts_code") or "")
        try:
            vol = float(row.get("vol") or 0)
        except (TypeError, ValueError):
            continue
        if math.isnan(vol):
            continue  # NaN 成交量视同缺失：NaN 恒不等于 0 且 call_vol<=0 守卫
            # 放行 NaN（NaN<=0 为 False），会把 NaN 比值存成"当前 PCR"并
            # 渲染成伪造的历史新低信号
        if code in put_codes:
            put_vol += vol
        elif code in call_codes:
            call_vol += vol
    if call_vol <= 0:
        return None
    return put_vol / call_vol


def _ms_pcr_on_date(
    tc: Any, trade_date: str, put_codes: set[str], call_codes: set[str],
) -> float | None:
    # 单次 opt_daily 查询加时限：该端点单次数据量小，正常 <1s；
    # 网络挂起时 socket 默认 30s × 全窗口 ~130 次查询会拖死整个 market_structure
    df = _run_with_timeout(
        lambda: tc.query("opt_daily", trade_date=trade_date, exchange="SSE"),
        8.0, f"opt_daily:{trade_date}",
    )
    return _ms_pcr_from_df(df, put_codes, call_codes)


def _ms_fetch_put_call_ratio(tc: Any) -> dict | None:
    """50ETF 认沽认购比（opt_daily，需 5000 积分）。"""
    from lib.stats import percentile_rank

    puts, calls = _ms_50etf_option_codes(tc)
    if not puts or not calls:
        return None
    put_set, call_set = set(puts), set(calls)
    cal = tc.query(
        "trade_cal", exchange="SSE",
        start_date=_days_ago(_PCR_HISTORY_5Y_CAL_DAYS + 5), end_date=_today(), is_open="1",
    )
    if cal is None or cal.empty:
        return None
    dates = sorted(str(d) for d in cal["cal_date"].tolist())
    raw_days = len(dates)
    sampled = _ms_subsample_trade_dates(dates, _PCR_MAX_DAILY_QUERIES)
    # 60 日分位窗口取最近 _PCR_HISTORY_60D 个自然日全分辨率（此前对降采样
    # 序列取 ratios[-60:] 实际横跨 ~3.5 年——降采样 step≈15 时 60 点覆盖 900+ 交易日）
    cutoff = _days_ago(_PCR_HISTORY_60D)
    recent_dates = [d for d in dates if d >= cutoff]
    fetch_dates = sorted(set(sampled) | set(recent_dates))

    ratio_by_date: dict[str, float] = {}
    # F1-5 修复：批量取数前单点预检——端点整体挂起/限流时（实测 56 天 × 8s
    # 超时风暴，单次报告拖慢数分钟），8s 探针失败重试一次、两次均败才整体
    # 降级跳过（单次网络抖动不抹掉整个 PCR 维度）。端点正常时探针结果直接
    # 复用（不再重复取 fetch_dates[-1]），净额外查询为 0。
    if fetch_dates:
        probe_df = _run_with_timeout(
            lambda: tc.query("opt_daily", trade_date=fetch_dates[-1], exchange="SSE"),
            8.0, f"opt_daily-probe:{fetch_dates[-1]}",
        )
        if probe_df is None:
            # 探针失败不整体丢弃：单次网络抖动/慢查询不应抹掉整个 PCR
            # 维度（旧实现单日失败仅跳过当日并带 stale/partial 标志）。
            # 重试一次，仍失败才整体降级。
            probe_df = _run_with_timeout(
                lambda: tc.query("opt_daily", trade_date=fetch_dates[-1], exchange="SSE"),
                8.0, f"opt_daily-probe2:{fetch_dates[-1]}",
            )
            if probe_df is None:
                logger.warning(
                    "opt_daily probe failed twice (%s); "
                    "skipping PCR fetch storm (%d dates)",
                    fetch_dates[-1], len(fetch_dates),
                )
                return None
        # 探针结果直接计入（不再重复取 fetch_dates[-1]）；当日无期权成交
        # 未命中代码集时留空，由主循环重取。
        probe_ratio = _ms_pcr_from_df(probe_df, put_set, call_set)
        if probe_ratio is not None:
            ratio_by_date[fetch_dates[-1]] = probe_ratio
        else:
            logger.debug("opt_daily probe %s: no usable PCR row", fetch_dates[-1])

    def _on_pcr_error(td: str, exc: Exception) -> None:
        logger.debug("opt_daily %s failed: %s", td, exc)

    # 全窗口并行取数（单次查询 8s 时限内部兜底；fan-out 样板共享
    # _base._map_parallel）：~123 次串行最坏 16 分钟
    remaining = [d for d in fetch_dates if d not in ratio_by_date]
    for td, r in _map_parallel(
        remaining,
        lambda td: _ms_pcr_on_date(tc, td, put_set, call_set),
        on_error=_on_pcr_error,
    ):
        if r is not None:
            ratio_by_date[td] = r
    if not ratio_by_date:
        return None
    # 单次扫描按 sampled 顺序构建 (date, ratio) 对（此前两次同谓词扫描
    # 生成 ratios 与 ratio_dates，alignment 靠"同一谓词"隐含保证）
    ratio_pairs = [(td, ratio_by_date[td]) for td in sampled if td in ratio_by_date]
    if not ratio_pairs:
        return None
    current = ratio_pairs[-1][1]
    current_date = ratio_pairs[-1][0]
    # 最新日查询失败 → current 静默回退到旧样本，需显式 staleness 标识
    # （ratio_pairs 非空 ⇒ current_date 必非 None，无需冗余守卫）
    stale = current_date != sampled[-1]
    ratios = [r for _, r in ratio_pairs]
    pct_5y = percentile_rank(ratios, current) if len(ratios) >= 5 else None
    ratios_60d = [ratio_by_date[td] for td in recent_dates if td in ratio_by_date]
    # current 在窗口内（current_date >= cutoff）才计算 60 日分位：最新 1-3 个
    # 采样日查询失败时 current 回退约 step×失败点数 天（降采样 step≈15），
    # 可能滑出 60 日窗口——此时 current 与窗口样本非同区间，分位无意义，
    # 置 None 而非渲染成"0.0% 低位"（与 stale/partial 标志并存，互不替代）
    pct_60d = (
        percentile_rank(ratios_60d, current)
        if len(ratios_60d) >= 5 and current_date >= cutoff else None
    )
    return {
        "ratio": round(current, 3),
        "percentile_5y": round(pct_5y, 1) if pct_5y is not None else None,
        "percentile_60d": round(pct_60d, 1) if pct_60d is not None else None,
        "current_date": current_date,
        "history_days": len(ratios),
        # 降采样是设计内的（5 年窗口按 _PCR_MAX_DAILY_QUERIES 均匀采样）：
        # partial 只在采样本身失败/缺失时置 True —— 实际取得的采样点数少于
        # 计划点数（len(ratios) < len(sampled)），或最新采样日失败致 current
        # 回退到旧样本（stale）。此前 raw_days > len(sampled) 在 5 年窗口
        # （~1220 交易日 vs 上限 80）下恒 True → partial 永久 true → 报告恒
        # 显示「历史样本不足」警告。
        "partial": len(ratios) < len(sampled) or stale,
        "sampled": raw_days > len(sampled),
        "sample_points": len(fetch_dates),
        "calendar_days": raw_days,
        "underlying": _50ETF_UNDERLYING,
        "source": "tushare.opt_daily",
    }


def _ms_fetch_short_margin_growth(tc: Any, symbol: str) -> dict | None:
    """融券余额增速（交易所 margin 优先，个股 margin_detail 回退）。"""
    from lib.stats import percentile_rank_mid

    # 增速序列约半数日为负：percentile_rank（v>0 过滤）把负增速日从分母剔除
    # → "5年最低位"系统性失真；percentile_rank_inclusive 对冻结序列给 100% 假
    # 信号 → 改用 mid-rank（count(<cur)/n + 0.5×count(==cur)/n）：冻结序列 50%

    df = tc.query("margin", start_date=_days_ago(1825), end_date=_today())
    if df is not None and not df.empty:
        by_date: dict[str, float] = {}
        for _, row in df.iterrows():
            td = str(row.get("trade_date") or "")
            rqye = row.get("rqye")
            if not td or rqye is None:
                continue
            by_date[td] = by_date.get(td, 0.0) + float(rqye)
        dates = sorted(by_date)
        if len(dates) >= 11:
            growths: list[float] = []
            for i in range(10, len(dates)):
                base, cur = by_date[dates[i - 10]], by_date[dates[i]]
                if base > 0:
                    growths.append((cur - base) / base * 100)
            if growths:
                current_g = growths[-1]
                pct = (percentile_rank_mid(growths, current_g)
                       if len(growths) >= 5 else None)
                return {
                    "growth_pct": round(current_g, 2),
                    "percentile_5y": round(pct, 1) if pct is not None else None,
                    "scope": "exchange",
                    "source": "tushare.margin",
                }
    margin = _ms_fetch_margin(tc, symbol)
    if margin and margin.get("rqye_change_pct") is not None:
        return {
            "growth_pct": margin["rqye_change_pct"],
            "scope": "stock",
            "source": margin.get("source", "tushare.margin_detail"),
        }
    return None


def _ms_new_high_ratio_from_panel(panel: dict[str, list[dict]]) -> float | None:
    if not panel:
        return None
    n_high = n_valid = 0
    for rows in panel.values():
        if len(rows) < 2:
            continue
        # 防御：跳过非 dict 行（此前 _map_parallel 双包装注入 str 行崩溃；
        # 与 schema._rows_newest_last 的非 dict 行剔除策略一致）
        dict_rows = [r for r in rows if isinstance(r, dict)]
        if len(dict_rows) < 2:
            continue
        closes = [safe_float(r.get("close")) for r in dict_rows]
        highs = [safe_float(r.get("high")) for r in dict_rows]
        closes = [c for c in closes if c is not None]
        highs = [h for h in highs if h is not None]
        if not closes or len(highs) < 2:
            continue
        n_valid += 1
        if closes[-1] >= max(highs[:-1]):
            n_high += 1
    if n_valid == 0:
        return None
    return n_high / n_valid * 100


def _ms_fetch_new_high_ratio(tc: Any) -> dict | None:
    """创新高个股占比（采样 daily，partial 标注）。"""
    from lib.stats import percentile_rank

    basic = tc.query("stock_basic", list_status="L", fields="ts_code")
    if basic is None or basic.empty:
        return None
    # 全市场种子随机抽样：此前取前 30 行恒为 000xxx SZ 小盘；等步长抽样
    # 头锚定 0 且 [:30] 截断使词序尾部（920xxx 北交所）永不入样。
    # 种子按当日日期（YYYYMMDD）播种：每日面板轮换（固定种子会恒抽同一
    # 批，停牌/退市样本永久缩小面板），同日内可复现。
    import random
    codes_all = sorted(str(c) for c in basic["ts_code"].tolist() if c is not None)
    if not codes_all:
        return None
    rng = random.Random(int(_today()))
    codes = rng.sample(codes_all, min(_NEW_HIGH_SAMPLE, len(codes_all)))
    if not codes:
        return None
    def _fetch_daily_panel_row(ts_code: str) -> list[dict] | None:
        # 单次 daily 查询加时限（与 _ms_pcr_on_date 同款 8s）：_map_parallel
        # 契约要求内部单次执行有超时兜底，否则挂起 socket 会拖住
        # with ThreadPoolExecutor 的 join，market_structure 整块阻塞数分钟
        # ⚠️ 只返回 records（不得返回 (ts_code, records) 元组）——_map_parallel
        # 本身返回 (item, result)，双重包装会使 panel 值为元组、rows[0] 为
        # 字符串 ts_code，_ms_new_high_ratio_from_panel 对其 .get() 直接
        # AttributeError（600206 实证：market_structure new_high_ratio fetch failed）
        df = _run_with_timeout(
            lambda: tc.query(
                "daily", ts_code=ts_code,
                start_date=_days_ago(70), end_date=_today(),
                fields="trade_date,close,high",
            ),
            8.0, f"daily:{ts_code}",
        )
        if df is None or df.empty:
            return None
        return df.sort_values("trade_date").to_dict("records")

    def _on_panel_error(ts_code: str, exc: Exception) -> None:
        logger.warning("new_high_ratio daily fetch failed for %s: %s", ts_code, exc)

    panel: dict[str, list[dict]] = {}
    for ts_code, records in _map_parallel(
        codes, _fetch_daily_panel_row, on_error=_on_panel_error):
        if records:
            panel[ts_code] = records
    current = _ms_new_high_ratio_from_panel(panel)
    if current is None:
        return None
    hist: list[float] = []
    if panel:
        min_len = min(len(v) for v in panel.values())
        # 排除全样本切片，避免 current 计入自身分位
        hist_end = min_len - 1 if min_len > 1 else 0
        for offset in range(max(0, min_len - 60), hist_end):
            slice_panel = {
                k: v[: offset + 1] for k, v in panel.items() if len(v) > offset
            }
            r = _ms_new_high_ratio_from_panel(slice_panel)
            if r is not None:
                hist.append(r)
    pct = percentile_rank(hist, current) if len(hist) >= 5 else None
    return {
        "ratio_pct": round(current, 2),
        "percentile_60d": round(pct, 1) if pct is not None else None,
        "sample_size": len(panel),
        "sample_requested": len(codes),
        "partial": len(panel) < _NEW_HIGH_SAMPLE,
        "source": "tushare.daily",
    }


def _ms_fetch_etf_flow(tc: Any) -> dict | None:
    """宽基 ETF（510300）份额变动估算资金流向。"""
    ts_code = _ETF_300_CODE
    df_share = tc.query(
        "fund_share", ts_code=ts_code,
        start_date=_days_ago(30), end_date=_today(),
    )
    df_price = tc.query(
        "fund_daily", ts_code=ts_code,
        start_date=_days_ago(30), end_date=_today(),
        fields="trade_date,close",
    )
    if df_share is None or df_share.empty:
        return None
    shares = df_share.sort_values("trade_date").to_dict("records")

    def _valid_fd_share(r: dict) -> bool:
        """fd_share 须为可解析的有限数值；None/空串/NaN/±inf 剔除。"""
        from math import isfinite
        v = r.get("fd_share")
        if v is None:
            return False
        try:
            f = float(v)
        except (TypeError, ValueError):
            return False
        return f == f and isfinite(f)  # NaN 不自等；inf 会污染 flow 估算

    # 保留全序列 + 有效行索引：窗口按「最后 N 个有效行」取，
    # 实际跨度（含被过滤的 NULL 行）如实标注，避免标 5d 实跨 8-9 日
    valid_idx = [i for i, r in enumerate(shares) if _valid_fd_share(r)]
    if not valid_idx:
        return None
    prices = {}
    if df_price is not None and not df_price.empty:
        for r in df_price.sort_values("trade_date").to_dict("records"):
            prices[str(r.get("trade_date", ""))] = safe_float(r.get("close"))

    def _net_flow(days: int) -> tuple[float, int] | None:
        if len(valid_idx) < days + 1:
            return None
        last_i, first_i = valid_idx[-1], valid_idx[-(days + 1)]
        first, last = shares[first_i], shares[last_i]
        d_shares = float(last.get("fd_share")) - float(first.get("fd_share"))
        span = last_i - first_i + 1  # 实际覆盖的交易日行数（含被过滤的 NULL 行）
        px = prices.get(str(last.get("trade_date", "")))
        if px is None or px <= 0:
            return None
        return d_shares * ONE_PER_WAN * px, span  # fd_share 单位：万份

    flow_5d = _net_flow(5)
    flow_10d = _net_flow(10)
    if flow_5d is None and flow_10d is None:
        return None
    out: dict[str, Any] = {
        "ts_code": ts_code,
        "source": "tushare.fund_share+fund_daily",
    }
    if not prices:
        out["price_incomplete"] = True
    if flow_5d is not None:
        out["net_flow_5d"] = round(flow_5d[0], 0)
        if flow_5d[1] > 5 + 1:
            out["net_flow_5d_span_rows"] = flow_5d[1]
    if flow_10d is not None:
        out["net_flow_10d"] = round(flow_10d[0], 0)
        if flow_10d[1] > 10 + 1:
            out["net_flow_10d_span_rows"] = flow_10d[1]
    return out


def extract_industry_from_basic_info(data: dict | None) -> str | None:
    """从 basic_info 主数据提取行业名（兼容 Tushare / akshare 字段）。"""
    if not data or not isinstance(data, dict):
        return None
    for key in ("industry", "行业", "所属行业"):
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def extract_industry_from_collection(collection: dict) -> str | None:
    """从 collection 维度列表提取行业名。"""
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == "basic_info":
            return extract_industry_from_basic_info(dim.get("data"))
    return None


def attach_market_structure(collection: dict, symbol: str) -> dict:
    """采集市场结构并写入 collection['market_structure']。"""
    industry = extract_industry_from_collection(collection)
    collection["market_structure"] = collect_market_structure(symbol, industry=industry)
    return collection["market_structure"]


def attach_industry_peers(collection: dict, symbol: str) -> dict[str, Any]:
    """采集同行可比数据并写入 collection['industry_peers']。"""
    industry = extract_industry_from_collection(collection)
    collection["industry_peers"] = collect_industry_peers(symbol, industry=industry)
    return collection["industry_peers"]


def attach_pe_band(collection: dict, *, years: int = 5) -> dict[str, Any] | None:
    """计算 PE Band 数据层并写入 collection['pe_band']（供 Phase 4 消费）。"""
    from lib.valuation import pe_band_series

    val_rows: list[dict] = []
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == "valuation":
            data = dim.get("data")
            if isinstance(data, list):
                val_rows = data
            break
    if not val_rows:
        collection["pe_band"] = None
        return None
    band = pe_band_series(val_rows, years=years)
    collection["pe_band"] = band
    return band


# P3-3: 价格异常检测原型（预留 v0.1.9 news 全栈使用）


def _extract_kline_from_collection(collection: dict) -> list:
    """从 collect_all 结果中提取 kline 维度数据列表。"""
    for dim in collection.get("dimensions", []):
        if dim.get("dimension") == "kline":
            data = dim.get("data")
            if isinstance(data, list):
                return data
    return []


def _bar_pct_chg(bar: dict, prev_bar: dict | None) -> float | None:
    """从 pct_chg 字段或相邻 close 计算涨跌幅（%）。"""
    raw = bar.get("pct_chg")
    if raw is not None:
        pct = safe_float(raw)
        if pct is not None:
            return pct
    if prev_bar is None:
        return None
    close = safe_float(bar.get("close"))
    prev_close = safe_float(prev_bar.get("close"))
    if close is not None and prev_close is not None and prev_close != 0:
        return (close - prev_close) / prev_close * 100
    return None


def _detect_price_shock(symbol: str, kline_data: list) -> dict:
    """价格异常检测原型。

    当前仅检测极端涨跌停，不做新闻关联。
    v0.1.9 将扩展为：价格异常 + 新闻关联 + 事件归因。

    Returns:
        {"has_shock": bool, "shock_dates": [...], "shock_type": str|None}
    """
    if not kline_data:
        return {"has_shock": False, "shock_dates": []}

    # 显式升序：tushare.daily 主源返回降序（无 pct_chg 字段，靠相邻 close 计算），
    # 不排序会把 +10% 涨停算成 −10% 跌停（_bar_pct_chg 依赖相邻顺序）
    from lib.technical import sort_kline_asc
    sorted_bars = sort_kline_asc(kline_data)
    bars = sorted_bars[-61:] if len(sorted_bars) > 1 else sorted_bars
    shocks = []
    for i in range(1, len(bars)):
        bar = bars[i]
        pct = _bar_pct_chg(bar, bars[i - 1])
        if pct is None:
            continue
        if abs(pct) >= 9.5:  # A股涨跌停阈值
            shocks.append({
                "date": bar.get("trade_date"),
                "pct_chg": round(pct, 2),
                "type": "limit_up" if pct > 0 else "limit_down",
            })

    def _classify(s: list) -> str | None:
        if not s:
            return None
        up = sum(1 for x in s if x["type"] == "limit_up")
        dn = len(s) - up
        if up >= 2 and dn == 0:
            return "连续涨停"
        elif dn >= 2 and up == 0:
            return "连续跌停"
        return "混合异常"

    return {
        "has_shock": len(shocks) > 0,
        "shock_dates": shocks,
        "shock_type": _classify(shocks),
    }


def attach_phase2_extras(collection: dict, symbol: str) -> None:
    """挂载 Phase 2 扩展数据（同行、PE Band）。"""
    errors: list[str] = []
    peers_existing = collection.get("industry_peers")
    if not peers_existing or peers_existing.get("error"):
        try:
            attach_industry_peers(collection, symbol)
        except Exception as exc:
            errors.append(f"industry_peers: {exc}")
            collection["industry_peers"] = {
                "peers": [],
                "target": None,
                "rankings": {},
                "industry_name": None,
                "sufficient": False,
                "error": f"同行采集异常: {exc}",
            }
    if collection.get("pe_band") is None:
        try:
            attach_pe_band(collection)
        except Exception as exc:
            errors.append(f"pe_band: {exc}")
            collection["pe_band"] = None

    # P1-2: industry_pricing — 依赖 basic_info 已采集的行业信息
    if collection.get("industry_pricing") is None:
        try:
            industry = extract_industry_from_collection(collection) or ""
            collection["industry_pricing"] = collect_industry_pricing(symbol, industry)
        except Exception as exc:
            errors.append(f"industry_pricing: {exc}")
            collection["industry_pricing"] = {
                "dimension": "industry_pricing", "data": None,
                "status": "missing", "error": f"行业定价采集异常: {exc}",
            }

    # P3-3: 价格异常检测（非阻塞）
    if collection.get("price_shock") is None:
        try:
            kline_bars = _extract_kline_from_collection(collection)
            collection["price_shock"] = _detect_price_shock(symbol, kline_bars)
        except Exception as exc:
            errors.append(f"price_shock: {exc}")
            collection["price_shock"] = {
                "has_shock": False, "shock_dates": [], "error": str(exc),
            }

    if errors:
        collection.setdefault("phase2_extras_errors", []).extend(errors)
        logger.warning("attach_phase2_extras partial failure for %s: %s", symbol, errors)


def _ms_try_fetch(
    result: dict[str, Any],
    key: str,
    fetch_fn: Callable[[], Any],
    *,
    unavailable_msg: str,
    on_success: Callable[[Any], str] | None = None,
) -> None:
    """采集单个子源并写入 result / availability（统一 try/except 模式）。"""
    try:
        value = fetch_fn()
        result[key] = value
        if value is None:
            _ms_set_unavailable(result["availability"], key, unavailable_msg)
        elif on_success is not None:
            result["availability"][key] = on_success(value)
        else:
            result["availability"][key] = "available"
    except Exception as exc:
        # 异常仅进日志；availability 用静态描述（str(exc) 会泄漏底层
        # Python 异常文本到报告「不可得：{reason}」渲染，用户不可读，
        # 且违反 R12h「不可得 + attempted sources」标注规范）
        logger.warning("market_structure %s fetch failed: %s", key, exc)
        _ms_set_unavailable(result["availability"], key, unavailable_msg)


def collect_market_structure(symbol: str, *, industry: str | None = None) -> dict:
    """采集市场结构因子（行业情绪/资金/ERP/换手）。各子源独立降级。"""
    result: dict[str, Any] = {
        "sw_index": None,
        "northbound": None,
        "margin": None,
        "moneyflow": None,
        "turnover": None,
        "erp": None,
        "pmi": None,
        "put_call_ratio": None,
        "short_margin": None,
        "new_high_ratio": None,
        "etf_flow": None,
        "availability": {},
    }
    config = env.get_config()
    _ms_keys = (
        "sw_index", "northbound", "margin", "moneyflow", "turnover", "erp",
        "put_call_ratio", "short_margin", "new_high_ratio", "etf_flow",
    )
    if not env.is_tushare_available(config):
        for key in _ms_keys:
            _ms_set_unavailable(result["availability"], key, "TUSHARE_TOKEN not configured")
        return result

    tc = _tushare_client(config)

    _ms_try_fetch(
        result, "sw_index",
        lambda: _ms_fetch_sw_index(tc, symbol, industry),
        unavailable_msg=(
            "申万行业指数不可得；Tushare sw_daily 需 5000 积分"
            "（https://tushare.pro/document/2?doc_id=327），"
            "2000 分档已尝试 akshare 回退"
        ),
        on_success=_ms_sw_index_availability_label,
    )
    _ms_try_fetch(
        result, "northbound",
        lambda: _ms_fetch_northbound_stock(tc, symbol),
        unavailable_msg="hsgt_top10 empty (not in top10) or akshare northbound unavailable",
    )
    _ms_try_fetch(
        result, "margin",
        lambda: _ms_fetch_margin(tc, symbol),
        unavailable_msg="margin_detail empty, insufficient history, or permission denied",
    )
    _ms_try_fetch(
        result, "moneyflow",
        lambda: _ms_fetch_moneyflow(tc, symbol),
        unavailable_msg="moneyflow empty or permission denied",
    )
    _ms_try_fetch(
        result, "turnover",
        lambda: _ms_fetch_turnover(tc, symbol),
        unavailable_msg="daily_basic turnover empty",
    )
    _ms_try_fetch(
        result, "erp",
        lambda: _ms_fetch_erp(tc, config),
        unavailable_msg="index_dailybasic or 10Y yield (FRED DGS10 / akshare CN10Y) unavailable",
        on_success=lambda v: (
            f"partial: {v.get('erp_days', 0)} aligned days (min {_ERP_MIN_ALIGNED_DAYS})"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "pmi",
        _ms_fetch_pmi,
        unavailable_msg="akshare macro_china_pmi unavailable",
    )
    _ms_try_fetch(
        result, "put_call_ratio",
        lambda: _ms_fetch_put_call_ratio(tc),
        unavailable_msg="opt_daily empty, no 50ETF options, or permission denied (5000 pts)",
        on_success=lambda v: (
            f"partial: {v.get('history_days', 0)} days"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "short_margin",
        lambda: _ms_fetch_short_margin_growth(tc, symbol),
        unavailable_msg="margin / margin_detail rqye empty or permission denied",
    )
    _ms_try_fetch(
        result, "new_high_ratio",
        lambda: _ms_fetch_new_high_ratio(tc),
        unavailable_msg="daily sample empty or insufficient",
        on_success=lambda v: (
            f"partial: sample {v.get('sample_size', 0)}/{_NEW_HIGH_SAMPLE}"
            if v.get("partial") else "available"
        ),
    )
    _ms_try_fetch(
        result, "etf_flow",
        lambda: _ms_fetch_etf_flow(tc),
        unavailable_msg="fund_share / fund_daily empty or permission denied",
        on_success=lambda v: (
            "partial: fund_daily close missing for flow estimate"
            if v.get("price_incomplete") else "available"
        ),
    )

    return result


# ---- 行业同行采集（v0.1.3 Phase 2） ----

# 同行分位：数值越高越好的指标（rank=1 表示最高）
_PEER_HIGHER_IS_BETTER = frozenset({"revenue_yoy", "roe"})


def _prior_year_end_date(end_date: str) -> str:
    """报告期 → 去年同期（同月同日，YYYYMMDD）。"""
    from lib.financials import prior_year_end_date
    return prior_year_end_date(end_date)


def _revenue_yoy_from_fina_rows(rows: list[dict]) -> float | None:
    """按 end_date 对齐去年同期营收，计算同比增速（%）。"""
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda r: str(r.get("end_date", "")))
    latest = sorted_rows[-1]
    rev_cur = safe_float(latest.get("revenue"))
    if rev_cur is None or rev_cur <= 0:
        return None
    prev_ed = _prior_year_end_date(str(latest.get("end_date", "")))
    if not prev_ed:
        return None
    from lib.financials import normalize_end_date
    rev_prev = None
    for r in reversed(sorted_rows[:-1]):
        if normalize_end_date(str(r.get("end_date", ""))) == prev_ed:
            rev_prev = safe_float(r.get("revenue"))
            break
    if rev_prev is None or rev_prev <= 0:
        return None
    return round((rev_cur - rev_prev) / rev_prev * 100, 2)


def _gross_margin_trend_from_rows(fin_rows: list[dict]) -> str | None:
    """近 2 个会计年度毛利率方向（供竞争加剧信号）。"""
    from lib.financials import gross_margin_trend_from_rows
    return gross_margin_trend_from_rows(fin_rows)


def _peer_metrics_from_fina(
    fin_rows: list[dict], fin_row: dict | None,
) -> dict[str, Any]:
    """从 fina_indicator 行提取同行对比 / 风险扫描字段。"""
    out: dict[str, Any] = {}
    if fin_row:
        out["roe"] = safe_float(fin_row.get("roe"))
        out["revenue_yoy"] = _revenue_yoy_from_fina_rows(fin_rows)
        gm = safe_float(fin_row.get("grossprofit_margin"))
        if gm is not None:
            out["grossprofit_margin"] = gm
            out["gross_margin"] = gm
        debt = safe_float(fin_row.get("debt_to_assets"))
        if debt is not None:
            out["debt_to_assets"] = debt
    trend = _gross_margin_trend_from_rows(fin_rows)
    if trend is not None:
        out["gross_margin_trend"] = trend
    return out


def _fetch_peer_fina_rows(tc: Any, code: str) -> list[dict]:
    """拉取近 2.5 年 fina_indicator，供 ROE / 毛利率 / 负债率等使用。"""
    fin_df = tc.query(
        "fina_indicator",
        ts_code=code,
        fields="ts_code,end_date,roe,revenue,grossprofit_margin,debt_to_assets",
        start_date=_days_ago(950),
        end_date=_today(),
    )
    if fin_df is None or fin_df.empty:
        return []
    return fin_df.sort_values("end_date").to_dict("records")


def collect_industry_peers(
    symbol: str,
    *,
    industry: str | None = None,
    max_peers: int = 10,
) -> dict[str, Any]:
    """申万行业同行池，PE/PB/ROE/营收增速分位排名。

    1. 查申万行业成分股（L3→L2→L1）
    2. 获取每只成分股的 PE(TTM)、PB、ROE、近一年营收增速
    3. 计算 target 在同行中的分位排名
    4. 返回可比公司表（上限 max_peers 家）
    """
    result: dict[str, Any] = {
        "peers": [],
        "target": None,
        "rankings": {},
        "industry_name": None,
        "peer_source": None,
        "sufficient": False,
    }

    config = env.get_config()
    if not env.is_tushare_available(config):
        result["error"] = "Tushare Token 不可用，无法采集同行数据"
        return result

    tc = _tushare_client(config)
    target_sym = _ts_code(symbol)

    industry = _resolve_sw_industry_name(tc, symbol, industry)
    if not industry:
        result["error"] = "无法确定行业分类"
        return result

    result["industry_name"] = industry

    index_code = _ms_lookup_sw_index_code(tc, industry)
    members: list[dict] = []
    name_by_code: dict[str, str] = {}
    peer_source: str | None = None

    if index_code:
        try:
            member_df = tc.query("index_member", index_code=index_code)
            if member_df is not None and not member_df.empty:
                members = member_df.to_dict("records")
                peer_source = "sw_index_member"
                for m in members:
                    code = str(m.get("ts_code", "")).strip()
                    if code:
                        name_by_code[code] = str(m.get("name", "")).strip()
        except Exception as exc:
            logger.debug("index_member failed for %s: %s", index_code, exc)

    if not members:
        basic_all = tc.query("stock_basic", fields="ts_code,name,industry")
        if basic_all is not None and not basic_all.empty:
            for _, row in basic_all.iterrows():
                if str(row.get("industry", "")).strip() == industry:
                    rec = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                    members.append(rec)
                    code = str(rec.get("ts_code", "")).strip()
                    if code:
                        name_by_code[code] = str(rec.get("name", "")).strip()
        if members:
            peer_source = "stock_basic_fallback"
            result["warning"] = (
                "同行池来自 Tushare stock_basic.industry 粗分类，非申万 L3 成分股；"
                "分位排名与可比公司表已降级，仅供参考。"
            )

    if not members:
        result["error"] = f"未找到「{industry}」行业成分股"
        return result

    result["peer_source"] = peer_source

    all_codes = sorted({str(m.get("ts_code", "")).strip() for m in members if m.get("ts_code")})
    if target_sym not in all_codes:
        all_codes.append(target_sym)
        basic_one = tc.query("stock_basic", ts_code=target_sym, fields="ts_code,name")
        if basic_one is not None and not basic_one.empty:
            name_by_code[target_sym] = str(basic_one.iloc[0].get("name", "")).strip()

    other_codes = sorted(c for c in all_codes if c != target_sym)[:max_peers]
    peer_codes = [target_sym, *other_codes]

    target_metrics: dict[str, Any] | None = None
    peers_metrics: list[dict[str, Any]] = []

    for code in peer_codes:
        try:
            fin_rows = _fetch_peer_fina_rows(tc, code)
            fin_row = fin_rows[-1] if fin_rows else None
            val_df = tc.query("daily_basic", ts_code=code,
                              fields="ts_code,pe_ttm,pb,total_mv",
                              start_date=_days_ago(30), end_date=_today(),
                              limit=1)

            val_row = val_df.iloc[-1].to_dict() if val_df is not None and not val_df.empty else None

            peer_entry: dict[str, Any] = {
                "symbol": code.split(".")[0] if "." in code else code,
                "name": name_by_code.get(code, ""),
                "pe_ttm": None,
                "pb": None,
                "roe": None,
                "revenue_yoy": None,
                "total_mv": None,
            }
            if fin_row:
                peer_entry.update(_peer_metrics_from_fina(fin_rows, fin_row))
            if val_row:
                pe_v = safe_float(val_row.get("pe_ttm"))
                if pe_v is not None and pe_v > 0:
                    peer_entry["pe_ttm"] = pe_v
                peer_entry["pb"] = safe_float(val_row.get("pb"))
                peer_entry["total_mv"] = safe_float(val_row.get("total_mv"))

            if code == target_sym:
                target_metrics = peer_entry
            else:
                peers_metrics.append(peer_entry)

        except Exception as exc:
            logger.debug("collect_industry_peers: skip %s: %s", code, exc)
            continue

    peers_metrics.sort(
        key=lambda p: (p.get("total_mv") is None, -(p.get("total_mv") or 0)),
    )

    if target_metrics:
        result["target"] = target_metrics

    result["peers"] = peers_metrics
    result["sufficient"] = (
        peer_source == "sw_index_member" and len(peers_metrics) >= 3
    )

    if target_metrics and len(peers_metrics) >= 1:
        rankings: dict[str, Any] = {}
        for metric in ("pe_ttm", "pb", "roe", "revenue_yoy"):
            tv = target_metrics.get(metric)
            pv = [p.get(metric) for p in peers_metrics if p.get(metric) is not None]
            if tv is not None and pv:
                below = sum(1 for v in pv if v < tv)
                above = sum(1 for v in pv if v > tv)
                pct = round(below / len(pv) * 100, 1)
                rankings[f"{metric}_pct"] = pct
                if metric in _PEER_HIGHER_IS_BETTER:
                    rankings[f"{metric}_rank"] = above + 1
                else:
                    rankings[f"{metric}_rank"] = below + 1
                rankings[f"{metric}_total"] = len(pv) + 1
            else:
                rankings[f"{metric}_pct"] = None
                rankings[f"{metric}_rank"] = None
                rankings[f"{metric}_total"] = None
        result["rankings"] = rankings

    return result


# ---- 行业横向对比（v0.1.6 CLI peer 命令） ----

_SORT_FIELD_MAP = {
    "market_cap": "total_mv",
    "revenue": "revenue_yoy",
    "roe": "roe",
}

_SOURCE_LABEL_MAP: dict[str, str] = {
    "sw_index_member": "tushare_5000",
    "stock_basic_fallback": "tushare_2000",
}


def _safe_peer_num(v) -> float | None:
    """Convert to float, filtering NaN and infinity."""
    return safe_float(v)


def _collect_peers_akshare(symbol: str, top_n: int, sort_by: str) -> dict:
    """akshare 回退方案：使用东方财富行业板块成分股进行行业横向对比。"""
    import akshare as ak  # noqa: F811

    # 1. 获取基本信息和行业分类
    info = _q_akshare_basic(symbol)
    if not info:
        raise RuntimeError("无法获取股票基本信息（akshare）")

    industry_name = (info.get("行业") or info.get("industry") or "").strip()
    if not industry_name:
        raise RuntimeError("无法确定行业分类（akshare）")

    # 2. 匹配东方财富行业板块名称
    with akshare_direct_session():
        try:
            boards = ak.stock_board_industry_name_em()
        except Exception as exc:
            raise RuntimeError(f"获取行业板块列表失败: {exc}") from exc

    if boards is None or boards.empty:
        raise RuntimeError("东方财富行业板块列表为空")

    matched_board: str | None = None
    for _, row in boards.iterrows():
        name = str(row.get("板块名称", ""))
        if name and (industry_name in name or name in industry_name):
            matched_board = name
            break

    if not matched_board:
        raise RuntimeError(
            f"未在东方财富板块列表中找到匹配行业: {industry_name}")

    # 3. 获取板块成分股列表
    with akshare_direct_session():
        try:
            cons = ak.stock_board_industry_cons_em(symbol=matched_board)
        except Exception as exc:
            raise RuntimeError(
                f"获取行业板块成分股失败 '{matched_board}': {exc}") from exc

    if cons is None or cons.empty:
        raise RuntimeError(f"行业板块无成分股: {matched_board}")

    industry_codes = set(cons["代码"].astype(str).str.zfill(6).tolist())

    # 4. 获取全市场实时快照数据
    with akshare_direct_session():
        try:
            spot = ak.stock_zh_a_spot_em()
        except Exception as exc:
            raise RuntimeError(f"获取实时行情快照失败: {exc}") from exc

    if spot is None or spot.empty:
        raise RuntimeError("实时行情快照为空")

    # 5. 按行业过滤，构建同行列表
    target_code = symbol.zfill(6)
    peers: list[dict] = []
    target_entry: dict | None = None

    for _, row in spot.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if code not in industry_codes:
            continue

        pe = _safe_peer_num(row.get("市盈率-动态"))
        if pe is not None and pe <= 0:
            pe = None  # 负 PE 无意义

        entry = {
            "symbol": code,
            "name": str(row.get("名称", "")),
            "pe_ttm": pe,
            "pb": _safe_peer_num(row.get("市净率")),
            "total_mv": _safe_peer_num(row.get("总市值")),
            "revenue_yoy": _safe_peer_num(row.get("营业收入同比增长率")),
            "roe": None,  # 快照数据不含 ROE
        }

        # Normalize total_mv: akshare spot data returns 元 → 亿元
        mv_raw = entry.get("total_mv")
        if mv_raw is not None:
            entry["total_mv"] = mv_raw / ONE_PER_YI

        if code == target_code:
            target_entry = entry
        else:
            peers.append(entry)

    if not target_entry:
        raise RuntimeError(
            f"标的 {symbol} 未在东方财富行业板块成分股中")

    # 6. 按指定字段排序并截取 top N
    sf = _SORT_FIELD_MAP.get(sort_by, "total_mv")
    peers.sort(key=lambda p: (p.get(sf) is None, -(p.get(sf) or 0)))
    peers = peers[:top_n]

    return {
        "peers": peers,
        "target": target_entry,
        "rankings": {},
        "industry_name": industry_name,
        "peer_source": "akshare_fallback",
        "sufficient": len(peers) >= 3,
    }


def collect_peer_comparison(
    symbol: str,
    top_n: int = 10,
    sort_by: str = "market_cap",
) -> dict:
    """行业横向对比：采集同行公司估值与财务对比数据。

    优先使用 Tushare 申万行业成分股（collect_industry_peers，需 2000+ 积分），
    失败时降级至 akshare 东方财富行业板块成分股。

    Args:
        symbol: 股票代码（如 "600176"）
        top_n: 目标对比公司数量（默认 10，不含标的自身）
        sort_by: 排序依据，可选 market_cap | revenue | roe

    Returns:
        dict with keys:
          - peers: list[dict] 同行公司数据
          - target: dict | None 标的自身数据
          - peer_source: str 数据源等级标签
          - industry_name: str | None
          - sort_by: str
          - top_n: int
          - error: str (仅完全失败时)
    """
    config = env.get_config()

    # 优先 Tushare 申万路径
    if env.is_tushare_available(config):
        try:
            ts_result = collect_industry_peers(symbol, max_peers=top_n + 1)
            if not ts_result.get("error"):
                peers = list(ts_result.get("peers", []))
                target = ts_result.get("target")

                src = ts_result.get("peer_source", "")
                source_label = _SOURCE_LABEL_MAP.get(src, src)

                sf = _SORT_FIELD_MAP.get(sort_by, "total_mv")
                peers.sort(key=lambda p: (
                    p.get(sf) is None, -(p.get(sf) or 0)))
                peers = peers[:top_n]

                # Normalize total_mv: Tushare daily_basic returns 万元 → 亿元
                for entry in ([target] if target else []) + peers:
                    mv = entry.get("total_mv")
                    if mv is not None:
                        entry["total_mv"] = mv / WAN_PER_YI

                return {
                    "symbol": symbol,
                    "peers": peers,
                    "target": target,
                    "rankings": ts_result.get("rankings", {}),
                    "peer_source": source_label,
                    "sort_by": sort_by,
                    "top_n": top_n,
                    "industry_name": ts_result.get("industry_name"),
                    "sufficient": ts_result.get("sufficient", False),
                }
        except Exception as exc:
            logger.debug("collect_peer_comparison tushare failed: %s", exc)

    # akshare 回退
    try:
        result = _collect_peers_akshare(symbol, top_n, sort_by)
        result["symbol"] = symbol
        result["sort_by"] = sort_by
        result["top_n"] = top_n
        return result
    except Exception as exc:
        logger.debug("collect_peer_comparison akshare failed: %s", exc)

    return {
        "symbol": symbol,
        "peers": [],
        "target": None,
        "rankings": {},
        "peer_source": "",
        "sort_by": sort_by,
        "top_n": top_n,
        "industry_name": None,
        "error": (
            "Tushare 与 akshare 均无法获取行业同行数据。"
            "请运行 `invest.py diagnose` 检查数据源可用性。"
        ),
    }
