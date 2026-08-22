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
    5b. 机会成本行 (R8): E/P vs 10Y 国债利差（估值机会成本代理，不称 ERP）
    6. 反推市场隐含 g
    7. ROE-PB 理论匹配
    8. 多情景（乐观/中性/悲观）× 多方法估值区间
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("valuation_calc")

# 确保项目 lib 在 sys.path 中
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.env import ensure_env_loaded
from lib.nums import ONE_PER_WAN, ONE_PER_YI, parse_shares_wan, safe_float
from lib.financials import normalize_end_date
from lib.shared_codes import symbol_to_ts_code
from lib.shared_dates import shanghai_days_ago, shanghai_today
# NOTE: median / percentile_rank 原为 calc_historical_percentile 本地使用，
# 该函数现已委托 lib.valuation.valuation_summary（缺陷4 单公式源），此处不再直接调用。
from lib.tushare_client import TushareClient

from lib._invest_path import ensure_skills_lib_on_path

ensure_skills_lib_on_path()

from quote_tencent import fetch_tencent_quote  # noqa: E402 — skills/lib 共享库（v0.2.7 腾讯行情唯一实现）

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_ERP = 0.060          # A 股 ERP 默认 6%
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
    """将 002466 → 002466.SZ（Tushare 格式）。

    路由委托共享 codes.symbol_to_ts_code（6/9→SH、4/8/920→BJ、else SZ），
    避免本文件第三张内联路由表与共享库分叉（review fix #14）。
    """
    s = symbol.strip()
    if "." in s:
        return s
    return symbol_to_ts_code(s)


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
        mv_raw = safe_float(r.get("总市值"))
        return {
            "price": safe_float(r.get("最新价")),
            "change_pct": safe_float(r.get("涨跌幅")),
            "total_mv_yi": mv_raw / ONE_PER_YI if mv_raw is not None else None,  # akshare 总市值单位: 元 → 亿元
            "pe_dynamic": safe_float(r.get("市盈率-动态")),
            "pb": safe_float(r.get("市净率")),
            "source": "akshare.stock_zh_a_spot_em",
        }
    except Exception:
        logger.warning("akshare 行情失败，回退腾讯", exc_info=True)
        return _get_quote_tencent(symbol)


def _get_quote_tencent(symbol: str) -> dict[str, Any]:
    """腾讯行情兜底。

    v0.2.7 起委托 skills/lib/quote_tencent 唯一实现（统一路由/解析/单位换算），
    本层只保留返回契约：price/change_pct/total_mv_yi（亿元，round 2 位）/
    pe_dynamic/pb/source；失败返回 {"price": None, "source": "failed: tencent",
    "error": ...}（get_quote_ak 直传调用方，契约不变）。
    """
    code = _fmt_code_ak(symbol)
    try:
        # 惰性导入 lib.proxy：与 etf/_sources 行为一致（强制直连，测试可 patch）
        from lib.proxy import no_proxy_session
        with no_proxy_session() as sess:
            q = fetch_tencent_quote(code, session=sess)
        if q is None:
            raise ValueError("腾讯行情字段不足或价格缺失")
        return {
            "price": q["price"],
            "change_pct": q["change_pct"],
            "total_mv_yi": round(q["total_mv_yi"], 2) if q["total_mv_yi"] is not None else None,
            "pe_dynamic": q["pe_ratio"],
            "pb": q["pb"],
            "source": "tencent.qt.gtimg.cn",
        }
    except Exception as exc:
        logger.warning("腾讯行情也失败: %s", exc)
        return {"price": None, "source": "failed: tencent", "error": str(exc)}


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
                return parse_shares_wan(raw)
        return None
    except Exception:
        logger.warning("akshare 总股本获取失败", exc_info=True)
        return None


def get_financials(ts: TushareClient, ts_code: str) -> list[dict]:
    """从 Tushare fina_indicator 获取最近 8-12 期财务数据。

    Tushare query() 返回 DataFrame。
    fina_indicator 的 eps 是累计值（年报 Q4 = 全年 EPS），需做差得单季。

    返回按 end_date 升序排列、去重后的行列表。
    """
    try:
        start_date = shanghai_days_ago(3 * 365)
        end_date = shanghai_today()
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


def get_annual_net_profit(ts: TushareClient, ts_code: str, years: int = 10) -> list[dict]:
    """R2: 近 N 年年度净利润（income 表 n_income_attr_p，年报期 1231 结尾）。

    income 表为累计口径：年报期即全年净利。fina_indicator 的 net_profit 字段
    在低积分档被过滤（R12b 实测 2000 分档返回 None），故稳态盈利一律走 income 表。
    返回 [{year: "YYYY1231", net_profit: float}] 升序。
    """
    start_date = shanghai_days_ago(years * 365)
    end_date = shanghai_today()
    try:
        result = ts.query(
            "income", ts_code=ts_code,
            start_date=start_date, end_date=end_date,
            fields="end_date,n_income_attr_p",
        )
        if result is None or (hasattr(result, "empty") and result.empty):
            return []
        rows = result.to_dict(orient="records") if hasattr(result, "to_dict") else list(result)
        annual: dict[str, float] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            ed = str(r.get("end_date", ""))
            if not ed.endswith("1231"):
                continue
            npv = r.get("n_income_attr_p")
            if npv is None:
                continue
            try:
                v = float(npv)
                if v != v:  # NaN 等同缺失（calc_steady_earnings 中位数污染防护）
                    continue
                annual[ed] = v  # 同一年重复行取最后一条（后覆盖前）
            except (TypeError, ValueError):
                continue
        return [{"year": ed, "net_profit": v} for ed, v in sorted(annual.items())]
    except Exception:
        logger.warning("Tushare income 年度净利查询失败", exc_info=True)
        return []


