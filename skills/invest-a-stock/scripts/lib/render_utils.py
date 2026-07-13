"""Shared render helpers (formatting, dim access, valuation cache)."""
from __future__ import annotations

import html as _html_mod
import json
import logging
import re
from pathlib import Path
from typing import Any

from lib.nums import coalesce_field, fmt_amount, safe_float as _safe_num
from lib.technical import compute, sort_kline_asc
from lib.participant_scan import (
    build_participant_behavior_section,
    moneyflow_cv_window,
    moneyflow_signal_label,
    northbound_label,
    resolve_moneyflow,
)

from .proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
)
from .schema import CrossValidation, DriverFactor, ProbabilityStructure, _CV_ICONS, _CV_LABELS, index_dimensions
from .version import get_package_version

logger = logging.getLogger(__name__)
ENGINE_VERSION = get_package_version()

_EASTMONEY_BLOCKED_SHORT = "东方财富(East Money)主动拒绝连接"
_RAW_CONNECTION_REFUSED_SHORT = "服务器拒绝连接"
_fmt = fmt_amount
_fmt_v2 = fmt_amount


# --- _meta_cv_line ---
def _meta_cv_line(meta: dict) -> str:
    """从 legacy _meta 生成交叉验证行。"""
    cv_status = meta.get("cross_validation")
    if not cv_status:
        return ""
    detail = meta.get("cross_validation_detail") or ""
    icon = _CV_ICONS.get(cv_status, "🔴")
    label = _CV_LABELS.get(cv_status, cv_status)
    default_detail = "多源数据一致" if cv_status == "convergence" else "多源数据存在差异"
    return f"{icon} **{label}** — {detail or default_detail}"


# --- sanitize_error ---
def sanitize_error(error: str, max_len: int = 60) -> str:
    """将原始 Python 异常转为可读的简短说明，截断到 max_len。

    优先检测东方财富封锁、DNS/代理等常见网络问题。
    """
    if not error:
        return "未知错误"
    # Coerce non-string types (e.g., Exception objects, int error codes)
    if not isinstance(error, str):
        error = str(error)
    if EASTMONEY_FAILURE_TUN_MARKER in error:
        return "push2 不可达（TUN/CDN），已用 Tushare/Baostock 替代"
    if EASTMONEY_FAILURE_PROXY_MARKER in error:
        return "HTTP 代理未绕过，请配置 Clash DIRECT 或关闭代理"
    if any(kw in error for kw in _EASTMONEY_BLOCKED_KEYWORDS):
        return _EASTMONEY_BLOCKED_SHORT
    if "Clash" in error or "VPN" in error:
        return "可能与 Clash / VPN 有关，关闭代理后重试"
    if "ProxyError" in error or "Max retries exceeded" in error:
        return "可能与 Clash / VPN 有关，关闭代理后重试"
    if "ConnectionError" in error or "Connection aborted" in error:
        return _RAW_CONNECTION_REFUSED_SHORT
    # 其他：取最后一段有意义的内容
    cleaned = re.sub(r"\s+", " ", error).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


# --- _source_status_block ---
def _source_status_block(all_sources: list[dict] | None) -> str:
    """生成各渠道的独立取证状态块。"""
    if not all_sources:
        return ""
    rows = []
    for s in all_sources:
        source = s.get("source", "?")
        avail = s.get("data_available", False)
        error = s.get("error") or ""
        qp = s.get("query_params", "")
        icon = "✅" if avail else ("❌" if error else "⏭️")
        status = "成功" if avail else (f"失败: {_sanitize_error(error, 80)}" if error else "未尝试")
        qp_str = f" `{qp}`" if qp else ""
        rows.append(f"  - **{source}** {icon} — {status}{qp_str}")
    return "\n".join(rows)


# --- _index_dims ---
def _index_dims(collection: dict) -> dict[str, dict]:
    """将 dimensions 列表转为 dict。委托 schema.index_dimensions。"""
    return index_dimensions(collection)


# --- _get_dim_data ---
def _get_dim_data(dims: dict[str, dict], key: str) -> Any:
    """获取维度主数据。"""
    d = dims.get(key, {})
    return d.get("data")


# --- _get_dim_meta ---
def _get_dim_meta(dims: dict[str, dict], key: str) -> dict:
    """获取维度 meta。"""
    d = dims.get(key, {})
    return d.get("_meta", {})


