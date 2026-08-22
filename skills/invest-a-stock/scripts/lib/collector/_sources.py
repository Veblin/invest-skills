"""Data source query functions — Tushare, akshare, baostock, TickFlow, Tencent."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import redirect_stdout  # :942 tickflow 输出抑制（code-review #9：直接 import，不再经 _base dir()-copy）
from datetime import datetime
from io import StringIO
from typing import Any, Callable

from .. import env
from ..nums import safe_float
from ..proxy import (
    akshare_direct_session,
    akshare_push2_available,
    em_request_with_retry,
    no_proxy_session,
)
from ..shared_codes import exchange_code as _exchange_code
from ..shared_dates import (
    shanghai_days_ago as _days_ago,
    shanghai_today as _today,
    yyyymmdd_to_iso as _to_iso_date,
)
from ._base import (
    _BAOSTOCK_LOCK,
    _baostock_code,
    _is_eastmoney_blocked_error,
    _latest_quarter_end,
    _proxy_bypass,
    _reraise_eastmoney_api_error,
    _ts_code,
)

# v0.2.7：腾讯行情唯一实现收敛至 skills/lib/quote_tencent（本文件旧副本删除），
# 本层只保留返回契约适配。
from lib._invest_path import ensure_skills_lib_on_path  # noqa: E402
ensure_skills_lib_on_path()  # noqa: E402
from lib.nums import ONE_PER_WAN, ONE_PER_YI  # noqa: E402
from quote_tencent import (  # noqa: E402
    fetch_tencent_quote,
    is_tencent_unsupported,
    tencent_market,
)


logger = logging.getLogger(__name__)

# ---- Tushare 客户端惰性加载 ----
# 使用 threading.local() 避免多线程共享同一个 requests.Session
# （requests.Session 不是线程安全的，且 TushareClient 内部维护配额计数无锁保护）

_tc_local = threading.local()


def _tushare_client(config: dict) -> Any:
    """按线程惰性加载 TushareClient，配置变化时重建实例。"""
    token = config.get("TUSHARE_TOKEN")
    if not hasattr(_tc_local, "instance") or getattr(_tc_local, "_tc_token", None) != token:
        from lib.tushare_client import TushareClient
        _tc_local.instance = TushareClient(token=token)
        _tc_local._tc_token = token
    return _tc_local.instance


# ---- run 级覆盖缓存（kline/quote 跨维度去重） ----
# quote 维度（近实时 10 日）与 kline 维度（400 日）同 run 跨线程并行拉取同一
# 接口，10 日窗口 ⊂ 400 日窗口——按 (source, symbol) 存最宽窗口的 raw 行，
# 命中（cached 覆盖请求窗口）时切片返回，最坏每标的省 2 次 tushare + 1 次
# akshare HTTP 调用（冷缓存下 quote/kline 并行同发时两者都会 fetch、无节省，
# 仅较宽窗口落地后同一进程后续同参调用复用）。按日失效（_today 上海口径），
# 仿 _orchestrate._cninfo_hold_cache 模式：dict + day key + 线程锁（跨维度
# 并行采集）+ None 不落缓存。
# 已知边界：长驻进程（webapp）同日多次采集时，缓存为当日首个 fetch 的快照，
# 开盘后当日 bar 出现不会自动追新（quote 有意近实时，本缓存仅去重不追新）；
# CLI 单进程单报告不受影响。
# 与磁盘 _kline_cache 的职责边界：本缓存管「同 run 内存去重」（quote 有意
# 不进磁盘缓存）；_kline_cache 管「跨 run 落盘复用」。互不干扰。
# INVEST_QUOTE_CACHE=0 禁用（逃生口，对齐 INVEST_KLINE_CACHE=0 命名）。
_run_kline_quote_cache: dict[tuple[str, str], tuple[str, str, Any]] = {}
_run_kline_quote_cache_day: str = ""
_run_kline_quote_cache_lock = threading.Lock()


def _date8(d: Any) -> str:
    """日期归一化为 8 位 YYYYMMDD（tushare 8 位 / akshare YYYY-MM-DD）。

    兼容斜杠（YYYY/MM/DD）、Timestamp 字符串（YYYY-MM-DD HH:MM:SS）、
    float-str（20260813.0）：只去横杠会把 8 位边界行词法比较排除（code-review）。
    """
    s = str(d or "").strip()
    if not s:
        return s
    return s.replace("-", "").replace("/", "").split(" ")[0].split("T")[0][:8]


def _slice_cached_rows(rows: Any, req_sd: str, req_ed: str) -> Any:
    """按窗口切片并返回新对象（list[dict] / dict 两种形态），不 mutate 缓存。"""
    sd8, ed8 = _date8(req_sd), _date8(req_ed)
    if isinstance(rows, dict):
        return {k: v for k, v in rows.items() if sd8 <= _date8(k) <= ed8}
    return [dict(r) for r in rows if sd8 <= _date8(r.get("trade_date", "")) <= ed8]


def _copy_cached_rows(rows: Any) -> Any:
    """副本（list[dict] / dict）：调用方 mutate 不污染缓存对象。"""
    if isinstance(rows, dict):
        return dict(rows)
    return [dict(r) for r in rows]


def _run_kline_quote_cached(source: str, symbol: str, req_sd: str, req_ed: str,
                            fetch: Callable[[], Any],
                            *, require_ed_equal: bool = False) -> Any:
    """覆盖缓存读取：cached 窗口覆盖请求窗口 → 切片；否则锁外 fetch，更宽窗口替换。

    require_ed_equal=True（akshare qfq 路径）：qfq 以窗口最新日锚定，仅当缓存
    与请求结束日相同才可切片（否则锚定日不同、数值不可比）。
    """
    global _run_kline_quote_cache_day
    if os.environ.get("INVEST_QUOTE_CACHE", "1") == "0":
        return fetch()
    day = _today()
    key = (source, symbol)
    with _run_kline_quote_cache_lock:
        if _run_kline_quote_cache_day != day:
            _run_kline_quote_cache.clear()
            _run_kline_quote_cache_day = day
        cached = _run_kline_quote_cache.get(key)
    if cached is not None:
        c_sd, c_ed, rows = cached
        if (_date8(c_sd) <= _date8(req_sd) and _date8(c_ed) >= _date8(req_ed)
                and (not require_ed_equal or _date8(c_ed) == _date8(req_ed))):
            sliced = _slice_cached_rows(rows, req_sd, req_ed)
            if sliced:
                return sliced
            return None  # 0 行切片 → None（对齐直连空窗口语义，不判成功）
    rows = fetch()
    if not rows:
        return rows  # None/空不落缓存（对齐 _cninfo_hold_cache）
    with _run_kline_quote_cache_lock:
        cur = _run_kline_quote_cache.get(key)
        if cur is None:
            _run_kline_quote_cache[key] = (req_sd, req_ed, rows)
        else:
            # 仅「超集」替换：任一边更宽就替换会驱逐另一侧更宽的缓存
            # （历史窗口丢掉含最新日的覆盖 → 宽窗口又得重拉，code-review）
            covers = (_date8(req_sd) <= _date8(cur[0])
                      and _date8(req_ed) >= _date8(cur[1]))
            wider = (_date8(req_sd) < _date8(cur[0])
                     or _date8(req_ed) > _date8(cur[1]))
            if covers and wider:
                _run_kline_quote_cache[key] = (req_sd, req_ed, rows)
    return _copy_cached_rows(rows)  # 副本：调用方 mutate 不污染缓存对象


# ---- 单个源查询函数 ----


def _require_tushare():
    """Tushare 鉴权 + 客户端惰性加载（复用 10 处 _q_tushare_* 的样板代码）。

    Returns:
        (config, TushareClient) — 包含配额计数与线程安全包装。
    Raises:
        RuntimeError: TUSHARE_TOKEN 未配置或不可用。
    """
    from .. import env as _env
    config = _env.get_config()
    if not _env.is_tushare_available(config):
        raise RuntimeError("TUSHARE_TOKEN not configured")
    return config, _tushare_client(config)


def _q_tushare_basic(symbol: str) -> dict | None:
    """Tushare 基本信息来源。"""
    config, tc = _require_tushare()
    df = tc.query("stock_basic", ts_code=_ts_code(symbol),
                  fields="ts_code,name,area,industry,market,list_date")
    if df is not None and not df.empty:
        return df.iloc[0].to_dict()
    return None


def _merge_cashflow_into_financials(
    financials: list[dict], cashflow: list[dict],
) -> list[dict]:
    """按 end_date 合并现金流字段（OCF/CapEx），供 DCF/CV-1。"""
    cf_by_date = {str(r.get("end_date", "")): r for r in cashflow}
    out: list[dict] = []
    for row in financials:
        merged = dict(row)
        cf = cf_by_date.get(str(row.get("end_date", "")))
        if cf:
            ncf = cf.get("n_cashflow_act")
            if ncf is not None:
                merged["n_cashflow_act"] = ncf
                merged["ocf"] = ncf
            # P0-1: c_pay_acq_const_fiolta → cap_ex（Tushare 实测仅此 Capex 字段可用）
            capex = cf.get("c_pay_acq_const_fiolta")
            if capex is not None:
                merged["cap_ex"] = capex
        out.append(merged)
    return out


def _merge_income_into_financials(
    financials: list[dict], income: list[dict],
) -> list[dict]:
    """按 end_date 合并利润表明细（EBIT/EBITDA/费用/税金），供 DCF 估值。"""
    inc_by_date = {str(r.get("end_date", "")): r for r in income}
    _income_passthrough = (
        "ebit", "ebitda", "fin_exp", "income_tax",
        "sell_exp", "admin_exp", "invest_income",
        "total_profit", "n_income_attr_p",
    )
    out: list[dict] = []
    for row in financials:
        merged = dict(row)
        inc = inc_by_date.get(str(row.get("end_date", "")))
        if inc:
            for key in _income_passthrough:
                val = inc.get(key)
                if val is not None:
                    merged[key] = val
            # R12b: revenue/net_profit 兜底 — fina_indicator 的这两个字段可能被
            # Tushare 积分档过滤（实测 2000 分档返回 None），由 income 表补齐。
            # 修复现象：业绩全景表营收/净利润列长期为 "-"（宜安 300328 实测）。
            if merged.get("revenue") is None:
                rev = inc.get("revenue") or inc.get("total_revenue")
                if rev is not None:
                    merged["revenue"] = rev
            if merged.get("net_profit") is None:
                np_attr = inc.get("n_income_attr_p")
                if np_attr is not None:
                    merged["net_profit"] = np_attr
            # 别名映射（与计划一致）
            tax = inc.get("income_tax")
            if tax is not None:
                merged["tax"] = tax
            sell = inc.get("sell_exp")
            if sell is not None:
                merged["selling_exp"] = sell
            # 推导折旧摊销（EBITDA - EBIT，Tushare 无单独 depr/amort 字段）
            ebit_v = inc.get("ebit")
            ebitda_v = inc.get("ebitda")
            if ebit_v is not None and ebitda_v is not None:
                try:
                    merged["depr_amort"] = float(ebitda_v) - float(ebit_v)
                except (TypeError, ValueError):
                    pass
        out.append(merged)
    return out


def _merge_balancesheet_into_financials(
    financials: list[dict], balancesheet: list[dict],
) -> list[dict]:
    """按 end_date 合并资产负债表字段（应收/存货/负债/权益/现金），供 DCF/CV-2。"""
    bs_by_date = {str(r.get("end_date", "")): r for r in balancesheet}
    _bs_passthrough = (
        "total_liab", "total_hldr_eqy_inc_min_int", "money_cap",
        "total_cur_assets", "total_cur_liab", "total_assets",
    )
    out: list[dict] = []
    for row in financials:
        merged = dict(row)
        bs = bs_by_date.get(str(row.get("end_date", "")))
        if bs:
            ar = bs.get("accounts_rece")
            if ar is None:
                ar = bs.get("accounts_receiv")
            if ar is not None:
                merged["accounts_receiv"] = ar
            inv = bs.get("inventories")
            if inv is None:
                inv = bs.get("inventory")
            if inv is not None:
                merged["inventory"] = inv
            # P0-1: 透传 DCF 所需资产负债表字段
            for key in _bs_passthrough:
                val = bs.get(key)
                if val is not None:
                    merged[key] = val
            # total_hldr_eqy_inc_min_int 别名 total_equity（与计划一致）
            eqy = bs.get("total_hldr_eqy_inc_min_int")
            if eqy is not None:
                merged["total_equity"] = eqy
        out.append(merged)
    return out


def _q_tushare_financials(symbol: str) -> list[dict] | None:
    config, tc = _require_tushare()
    ts = _ts_code(symbol)
    # R1 验收修正（C12）：730 自然日仅约 2 个年报期，classify 需 ≥3 个年报 →
    # 扩窗至 1460 自然日（4 年），R1 收益驱动假设在实时路径可渲染
    lookback = _days_ago(1460)
    end = _today()
    df = tc.query(
        "fina_indicator", ts_code=ts,
        fields=(
            "ts_code,end_date,roe,eps,profit_dedt,revenue,net_profit,"
            "grossprofit_margin,netprofit_margin,assets_turn,eqt_to_debt,"
            "debt_to_assets,ebit,ebitda,fcff,fcfe"
        ),
        start_date=lookback, end_date=end,
    )
    if df is None or df.empty:
        return None
    records = df.to_dict("records")
    for rec in records:
        em = rec.get("eqt_to_debt")
        if em is not None and safe_float(em) not in (None, 0):
            rec["equity_multiplier"] = 1.0 + 1.0 / float(em)
        elif rec.get("debt_to_assets") is not None:
            da = safe_float(rec.get("debt_to_assets"))
            if da is not None and 0 <= da <= 100:
                # Tushare debt_to_assets 为百分比（0-100），如 0.8 表示 0.8%
                rec["equity_multiplier"] = 1.0 / max(0.01, (100.0 - da) / 100.0)
    # P0-1: income 查询 — 补齐 DCF 所需费用/利润明细字段
    inc_df = tc.query("income", ts_code=ts,
                      fields=(
                          "ts_code,end_date,ebit,ebitda,fin_exp,income_tax,"
                          "sell_exp,admin_exp,invest_income,"
                          "total_profit,n_income_attr_p,"
                          "revenue,total_revenue,operate_profit"
                      ),
                      start_date=lookback, end_date=end)
    if inc_df is not None and not inc_df.empty:
        records = _merge_income_into_financials(records, inc_df.to_dict("records"))
    elif inc_df is None or inc_df.empty:
        logger.warning("Tushare income query returned empty for %s; ebit/ebitda/fin_exp fields will be missing from records", ts)
    # P0-1: cashflow 扩字段 — 补齐 DCF 所需 CapEx（Tushare 此表无 depr/amort，由 fina_indicator 的 ebitda-ebit 推导）
    cf_df = tc.query("cashflow", ts_code=ts,
                     fields=(
                         "ts_code,end_date,n_cashflow_act,"
                         "c_pay_acq_const_fiolta"
                     ),
                     start_date=lookback, end_date=end)
    if cf_df is not None and not cf_df.empty:
        records = _merge_cashflow_into_financials(records, cf_df.to_dict("records"))
    elif cf_df is None or cf_df.empty:
        logger.warning("Tushare cashflow query returned empty for %s; n_cashflow_act field will be missing from records", ts)
    # P0-1: balancesheet 扩字段 — 补齐 DCF 所需负债/权益/现金字段
    bs_df = tc.query("balancesheet", ts_code=ts,
                     fields=(
                         "ts_code,end_date,accounts_rece,inventories,"
                         "total_liab,total_hldr_eqy_inc_min_int,"
                         "money_cap,total_cur_assets,total_cur_liab,"
                         "total_assets"
                     ),
                     start_date=lookback, end_date=end)
    if bs_df is not None and not bs_df.empty:
        records = _merge_balancesheet_into_financials(records, bs_df.to_dict("records"))
    elif bs_df is None or bs_df.empty:
        logger.warning("Tushare balancesheet query returned empty for %s; accounts_receiv/inventory fields will be missing from records", ts)
    return records


def _q_tushare_shareholders(symbol: str) -> list[dict] | None:
    """Tushare 十大股东（最新报告期）。"""
    config, tc = _require_tushare()
    df = tc.query("top10_floatholders", ts_code=_ts_code(symbol),
                  fields="ts_code,end_date,holder_name,hold_amount,hold_ratio",
                  period=_latest_quarter_end())
    if df is not None and not df.empty:
        return df.to_dict("records")
    return None


def _q_tushare_daily(symbol: str, **kwargs) -> list[dict] | None:
    req_sd = kwargs.get("start_date")
    req_ed = kwargs.get("end_date")

    def _fetch() -> list[dict] | None:
        config, tc = _require_tushare()
        df = tc.query("daily", ts_code=_ts_code(symbol),
                      fields="trade_date,open,high,low,close,vol,amount",
                      **kwargs)
        if df is not None and not df.empty:
            return df.to_dict("records")
        return None

    if not req_sd or not req_ed:
        return _fetch()  # 未显式给窗口（_q_tushare_daily_qfq 默认空串）不缓存
    return _run_kline_quote_cached("tushare.daily", symbol, req_sd, req_ed, _fetch)


def _q_tushare_adj_factor(symbol: str, start_date: str = "",
                          end_date: str = "") -> dict[str, float] | None:
    """Tushare 复权因子，返回 {trade_date: adj_factor}（前复权计算基础）。"""
    sd = start_date or _days_ago(400)
    ed = end_date or _today()

    def _fetch() -> dict[str, float] | None:
        config, tc = _require_tushare()
        df = tc.query("adj_factor", ts_code=_ts_code(symbol),
                      start_date=sd, end_date=ed)
        if df is None or df.empty:
            return None
        factors: dict[str, float] = {}
        for r in df.to_dict("records"):
            td = str(r.get("trade_date") or "")
            f = r.get("adj_factor")
            if td and f is not None:
                try:
                    factors[td] = float(f)
                except (TypeError, ValueError):
                    pass
        return factors or None

    return _run_kline_quote_cached("tushare.adj_factor", symbol, sd, ed, _fetch)


def _apply_qfq(rows: list[dict], factors: dict[str, float]) -> list[dict] | None:
    """前复权（list[dict] 版）——委托 skills/lib/qfq.apply_qfq_rows（round_prices=True）。

    孪生实现：gap-scan kline_source.build_stock_kline（apply_qfq DataFrame 版，
    整股 isna 拒绝、不 rounding）——批量扫描 vs 单标的的语义差异合理，勿互走；
    契约测试：skills/lib/tests/test_qfq_contract.py。

    公式与语义同 canonical：qfq_price = raw_price × factor / latest_factor（以
    最新日为基准）；vol/amount 不变；复权因子缺失/非法 → 整体拒绝（返回 None）。
    锚定"最新日"按 trade_date 取最大行（而非 rows[-1]）——Tushare 日线降序返回，
    rows[-1] 是最旧 bar，曾导致整个序列被 f_oldest 错标度。
    """
    from lib.qfq import apply_qfq_rows  # 惰性导入（store.py 同款模式）

    # check_finite=True：与 DataFrame 路径（gap 语义）统一 NaN 完整性策略——
    # NaN 因子（nan <= 0 为 False 会穿过旧校验）整体拒绝而非毒化下游指标
    return apply_qfq_rows(rows, factors, round_prices=True, check_finite=True)


def _q_tushare_daily_qfq(symbol: str, start_date: str = "",
                         end_date: str = "") -> list[dict] | None:
    """Tushare 日K线（前复权，adj_factor 自算）。

    统一 K 线复权语义：技术指标（MA/BOLL/RSI）在不复权价格上会被除权日
    跳变污染；前复权使历史价格连续。多一次 adj_factor 调用，受 Tushare
    限流约束，由同日缓存（_kline_cache）消化。
    """
    rows = _q_tushare_daily(symbol, start_date=start_date, end_date=end_date)
    if not rows:
        return None
    factors = _q_tushare_adj_factor(symbol, start_date=start_date, end_date=end_date)
    if factors is None:
        return None
    return _apply_qfq(rows, factors)


def _normalize_northbound_records(records: list[dict], source: str) -> list[dict]:
    """统一主力资金/北向净额为「元」。

    Tushare moneyflow: ``net_mf_amount`` 单位为万元。
    akshare 北向: ``今日增持资金`` 映射为 ``net_mf_vol``，单位已是元。
    输出同时写入 ``net_mf_amount`` 与 ``net_mf_vol``（后者为兼容别名）。
    """
    if not records:
        return records
    # 仅 moneyflow.net_mf_amount 为万元；hsgt_top10 / akshare 已是元。
    # moneyflow 的 net_mf_vol 是「手」量纲，禁止万元 scale 回落放大。
    is_moneyflow = source.startswith("tushare.moneyflow")
    scale = ONE_PER_WAN if is_moneyflow else 1.0
    out: list[dict] = []
    for r in records:
        row = dict(r)
        raw = row.get("net_mf_amount")
        if raw is None and not is_moneyflow:
            raw = row.get("net_mf_vol")
        if raw is not None:
            yuan = float(raw) * scale
            row["net_mf_amount"] = yuan
            row["net_mf_vol"] = yuan
        out.append(row)
    return out


def _flow_amount_yuan(record: dict) -> float | None:
    """从归一化后的资金流记录读取净额（元），缺失时返回 None。"""
    val = record.get("net_mf_amount")
    if val is None:
        val = record.get("net_mf_vol")
    if val is None:
        return None
    return float(val)


def _q_tushare_moneyflow(symbol: str) -> list[dict] | None:
    config, tc = _require_tushare()
    df = tc.query("moneyflow", ts_code=_ts_code(symbol),
                  fields="ts_code,trade_date,net_mf_amount,buy_sm_vol,sell_sm_vol,net_mf_vol",
                  start_date=_days_ago(10), end_date=_today())
    if df is not None and not df.empty:
        return _normalize_northbound_records(df.to_dict("records"), "tushare.moneyflow")
    return None


def _q_tushare_hsgt_top10(symbol: str) -> list[dict] | None:
    """个股沪/深股通成交（仅上榜日有数据）。net_amount 单位：元。"""
    config, tc = _require_tushare()
    df = tc.query("hsgt_top10", ts_code=_ts_code(symbol),
                  fields="ts_code,trade_date,net_amount",
                  start_date=_days_ago(30), end_date=_today())
    if df is None or df.empty:
        return None
    rows = [
        {"trade_date": r.get("trade_date"), "net_mf_amount": r.get("net_amount")}
        for r in df.to_dict("records")
        if r.get("net_amount") is not None
    ]
    if not rows:
        return None
    return _normalize_northbound_records(rows, "tushare.hsgt_top10")


def _q_akshare_basic(symbol: str) -> dict | None:
    """akshare 基本信息来源（东方财富 push2 API）。"""
    with akshare_direct_session():
        import akshare as ak
        try:
            result = ak.stock_individual_info_em(symbol=symbol.strip().zfill(6),
                                                  timeout=8)
            if result is not None:
                if hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    if isinstance(records, list) and records:
                        return {str(r.get("item", "")): r.get("value", "") for r in records}
                if isinstance(result, dict):
                    return result
            return None
        except Exception as e:
            _reraise_eastmoney_api_error(e)


def _q_akshare_financials(symbol: str) -> list[dict] | None:
    with _proxy_bypass():
        import akshare as ak
        result = ak.stock_financial_abstract_ths(symbol=symbol.strip().zfill(6),
                                                 indicator="按报告期")
        if result is not None and hasattr(result, "to_dict"):
            records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
            if records:
                return [_map_akshare_financial_keys(r) for r in records]
        return None


def _q_akshare_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """akshare K线来源（东方财富 push2 API）。R12h：指数退避重试（1s→2s→4s，max 3）。"""
    sd = start_date or _days_ago(365)
    ed = end_date or _today()

    def _fetch() -> list[dict] | None:
        with akshare_direct_session():
            import akshare as ak
            sd_fmt = _to_iso_date(sd)
            ed_fmt = _to_iso_date(ed)
            try:
                def _fetch_once() -> Any:
                    return ak.stock_zh_a_hist(symbol=symbol.strip().zfill(6),
                                              period="daily",
                                              start_date=sd_fmt,
                                              end_date=ed_fmt,
                                              adjust="qfq",  # 前复权：统一复权语义
                                              timeout=10)
                # deadline 30s：与 timeout=10 × 3 次退避预算匹配，超时后不再后台重试
                result = em_request_with_retry(_fetch_once, deadline=time.monotonic() + 30)
                if result is not None and hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    if records:
                        return [_map_akshare_kline_keys(r) for r in records]
                return None
            except Exception as e:
                _reraise_eastmoney_api_error(e)

    # require_ed_equal：qfq 以窗口最新日锚定，仅同结束日才可切片复用
    return _run_kline_quote_cached("akshare.kline", symbol, sd, ed, _fetch,
                                   require_ed_equal=True)


def _q_akshare_northbound(symbol: str) -> list[dict] | None:
    """akshare 北向资金（东方财富）。R12h：与 kline 路径同型指数退避重试（1s→2s→4s）。"""
    with akshare_direct_session():
        import akshare as ak
        try:
            def _fetch() -> Any:
                return ak.stock_hsgt_individual_em(symbol=symbol.strip().zfill(6))
            # akshare 该接口无 timeout 参数（依赖 socket 默认超时）；deadline
            # 30s 确保超时后后台 daemon 线程不再发起重试
            result = em_request_with_retry(_fetch, deadline=time.monotonic() + 30)
            if result is not None and hasattr(result, "to_dict"):
                records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                if records:
                    mapped = [_map_akshare_northbound_keys(r) for r in records]
                    return _normalize_northbound_records(mapped, "akshare.northbound")
            return None
        except Exception as e:
            _reraise_eastmoney_api_error(e)


# ---- akshare 中文列名 → 英文键名映射 ----

def _map_akshare_kline_keys(r: dict) -> dict:
    """akshare stock_zh_a_hist 列名映射。"""
    return {
        "trade_date": str(r.get("日期", "")),
        "open": r.get("开盘"),
        "high": r.get("最高"),
        "low": r.get("最低"),
        "close": r.get("收盘"),
        "vol": r.get("成交量"),
    }


def _parse_akshare_num(v) -> float | None:
    """将 akshare 返回的字符串数值转为 float，兼容 '%' / '万亿' / '亿' / '万' 后缀。

    例如 "8.37%" → 8.37, "17.88亿" → 1788000000.0, "2.35万亿" → 2.35e12
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        multiplier = 1.0
        if "万亿" in s:
            multiplier = 1e12
            s = s.replace("万亿", "")
        elif "亿" in s:
            multiplier = ONE_PER_YI
            s = s.replace("亿", "")
        elif "万" in s:
            multiplier = ONE_PER_WAN
            s = s.replace("万", "")
        if "%" in s:
            s = s.replace("%", "")
        try:
            return float(s) * multiplier
        except (ValueError, TypeError):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _map_akshare_financial_keys(r: dict) -> dict:
    """akshare stock_financial_abstract_ths 列名映射。

    注意：akshare 返回的数值带中文单位（如 "17.88亿"、"8.37%"),
    _parse_akshare_num 将其转换为与 Tushare 一致的纯 float 格式。
    """
    out = {
        "end_date": str(r.get("报告期", "")),
        "roe": _parse_akshare_num(r.get("净资产收益率")),
        "eps": _parse_akshare_num(r.get("基本每股收益")),
        "profit_dedt": _parse_akshare_num(r.get("扣非净利润")),
        "revenue": _parse_akshare_num(r.get("营业总收入")),
        "net_profit": _parse_akshare_num(r.get("净利润")),
    }
    gm = _parse_akshare_num(r.get("销售毛利率") or r.get("毛利率"))
    if gm is not None:
        out["grossprofit_margin"] = gm
    ar = _parse_akshare_num(r.get("应收账款"))
    if ar is not None:
        out["accounts_receiv"] = ar
    inv = _parse_akshare_num(r.get("存货"))
    if inv is not None:
        out["inventory"] = inv
    return out