def calc_steady_earnings(
    annual_rows: list[dict],
    *,
    cycle_start: str | None = None,
    cycle_end: str | None = None,
    method: str = "median",
    min_years: int = 5,
) -> dict:
    """R2: 稳态盈利估算（穿越周期视角）。

    method:
      median  — 年度净利润中位数（默认）
      trimmed — 截尾均值（去最高最低年）
      range   — 用户定义周期区间（cycle_start/cycle_end）内的均值
    样本 < min_years → 返回不可得（避免用 2-3 年数据冒充稳态——海力士式
    "周期高点低 PE 陷阱"的识别前提是足够长的周期样本）。
    """
    rows = [r for r in annual_rows if r.get("net_profit") is not None]
    if cycle_start:
        rows = [r for r in rows if r["year"] >= cycle_start]
    if cycle_end:
        rows = [r for r in rows if r["year"] <= cycle_end]
    if len(rows) < min_years:
        return {
            "available": False,
            "reason": f"年度样本 {len(rows)} < {min_years} 年，稳态盈利不可得",
            "n_years": len(rows),
        }
    vals = sorted(float(r["net_profit"]) for r in rows)
    if method == "median":
        n = len(vals)
        mid = n // 2
        steady = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2
    elif method == "trimmed":
        if len(vals) <= 2:
            steady = sum(vals) / len(vals)
        else:
            steady = sum(vals[1:-1]) / (len(vals) - 2)
    elif method == "range":
        steady = sum(vals) / len(vals)
    else:
        raise ValueError(f"未知 method: {method}")
    return {
        "available": True,
        "method": method,
        "steady_earnings": round(steady, 2),
        "n_years": len(vals),
        "period": f"{rows[0]['year']}~{rows[-1]['year']}",
        "min": vals[0],
        "max": vals[-1],
        "latest": vals[-1],
    }


# 周期中枢 PE 兜底配置（钢铁/化工/有色等周期行业历史常见中枢——经验估计值，逐项来源待补；
# 规范：R4 行业模块化后迁入 lib/industry/ 并逐项标注参数来源与适用期间，当前为过渡兜底）
_CYCLE_PE_DEFAULT = 12.0
_CYCLE_PE_BY_INDUSTRY: dict[str, float] = {
    "钢铁": 8.0,
    "煤炭": 9.0,
    "石油石化": 10.0,
    "化工": 12.0,
    "有色金属": 15.0,
    "小金属": 15.0,
    "建筑材料": 12.0,
}


def calc_cycle_pe(industry: str | None = None, user_pe: float | None = None) -> float:
    """R2: 周期中枢 PE。优先级：用户覆盖 > 行业配置 > 默认 12。"""
    if user_pe is not None:
        return float(user_pe)
    if industry:
        for key, val in _CYCLE_PE_BY_INDUSTRY.items():
            if key in industry:
                return val
    return _CYCLE_PE_DEFAULT


def steady_valuation_band(
    steady: dict, cycle_pe: float, *, band_pct: float = 0.25,
) -> dict | None:
    """R2: 稳态盈利 × 周期中枢 PE → 穿越周期估值区间（±band_pct 带宽）。

    输出为多情景参考（乐观=中枢 PE 上沿 / 悲观=下沿），非单一目标价。
    """
    if not steady.get("available"):
        return None
    if not (steady.get("steady_earnings", 0) > 0):
        # 亏损期（或 NaN）→ 稳态带无意义（镜像 Step 10 takeover 的 >0 守卫）
        return None
    mid = steady["steady_earnings"] * cycle_pe
    return {
        "low": round(mid * (1 - band_pct), 2),
        "mid": round(mid, 2),
        "high": round(mid * (1 + band_pct), 2),
        "cycle_pe": cycle_pe,
        "band_pct": band_pct,
    }


# 金融业豁免关键词（EV/EBITDA 对银行/保险无意义）
_FINANCIAL_INDUSTRY_KEYWORDS = ("银行", "保险", "证券", "多元金融", "非银")


def is_financial_industry(industry: str | None) -> bool:
    """R3: 金融业判定（EV/EBITDA 不适用）。"""
    if not industry:
        return False
    return any(k in industry for k in _FINANCIAL_INDUSTRY_KEYWORDS)