# --- _get_analysis_cards ---
def _get_analysis_cards(collection: dict) -> dict:
    """Safe accessor for analysis_cards from collection._meta."""
    return (collection.get("_meta") or {}).get("analysis_cards") or {}


# --- _references_appendix ---
def _references_appendix(collection: dict[str, Any]) -> str:
    """引用来源附录。"""
    lines = ["---", "", "## 📚 引用来源（References）", ""]
    lines.append("| 维度 | 渠道 | 追溯路径 | 数据详情 |")
    lines.append("|------|------|----------|---------|")

    for dim in collection.get("dimensions", []):
        display = dim.get("display", dim.get("dimension", "?"))
        dim_data = dim.get("data")
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            meta = dim.get("_meta", {})
            icon = "✅" if dim_data is not None else "❌"
            qp = meta.get("query_params", "")
            src_name = meta.get("source", "?")
            detail = _data_fields(dim.get("dimension", ""), dim_data)
            status = detail if dim_data is not None else "不可用"
            lines.append(f"| {display} | {src_name} | `{qp}` | {icon} {status} |")
            continue

        first = True
        for s in all_src:
            src_name = s.get("source", "?")
            avail = s.get("data_available", False)
            error = s.get("error", "")
            qp = s.get("query_params", "")
            dim_label = display if first else ""
            first = False
            if avail:
                detail = _data_fields(dim.get("dimension", ""), dim_data)
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ✅ {detail} |")
            elif error:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ❌ {_sanitize_error(error, 55)} |")
            else:
                lines.append(f"| {dim_label} | {src_name} | — | ⏭️ 未尝试 |")

    return "\n".join(lines)


# --- _risk_footer ---
def _risk_footer() -> str:
    return f"""---

> ⚠️ **免责声明:** 本报告由 invest:a-stock v{ENGINE_VERSION} 自动化引擎生成，仅供研究备忘录与多因子分析参考。
> 不构成任何投资建议、买卖指令或目标价预测。所有技术指标均为市场状态描述，非交易信号。
> 数据来源见上文 References 表，可能与实际公告存在差异，请以公司公告和交易所数据为准。"""


# --- _missing_section ---
def _missing_section(title: str, reason: str) -> str:
    return f"""## {title}

> **未获取到任何有效数据，无法判断。**
> 原因: {reason}

🔍 **待独立验证:** 确认数据源配置后重试，或通过 WebSearch 手动补充。"""


# --- _cv ---
def _cv(
    status: str, code: str, data_pair: str, detail: str, reliability: str,
) -> str:
    return CrossValidation(status, code, data_pair, detail, reliability).to_markdown()


# --- _evidence_conclusion_block ---
def _evidence_conclusion_block(conclusion: str, evidences: list[tuple[str, str]]) -> str:
    """LAW 12 证据-结论映射块。evidences: [(强度符号, 描述), ...]"""
    lines = [f"**结论：{conclusion}**", "", "支持证据："]
    for sym, desc in evidences:
        lines.append(f"  {sym} {desc}")
    strong = sum(1 for s, _ in evidences if s == "✅")
    weak = sum(1 for s, _ in evidences if s == "❓")
    if strong >= 2:
        strength = "强"
        note = "多条直接数据支撑，主要竞争性解释已排除。"
    elif weak >= len(evidences) // 2 + 1:
        strength = "弱"
        note = "证据以相关性或单一来源为主，结论可信度受限。"
    else:
        strength = "中"
        note = "数据方向支持结论，但存在其他合理解释或来源单一。"
    lines.extend(["", f"综合证据强度：{strength}", f"  {note}"])
    return "\n".join(lines)


# --- _v3_cv7_assessment ---
def _v3_cv7_assessment(
    pe_pct: float | None, mf_out: float | int | None,
) -> tuple[str, str] | None:
    """CV-7：PE 分位 vs 主力资金方向。

    分位边界与 valuation.ZONE_LOW/HIGH_THRESHOLD 一致（严格 <30 / >70）。
    """
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

    if pe_pct is None or mf_out is None:
        return None
    mf_f = float(mf_out)
    if pe_pct < ZONE_LOW_THRESHOLD and mf_f < 0:
        return "convergence", f"PE 低位（{pe_pct:.1f}%）且主力资金净流出"
    if pe_pct > ZONE_HIGH_THRESHOLD and mf_f > 0:
        return "divergence", f"PE 高位（{pe_pct:.1f}%）但主力资金净流入"
    return "gap", "估值与资金流向未呈现典型背离/共振"