def _map_akshare_northbound_keys(r: dict) -> dict:
    """akshare stock_hsgt_individual_em 列名映射。"""
    return {
        "trade_date": str(r.get("持股日期", "")),
        "net_mf_vol": r.get("今日增持资金"),
    }


# ---- akshare 股东信息 ----

def _akshare_top10_code(symbol: str) -> str:
    """akshare 股东接口需要的代码格式：sh600519 / sz000858（委托 _exchange_code）。"""
    return _exchange_code(symbol)["akshare"]


def _q_akshare_shareholders(symbol: str) -> list[dict] | None:
    """akshare 前十大股东来源（东方财富 datacenter API）。"""
    with akshare_direct_session():
        import akshare as ak
        dates_to_try = _latest_quarter_dates()
        for date_str in dates_to_try:
            try:
                code = _akshare_top10_code(symbol)
                result = ak.stock_gdfx_top_10_em(symbol=code, date=date_str)
                if result is not None and hasattr(result, "to_dict"):
                    records = result.to_dict("records") if callable(result.to_dict) else result.to_dict
                    if records:
                        return [{"holder_name": str(r.get("股东名称", "")),
                                 "hold_amount": r.get("持股数"),
                                 "hold_ratio": r.get("占总股本持股比例")}
                                for r in records[:10]]
            except Exception as e:
                if _is_eastmoney_blocked_error(str(e)):
                    _reraise_eastmoney_api_error(e)
                continue
        return None