def _nan_to_none(v: Any) -> float | None:
    """NaN → None（income 表 ebitda 等字段可能返回 NaN，须与缺失同等对待）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    return None if math.isnan(f) else f


def calc_ev_ebitda(
    *,
    total_mv_yi: float | None,
    cash: float | None,
    st_loan: float | None = None,
    lt_loan: float | None = None,
    bond_payable: float | None = None,
    ebitda: float | None = None,
    ebitda_period: str | None = None,
    industry: str | None = None,
) -> dict:
    """R3: EV/EBITDA 可审计桥接表。

    口径：EV = 市值 + 有息负债（短贷+长贷+应付债券） - 现金
          EBITDA = fina_indicator.ebitda（**年报期 1231，全年口径**）
    注意：fina_indicator.ebitda 为累计 YTD 口径，仅 1231 年报期为全年数；
    非年报期（0331/0630/0930）累计值会使 EV/EBITDA 高估（如 Q1 累计仅约
    全年 1/4）。调用方必须只传 1231 年报期的 EBITDA，并可通过 ebitda_period
    标注实际使用的报告期。
    可审计性：逐项标注可得/缺失；有息负债全缺失时降级为
    「净现金口径 EV = 市值 - 现金」，并提示方向偏差。
    金融业 → 不适用（EBITDA 无意义）。
    """
    if is_financial_industry(industry):
        return {"available": False, "reason": "不适用（金融业，EBITDA 无意义）",
                "exempt": True}
    total_mv_yi = _nan_to_none(total_mv_yi)
    cash = _nan_to_none(cash)
    st_loan = _nan_to_none(st_loan)
    lt_loan = _nan_to_none(lt_loan)
    bond_payable = _nan_to_none(bond_payable)
    ebitda = _nan_to_none(ebitda)
    # ebitda_period 是报告期字符串（如 "20251231"），不做数值转换
    missing: list[str] = []
    if cash is None:
        missing.append("cash")
    if st_loan is None:
        missing.append("short_loan")
    if lt_loan is None:
        missing.append("long_loan")
    if bond_payable is None:
        missing.append("bond_payable")
    if ebitda is None:
        missing.append("ebitda")

    debt = None
    if st_loan is not None or lt_loan is not None or bond_payable is not None:
        debt = (st_loan or 0.0) + (lt_loan or 0.0) + (bond_payable or 0.0)
    ev = None
    if total_mv_yi is not None and cash is not None:
        ev = total_mv_yi * ONE_PER_YI -(cash or 0.0)
        if debt is not None:
            ev += debt
    ratio = None
    if ev is not None and ebitda not in (None, 0.0):
        ratio = round(ev / ebitda, 2)

    # 有息负债部分缺失（2000 分档过滤单个科目）时，缺失分量被按 0 计 →
    # EV 被低估且无提示。note 三分支：全缺失 → 净现金口径；部分缺失 →
    # 显式降级说明（review #11）；完整 → None。
    missing_debt = [
        label for label, v in (("短贷", st_loan), ("长贷", lt_loan),
                               ("应付债券", bond_payable))
        if v is None
    ]
    # 实际可得分量标签（引擎预计算，供渲染行精确标注构成；缺失分量
    # 按 0 计但不得出现在标签里，否则行内口径误导 —— batch-test P1-2）
    debt_parts = [label for label, v in (("短贷", st_loan), ("长贷", lt_loan),
                                         ("应付债券", bond_payable))
                  if v is not None]
    if debt is None:
        note = ("有息负债不可得（低积分档字段过滤），EV 为净现金口径近似，"
                "若公司有负债则实际 EV 更高")
    elif missing_debt:
        note = (
            f"有息负债部分缺失（{', '.join(missing_debt)} 不可得），EV 按可得"
            "分量计算，若缺失项实际存在则 EV 被低估"
        )
    else:
        note = None

    return {
        "available": ev is not None and ebitda is not None,
        "exempt": False,
        "bridge": {
            "mv_yi": round(total_mv_yi, 2) if total_mv_yi is not None else None,
            "cash_yi": round(cash / ONE_PER_YI, 2) if cash is not None else None,
            "interest_debt_yi": round(debt / ONE_PER_YI, 2) if debt is not None else None,
            "ev_yi": round(ev / ONE_PER_YI, 2) if ev is not None else None,
        },
        "ebitda_yi": round(ebitda / ONE_PER_YI, 2) if ebitda is not None else None,
        "ebitda_period": ebitda_period,
        "ev_ebitda": ratio,
        "debt_available": debt is not None,
        "debt_label": "+".join(debt_parts) if debt_parts else None,
        "missing": missing,
        "note": note,
    }


def _latest_annual_ebitda(fin_rows: list[dict]) -> tuple[float | None, str | None, str | None]:
    """从 fina_indicator 行中取**最近年报期（1231）**的 EBITDA。

    fina_indicator.ebitda 为累计 YTD 口径，仅 1231 年报期为全年数。
    用最新非年报期（如 2026Q1 累计）会静默高估 EV/EBITDA 约 4 倍，
    故必须限定年报期；若 3 年内无 1231 行 → 明确降级（返回不可得 + 口径说明），
    绝不静默使用累计期。

    Returns:
        (ebitda, period, note)：note 非 None 表示降级/不可得说明。
    """
    annual = [
        r for r in fin_rows
        if normalize_end_date(str(r.get("end_date", ""))).endswith("1231")
    ]
    for r in reversed(annual):
        try:
            v = float(r["ebitda"])
        except (TypeError, ValueError, KeyError):
            continue
        if v != v:  # NaN 等同缺失（与 _latest_annual_ebitda_from_income 同型）
            continue
        return v, str(r.get("end_date", "")), None
    latest_ed = str(fin_rows[-1].get("end_date", "")) if fin_rows else "?"
    return None, None, (
        f"EBITDA 不可得：3 年内无 1231 年报期（最新期 {latest_ed} 为累计口径，"
        "不可换算为全年 EBITDA，EV/EBITDA 不计算）"
    )


def _latest_annual_ebitda_from_income(
    ts: TushareClient, ts_code: str
) -> tuple[float | None, str | None]:
    """income 表 ebitda 兜底（fina_indicator 2000 分档过滤 ebitda 时，R12b 同型）。

    同口径纪律：income.ebitda 同为累计口径，仅取最近 1231 年报期（全年数）；
    3 年内无年报期或查询失败 → (None, None)，由调用方走「不可得」降级。
    """
    try:
        df = ts.query(
            "income", ts_code=ts_code,
            start_date=shanghai_days_ago(3 * 365), end_date=shanghai_today(),
            fields="end_date,ebitda",
        )
        if df is None or (hasattr(df, "empty") and df.empty):
            return None, None
        rows = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
    except Exception as exc:
        logger.warning("Tushare income ebitda 查询失败（R3 兜底）: %s", exc)
        return None, None
    annual = [
        r for r in rows
        if isinstance(r, dict)
        and normalize_end_date(str(r.get("end_date", ""))).endswith("1231")
    ]
    # 取最近 1231 期（升序 + reverse），与 _latest_annual_ebitda 的
    # reversed 语义一致——此前升序取第一个 = 3 年窗口内最旧一期（review #2）
    for r in sorted(annual, key=lambda x: str(x.get("end_date", "")), reverse=True):
        try:
            v = float(r["ebitda"])
        except (TypeError, ValueError, KeyError):
            continue
        if v != v:  # NaN 等同缺失
            continue
        return v, str(r.get("end_date", ""))
    return None, None


def get_daily_basic_history(
    ts: TushareClient, ts_code: str, years: int = 5
) -> list[dict]:
    """获取 PE/PB 历史序列。

    Tushare query() 直接返回 DataFrame，不包在 dict 里。
    daily_basic 支持 start_date/end_date 参数。
    """
    start_date = shanghai_days_ago(years * 365)
    end_date = shanghai_today()
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

    尝试 akshare；失败标注「不可得」（v0.2.7 起无静态近似兜底——硬编码
    近似值会随日期过期，违反数据可追溯原则，AGENTS.md 约束 2/3）。
    返回 (yield_decimal, source_description)；调用方对 None 均已有降级
    （run_valuation 的 implied_growth / opportunity_cost / roe_pb / scenarios
    全部 guard rf is None）。
    """
    try:
        import akshare as ak
        # v0.2.7 review：start_date 硬编码 "20260101" 是时间炸弹；且
        # ak.bond_china_yield 要求窗口 < 1 年、默认 end_date="20210124" 已过期
        # （start > end 会恒空）。动态取最近 360 天（严格小于一年上限）；
        # 返回序列按日期升序（akshare 内 sort_values），iloc[-1] 为最新。
        end_date = shanghai_today()
        start_date = shanghai_days_ago(360)
        df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
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

    return None, "不可得：akshare.bond_china_yield 无数据（已移除静态近似兜底）"


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
    """计算 TTM EPS：最近 **连续** 4 个单季 EPS 之和（不允许断档季）。

    使用 fina_indicator 的 eps（累计值），转为单季后求和。
    与 calc_ocf_quality 对齐：经 _latest_contiguous_ttm_dates 校验连续性。
    缺季/断档时降级：最新期无法构成连续窗口 → 回退更早连续窗口并标注陈旧；
    无任何连续 4 期 → 标不可得。绝不静默混入过期季度（混入会污染
    当前 PE、隐含增长 g 及全部多情景估值）。

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

    dates = [q["end_date"] for q in standalone]
    contiguous = _latest_contiguous_ttm_dates(dates)
    if contiguous is None:
        return {
            "ttm_eps": None,
            "error": (
                "无连续 4 个报告期单季 EPS（最近期序 "
                f"{', '.join(dates[-4:])} 存在断档/空洞），"
                "TTM EPS 标为不可得，避免混入过期季度"
            ),
            "quarterly_eps": standalone,
        }
    by_date = {q["end_date"]: q for q in standalone}
    last4 = [by_date[d] for d in contiguous]
    stale = contiguous[-1] != dates[-1]
    ttm_eps = sum(q["eps_standalone"] for q in last4)

    # 净利润绝对值
    net_profit_ttm = None
    if total_shares_wan:
        net_profit_ttm = ttm_eps * total_shares_wan * ONE_PER_WAN

    return {
        "ttm_eps": round(ttm_eps, 4),
        "ttm_net_profit_yi": round(net_profit_ttm / ONE_PER_YI, 2) if net_profit_ttm else None,
        "quarterly_eps": last4,
        "n_quarters": len(last4),
        "stale": stale,
        "stale_note": (
            f"最新期 {dates[-1]} 无法构成连续 TTM，回退至截至 "
            f"{contiguous[-1]} 的连续窗口（陈旧 TTM，含缺季断档）"
            if stale else None
        ),
        "method": "fina_indicator eps 累计 → 单季差 → TTM=Σ(最近连续4个单季)",
        "note": (
            "fina_indicator 的 eps 为年度累计值（0331=Q1, 0630=H1, 0930=前3Q, 1231=全年），"
            "单季 EPS = 本期累计 − 前期累计。TTM = Σ(最近连续4个单季)；"
            "断档时降级为不可得或陈旧标注。"
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


def _prev_report_end_date(ed: str) -> str | None:
    """季度报告期末 → 上一季度末（YYYYMMDD）。非法 mmdd 返回 None。"""
    if len(ed) < 8:
        return None
    year, mmdd = int(ed[:4]), ed[4:8]
    chain = {
        "0331": (year - 1, "1231"),
        "0630": (year, "0331"),
        "0930": (year, "0630"),
        "1231": (year, "0930"),
    }
    if mmdd not in chain:
        return None
    y, m = chain[mmdd]
    return f"{y}{m}"


def _latest_contiguous_ttm_dates(matched_dates: list[str]) -> list[str] | None:
    """在已匹配的 end_date 中，找最近一段连续 4 个报告期（真 TTM）。

    从最新匹配日往回要求上一季也在集合中；若最新锚点断档，再试更早的锚点。
    """
    matched_set = set(matched_dates)
    for end in reversed(matched_dates):
        window = [end]
        cur = end
        for _ in range(3):
            prev = _prev_report_end_date(cur)
            if prev is None or prev not in matched_set:
                break
            window.append(prev)
            cur = prev
        else:
            window.reverse()
            return window
    return None


def calc_ocf_quality(fin_rows: list[dict]) -> dict[str, Any]:
    """经营现金流 / 净利润 质量比。

    使用 fina_indicator 的 ocfps（每股经营现金流）和 eps（累计每股收益）。
    用最近 **连续** 4 个单季 ocfps 之和 / 同窗 eps 之和（真 TTM，不允许断档季）。
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

    # 对齐 eps/ocfps：交集后取最近一段连续 4 季（非任意 last-4）
    eps_by_date = {q["end_date"]: q for q in standalone_eps}
    ocf_by_date = {q["end_date"]: q for q in ocf_standalone}
    matched_dates = sorted(set(eps_by_date) & set(ocf_by_date))
    if len(matched_dates) < 4:
        return {
            "ocf_np_ratio": None,
            "error": f"数据不足（EPS/OCFPS 匹配仅{len(matched_dates)}期）",
        }
    last4_dates = _latest_contiguous_ttm_dates(matched_dates)
    if last4_dates is None:
        return {
            "ocf_np_ratio": None,
            "error": "数据不足（无连续4个 EPS/OCFPS 匹配单季）",
        }
    eps_last4 = [eps_by_date[d] for d in last4_dates]
    ocf_last4 = [ocf_by_date[d] for d in last4_dates]

    sum_eps = sum(q["eps_standalone"] for q in eps_last4)
    sum_ocf = sum(q["ocfps_standalone"] for q in ocf_last4)

    if sum_eps <= 0:
        return {"ocf_np_ratio": None, "error": "TTM EPS 非正，无法计算覆盖比"}

    ratio = sum_ocf / sum_eps
    return {
        "ocf_np_ratio": round(ratio, 4),
        "ttm_ocfps": round(sum_ocf, 4),
        "ttm_eps": round(sum_eps, 4),
        "quality": "健康" if ratio >= 0.8 else ("偏低" if ratio >= 0.5 else "🔴 预警（<0.5）"),
        "end_date": eps_last4[-1]["end_date"] if eps_last4 else "?",
        "note": "基于最近连续 4 个单季 EPS/OCFPS 之和（fina_indicator 累计→单季差→TTM）",
    }