# --- _v3_cv7_block ---
def _v3_cv7_block(pe_pct: float | None, mf_out: float | int | None) -> str | None:
    assessed = _v3_cv7_assessment(pe_pct, mf_out)
    if assessed is None:
        return None
    status, detail = assessed
    return _cv(status, "CV-7", "PE 分位 vs 资金流出", detail, "中")


# --- _v3_cv8_assessment ---
def _v3_cv8_assessment(
    erp: dict | None,
    pcr: dict | None,
    short_margin: dict | None,
) -> tuple[str, str] | None:
    """CV-8：ERP 分位 vs 认沽认购比 vs 融券增速。"""
    erp_p = (erp or {}).get("percentile_5y")
    pcr_p = (pcr or {}).get("percentile_5y") or (pcr or {}).get("percentile_60d")
    sm_g = (short_margin or {}).get("growth_pct")
    sm_p = (short_margin or {}).get("percentile_5y")

    flags: list[tuple[str, bool | None]] = []
    if erp_p is not None:
        flags.append(("ERP", erp_p >= 70))
    if pcr_p is not None:
        flags.append(("PCR", pcr_p >= 70))
    if sm_g is not None:
        flags.append(("融券增速", sm_g > 0))
    elif sm_p is not None:
        flags.append(("融券分位", sm_p >= 70))

    scored = [(n, v) for n, v in flags if v is not None]
    if len(scored) < 2:
        return None

    left_n = sum(1 for _, v in scored if v)
    erp_s = f"{erp_p:.1f}%" if erp_p is not None else "-"
    pcr_s = f"{pcr_p:.1f}%" if pcr_p is not None else "-"
    sm_s = f"{sm_g:+.2f}%" if sm_g is not None else (
        f"{sm_p:.1f}%分位" if sm_p is not None else "-"
    )
    detail_base = f"ERP 5年分位 {erp_s}；认沽认购比分位 {pcr_s}；融券 {sm_s}"

    if left_n >= 2:
        return "convergence", f"左侧情绪指标共振 — {detail_base}"
    if erp_p is not None and pcr_p is not None and erp_p >= 70 and pcr_p < 30:
        return "divergence", f"ERP 偏高但认沽认购比未处高位 — {detail_base}"
    if erp_p is not None and pcr_p is not None and erp_p < 30 and pcr_p >= 70:
        return "divergence", f"认沽认购比偏高但 ERP 未处高位 — {detail_base}"
    return "gap", f"情绪三角未呈现典型共振或背离 — {detail_base}"


# --- _v3_cv8_block ---
def _v3_cv8_block(
    erp: dict | None,
    pcr: dict | None,
    short_margin: dict | None,
) -> str | None:
    assessed = _v3_cv8_assessment(erp, pcr, short_margin)
    if assessed is None:
        return None
    status, detail = assessed
    return _cv(status, "CV-8", "ERP 分位 vs 认沽认购比 vs 融券增速", detail, "中")


# --- _v3_trend_stage_hints ---
def _v3_trend_stage_hints(label: str) -> str:
    """LAW 16：并列阶段对照，不勾选单一结论。"""
    base = "□ 上升趋势  □ 筑底区间  □ 高位震荡  □ 下降趋势  □ 不明确"
    if not label:
        return base
    if "多头" in label:
        hint = "数据更接近「上升趋势」描述，但不排除震荡或反转"
    elif "空头" in label:
        hint = "数据更接近「下降趋势」描述，但不排除筑底或反弹"
    else:
        hint = "数据更接近「高位震荡/整理」描述，方向待确认"
    return f"{base}\n  - 对照说明（非结论）: {hint}"


# --- _v3_price_change ---
def _v3_price_change(dims: dict[str, dict]) -> tuple[float | None, int | None]:
    """返回 (涨跌幅%, 实际跨度交易日数)。不足 20 日时仍计算但 window < 20。"""
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list) or len(kline) < 2:
        return None, None
    rows = sort_kline_asc(kline)
    if len(rows) >= 21:
        recent = rows[-21:]
    else:
        recent = rows
    c0 = recent[0].get("close")
    c1 = recent[-1].get("close")
    if c0 is None or c1 is None:
        return None, None
    if float(c0) == 0:
        return None, None  # zero close, cannot compute percentage
    window = len(recent) - 1
    pct = (float(c1) - float(c0)) / float(c0) * 100
    return pct, window


