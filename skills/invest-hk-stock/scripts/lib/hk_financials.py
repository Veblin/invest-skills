"""港股财务摘要（东财 stock_financial_hk_analysis_indicator_em，v0.2.9 港股数据引入 v1）。

2026-09-06 实测：接口须在 akshare_direct_session 直连上下文内调用（datacenter-web
域；push2his 域当前环境被拒但本接口不受影响）。含 CURRENCY 字段（CNY 报表公司显式标注
——币种纪律）。

⚠️ **2026-09-13 复核修正**：本接口返回**年报行**（`DATE_TYPE_CODE=001`）——12 只样本
× 9 期 = 108 行**全为 001**，无中报行；`ROE_AVG` 即**年度 ROE**。原写「年报(001)+
中报(002)全史行」不实。财年各异的公司（6 月财年 00016/00017/00083/00659、3 月财年
09988）报告期不同，但都是**年度**口径——故**不得按 report_date 日历后缀推断中报**。

科目映射（东财港股列 → 引擎默认键）：OPERATE_INCOME→revenue、HOLDER_PROFIT→net_profit、
GROSS_PROFIT_RATIO→gross_margin、NET_PROFIT_RATIO→net_margin、ROE_AVG→roe、
BASIC_EPS→eps、BPS→bps；*_YOY 为同比。扣非(A股口径)无对应——港股无此概念。
"""
from __future__ import annotations

from typing import Any

from hk_codes import parse_hk_symbol

_FIELD_MAP = {
    "REPORT_DATE": "report_date",
    # ⚠️ 口径判据（2026-09-13 实测）：`DATE_TYPE_CODE` 001=年报 / 002=中报；
    # `START_DATE` = 报告期起始日 → 期长可直接算出。两者供调用方判断「期间值 vs 年度值」，
    # **不得按 report_date 的日历后缀猜**（6 月财年公司的年报正是 06-30）。
    "DATE_TYPE_CODE": "report_type",
    "START_DATE": "period_start",
    "OPERATE_INCOME": "revenue",
    "OPERATE_INCOME_YOY": "revenue_yoy",
    "HOLDER_PROFIT": "net_profit",
    "HOLDER_PROFIT_YOY": "net_profit_yoy",
    "GROSS_PROFIT_RATIO": "gross_margin",
    "NET_PROFIT_RATIO": "net_margin",
    "ROE_AVG": "roe",
    "BASIC_EPS": "eps",
    "BPS": "bps",
    "CURRENCY": "currency",
}


def _norm_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for src, dst in _FIELD_MAP.items():
        v = row.get(src)
        out[dst] = v
    # 东财港股指标列实测已是百分数单位（腾讯 FY2025 GROSS_PROFIT_RATIO=56.21；
    # 京东物流 FY2023 NET_PROFIT_RATIO=0.7005 = 0.70% 薄利）——**原值透传**，
    # 不做 abs<=1 放大（review2 HK-2：曾把 0.7005 放大成 70.05%）。YOY 列不信任，报告自算。
    out["currency"] = str(out.get("currency") or "HKD").strip()
    out["report_date"] = str(out.get("report_date") or "")[:10]
    out["period_start"] = str(out.get("period_start") or "")[:10]
    out["report_type"] = str(out.get("report_type") or "").strip()
    return out


def fetch_financials(sym: str) -> list[dict[str, Any]]:
    """年报全史（实测**只返回年报行**，见模块 docstring）→ 按 report_date 降序。

    ⚠️ 原 docstring 写「年报(001)+中报(002)全史行」——2026-09-13 复核实测（12 只
    × 9 期 = 108 行）**全部为 001**，无中报行。据此作口径判断的代码须改判据，
    不得假设本函数会给出中期行。失败 → 空列表（调用方标注不可得）。
    """
    import akshare as ak

    code = parse_hk_symbol(sym)
    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
    except Exception:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append(_norm_row(dict(r)))
    rows.sort(key=lambda r: r["report_date"], reverse=True)
    return rows