def calc_historical_percentile(
    daily_rows: list[dict],
) -> dict[str, Any]:
    """PE/PB 历史分位计算。

    Tushare daily_basic 对亏损期返回 None/null PE（非负值），
    无法直接计为负值天数。通过 daily_rows 总数 vs PE 有效样本数的差值推断。

    核心统计（当前值/分位/中位数）委托 lib.valuation.valuation_summary——
    权威引擎单一公式源（缺陷4：消除脚本路径与 lib 路径双份公式漂移）。
    脚本侧仅保留 rows→序列数据预处理、±1σ Band（lib 无对应）与输出 schema 映射。

    窗口标签由样本行数经 lib.valuation.valuation_window_label 推断
    （≥1250 交易日即"近5年"）——不再接受 years 参数（死参数，从未参与计算）。
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

    if not pe_seq and not pb_seq:
        return {"error": "PE/PB 历史数据不足"}

    total_daily = len(daily_rows)
    from lib.valuation import (
        valuation_summary as _lib_valuation_summary,
        valuation_window_label,
    )
    vs = _lib_valuation_summary(
        pe_seq, pb_seq, window_label=valuation_window_label(total_daily))

    result: dict[str, Any] = {"n_samples": total_daily, "warnings": list(vs.get("warnings") or [])}

    pe = vs.get("pe") or {}
    if pe.get("current") is not None:
        current_pe = pe["current"]
        n = len(pe_seq)
        mu = sum(pe_seq) / n
        sigma = math.sqrt(sum((v - mu) ** 2 for v in pe_seq) / n)
        pe_neg_inferred = pe_none_count
        pe_neg_pct = pe_neg_inferred / total_daily if total_daily > 0 else 0.0
        result.update({
            "pe_valid": n,
            "pe_none_or_neg": pe_neg_inferred,
            "pe_neg_pct": round(pe_neg_pct, 4),
            "pe_current": current_pe,
            "pe_pct": round(pe["pct"], 1) if pe.get("pct") is not None else None,
            "pe_median": pe.get("median"),
            "pe_mean": round(mu, 2),
            "pe_sigma": round(sigma, 2) if sigma else None,
            "pe_plus_1sigma": round(mu + sigma, 2) if sigma else None,
            "pe_minus_1sigma": round(mu - sigma, 2) if sigma else None,
        })
        if pe_neg_pct > 0.3:
            result["warnings"].append(
                f"PE 历史序列中约 {pe_neg_pct * 100:.0f}% 交易日 PE 不可得（通常为亏损期），"
                f"PE 分位数仅作位置参考，不反映估值贵贱。PB 分位更有参考价值。"
            )

    pb = vs.get("pb") or {}
    if pb.get("current") is not None:
        result.update({
            "pb_current": pb["current"],
            "pb_pct": round(pb["pct"], 1) if pb.get("pct") is not None else None,
            "pb_median": pb.get("median"),
        })

    return result


def implied_growth_detailed(
    pe: float,
    rf: float,
    erp: float = DEFAULT_ERP,
) -> dict[str, Any]:
    """戈登模型反推隐含增长率 + 不同 g 假设下的合理 PE。

    g_implied = r - E/P = (rf + erp) - 1/pe
    fair_pe(g) = 1 / (rf + erp - g)

    核心计算（PE 非正检查 / r / g_implied / PE>50 提示）委托
    lib.valuation.implied_growth——权威引擎（缺陷4：消除脚本路径与 lib 路径
    双份公式漂移）。fair_pe_by_g 表与 note 为脚本侧特有展示（lib 无对应），保留本地。
    """
    from lib.valuation import implied_growth as _lib_implied_growth

    core = _lib_implied_growth(pe, rf, erp)
    if core.get("error"):
        return {"error": core["error"]}

    r = core["r"]
    earnings_yield = 1.0 / pe
    g_implied = core["g_implied"]

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
        "r_required": r,
        "earnings_yield": round(earnings_yield, 4),
        "g_implied": g_implied,
        "fair_pe_by_g": fair_pe_table,
        "note": (
            f"当前 PE {pe:.2f}x 隐含永续增长率 {g_implied * 100:.2f}%。"
            f"若 g_implied < 0，市场定价了盈利萎缩预期。"
        ),
    }


def calc_opportunity_cost(pe: float | None, rf_10y: float | None) -> dict:
    """R8: 估值机会成本行 — 盈利收益率 (E/P) vs 中国 10Y 国债收益率。

    earnings_yield = 1/pe（pe>0 时），ey_minus_10y = E/P − 10Y（利差）。
    利差是"持有一单位盈利收益 vs 无风险收益"的机会成本代理——**不称 ERP**
    （ERP 另有含权益风险溢价的定义口径）。E/P 高于 10Y 越多，估值相对债券
    越便宜；倒挂（利差 < 0）说明市场把大部分收益押注在增长预期上。

    pe<=0（亏损期）或 rf 缺失 → available=False，标注不可得。
    """
    if pe is None or pe <= 0 or rf_10y is None:
        reasons: list[str] = []
        if pe is None or pe <= 0:
            reasons.append("PE 非正（亏损期或无有效 PE）")
        if rf_10y is None:
            reasons.append("中国 10Y 国债收益率不可得")
        return {
            "available": False,
            "reason": "；".join(reasons),
        }
    earnings_yield = 1.0 / pe
    ey_minus_10y = earnings_yield - rf_10y
    return {
        "available": True,
        "pe": round(pe, 2),
        "earnings_yield_pct": round(earnings_yield * 100, 2),
        "rf_10y_pct": round(rf_10y * 100, 2),
        "ey_minus_10y_pp": round(ey_minus_10y * 100, 2),
        "note": (
            "口径：盈利收益率 E/P = 1/PE（TTM），对比中国 10Y 国债收益率；"
            "利差 (E/P − 10Y) 为估值机会成本代理，不称 ERP。"
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
        # 亏损期（eps_fwd<=0）：PE 法/盈利收益法无意义，仅 PB 法产出；
        # 负价格区间会误导"综合区间/处于中性偏低"判断
        price_pe = round(eps_fwd * pe_mult, 2) if eps_fwd > 0 else None
        price_pb = round(bvps * pb_mult, 2)
        if eps_fwd > 0 and r > g_assume:
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
    # R2: 稳态盈利估值（穿越周期视角）
    steady: dict = field(default_factory=dict)
    # R3: EV/EBITDA 企业价值桥接
    ev_ebitda: dict = field(default_factory=dict)
    # R8: 机会成本行（盈利收益率 vs 10Y 国债利差）
    opportunity_cost: dict = field(default_factory=dict)
    # 获取来源
    sources: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _query_latest_balancesheet(ts, ts_code: str) -> tuple[float | None, float | None,
                                                          float | None, float | None]:
    """R3: 最新一期 balancesheet 的 money_cap/short_loan/long_loan/bond_payable（元）。

    查询失败返回全 None（与 R12b 同型兜底）。
    """
    cash = st_loan = lt_loan = bond_payable = None
    try:
        bs_df = ts.query(
            "balancesheet", ts_code=ts_code,
            start_date=shanghai_days_ago(2 * 365), end_date=shanghai_today(),
            fields="end_date,money_cap,short_loan,long_loan,bond_payable",
        )
        if bs_df is not None and not (hasattr(bs_df, "empty") and bs_df.empty):
            bs_rows = bs_df.to_dict(orient="records") if hasattr(bs_df, "to_dict") else list(bs_df)
            latest = None
            for r in sorted(bs_rows, key=lambda x: str(x.get("end_date", ""))):
                latest = r
            if latest:
                cash = safe_float(latest.get("money_cap"))
                st_loan = safe_float(latest.get("short_loan"))
                lt_loan = safe_float(latest.get("long_loan"))
                bond_payable = safe_float(latest.get("bond_payable"))
    except Exception:
        logger.warning("Tushare balancesheet 查询失败（R3 EV 桥接）", exc_info=True)
    return cash, st_loan, lt_loan, bond_payable


def run_valuation(
    symbol: str,
    rf_override: float | None = None,
    erp_override: float | None = None,
    *,
    steady: bool = False,
    cycle_start: str | None = None,
    cycle_end: str | None = None,
    cycle_method: str = "median",
    cycle_pe: float | None = None,
    ev_ebitda: bool = False,
    ev_ebitda_industry: str | None = None,
) -> ValuationResult:
    """执行完整估值计算流程。

    Args:
        symbol: 股票代码 (如 "002466")
        rf_override: 手动指定无风险利率（小数）
        erp_override: 手动指定 ERP（小数）
        steady: R2 — 追加稳态盈利估值（穿越周期视角）
        cycle_start/cycle_end: 用户定义周期区间（cycle_method="range" 时生效）
        cycle_method: median / trimmed / range
        cycle_pe: 周期中枢 PE（默认行业配置/12）

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
            shares_wan = round(result.total_mv_yi * ONE_PER_YI /(price * ONE_PER_WAN), 2)
            result.total_shares_wan = shares_wan
    elif price and shares_wan:
        result.total_mv_yi = round(price * shares_wan * ONE_PER_WAN / ONE_PER_YI, 2)

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

    # ---- Step 6b (R8): 机会成本行（盈利收益率 vs 10Y 国债利差）----
    # pe 用 Step 6 同源的 current_pe（实时价格/TTM EPS，回退 daily_basic），
    # rf 用 Step 5 取得的中国 10Y 国债；亏损或无 rf 时返回不可得标注。
    result.opportunity_cost = calc_opportunity_cost(current_pe, rf)

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

    # ---- Step 9 (R2): 稳态盈利估值（value --steady）----
    if steady:
        annual = get_annual_net_profit(ts, ts_code)
        ste = calc_steady_earnings(
            annual,
            cycle_start=cycle_start, cycle_end=cycle_end, method=cycle_method,
        )
        block = {"steady": ste, "annual": annual}
        if ste.get("available"):
            cyc_pe = calc_cycle_pe(industry=None, user_pe=cycle_pe)
            band = steady_valuation_band(ste, cyc_pe)
            block["cycle_pe"] = cyc_pe
            block["band"] = band
            # 当期市值 vs 稳态估值对照（海力士式"周期高点低 PE 陷阱"识别）
            # steady_earnings 为全公司净利（元）× PE → 稳态市值（元），转亿元对照。
            if result.total_mv_yi and band:
                block["mv_vs_steady"] = {
                    "total_mv_yi": result.total_mv_yi,
                    # 存储侧保留原精度：round(…, 2) 在带 <0.005 亿元时塌为 0.0，
                    # 损坏落盘字段且渲染侧误报「数值不可得」；渲染侧自行舍入（review #8）
                    "steady_mv_low_yi": band["low"] / ONE_PER_YI,
                    "steady_mv_mid_yi": band["mid"] / ONE_PER_YI,
                    "steady_mv_high_yi": band["high"] / ONE_PER_YI,
                }
        result.steady = block

    # ---- Step 10 (R3): EV/EBITDA 桥接（value --ev-ebitda）----
    if ev_ebitda:
        # fina_indicator.ebitda 为累计 YTD 口径，仅年报期（1231）为全年数；
        # 必须取年报期，无年报期时明确降级（_latest_annual_ebitda 返回口径说明）
        ebitda_v, ebitda_period, ebitda_note = _latest_annual_ebitda(fin_rows)
        if ebitda_v is None:
            # R12b 同型兜底：fina_indicator 2000 分档过滤 ebitda → income 表补齐
            inc_ebitda, inc_period = _latest_annual_ebitda_from_income(ts, ts_code)
            if inc_ebitda is not None:
                ebitda_v, ebitda_period = inc_ebitda, inc_period
                ebitda_note = (
                    f"EBITDA 取自 income 表（fina_indicator 积分过滤兜底），"
                    f"报告期 {inc_period}，年报口径"
                )
        cash, st_loan, lt_loan, bond_payable = _query_latest_balancesheet(ts, ts_code)
        ev_block = calc_ev_ebitda(
            total_mv_yi=result.total_mv_yi,
            cash=cash, st_loan=st_loan, lt_loan=lt_loan, bond_payable=bond_payable,
            ebitda=ebitda_v, ebitda_period=ebitda_period, industry=ev_ebitda_industry,
        )
        if ebitda_note:
            ev_block["ebitda_note"] = ebitda_note
        # 私有化检验（研究问题，非结论）：市值 / 稳态盈利 → 回本年限
        ste = (result.steady or {}).get("steady")
        if (ev_block.get("available") and ste and ste.get("available")
                and result.total_mv_yi and ste.get("steady_earnings", 0) > 0):
            payback = result.total_mv_yi / (ste["steady_earnings"] / ONE_PER_YI)
            ev_block["takeover_payback_years"] = round(payback, 1)
            ev_block["takeover_note"] = (
                "研究问题（非结论）：在稳态盈利假设下当前市值的回本年限；"
                "不构成买入/目标价判断")
        result.ev_ebitda = ev_block

    return result


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def _format_header(result: ValuationResult, lines: list[str], sep: str) -> None:
    """标题 + 错误块。"""
    lines.append("")
    lines.append(sep)
    lines.append(f"  A 股科学估值计算 — {result.symbol} — {result.timestamp}")
    lines.append(sep)

    # 错误
    if result.errors:
        for e in result.errors:
            lines.append(f"  ❌ {e}")
        lines.append(sep)