# --- _v3_price_window_label ---
def _v3_price_window_label(window: int | None) -> str:
    if window is None:
        return "涨跌幅"
    if window >= 20:
        return "近 20 个交易日"
    return f"近 {window} 个交易日（K 线不足 20 日）"


# --- _v3_load_valuation_summary ---
def _v3_load_valuation_summary(
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> dict | None:
    """加载 valuation_summary 并写入 val_cache（含 pe 中位数供场景化估算）。"""
    if val_cache is not None and "val_summary" in val_cache:
        return val_cache["val_summary"]
    val_data = _get_dim_data(dims, "valuation")
    summary: dict | None = None
    if val_data and isinstance(val_data, list):
        from lib.valuation import valuation_summary, valuation_window_label
        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted]
        pb_seq = [r.get("pb") for r in val_sorted]
        ps_seq = [r.get("ps_ttm") or r.get("ps") for r in val_sorted]
        dv_ratio: float | None = None
        for r in reversed(val_sorted):
            if r.get("dv_ratio") is not None:
                dv_ratio = _safe_num(r.get("dv_ratio"))
                break
        window_label = valuation_window_label(len(val_sorted))
        summary = valuation_summary(
            pe_seq, pb_seq, ps_seq=ps_seq, dv_ratio=dv_ratio, window_label=window_label,
        )
    if val_cache is not None:
        val_cache["val_summary"] = summary
        if summary:
            pe = summary.get("pe") or {}
            pb = summary.get("pb") or {}
            val_cache["result"] = (pe.get("pct"), pb.get("pct"), pe.get("zone"))
        else:
            val_cache["result"] = (None, None, None)
    return summary