# ---- akshare 行业数据查询辅助 ----


def _q_akshare_industry_board(symbol: str, industry_name: str = "") -> dict | None:
    """获取个股所属行业板块的近期行情（akshare 东方财富）。

    Returns:
        dict with keys: industry_name, board_code, recent_return_pct
        或 None（采集失败时）
    """
    if not env.is_akshare_available() or not akshare_push2_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            # 获取行业板块列表
            df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                return None

            # 获取个股所属行业（优先使用预取，避免重复 API）
            if not industry_name:
                info = _q_akshare_basic(symbol)
                if not info:
                    return None
                industry_name = info.get("行业") or info.get("industry", "")
            if not industry_name:
                return None

            # 在板块列表中匹配行业
            matched = df[df["板块名称"].str.contains(industry_name, na=False)]
            if matched.empty:
                # 模糊匹配
                for _, row in df.iterrows():
                    name = str(row.get("板块名称", ""))
                    if industry_name in name or name in industry_name:
                        matched = df[df["板块名称"] == name]
                        break

            if matched.empty:
                return {"industry_name": industry_name, "board_code": None,
                        "note": "未在板块列表中找到匹配"}

            board_name = str(matched.iloc[0]["板块名称"])
            board_code = str(matched.iloc[0]["板块代码"])

            # 获取板块历史行情（近30日）
            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=board_name,
                    period="日k",
                    start_date=_to_iso_date(_days_ago(30)),
                    end_date=_to_iso_date(_today()),
                    adjust="",
                )
                if hist is not None and not hist.empty:
                    closes = [
                        f for v in hist["收盘"].tolist()
                        if (f := safe_float(v)) is not None
                    ]
                    recent_ret = None
                    if len(closes) >= 2 and closes[0] != 0:
                        recent_ret = safe_float(
                            (closes[-1] - closes[0]) / closes[0] * 100,
                        )
                    return {
                        "industry_name": industry_name,
                        "board_name": board_name,
                        "board_code": board_code,
                        "recent_return_pct": (
                            round(recent_ret, 2) if recent_ret is not None else None
                        ),
                        "trading_days_in_window": len(closes),
                        "source": "akshare.stock_board_industry_hist_em",
                    }
            except Exception as exc:
                logger.debug("akshare board hist failed for %s: %s", board_name, exc)

            return {
                "industry_name": industry_name,
                "board_name": board_name,
                "board_code": board_code,
                "source": "akshare.stock_board_industry_name_em",
            }
    except Exception as exc:
        logger.debug("akshare industry board failed for %s: %s", symbol, exc)
        return None