def _format_section_basic_params(result: ValuationResult, lines: list[str]) -> None:
    """一、基础参数。"""
    lines.append("")
    lines.append("━" * 60)
    # LAW 17: 标题含关键数据
    price_s = f"{result.price:.2f} 元" if result.price else "?"
    mv_s = f"{result.total_mv_yi:.2f} 亿" if result.total_mv_yi else "?"
    rf_s = f"Rf={result.rf_china_10y * 100:.2f}%" if result.rf_china_10y else "Rf=?"
    lines.append(f"  一、基础参数：{price_s} · {mv_s} 市值 · {rf_s}")
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


def _format_section_financials(result: ValuationResult, lines: list[str]) -> None:
    """二、核心财务数据（TTM EPS · BVPS · ROE · OCF 质量）。"""
    lines.append("")
    lines.append("━" * 60)
    lines.append("  二、核心财务数据（TTM EPS · BVPS · ROE · OCF 质量）")
    lines.append("━" * 60)

    ttm = result.ttm
    if ttm.get("ttm_eps") is not None:
        lines.append(f"  TTM EPS            {ttm['ttm_eps']:.4f} 元/股")
        if result.price and ttm['ttm_eps'] > 0:
            current_pe_calc = result.price / ttm['ttm_eps']
            lines.append(f"  TTM PE (实时)      {current_pe_calc:.1f}x (= {result.price:.2f} / {ttm['ttm_eps']:.4f})")
        if ttm.get("stale"):
            lines.append(f"  ⚠️ 陈旧标注        {ttm.get('stale_note', 'TTM 非最新期（缺季断档回退）')}")
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