# --- _v3_valuation_percentiles ---
def _v3_valuation_percentiles(
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> tuple[float | None, float | None, str | None]:
    if val_cache is not None and "result" in val_cache:
        return val_cache["result"]
    summary = _v3_load_valuation_summary(dims, val_cache)
    if not summary:
        return (None, None, None)
    pe = summary.get("pe") or {}
    pb = summary.get("pb") or {}
    return (pe.get("pct"), pb.get("pct"), pe.get("zone"))


# --- _fmt_end_date ---
def _fmt_end_date(val: Any) -> str:
    """报告期展示（避免把 YYYYMMDD 当数字格式化）。"""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    digits = s.replace("-", "").replace("/", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


# --- _fin_field_num ---
def _fin_field_num(row: dict, *keys: str) -> float | None:
    """按字段名取数值；0.0 为合法值（不用 truthy 判断）。委托 lib.nums.coalesce_field。"""
    return coalesce_field(row, *keys)


# --- _get_safe ---
def _get_safe(rows: list[dict], field: str, default: Any = None) -> Any:
    """从已排序财务记录列表中提取最新非 None 值。"""
    for r in reversed(rows):
        v = r.get(field)
        if v is not None:
            n = _safe_num(v)
            return n if n is not None else v
    return default


# --- _coalesce_fin_field ---
def _coalesce_fin_field(rows: list[dict], *fields: str) -> float | None:
    """按字段顺序合并财务数值；0.0 视为有效值，不用 `or` 链。"""
    for field in fields:
        v = _get_safe(rows, field)
        if v is not None:
            n = _safe_num(v)
            if n is not None:
                return n
    return None


# --- _fmt_num ---
def _fmt_num(v: Any, *, decimals: int = 2, suffix: str = "") -> str:
    """安全格式化数值（兼容 numpy / Decimal 等经 _safe_num 归一化后的类型）。"""
    n = _safe_num(v)
    if n is None:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)
    return f"{n:.{decimals}f}{suffix}"


# --- _historical_pe_median ---
def _historical_pe_median(val_cache: dict | None, dims: dict[str, dict]) -> float | None:
    """从 valuation 维度取 PE 历史中位数（LAW 3 可追溯）。"""
    summary = _v3_load_valuation_summary(dims, val_cache)
    if not summary:
        return None
    pe = summary.get("pe") or {}
    median = pe.get("median")
    return float(median) if median is not None else None


# --- _bull_bear_valuation_divergence_text ---
def _bull_bear_valuation_divergence_text(
    pe_pct: float,
    pe_zone: str | None,
    rev_yoy: float,
) -> str:
    """模块 5c：按 PE 历史区间位置分支 Bull/Bear 估值叙事。"""
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

    zone_label = pe_zone or "中间带"
    if pe_pct < ZONE_LOW_THRESHOLD:
        return (
            f"Bull 认为 PE 历史区间位置 {pe_pct:.1f}%（{zone_label}），"
            f"定价偏悲观、存在修复空间；Bear 认为营收同比 {rev_yoy:+.1f}%"
            f"不足以支撑估值向上修复。"
        )
    if pe_pct > ZONE_HIGH_THRESHOLD:
        return (
            f"Bull 认为营收同比 {rev_yoy:+.1f}% 可支撑当前定价；"
            f"Bear 认为 PE 历史区间位置 {pe_pct:.1f}%（{zone_label}），"
            f"估值透支、均值回归风险上升。"
        )
    return (
        f"Bull 认为营收同比 {rev_yoy:+.1f}% 与 PE 历史区间位置 {pe_pct:.1f}%"
        f"尚未完全定价；Bear 认为二者匹配度存疑，需观察增速能否维持。"
    )


# --- _periods_per_year ---
def _periods_per_year(fin_list: list[dict]) -> int:
    """估算每年报告期数（4=季报，2=半年报，1=年报）。"""
    if len(fin_list) < 2:
        return 4
    dates = sorted(set(str(r.get("end_date", "")).replace("-", "")[:6] for r in fin_list if r.get("end_date")))
    if len(dates) <= 1:
        return 4
    return min(4, len(dates))


# --- _compute_metric_cagr ---
def _compute_metric_cagr(
    fin_list: list[dict], field: str,
) -> tuple[float | None, float | None]:
    """从已排序财务列表计算 CAGR（%）及年跨度。"""
    rows = [r for r in fin_list if _safe_num(r.get(field)) is not None]
    if len(rows) < 2:
        return None, None
    first_v = _safe_num(rows[0].get(field))
    last_v = _safe_num(rows[-1].get(field))
    if first_v is None or last_v is None or first_v <= 0:
        return None, None
    span = max(1, (len(rows) - 1) / _periods_per_year(rows))
    if span >= 0.5:
        return ((last_v / first_v) ** (1 / span) - 1) * 100, span
    return (last_v - first_v) / first_v * 100, span


# --- _wrap_details ---
def _wrap_details(summary: str, content: str) -> str:
    if not content:
        return content
    return f"<details>\n<summary>{summary}</summary>\n\n{content}\n\n</details>"


_sanitize_error = sanitize_error


def _data_fields(dimension: str, data: Any) -> str:
    """提取维度获取到的有效数据字段摘要。

    返回逗号分隔的字段名列表，如 "公司名称、行业、上市日期"。
    """
    if data is None:
        return ""
    if isinstance(data, dict):
        # 字段名映射：中文字段更可读
        key_display = {
            "name": "公司名称", "area": "地区", "industry": "行业",
            "market": "上市市场", "list_date": "上市日期",
            "price": "最新价", "change_pct": "涨跌幅", "turnover_rate": "换手率",
            "pe_ratio": "PE", "total_mv": "总市值",
            "pe_ttm": "PE(TTM)", "pb": "PB", "ps_ttm": "PS(TTM)",
            "dv_ratio": "股息率", "history_available": "历史分位",
        }
        fields = []
        for k in data:
            display = key_display.get(k, k)
            if data[k] is not None:
                fields.append(display)
        return "、".join(fields) if fields else "有数据"
    if isinstance(data, list) and data:
        # 取第一条记录的键
        first = data[0]
        if isinstance(first, dict):
            fin_keys = {
                "end_date": "报告期", "roe": "ROE", "eps": "EPS",
                "profit_dedt": "扣非净利润", "revenue": "营收", "net_profit": "净利润",
                "trade_date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘", "vol": "成交量",
                "holder_name": "股东名称", "hold_ratio": "持股比例",
                "net_mf_vol": "净流向",
            }
            fields = [fin_keys.get(k, k) for k in first if first[k] is not None]
            return "、".join(fields) if fields else f"{len(data)}条记录"
        return f"{len(data)}条记录"
    return "有数据"