def _q_akshare_industry_pe(symbol: str, industry_name: str = "") -> dict | None:
    """获取行业PE中位数（akshare/巨潮资讯）。

    Returns:
        dict with: industry_pe_median, industry_pe_avg, company_pe, relative_position
        或 None
    """
    if not env.is_akshare_available() or not akshare_push2_available():
        return None
    try:
        with akshare_direct_session():
            import akshare as ak
            df = ak.stock_board_industry_pe_ratio_cninfo()
            if df is None or df.empty:
                return None

            # 获取个股行业（优先使用预取）
            if not industry_name:
                info = _q_akshare_basic(symbol)
                if not info:
                    return None
                industry_name = info.get("行业") or info.get("industry", "")
            # P0：空名守卫（对齐孪生函数 _q_akshare_industry_board）——行业字段缺失时
            # str.contains("") 全表匹配会静默取巨潮 PE 表首行作为本股行业 PE（数据错误）
            if not industry_name:
                return None

            # 匹配行业PE
            matched = df[df["行业名称"].str.contains(industry_name, na=False)]
            if matched.empty:
                for _, row in df.iterrows():
                    name = str(row.get("行业名称", ""))
                    if industry_name in name or name in industry_name:
                        matched = df[df["行业名称"] == name]
                        break

            if matched.empty:
                return {"industry_name": industry_name, "note": "未匹配到行业PE数据"}

            row = matched.iloc[0]
            pe_median = safe_float(row.get("市盈率中位数") or row.get("市盈率"))
            pe_avg = safe_float(row.get("市盈率平均值"))

            return {
                "industry_name": str(row.get("行业名称", "")),
                "industry_pe_median": pe_median,
                "industry_pe_avg": pe_avg,
                "stock_count": safe_float(row.get("公司数量")),
                "source": "akshare.stock_board_industry_pe_ratio_cninfo",
            }
    except Exception as exc:
        logger.debug("akshare industry PE failed for %s: %s", symbol, exc)
        return None