def _format_section_percentile(result: ValuationResult, lines: list[str]) -> None:
    """三、历史估值位置（PE/PB 分位 · Band · 中位数对照）。"""
    lines.append("")
    lines.append("━" * 60)
    lines.append("  三、历史估值位置（PE/PB 分位 · Band · 中位数对照）")
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


def _format_section_implied_growth(result: ValuationResult, lines: list[str]) -> None:
    """四、盈利收益率 vs 要求回报率 · 隐含增长率 g_implied 对照（含 R8 机会成本行）。"""
    lines.append("")
    lines.append("━" * 60)
    lines.append("  四、盈利收益率 vs 要求回报率 · 隐含增长率 g_implied 对照")
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

    # ---- R8: 机会成本行（估值段落末尾）----
    oc = result.opportunity_cost or {}
    if oc.get("available"):
        lines.append("")
        lines.append(
            f"  机会成本          盈利收益率 E/P = {oc['earnings_yield_pct']:.2f}%"
            f" vs 10Y 国债 {oc['rf_10y_pct']:.2f}%"
            f" → 利差 (E/P−10Y) = {oc['ey_minus_10y_pp']:.2f}pp"
        )
    elif oc:
        lines.append("")
        lines.append(f"  机会成本          不可得（{oc.get('reason', 'PE/Rf 不可得')}）")


