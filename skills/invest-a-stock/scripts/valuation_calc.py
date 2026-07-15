#!/usr/bin/env python3
"""A 股科学估值计算器（v0.2.0）。

多方法交叉估值，每步计算标注追溯路径。不做买卖建议，不输出单一目标价。

使用方法:
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 002466
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 002466 --rf 0.0173 --erp 0.06
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 002466 --json

数据源:
    - Tushare: fina_indicator（财务）、daily_basic（PE/PB 历史）
    - akshare: 实时行情、中国 10Y 国债收益率
    - 腾讯行情: 兜底实时报价

估值流程:
    1. 获取当前价格 / 总股本 / 市值
    2. 获取最近 8 期财务数据，计算 TTM EPS / BVPS
    3. 获取 PE/PB 历史序列，计算历史分位
    4. 获取中国 10Y 国债收益率作为 Rf
    5. 盈利收益率框架 (Rf + ERP)
    6. 反推市场隐含 g
    7. ROE-PB 理论匹配
    8. 多情景（乐观/中性/悲观）× 多方法估值区间
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
import math
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Any

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("valuation_calc")

# 确保项目 lib 在 sys.path 中
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.env import get_config, ensure_env_loaded, PROJECT_ROOT
from lib.nums import safe_float, coalesce_field
from lib.financials import normalize_end_date, parse_end_date
from lib.tushare_client import TushareClient

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_ERP = 0.060          # A 股 ERP 默认 6%
DEFAULT_TERMINAL_G = 0.025   # 永续增长率默认 2.5%
CHINA_BOND_DAYS = 2000       # 中国国债收益率回溯天数

# 情景定义
SCENARIOS = {
    "bull":   {"label": "乐观", "prob": 0.20, "growth_mult": 1.5, "margin_delta_pp": +2.0},
    "base":   {"label": "中性", "prob": 0.50, "growth_mult": 1.0, "margin_delta_pp":  0.0},
    "bear":   {"label": "悲观", "prob": 0.30, "growth_mult": 0.5, "margin_delta_pp": -3.0},
}


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

def _fmt_code(symbol: str) -> str:
    """将 002466 → 002466.SZ（Tushare 格式）。"""
    s = symbol.strip()
    if "." in s:
        return s
    if s.startswith(("60", "68")):
        return f"{s}.SH"
    return f"{s}.SZ"


def _fmt_code_ak(symbol: str) -> str:
    """纯数字代码，给 akshare 用。"""
    return symbol.strip().replace(".SZ", "").replace(".SH", "")


def get_quote_ak(symbol: str) -> dict[str, Any]:
    """从 akshare 获取实时行情。失败则回退腾讯行情。"""
    import akshare as ak
    code = _fmt_code_ak(symbol)
    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        if row.empty:
            raise ValueError(f"未找到 {code}")
        r = row.iloc[0]
        return {
            "price": safe_float(r.get("最新价")),
            "change_pct": safe_float(r.get("涨跌幅")),
            "total_mv_yi": safe_float(r.get("总市值")),
            "pe_dynamic": safe_float(r.get("市盈率-动态")),
            "pb": safe_float(r.get("市净率")),
            "source": "akshare.stock_zh_a_spot_em",
        }
    except Exception:
        logger.warning("akshare 行情失败，回退腾讯", exc_info=True)
        return _get_quote_tencent(symbol)


def _get_quote_tencent(symbol: str) -> dict[str, Any]:
    """腾讯行情兜底。"""
    import requests
    code = _fmt_code_ak(symbol)
    mkt = "sh" if code.startswith(("6", "68")) else "sz"
    url = f"http://qt.gtimg.cn/q={mkt}{code}"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        raw = r.text
        # 腾讯格式: v_<code>="...~...~..."
        fields = raw.split("~")
        if len(fields) < 45:
            raise ValueError(f"腾讯行情字段不足: {len(fields)}")
        price = safe_float(fields[3])
        change_pct = safe_float(fields[32])
        pe = safe_float(fields[39])
        pb = safe_float(fields[46]) if len(fields) > 46 else None
        total_mv = safe_float(fields[45]) if len(fields) > 45 else None
        return {
            "price": price,
            "change_pct": change_pct,
            "total_mv_yi": total_mv,
            "pe_dynamic": pe,
            "pb": pb,
            "source": "tencent.qt.gtimg.cn",
        }
    except Exception:
        logger.warning("腾讯行情也失败")
        return {"price": None, "source": "failed: tencent", "error": str(r) if 'r' in dir() else "request failed"}


def get_total_shares_ak(symbol: str) -> float | None:
    """从 akshare 获取总股本（万股）。"""
    import akshare as ak
    code = _fmt_code_ak(symbol)
    try:
        info = ak.stock_individual_info_em(symbol=code)
        for _, row in info.iterrows():
            if row.get("item") == "总股本":
                raw = row.get("value")
                if raw is None:
                    continue
                return _parse_shares(raw)
        return None
    except Exception:
        logger.warning("akshare 总股本获取失败", exc_info=True)
        return None


def _parse_shares(raw: str | float | int) -> float | None:
    """解析总股本文本 → 万股。"""
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).replace(",", "").strip()
    mult = 1.0
    if "亿" in s:
        mult = 1e4
        s = s.replace("亿", "")
    elif "万" in s:
        s = s.replace("万", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


def get_financials(ts: TushareClient, ts_code: str) -> list[dict]:
    """从 Tushare fina_indicator 获取最近 8-12 期财务数据。

    Tushare query() 返回 DataFrame。
    fina_indicator 的 eps 是累计值（年报 Q4 = 全年 EPS），需做差得单季。

    返回按 end_date 升序排列、去重后的行列表。
    """
    try:
        start_date = (date.today() - timedelta(days=3 * 365)).strftime("%Y%m%d")
        end_date = date.today().strftime("%Y%m%d")
        result = ts.query(
            "fina_indicator",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if result is None or (hasattr(result, "empty") and result.empty):
            logger.info("Tushare fina_indicator 返回空 (%s)", ts_code)
            return []
        rows = result.to_dict(orient="records") if hasattr(result, "to_dict") else list(result)
        if not rows:
            return []
        sorted_rows = sorted(
            (r for r in rows if isinstance(r, dict) and r.get("end_date")),
            key=lambda r: str(r.get("end_date", "")),
        )
        # 去重：同一 end_date 取最后一条
        seen = {}
        for r in sorted_rows:
            seen[str(r.get("end_date"))] = r
        # 返回全部去重行（TTM 计算需要连续 5 期：4 个单季差 + 1 个基期）
        return list(seen.values())
    except Exception:
        logger.warning("Tushare fina_indicator 失败", exc_info=True)
        return []


def get_daily_basic_history(
    ts: TushareClient, ts_code: str, years: int = 5
) -> list[dict]:
    """获取 PE/PB 历史序列。

    Tushare query() 直接返回 DataFrame，不包在 dict 里。
    daily_basic 支持 start_date/end_date 参数。
    """
    start_date = (date.today() - timedelta(days=years * 365)).strftime("%Y%m%d")
    end_date = date.today().strftime("%Y%m%d")
    try:
        result = ts.query(
            "daily_basic",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv",
        )
        if result is None or (hasattr(result, "empty") and result.empty):
            logger.info("Tushare daily_basic 返回空 (%s)", ts_code)
            return []
        rows = result.to_dict(orient="records") if hasattr(result, "to_dict") else list(result)
        return sorted(
            (r for r in rows if isinstance(r, dict) and r.get("trade_date")),
            key=lambda r: str(r.get("trade_date", "")),
        )
    except Exception:
        logger.warning("Tushare daily_basic 历史失败", exc_info=True)
        return []


def get_china_bond_yield() -> tuple[float | None, str]:
    """获取中国 10 年期国债收益率。

    尝试顺序：akshare → Web 兜底。
    返回 (yield_decimal, source_description)。
    """
    # 方法 1: akshare
    try:
        import akshare as ak
        df = ak.bond_china_yield(start_date="20260101")
        if df is not None and not df.empty:
            col_10y = None
            for col in df.columns:
                if "10" in str(col) and "年" in str(col):
                    col_10y = col
                    break
            if col_10y is None:
                col_10y = df.columns[-1]
            last_val = safe_float(df[col_10y].iloc[-1])
            if last_val is not None:
                return last_val / 100.0, "akshare.bond_china_yield"
    except Exception:
        logger.debug("akshare 国债收益率失败", exc_info=True)

    # 方法 2: 用已知近期值作为合理默认值（标注为"近似值"）
    # 2026-07-14 中国 10Y 约 1.73%
    return 0.0173, "default (~1.73%, 2026-07-14 近似)"


# ---------------------------------------------------------------------------
# 计算函数
# ---------------------------------------------------------------------------

def _standalone_quarterly_eps(fin_rows: list[dict]) -> list[dict]:
    """将 fina_indicator 的累计 EPS 转为单季 EPS。

    fina_indicator 的 eps 是年度累计值（0331=Q1, 0630=H1, 0930=3Q, 1231=全年）。
    单季 EPS = 本期累计 - 上年同期累计（跨年）或 本期累计 - 上期累计（同年）。

    返回按 end_date 升序的 [{"end_date": str, "eps_standalone": float}, ...]。
    """
    if len(fin_rows) < 2:
        return []

    standalone = []
    for i, row in enumerate(fin_rows):
        ed = str(row.get("end_date", ""))
        eps_cum = safe_float(row.get("eps"))
        if eps_cum is None or len(ed) < 8:
            continue

        mmdd = ed[4:]  # e.g. "0331", "0630", "0930", "1231"
        eps_standalone = None

        if mmdd == "0331":
            # Q1: 本身就是单季（新年第一份报告）
            eps_standalone = eps_cum
        else:
            # 半年报/三季报/年报: 寻找上一期累计值
            # 优先级：同年上一期 > 上一年同期
            prev_cum = None
            year = ed[:4]
            prev_mmdd = {"0630": "0331", "0930": "0630", "1231": "0930"}.get(mmdd)
            if prev_mmdd:
                prev_ed = year + prev_mmdd
                for r in fin_rows:
                    if str(r.get("end_date", "")) == prev_ed:
                        prev_cum = safe_float(r.get("eps"))
                        break
            if prev_cum is not None:
                eps_standalone = eps_cum - prev_cum

        if eps_standalone is not None:
            standalone.append({
                "end_date": ed,
                "eps_standalone": round(eps_standalone, 4),
            })

    return standalone


def calc_ttm_eps(fin_rows: list[dict], total_shares_wan: float | None = None) -> dict[str, Any]:
    """计算 TTM EPS：最近 4 个单季 EPS 之和。

    使用 fina_indicator 的 eps（累计值），转为单季后求和。

    Args:
        fin_rows: 财务行列表（按 end_date 升序且已去重）
        total_shares_wan: 总股本（万股），仅用于显示净利润（非必须）
    """
    standalone = _standalone_quarterly_eps(fin_rows)
    if len(standalone) < 4:
        return {
            "ttm_eps": None,
            "error": f"可计算单季 EPS 不足4期（当前{len(standalone)}期）",
            "quarterly_eps": standalone,
        }

    last4 = standalone[-4:]
    ttm_eps = sum(q["eps_standalone"] for q in last4)

    # 净利润绝对值
    net_profit_ttm = None
    if total_shares_wan:
        net_profit_ttm = ttm_eps * total_shares_wan * 1e4

    return {
        "ttm_eps": round(ttm_eps, 4),
        "ttm_net_profit_yi": round(net_profit_ttm / 1e8, 2) if net_profit_ttm else None,
        "quarterly_eps": last4,
        "n_quarters": len(last4),
        "method": "fina_indicator eps 累计 → 单季差 → TTM=Σ(最近4个单季)",
        "note": (
            "fina_indicator 的 eps 为年度累计值（0331=Q1, 0630=H1, 0930=前3Q, 1231=全年），"
            "单季 EPS = 本期累计 − 前期累计。TTM = Σ(最近4个单季)。"
        ),
    }


def calc_bvps(fin_rows: list[dict]) -> dict[str, Any]:
    """从最新一期财报获取 BVPS（每股净资产）。

    fina_indicator 直接提供 bps 字段（每股净资产），无需自己算。
    """
    if not fin_rows:
        return {"bvps": None, "error": "无财务数据"}
    latest = fin_rows[-1]
    bps = safe_float(latest.get("bps"))
    if bps is None:
        return {"bvps": None, "error": "bps 字段不可得"}
    return {
        "bvps": round(bps, 4),
        "end_date": str(latest.get("end_date", "")),
        "source": "fina_indicator.bps（每股净资产）",
    }


def calc_roe_annualized(fin_rows: list[dict]) -> dict[str, Any]:
    """年化 ROE：根据报告期区分年度化乘数。

    fina_indicator 的 roe 是累计值（0331=Q1, 0630=H1, 0930=3Q, 1231=全年）。
    年化乘数：Q1×4, H1×2, 3Q×4/3, 年报×1（不作放大）。

    Returns 累计 ROE（YTD）和年化 ROE。
    """
    if not fin_rows:
        return {"roe_cumulative": None, "roe_annualized": None, "error": "无财务数据"}
    latest = fin_rows[-1]
    roe_q = safe_float(latest.get("roe"))
    if roe_q is None:
        return {"roe_cumulative": None, "roe_annualized": None, "error": "ROE 不可得"}
    ed = normalize_end_date(str(latest.get("end_date", "")))
    mmdd = ed[4:8] if len(ed) >= 8 else ""
    _ROE_MULT = {"0331": 4, "0630": 2, "0930": 4 / 3, "1231": 1}
    multiplier = _ROE_MULT.get(mmdd, 1)  # default 1=annual, conservative when end_date unknown/parse fails
    return {
        "roe_cumulative": round(roe_q, 2),   # YTD from fina_indicator, not single-quarter
        "roe_annualized": round(roe_q * multiplier, 2),
        "end_date": ed,
    }


def calc_ocf_quality(fin_rows: list[dict]) -> dict[str, Any]:
    """经营现金流 / 净利润 质量比。

    使用 fina_indicator 的 ocfps（每股经营现金流）和 eps（累计每股收益）。
    用最近 4 个单季 ocfps 之和 / 最近 4 个单季 eps 之和。
    """
    if not fin_rows:
        return {"ocf_np_ratio": None, "error": "无数据"}

    # 计算单季 OCF per share（类似 EPS 做差法）
    standalone_eps = _standalone_quarterly_eps(fin_rows)
    if len(standalone_eps) < 4:
        return {"ocf_np_ratio": None, "error": f"数据不足（{len(standalone_eps)}期）"}

    # 对 ocfps 做同样的单季差
    ocf_standalone = []
    for i, row in enumerate(fin_rows):
        ed = str(row.get("end_date", ""))
        ocfps_cum = safe_float(row.get("ocfps"))
        if ocfps_cum is None or len(ed) < 8:
            continue
        mmdd = ed[4:]
        if mmdd == "0331":
            ocf_standalone.append({"end_date": ed, "ocfps_standalone": ocfps_cum})
        else:
            prev_mmdd = {"0630": "0331", "0930": "0630", "1231": "0930"}.get(mmdd)
            if prev_mmdd:
                prev_ed = ed[:4] + prev_mmdd
                prev_cum = None
                for r in fin_rows:
                    if str(r.get("end_date", "")) == prev_ed:
                        prev_cum = safe_float(r.get("ocfps"))
                        break
                if prev_cum is not None:
                    ocf_standalone.append({
                        "end_date": ed,
                        "ocfps_standalone": round(ocfps_cum - prev_cum, 4),
                    })

    # 对齐 eps 和 ocfps 的最近 4 期
    eps_last4 = standalone_eps[-4:]
    ocf_last4 = ocf_standalone[-4:] if len(ocf_standalone) >= 4 else ocf_standalone

    sum_eps = sum(q["eps_standalone"] for q in eps_last4)
    sum_ocf = sum(q.get("ocfps_standalone", 0) for q in ocf_last4)

    if sum_eps <= 0:
        return {"ocf_np_ratio": None, "error": "TTM EPS 非正，无法计算覆盖比"}

    ratio = sum_ocf / sum_eps
    return {
        "ocf_np_ratio": round(ratio, 4),
        "ttm_ocfps": round(sum_ocf, 4),
        "ttm_eps": round(sum_eps, 4),
        "quality": "健康" if ratio >= 0.8 else ("偏低" if ratio >= 0.5 else "🔴 预警（<0.5）"),
        "end_date": eps_last4[-1]["end_date"] if eps_last4 else "?",
        "note": "基于最近 4 个单季 EPS/OCFPS 之和计算（fina_indicator 累计→单季差→TTM）",
    }


def calc_historical_percentile(
    daily_rows: list[dict],
    years: int = 5,
) -> dict[str, Any]:
    """PE/PB 历史分位计算。

    Tushare daily_basic 对亏损期返回 None/null PE（非负值），
    无法直接计为负值天数。通过 daily_rows 总数 vs PE 有效样本数的差值推断。
    """
    pe_seq = []
    pb_seq = []
    pe_none_count = 0  # PE=None 的行数（通常对应亏损期）
    for r in daily_rows:
        pe_v = safe_float(r.get("pe_ttm") or r.get("pe"))
        pb_v = safe_float(r.get("pb"))
        if pe_v is None:
            pe_none_count += 1
        elif pe_v > 0:
            pe_seq.append(pe_v)
        else:
            pe_none_count += 1  # PE ≤ 0 也算不可用
        if pb_v is not None and pb_v > 0:
            pb_seq.append(pb_v)

    if not pe_seq or not pb_seq:
        return {"error": "PE/PB 历史数据不足"}

    def _pct(seq: list[float], cur: float) -> float:
        return sum(1 for v in seq if v < cur) / len(seq) * 100

    def _median(seq: list[float]) -> float:
        s = sorted(seq)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2

    current_pe = pe_seq[-1]
    current_pb = pb_seq[-1]
    pe_pct = _pct(pe_seq, current_pe)
    pb_pct = _pct(pb_seq, current_pb)

    n = len(pe_seq)
    mu = sum(pe_seq) / n
    sigma = math.sqrt(sum((v - mu) ** 2 for v in pe_seq) / n)

    # 推断亏损期占比：每日一行但 PE 不可得，或直接估算
    # 5 年约 1210 个交易日，实际返回行数可能因 Tushare 接口限制而偏少
    total_daily = len(daily_rows)
    pe_neg_inferred = pe_none_count
    pe_neg_pct = pe_neg_inferred / total_daily if total_daily > 0 else 0.0

    warnings = []
    if pe_neg_pct > 0.3:
        warnings.append(
            f"PE 历史序列中约 {pe_neg_pct * 100:.0f}% 交易日 PE 不可得（通常为亏损期），"
            f"PE 分位数仅作位置参考，不反映估值贵贱。PB 分位更有参考价值。"
        )

    return {
        "n_samples": total_daily,
        "pe_valid": len(pe_seq),
        "pe_none_or_neg": pe_neg_inferred,
        "pe_neg_pct": round(pe_neg_pct, 4),
        "pe_current": round(current_pe, 2),
        "pe_pct": round(pe_pct, 1),
        "pe_median": round(_median(pe_seq), 2),
        "pe_mean": round(mu, 2),
        "pe_sigma": round(sigma, 2) if sigma else None,
        "pe_plus_1sigma": round(mu + sigma, 2) if sigma else None,
        "pe_minus_1sigma": round(mu - sigma, 2) if sigma else None,
        "pb_current": round(current_pb, 2),
        "pb_pct": round(pb_pct, 1),
        "pb_median": round(_median(pb_seq), 2),
        "warnings": warnings,
    }


def implied_growth_detailed(
    pe: float,
    rf: float,
    erp: float = DEFAULT_ERP,
) -> dict[str, Any]:
    """戈登模型反推隐含增长率 + 不同 g 假设下的合理 PE。

    g_implied = r - E/P = (rf + erp) - 1/pe
    fair_pe(g) = 1 / (rf + erp - g)
    """
    if pe <= 0:
        return {"error": "PE 非正"}

    r = rf + erp
    earnings_yield = 1.0 / pe
    g_implied = r - earnings_yield

    # 不同 g 下的合理 PE
    fair_pe_table = []
    for g_assume in [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]:
        if r <= g_assume:
            fair_pe = float("inf")
        else:
            fair_pe = 1.0 / (r - g_assume)
        fair_pe_table.append({
            "g_assumption": f"{g_assume * 100:.1f}%",
            "fair_pe": round(fair_pe, 1) if fair_pe != float("inf") else "∞",
            "description": (
                "零增长" if g_assume == 0 else
                "温和增长" if g_assume <= 0.02 else
                "结构性增长" if g_assume <= 0.03 else
                "乐观增长"
            ),
        })

    return {
        "rf": round(rf, 4),
        "erp": erp,
        "r_required": round(r, 4),
        "earnings_yield": round(earnings_yield, 4),
        "g_implied": round(g_implied, 4),
        "fair_pe_by_g": fair_pe_table,
        "note": (
            f"当前 PE {pe:.2f}x 隐含永续增长率 {g_implied * 100:.2f}%。"
            f"若 g_implied < 0，市场定价了盈利萎缩预期。"
        ),
    }


def roe_pb_match(
    roe_annualized: float,
    bvps: float,
    rf: float,
    erp: float = DEFAULT_ERP,
) -> dict[str, Any]:
    """ROE-PB 理论匹配表。

    PB_theoretical = (ROE - g) / (r - g)
    针对不同的 ROE 和 g 假设，计算理论 PB 和对应价格。
    """
    if roe_annualized is None or bvps is None:
        return {"error": "ROE/BVPS 不可得"}

    r = rf + erp
    rows = []
    for roe_label, roe_val in [
        ("Q1年化 (17%)", min(roe_annualized, 25.0)),
        ("周期均值 (12%)", 12.0),
        ("保守均值 (8%)", 8.0),
        ("低谷 (5%)", 5.0),
    ]:
        roe_decimal = roe_val / 100.0
        g_default = min(roe_decimal * 0.4, 0.03)  # 假设留存率 40%
        if r <= g_default:
            pb_theoretical = float("inf")
        else:
            pb_theoretical = (roe_decimal - g_default) / (r - g_default)
        price_theoretical = pb_theoretical * bvps if pb_theoretical != float("inf") else float("inf")
        rows.append({
            "roe_assumption": f"{roe_val:.0f}%",
            "g_assumed": f"{g_default * 100:.1f}%",
            "pb_theoretical": round(pb_theoretical, 2) if pb_theoretical != float("inf") else "∞",
            "price_theoretical": round(price_theoretical, 2) if price_theoretical != float("inf") else "∞",
        })

    return {"r_required": round(r, 4), "bvps": round(bvps, 4), "rows": rows}


def multi_scenario_valuation(
    price: float,
    ttm_eps: float,
    bvps: float,
    rf: float,
    erp: float,
    pe_median: float,
    pb_median: float,
    forward_eps_estimates: dict[str, float] | None = None,
    pe_negative_pct: float = 0.0,
) -> dict[str, Any]:
    """多情景多方法估值综合计算。

    估值倍数选择策略：
      - PE 法：当历史亏损期占比 >30% 时，历史 PE 中位数失真，
        使用 Gordon 模型反推的合理 PE（基于不同 g 假设）代替。
      - PB 法：始终使用历史 PB 中位数（PB 不受盈亏影响，更稳健）。
      - 盈利收益法：直接使用 Gordon 模型 fair_pe。

    Args:
        pe_negative_pct: PE 序列中亏损期占比（0-1），>0.3 时触发 PE 中位数失真保护
    """
    r = rf + erp

    # 判断 PE 中位数是否失真（亏损期占比 > 30%）
    pe_median_distorted = pe_negative_pct > 0.3
    if pe_median_distorted:
        # 使用 Gordon 模型合理 PE 代替失真的历史 PE 中位数
        # g=1%: 保守永续增长 → PE ≈ 1/(r-0.01)
        # g=2%: 温和永续增长 → PE ≈ 1/(r-0.02)
        safe_pe_base = 1.0 / max(r - 0.02, 0.01)  # 温和增长 PE
        safe_pe_bull = 1.0 / max(r - 0.03, 0.01)  # 乐观增长 PE
        safe_pe_bear = 1.0 / max(r - 0.005, 0.01)  # 低增长 PE
    else:
        safe_pe_base = pe_median
        safe_pe_bull = pe_median * 1.2
        safe_pe_bear = pe_median * 0.7

    # 默认前瞻 EPS
    if forward_eps_estimates is None:
        forward_eps_estimates = {
            "bull": ttm_eps * 1.3,
            "base": ttm_eps * 1.0,
            "bear": ttm_eps * 0.7,
        }

    def _calc_methods(
        eps_fwd: float, pe_mult: float, pb_mult: float, g_assume: float,
    ) -> dict:
        price_pe = round(eps_fwd * pe_mult, 2)
        price_pb = round(bvps * pb_mult, 2)
        if r > g_assume:
            fair_pe_ey = 1.0 / (r - g_assume)
            price_ey = round(eps_fwd * fair_pe_ey, 2)
        else:
            fair_pe_ey = float("inf")
            price_ey = float("inf")
        return {
            "price_pe": price_pe,
            "pe_multiple": round(pe_mult, 1),
            "price_pb": price_pb,
            "pb_multiple": round(pb_mult, 2),
            "price_earnings_yield": price_ey if price_ey != float("inf") else "∞",
            "fair_pe_ey": round(fair_pe_ey, 1) if fair_pe_ey != float("inf") else "∞",
        }

    # 情景参数
    pe_mults = {
        "bull": safe_pe_bull,
        "base": safe_pe_base,
        "bear": safe_pe_bear,
    }
    pb_mults = {  # PB 分位：乐观=中位数, 中性=中位数×0.7, 悲观=中位数×0.5
        "bull": pb_median * 0.95,
        "base": pb_median * 0.70,
        "bear": pb_median * 0.50,
    }
    g_assumes = {"bull": 0.03, "base": 0.02, "bear": 0.005}

    scenarios = {}
    for key, cfg in SCENARIOS.items():
        eps_fwd = forward_eps_estimates.get(key, ttm_eps)
        scenarios[key] = {
            "label": cfg["label"],
            "probability": f"{cfg['prob'] * 100:.0f}%",
            "eps_forward": round(eps_fwd, 4),
            "methods": _calc_methods(
                eps_fwd, pe_mults[key], pb_mults[key], g_assumes[key],
            ),
        }

    return {
        "rf": round(rf, 4),
        "erp": erp,
        "r_required": round(r, 4),
        "current_price": price,
        "ttm_eps": round(ttm_eps, 4) if ttm_eps else None,
        "bvps": round(bvps, 4) if bvps else None,
        "pe_median_ref": round(pe_median, 1),
        "pb_median_ref": round(pb_median, 1),
        "pe_median_distorted": pe_median_distorted,
        "pe_negative_pct": round(pe_negative_pct * 100, 0) if pe_negative_pct else 0,
        "scenarios": scenarios,
    }


# ---------------------------------------------------------------------------
# 主计算流程
# ---------------------------------------------------------------------------

@dataclass
class ValuationResult:
    """估值计算结果容器。"""
    symbol: str
    timestamp: str
    # 基础数据
    price: float | None = None
    total_shares_wan: float | None = None
    total_mv_yi: float | None = None
    rf_china_10y: float | None = None
    rf_source: str = ""
    erp: float = DEFAULT_ERP
    # 财务
    ttm: dict = field(default_factory=dict)
    bvps_data: dict = field(default_factory=dict)
    roe_data: dict = field(default_factory=dict)
    ocf_quality: dict = field(default_factory=dict)
    # 历史分位
    percentile: dict = field(default_factory=dict)
    # 估值计算
    implied_growth: dict = field(default_factory=dict)
    roe_pb_match: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)
    # 获取来源
    sources: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_valuation(
    symbol: str,
    rf_override: float | None = None,
    erp_override: float | None = None,
) -> ValuationResult:
    """执行完整估值计算流程。

    Args:
        symbol: 股票代码 (如 "002466")
        rf_override: 手动指定无风险利率（小数）
        erp_override: 手动指定 ERP（小数）

    Returns:
        ValuationResult
    """
    ensure_env_loaded()
    ts = TushareClient()
    ts_code = _fmt_code(symbol)
    code_ak = _fmt_code_ak(symbol)
    rf = rf_override
    erp = erp_override if erp_override is not None else DEFAULT_ERP

    result = ValuationResult(
        symbol=symbol,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        erp=erp,
    )

    # ---- Step 1: 行情 ----
    quote = get_quote_ak(symbol)
    price = quote.get("price")
    result.price = price
    result.sources["quote"] = quote.get("source", "unknown")

    # ---- Step 2: 总股本 ----
    shares_wan = get_total_shares_ak(symbol)
    result.total_shares_wan = shares_wan

    # 市值：优先用 tencent 报价中的 total_mv
    if quote.get("total_mv_yi"):
        result.total_mv_yi = quote.get("total_mv_yi")
        # 如果 akshare 拿不到总股本，但 quote 有 市值/价格，反推总股本
        if shares_wan is None and price and result.total_mv_yi and result.total_mv_yi > 0:
            shares_wan = round(result.total_mv_yi * 1e8 / (price * 1e4), 2)
            result.total_shares_wan = shares_wan
    elif price and shares_wan:
        result.total_mv_yi = round(price * shares_wan * 1e4 / 1e8, 2)

    if result.total_shares_wan is None:
        result.errors.append("总股本获取失败（akshare 不可用且行情无市值），部分计算将跳过")

    # ---- Step 3: 财务数据 ----
    fin_rows = get_financials(ts, ts_code)
    if not fin_rows:
        result.errors.append("Tushare fina_indicator 无数据")
    result.sources["financials"] = f"Tushare fina_indicator: {len(fin_rows)} rows"

    # TTM EPS（fin_rows 已去重，eps 累计→单季差→TTM）
    result.ttm = calc_ttm_eps(fin_rows, result.total_shares_wan)
    if result.ttm.get("error") and not result.ttm.get("ttm_eps"):
        result.warnings.append(f"TTM EPS: {result.ttm['error']}")

    # BVPS（直接使用 fina_indicator.bps）
    result.bvps_data = calc_bvps(fin_rows)

    # ROE
    result.roe_data = calc_roe_annualized(fin_rows)

    # OCF 质量
    result.ocf_quality = calc_ocf_quality(fin_rows)

    # ---- Step 4: PE/PB 历史分位 ----
    daily_rows = get_daily_basic_history(ts, ts_code)
    result.percentile = calc_historical_percentile(daily_rows)
    if result.percentile.get("error"):
        result.warnings.append(f"历史分位: {result.percentile['error']}")
    else:
        for w in result.percentile.get("warnings", []):
            result.warnings.append(w)
    result.sources["daily_basic"] = f"Tushare daily_basic: {result.percentile.get('n_samples', 0)} rows"

    # ---- Step 5: 无风险利率 ----
    if rf is None:
        rf, rf_src = get_china_bond_yield()
        result.rf_source = rf_src
    else:
        result.rf_source = "manual override"
    result.rf_china_10y = rf

    # ---- Step 6: 隐含增长率 ----
    # 优先使用当前价格 / TTM EPS 计算的 PE（更实时），
    # 若 TTM EPS 不可得则回退到 daily_basic PE
    current_pe = None
    ttm_eps = result.ttm.get("ttm_eps")
    if price and ttm_eps and ttm_eps > 0:
        current_pe = price / ttm_eps
    if current_pe is None:
        current_pe = result.percentile.get("pe_current")
    if current_pe and rf:
        result.implied_growth = implied_growth_detailed(current_pe, rf, erp)
    else:
        result.implied_growth = {"error": "PE/Rf 不可得"}

    # ---- Step 7: ROE-PB 匹配 ----
    bvps = result.bvps_data.get("bvps")
    roe_ann = result.roe_data.get("roe_annualized")
    if roe_ann is not None and bvps is not None and rf is not None:
        result.roe_pb_match = roe_pb_match(roe_ann, bvps, rf, erp)
    else:
        result.roe_pb_match = {"error": "ROE/BVPS/Rf 不足"}

    # ---- Step 8: 多情景综合 ----
    ttm_eps = result.ttm.get("ttm_eps")
    if price and ttm_eps and bvps and rf:
        pe_median = result.percentile.get("pe_median", 15)
        pb_median = result.percentile.get("pb_median", 2.0)
        pe_neg_pct = result.percentile.get("pe_neg_pct", 0.0)
        result.scenarios = multi_scenario_valuation(
            price=price, ttm_eps=ttm_eps, bvps=bvps,
            rf=rf, erp=erp,
            pe_median=pe_median, pb_median=pb_median,
            pe_negative_pct=pe_neg_pct,
        )
    else:
        result.scenarios = {"error": "基础数据不足"}

    return result


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def format_output(result: ValuationResult) -> str:
    """将 ValuationResult 格式化为可读文本输出。"""
    lines: list[str] = []
    sep = "─" * 72

    lines.append("")
    lines.append(sep)
    lines.append(f"  A 股科学估值计算 — {result.symbol} — {result.timestamp}")
    lines.append(sep)

    # 错误
    if result.errors:
        for e in result.errors:
            lines.append(f"  ❌ {e}")
        lines.append(sep)

    # ==== 1. 基础参数 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  一、基础参数")
    lines.append("━" * 60)
    lines.append(f"  当前股价          {result.price:.2f} 元" if result.price else "  当前股价          不可得")
    lines.append(f"  总股本            {result.total_shares_wan:,.0f} 万股" if result.total_shares_wan else "  总股本            不可得")
    lines.append(f"  总市值            {result.total_mv_yi:.2f} 亿" if result.total_mv_yi else "  总市值            不可得")
    lines.append(f"  中国 10Y 国债 (Rf) {result.rf_china_10y * 100:.2f}%" if result.rf_china_10y else "  中国 10Y 国债      不可得")
    lines.append(f"    <- 来源: {result.rf_source}")
    lines.append(f"  ERP (假设)        {result.erp * 100:.1f}%")
    r_required = (result.rf_china_10y or 0) + result.erp
    lines.append(f"  要求回报率 r       {r_required * 100:.2f}% (= Rf + ERP)")
    lines.append(f"  行情来源           {result.sources.get('quote', '?')}")

    # ==== 2. 财务数据 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  二、核心财务数据（最近一期）")
    lines.append("━" * 60)

    ttm = result.ttm
    if ttm.get("ttm_eps") is not None:
        lines.append(f"  TTM EPS            {ttm['ttm_eps']:.4f} 元/股")
        if result.price and ttm['ttm_eps'] > 0:
            current_pe_calc = result.price / ttm['ttm_eps']
            lines.append(f"  TTM PE (实时)      {current_pe_calc:.1f}x (= {result.price:.2f} / {ttm['ttm_eps']:.4f})")
        lines.append(f"  计算方法           {ttm.get('method', '')}")
        lines.append(f"  计算范围           {ttm.get('n_quarters', '?')} 个单季（累计→差→求和）")
        if ttm.get("quarterly_eps"):
            lines.append("  各单季 EPS:")
            for q in ttm["quarterly_eps"]:
                eps_s = q.get("eps_standalone", "?")
                lines.append(f"    {q['end_date']}: {eps_s}")
        if ttm.get("ttm_net_profit_yi"):
            lines.append(f"  TTM 净利润（估算） {ttm['ttm_net_profit_yi']:.2f} 亿")
    else:
        lines.append(f"  TTM EPS           不可得 ({ttm.get('error', '')})")

    bvps_d = result.bvps_data
    if bvps_d.get("bvps") is not None:
        lines.append(f"  BVPS (每股净资产)  {bvps_d['bvps']:.2f} 元")
        lines.append(f"    <- 报告期: {bvps_d.get('end_date', '?')}")
        if result.price and bvps_d["bvps"]:
            lines.append(f"  PB (当前)          {result.price / bvps_d['bvps']:.2f}x")
    else:
        lines.append(f"  BVPS              不可得 ({bvps_d.get('error', '')})")

    roe_d = result.roe_data
    if roe_d.get("roe_cumulative") is not None:
        lines.append(f"  累计 ROE（YTD）      {roe_d['roe_cumulative']:.2f}%")
        lines.append(f"  年化 ROE           {roe_d['roe_annualized']:.2f}%")
        lines.append(f"    <- 报告期: {roe_d.get('end_date', '?')}")

    ocf = result.ocf_quality
    if ocf.get("ocf_np_ratio") is not None:
        flag = ocf["quality"]
        lines.append(f"  OCF/净利润(TTM)     {ocf['ocf_np_ratio']:.2f}  {flag}")
        lines.append(f"    <- TTM OCFPS {ocf.get('ttm_ocfps', '?'):.4f} / TTM EPS {ocf.get('ttm_eps', '?'):.4f}")
        if ocf.get("note"):
            lines.append(f"    方法: {ocf['note']}")

    # ==== 3. 历史分位 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  三、历史估值位置")
    lines.append("━" * 60)

    pct = result.percentile
    if pct.get("pe_current"):
        lines.append(f"  PE(TTM) 当前       {pct['pe_current']:.2f}x")
        lines.append(f"  历史分位           {pct['pe_pct']:.1f}%（中位数 {pct.get('pe_median', '?'):.2f}x）")
        lines.append(f"  PE Band (±1σ)      {pct.get('pe_minus_1sigma', '?'):.1f} ~ {pct.get('pe_plus_1sigma', '?'):.1f}")
        lines.append(f"  有效样本           {pct.get('pe_valid', '?')} 交易日")
        if pct.get("pe_none_or_neg"):
            lines.append(f"  ⚠️  {pct['pe_none_or_neg']} 个交易日亏损被排除，PE 分位仅作位置参考")
    if pct.get("pb_current"):
        lines.append(f"  PB 当前            {pct['pb_current']:.2f}x")
        lines.append(f"  历史分位           {pct['pb_pct']:.1f}%（中位数 {pct.get('pb_median', '?'):.2f}x）")

    if pct.get("error"):
        lines.append(f"  历史分位          不可得 ({pct['error']})")

    # ==== 4. 隐含增长率 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  四、盈利收益率 vs 要求回报率 (Fed Model 变体)")
    lines.append("━" * 60)

    ig = result.implied_growth
    if ig.get("g_implied") is not None:
        lines.append(f"  Rf                 {ig['rf'] * 100:.2f}%")
        lines.append(f"  ERP                {ig['erp'] * 100:.1f}%")
        lines.append(f"  r (要求回报率)      {ig['r_required'] * 100:.2f}%")
        lines.append(f"  盈利收益率 (E/P)    {ig['earnings_yield'] * 100:.2f}%")
        lines.append(f"  ──────────────────────────────────")
        lines.append(f"  隐含增长率 g       {ig['g_implied'] * 100:.2f}%")
        if ig["g_implied"] < 0:
            lines.append(f"  🔴 负增长 —— 市场在定价盈利逐年萎缩")
        elif ig["g_implied"] < 0.02:
            lines.append(f"  🟡 低增长 —— 市场定价温和/保守")
        else:
            lines.append(f"  🟢 正增长 —— 市场定价结构性成长")

        lines.append("")
        lines.append("  不同 g 假设下的合理 PE:")
        lines.append(f"  {'g 假设':>12s}  {'合理 PE':>10s}  {'描述':<16s}")
        lines.append(f"  {'─' * 12}  {'─' * 10}  {'─' * 16}")
        for row in ig.get("fair_pe_by_g", []):
            lines.append(
                f"  {row['g_assumption']:>12s}  {str(row['fair_pe']):>10s}  {row['description']:<16s}"
            )

        # 当前 PE 对应 g
        current_pe_val = result.percentile.get("pe_current")
        if current_pe_val is None and result.price and result.ttm.get("ttm_eps"):
            if result.ttm["ttm_eps"] > 0:
                current_pe_val = result.price / result.ttm["ttm_eps"]
        # 当前 PE 来源说明
        ttm_eps_val = result.ttm.get("ttm_eps")
        if ttm_eps_val and result.price and ttm_eps_val > 0:
            realtime_pe = result.price / ttm_eps_val
            lines.append(f"  ──────────────────────────────────")
            lines.append(f"  当前 PE = {result.price:.2f} / {ttm_eps_val:.4f} = {realtime_pe:.1f}x")
            lines.append(f"    → 定价永续增长率 g ≈ {ig['g_implied'] * 100:.2f}%")
            lines.append(f"    (vs daily_basic PE {result.percentile.get('pe_current', '?'):.1f}x @ 旧收盘价)")
        elif current_pe_val:
            lines.append(f"  ──────────────────────────────────")
            lines.append(f"  当前 PE {current_pe_val:.1f}x → 定价 g ≈ {ig['g_implied'] * 100:.2f}%")
    else:
        lines.append(f"  计算不可得 ({ig.get('error', '')})")

    # ==== 5. ROE-PB 匹配 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  五、ROE-PB 理论匹配")
    lines.append("━" * 60)

    rpm = result.roe_pb_match
    if rpm.get("rows"):
        lines.append(f"  要求回报率 r = {rpm['r_required'] * 100:.2f}%")
        lines.append(f"  BVPS = {rpm['bvps']:.2f} 元")
        lines.append("")
        lines.append(f"  {'ROE 假设':>18s}  {'g 假设':>10s}  {'理论 PB':>10s}  {'理论价格':>10s}")
        lines.append(f"  {'─' * 18}  {'─' * 10}  {'─' * 10}  {'─' * 10}")
        for row in rpm["rows"]:
            lines.append(
                f"  {row['roe_assumption']:>18s}  {row['g_assumed']:>10s}  "
                f"{str(row['pb_theoretical']):>10s}  {str(row['price_theoretical']):>10s}"
            )
    else:
        lines.append(f"  计算不可得 ({rpm.get('error', '')})")

    # ==== 6. 多情景综合 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  六、多情景 × 多方法 综合估值区间")
    lines.append("━" * 60)

    sc = result.scenarios
    if sc.get("scenarios"):
        lines.append(f"  要求回报率 r = {sc['r_required'] * 100:.2f}%")
        lines.append(f"  TTM EPS = {sc.get('ttm_eps', '?'):.4f} | BVPS = {sc.get('bvps', '?'):.2f}")
        lines.append(f"  PB 中位数 (参考) = {sc.get('pb_median_ref', '?'):.1f}x")
        if sc.get("pe_median_distorted"):
            lines.append(f"  ⚠️  PE 中位数 ({sc.get('pe_median_ref', '?'):.1f}x) 已失真"
                         f"（历史 {sc.get('pe_negative_pct', 0):.0f}% 交易日亏损），")
            lines.append(f"      改用 Gordon 模型合理 PE 代替（基于 g 假设 + r）")
        else:
            lines.append(f"  PE 中位数 (参考) = {sc.get('pe_median_ref', '?'):.1f}x")
        lines.append("")

        for key, cfg in sc["scenarios"].items():
            m = cfg["methods"]
            lines.append(f"  ┌─ {cfg['label']}情景（概率 {cfg['probability']}）")
            lines.append(f"  │  假设前瞻 EPS: {cfg['eps_forward']:.4f} 元/股")
            lines.append(f"  │  PE 法 ({m['pe_multiple']:.1f}x):         {m['price_pe']:.2f} 元")
            lines.append(f"  │  PB 法 ({m['pb_multiple']:.2f}x):         {m['price_pb']:.2f} 元")
            pey = m.get("price_earnings_yield", "∞")
            if isinstance(pey, (int, float)) and pey < 99999:
                lines.append(f"  │  盈利收益法 (PE={m.get('fair_pe_ey', '?')}x): {pey:.2f} 元")
            else:
                lines.append(f"  │  盈利收益法: N/A (g≥r)")
            prices_valid = [
                p for p in [m["price_pe"], m["price_pb"], pey]
                if isinstance(p, (int, float)) and p < 99999
            ]
            if prices_valid:
                lines.append(f"  │  → 综合区间: {min(prices_valid):.0f} ~ {max(prices_valid):.0f} 元")
            lines.append(f"  └{'─' * 50}")
    else:
        lines.append(f"  计算不可得 ({sc.get('error', '')})")

    # ==== 7. 汇总 ====
    lines.append("")
    lines.append("━" * 60)
    lines.append("  七、综合估值参考区间")
    lines.append("━" * 60)

    if sc.get("scenarios"):
        bull_m = sc["scenarios"]["bull"]["methods"]
        base_m = sc["scenarios"]["base"]["methods"]
        bear_m = sc["scenarios"]["bear"]["methods"]

        def _range(m):
            ps = [
                p for p in [m["price_pe"], m["price_pb"],
                            m.get("price_earnings_yield")]
                if isinstance(p, (int, float)) and p < 99999
            ]
            return (min(ps), max(ps)) if ps else (0, 0)

        b1, b2 = _range(bull_m)
        n1, n2 = _range(base_m)
        p1, p2 = _range(bear_m)

        lines.append(f"  {'情景':<8s} {'价格区间':>16s}  {'关键假设'}")
        lines.append(f"  {'─' * 8}  {'─' * 16}  {'─' * 30}")
        lines.append(f"  {'乐观':<8s}  {b1:6.0f} ~ {b2:5.0f} 元   高增长+估值扩张（概率 20%）")
        lines.append(f"  {'中性':<8s}  {n1:6.0f} ~ {n2:5.0f} 元   稳健增长+估值中性（概率 50%）")
        lines.append(f"  {'悲观':<8s}  {p1:6.0f} ~ {p2:5.0f} 元   盈利收缩+估值压缩（概率 30%）")
        lines.append("")
        lines.append(f"  ⚠️  当前价格 {result.price:.2f} 元处于{'中性偏低' if result.price <= n2 else '中性区间' if result.price <= b1 else '偏高'}位置")

    # 质量预警
    if result.warnings:
        lines.append("")
        lines.append("  ⚠️  预警:")
        for w in result.warnings:
            lines.append(f"    - {w}")

    # OCF 预警
    ocf_r = result.ocf_quality.get("ocf_np_ratio")
    if ocf_r is not None and ocf_r < 0.5:
        lines.append("")
        lines.append(f"  🔴 重点预警: OCF/净利润 = {ocf_r:.2f}！利润高度依赖非现金项目（如投资收益），")
        lines.append(f"     实际自由现金流生成能力远弱于利润表所示。估值时应给予折价。")

    lines.append("")
    lines.append("━" * 60)
    lines.append("  ⚠️  免责声明")
    lines.append("━" * 60)
    lines.append("  以上所有估值计算均为基于公开数据的多情景假设推演，依赖对 Rf/ERP/g/ROE")
    lines.append("  等参数的主观选择。估值区间仅供参考，不构成任何投资建议、买卖指令或")
    lines.append("  目标价预测。周期股盈利波动极大，任何单点估值均有重大误差风险。")
    lines.append(sep)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="A 股科学估值计算器 — 多方法交叉估值",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 002466
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 002466 --rf 0.0173 --erp 0.06
    uv run python skills/invest-a-stock/scripts/valuation_calc.py 600519 --json
        """,
    )
    parser.add_argument("symbol", help="股票代码，如 002466 或 600519")
    parser.add_argument("--rf", type=float, default=None,
                        help="无风险利率（小数），默认自动获取中国 10Y 国债")
    parser.add_argument("--erp", type=float, default=DEFAULT_ERP,
                        help=f"股权风险溢价（小数），默认 {DEFAULT_ERP * 100:.0f}%%")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式")
    args = parser.parse_args()

    result = run_valuation(
        symbol=args.symbol,
        rf_override=args.rf,
        erp_override=args.erp,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2,
                         default=str))
    else:
        print(format_output(result))

    # 有严重错误时退出码 1
    if result.errors:
        critical = [e for e in result.errors if "失败" in e or "不可得" in e]
        if len(critical) >= 3:
            sys.exit(1)


if __name__ == "__main__":
    main()