def _latest_quarter_dates(as_of: datetime | None = None, count: int = 5) -> list[str]:
    """返回最近 count 个已结束季末日期（YYYYMMDD），用于股东多期查询。"""
    import calendar

    now = as_of or datetime.now()
    dates: list[str] = []
    year, quarter = now.year, (now.month - 1) // 3 + 1

    while len(dates) < count:
        end_month = quarter * 3
        last_day = calendar.monthrange(year, end_month)[1]
        q_end = datetime(year, end_month, last_day)
        if q_end <= now:
            dates.append(q_end.strftime("%Y%m%d"))
        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1
    return dates


def _q_baostock_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """Baostock K 线来源（免费、稳定，适合历史日K线）。

    使用 _BAOSTOCK_LOCK 串行化访问：Baostock 内部使用全局单例 socket，
    多线程并行调用会导致连接竞态。
    """
    with _BAOSTOCK_LOCK, _proxy_bypass():
        import baostock as bs
        logged_in = False
        try:
            lg = bs.login()
            if lg.error_code != "0":
                logger.warning("baostock login failed: %s", lg.error_msg)
                return None
            logged_in = True

            sd = start_date or _days_ago(365)
            ed = end_date or _today()
            sd_fmt = _to_iso_date(sd)
            ed_fmt = _to_iso_date(ed)

            bs_code = _baostock_code(symbol)
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=sd_fmt, end_date=ed_fmt,
                frequency="d", adjustflag="2",  # 前复权：统一复权语义（gap-scan 同款）
            )
            if rs.error_code != "0":
                logger.warning("baostock query failed: %s", rs.error_msg)
                return None

            rows = []
            while rs.next():
                row = rs.get_row_data()
                rows.append({
                    "trade_date": row[0].replace("-", ""),
                    # safe_float：baostock 空字段返回字符串 "nan"（truthy），
                    # 裸 float() 会穿透 NaN 污染下游（gap-scan 38a7e1e 同型先例；
                    # 孪生实现 kline_source.build_stock_kline 用 to_numeric+
                    # 整股 isna 拒绝——单标的逐字段 guard vs 批量整股拒绝，
                    # 契约测试 skills/lib/tests/test_qfq_contract.py）
                    "open": safe_float(row[1]),
                    "high": safe_float(row[2]),
                    "low": safe_float(row[3]),
                    "close": safe_float(row[4]),
                    "vol": safe_float(row[5]) or 0,
                    "amount": safe_float(row[6]) or 0,
                })
            return rows if rows else None
        except Exception as e:
            logger.warning("baostock query failed: %s", e)
            return None
        finally:
            if logged_in:
                try:
                    bs.logout()
                except Exception:
                    pass