def _format_section_roe_pb(result: ValuationResult, lines: list[str]) -> None:
    """五、ROE-PB 理论匹配。"""
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


def _format_section_scenarios(result: ValuationResult, lines: list[str]) -> None:
    """六、多情景 × 多方法 综合估值区间。"""
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
            pe_price_s = (f"{m['price_pe']:.2f} 元" if m["price_pe"] is not None
                          else "N/A (亏损期)")
            lines.append(f"  │  PE 法 ({m['pe_multiple']:.1f}x):         {pe_price_s}")
            lines.append(f"  │  PB 法 ({m['pb_multiple']:.2f}x):         {m['price_pb']:.2f} 元")
            pey = m.get("price_earnings_yield", "∞")
            if isinstance(pey, (int, float)) and pey < 99999:
                lines.append(f"  │  盈利收益法 (PE={m.get('fair_pe_ey', '?')}x): {pey:.2f} 元")
            else:
                why = "亏损期" if m["price_pe"] is None else "g≥r"
                lines.append(f"  │  盈利收益法: N/A ({why})")
            prices_valid = [
                p for p in [m["price_pe"], m["price_pb"], pey]
                if isinstance(p, (int, float)) and p < 99999
            ]
            if prices_valid:
                lines.append(f"  │  → 综合区间: {min(prices_valid):.0f} ~ {max(prices_valid):.0f} 元")
            lines.append(f"  └{'─' * 50}")
    else:
        lines.append(f"  计算不可得 ({sc.get('error', '')})")


def _format_section_summary(result: ValuationResult, lines: list[str], sep: str) -> None:
    """七、综合估值参考区间 + 质量预警 + OCF 预警 + 免责声明。"""
    lines.append("")
    lines.append("━" * 60)
    lines.append("  七、综合估值参考区间")
    lines.append("━" * 60)

    sc = result.scenarios
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


def format_output(result: ValuationResult) -> str:
    """将 ValuationResult 格式化为可读文本输出。"""
    lines: list[str] = []
    sep = "─" * 72

    _format_header(result, lines, sep)
    _format_section_basic_params(result, lines)
    _format_section_financials(result, lines)
    _format_section_percentile(result, lines)
    _format_section_implied_growth(result, lines)
    _format_section_roe_pb(result, lines)
    _format_section_scenarios(result, lines)
    _format_section_summary(result, lines, sep)

    return "\n".join(lines)