def _q_tickflow_kline(symbol: str, start_date: str = "", end_date: str = "") -> list[dict] | None:
    """TickFlow K 线来源（独立数据管道，非东方财富，免费免注册）。

    TickFlow 提供独立的行情数据源（非东方财富爬虫），与现有
    akshare（东方财富）、baostock、Tushare 形成第4条验证链路。
    免费 tier 无需注册，提供完整日K历史数据。
    如 free tier 升级提示输出到 stdout（不影响数据采集），
    建议调用方将 stdout 重定向到 stderr 或丢弃。

    API: TickFlow.free().klines.get(symbol, start=ms, end=ms, adjust="forward")
    Symbol 格式: "600176.SH" (SH/SZ/BJ, 与 Tushare 格式一致)
    """
    try:
        import tickflow as tf
    except ImportError as exc:
        raise Exception("tickflow 未安装，请运行: uv sync") from exc

    sd = start_date or _days_ago(400)
    ed = end_date or _today()

    # TickFlow 使用毫秒级 Unix 时间戳
    from zoneinfo import ZoneInfo
    sh = ZoneInfo("Asia/Shanghai")
    start_ms = int(datetime.strptime(sd, "%Y%m%d").replace(tzinfo=sh).timestamp() * 1000)
    end_ms = int(datetime.strptime(ed, "%Y%m%d").replace(tzinfo=sh).timestamp() * 1000)

    # TickFlow 使用 Tushare 风格代码格式：600176.SH
    tf_symbol = _exchange_code(symbol)["tushare"]

    try:
        with redirect_stdout(StringIO()):
            client = tf.TickFlow.free()
        df = client.klines.get(
            tf_symbol,
            period="1d",
            start_time=start_ms,
            end_time=end_ms,
            adjust="forward",
            as_dataframe=True,
        )
    except Exception:
        logger.warning("tickflow query failed for %s", symbol)
        return None

    if df is None or df.empty:
        return None

    # 标准化列名：trade_date (YYYYMMDD), open, high, low, close, vol, amount
    rows = []
    for _, row in df.iterrows():
        td = str(row.get("trade_date", ""))
        if td:
            td = td.replace("-", "")  # YYYY-MM-DD → YYYYMMDD

        rows.append({
            "trade_date": td,
            "open": safe_float(row.get("open")),
            "high": safe_float(row.get("high")),
            "low": safe_float(row.get("low")),
            "close": safe_float(row.get("close")),
            "vol": safe_float(row.get("volume")) or 0,
            "amount": safe_float(row.get("amount")) or 0,
        })

    return rows if rows else None


def _is_tencent_unsupported(symbol: str) -> bool:
    """北交所代码（4/8/920 前缀）→ 腾讯 qt.gtimg.cn 行情无覆盖。

    保留兼容名（_orchestrate star-copy 消费方可能引用）；逻辑已收敛至
    skills/lib/quote_tencent.is_tencent_unsupported（唯一实现）。
    """
    return is_tencent_unsupported(symbol)


def _q_tencent_quote(symbol: str) -> dict | None:
    """腾讯行情。北交所（4/8/920 前缀）腾讯无覆盖 → 不请求、标注不可得。

    v0.2.7 起委托 skills/lib/quote_tencent 唯一实现（统一路由/解析/单位换算），
    本层只保留返回契约：映射 total_mv_yi → total_mv，且不引入 amount 等新键
    （避免 _merge_realtime 把新键并入 legacy dict 造成行为漂移）。
    """
    if is_tencent_unsupported(symbol):
        logger.warning("tencent quote: 北交所代码 %s 腾讯行情无覆盖，跳过（不误路由）", symbol)
        return None
    with no_proxy_session() as sess:
        q = fetch_tencent_quote(symbol, session=sess)
    if q is None:
        return None
    return {
        "price": q["price"],
        "change_pct": q["change_pct"],
        "high": q["high"],
        "low": q["low"],
        "volume": q["volume"],
        "turnover_rate": q["turnover_rate"],
        "pe_ratio": q["pe_ratio"],
        "total_mv": q["total_mv_yi"],  # 亿元（单位语义见 quote_tencent 模块）
    }