def _format_steady_block(steady: dict) -> str:
    """R2: 稳态盈利估值块文本渲染（value --steady）。"""
    ste = steady.get("steady") or {}
    lines = ["", "【稳态盈利估值（R2 · 穿越周期视角）】"]
    if not ste.get("available"):
        lines.append(f"  ⚠️ {ste.get('reason', '稳态盈利不可得')}")
        return "\n".join(lines)
    lines.append(f"  年度净利样本: {ste.get('period')}（{ste.get('n_years')} 年, method={ste.get('method')}）")
    steady_earnings = ste["steady_earnings"]
    if steady_earnings <= 0:
        lines.append(
            f"  稳态盈利: {steady_earnings/ONE_PER_YI:.2f} 亿元"
            f"（年度区间 {ste['min']/ONE_PER_YI:.2f}~{ste['max']/ONE_PER_YI:.2f} 亿元，"
            "亏损期——稳态估值带不适用）"
        )
    else:
        lines.append(
            f"  稳态盈利: {steady_earnings/ONE_PER_YI:.2f} 亿元"
            f"（年度区间 {ste['min']/ONE_PER_YI:.2f}~{ste['max']/ONE_PER_YI:.2f} 亿元）"
        )
    band = steady.get("band")
    mv = steady.get("mv_vs_steady")
    if band:
        lines.append(
            f"  周期中枢 PE: {band['cycle_pe']} | 稳态市值带:"
            f" {band['low']/ONE_PER_YI:.0f}~{band['mid']/ONE_PER_YI:.0f}~{band['high']/ONE_PER_YI:.0f} 亿元（±{band['band_pct']*100:.0f}%）"
        )
    if mv and mv.get("total_mv_yi"):
        # round(band/ONE_PER_YI, 2) 在带 <0.005 亿元时塌为 0.0（review #5）：
        # truthiness 判断会误落「处于稳态市值带内」且除零——改为显式 None/0.0 判定
        high_yi = mv.get("steady_mv_high_yi")
        low_yi = mv.get("steady_mv_low_yi")
        if high_yi not in (None, 0.0) and mv["total_mv_yi"] > high_yi:
            over = (mv["total_mv_yi"] / high_yi - 1) * 100
            pos = f"高于稳态上沿 {over:.0f}%——历史经验：周期股盈利高点常伴随低 PE 错觉（海力士式），但并非充分条件"
        elif low_yi not in (None, 0.0) and mv["total_mv_yi"] < low_yi:
            under = (low_yi / mv["total_mv_yi"] - 1) * 100
            pos = f"低于稳态下沿 {under:.0f}%——穿越周期视角存在低估"
        elif high_yi in (None, 0.0) or low_yi in (None, 0.0):
            pos = "稳态带数值不可得/过小（亿元口径舍入为 0），位置对照跳过"
        else:
            pos = "处于稳态市值带内"
        lines.append(f"  当期市值 {mv['total_mv_yi']:.0f} 亿元 vs 稳态带: {pos}")
    lines.append("  （稳态估值为多情景参考，非目标价；概率权重由用户自设）")
    return "\n".join(lines)


def _format_ev_ebitda_block(ev: dict) -> str:
    """R3: EV/EBITDA 桥接表文本渲染（value --ev-ebitda）。"""
    lines = ["", "【EV/EBITDA 企业价值桥接（R3）】"]
    if ev.get("exempt"):
        lines.append(f"  ⚠️ {ev.get('reason', '不适用')}")
        return "\n".join(lines)
    if not ev.get("available"):
        if ev.get("exempt"):
            # 金融业豁免：非「数据不可得」（review #8，否则输出空括号「缺失: 」）
            lines.append(f"  ⚠️ EV/EBITDA 不适用（{ev.get('reason') or '行业豁免'}）")
        else:
            lines.append(f"  ⚠️ 桥接数据不可得（缺失: {', '.join(ev.get('missing') or [])}）")
        if ev.get("ebitda_note"):
            lines.append(f"  · {ev['ebitda_note']}")
        if ev.get("note"):
            lines.append(f"  · {ev['note']}")
        return "\n".join(lines)
    b = ev["bridge"]
    lines.append("  桥接表（逐项可审计）:")
    lines.append(f"    - 市值: {b['mv_yi']} 亿元")
    if b["interest_debt_yi"] is not None:
        # 标签由引擎预计算（debt_label 仅含可得分量，缺失科目按 0 计但不出现在
        # 标签，防行内口径误导，batch-test P1-2）；interest_debt_yi 非 None 时
        # debt_label 必非 None（valuation_calc 同分支保证），无兜底
        lines.append(f"    + 有息负债: {b['interest_debt_yi']} 亿元（{ev['debt_label']}）")
    else:
        lines.append(f"    + 有息负债: 不可得（降级净现金口径）")
    lines.append(f"    - 现金: {b['cash_yi']} 亿元")
    lines.append(f"    = EV: {b['ev_yi']} 亿元")
    period_s = f"（{ev['ebitda_period']} 年报期）" if ev.get("ebitda_period") else ""
    ratio = ev.get("ev_ebitda")
    ebitda_yi = ev.get("ebitda_yi")
    if ratio is not None:
        lines.append(f"  EBITDA: {ebitda_yi} 亿元{period_s} → EV/EBITDA = {ratio}x")
    elif ebitda_yi is None:
        # 防御：available=True 但 EBITDA 缺失（老信封/语义漂移）——缺失 ≠ 为 0，
        # 不得断言「EBITDA 为 0」（review #8，事实性）
        lines.append(f"  EBITDA: 不可得{period_s} → EV/EBITDA 不适用（EBITDA 缺失，比率无定义）")
    elif ebitda_yi == 0:
        # calc_ev_ebitda 仅 ebitda not in (None, 0.0) 时计算 ratio（review #4）
        # ——EBITDA 恰为 0 时比率无定义，不渲染 Nonex
        lines.append(f"  EBITDA: 0 亿元{period_s} → EV/EBITDA 不适用（EBITDA 为 0，比率无定义）")
    else:
        # EBITDA>0 但 ratio 未计算（理论不可达）：如实标注，不臆断原因
        lines.append(f"  EBITDA: {ebitda_yi} 亿元{period_s} → EV/EBITDA 不适用（比率未计算）")
    if ev.get("note"):
        lines.append(f"  ⚠️ {ev['note']}")
    if ev.get("takeover_payback_years"):
        lines.append(f"  私有化检验（研究问题）: 回本年限 ≈ {ev['takeover_payback_years']} 年")
        lines.append(f"    · {ev['takeover_note']}")
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