# ---- 查询参数字符串生成 ----

def _qp_tushare(api: str, symbol: str, **kw) -> str:
    pairs = [f"{k}='{v}'" for k, v in sorted(kw.items()) if v]
    return f"pro.{api}(ts_code='{_ts_code(symbol)}'{', ' + ', '.join(pairs) if pairs else ''})"


def _qp_akshare(name: str, symbol: str, **kw) -> str:
    pairs = [f"{k}='{v}'" for k, v in sorted(kw.items()) if v]
    return f"ak.{name}(symbol='{symbol.strip().zfill(6)}'{', ' + ', '.join(pairs) if pairs else ''})"


def _qp_tencent(symbol: str) -> str:
    if is_tencent_unsupported(symbol):
        return "qt.gtimg.cn: 北交所无覆盖（不请求）"
    market = tencent_market(symbol)
    return f"qt.gtimg.cn/q={market}{symbol}"


def _qp_baostock(symbol: str, start_date: str, end_date: str) -> str:
    code = _baostock_code(symbol)
    return (
        f"bs.query_history_k_data_plus(code='{code}', "
        f"start='{start_date}', end='{end_date}', frequency='d')"
    )


def _qp_tickflow(symbol: str, start_date: str, end_date: str) -> str:
    """TickFlow K-line 查询参数字符串。"""
    code = _exchange_code(symbol)["tushare"]
    return (
        f"tf.TickFlow.free().klines.get(symbol='{code}', "
        f"start={start_date}, end={end_date}, adjust='forward')"
    )


