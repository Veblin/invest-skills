"""估值分析模块。

输入: PE_TTM/PB/PS 历史序列（由 collector 采集后传入）
输出: 分位、区间标签、估值描述文本

原则:
  - 不输出买卖建议、目标价、仓位建议
  - 输出格式为"状态描述"而非"交易信号"
  - 数据不足时标注而非静默跳过
"""

from __future__ import annotations

from typing import Any


def percentile_rank(seq: list[float], current: float) -> float | None:
    """计算 current 在 seq 中的百分位（严格低于 current 的比例 × 100）。

    percentile = count_(v < current) / total × 100
    即：值越低，百分位越小 → "低于历史 X% 的时间"

    使用严格小于（不包含等于），避免当前值等于历史极值时
    分位被推向极端（最小值→0%，最大值→100%），使 zone 判断更稳健。

    Args:
        seq: 历史估值序列（正数）
        current: 当前值

    Returns:
        百分位 [0, 100]，数据不足时返回 None
    """
    valid = [v for v in seq if v is not None and v > 0]
    if not valid:
        return None
    below = sum(1 for v in valid if v < current)
    return (below / len(valid)) * 100


def zone_label(pct: float, low_threshold: float = 30.0,
               high_threshold: float = 70.0) -> str:
    """根据百分位返回估值区间标签。

    pct < 30  → "偏低"（当前值低于历史 70% 的时间）
    pct 30-70 → "适中"
    pct > 70  → "偏高"（当前值高于历史 70% 的时间）
    """
    if pct < low_threshold:
        return "偏低"
    elif pct > high_threshold:
        return "偏高"
    return "适中"


def valuation_summary(
    pe_ttm_seq: list[float | None],
    pb_seq: list[float | None],
    current_pe: float | None = None,
    current_pb: float | None = None,
    current_ps: float | None = None,
    ps_seq: list[float | None] | None = None,
    dv_ratio: float | None = None,
    *,
    window_label: str = "近5年",
) -> dict[str, Any]:
    """生成估值状态结构化描述。

    Args:
        pe_ttm_seq: 历史 PE(TTM) 序列（升序，旧→新）
        pb_seq: 历史 PB 序列
        current_pe: 当前 PE(TTM)，None 时取序列最后一值
        current_pb: 当前 PB，None 时取序列最后一值
        current_ps: 当前 PS(TTM)，可选
        ps_seq: 历史 PS 序列，可选
        dv_ratio: 股息率（最近交易日），可选
        window_label: 分位窗口描述，如"近5年"、"上市以来"

    Returns:
        dict 含 pe/pb/ps 分位、zone、median、样本数等
    """
    pe_seq_clean = [v for v in pe_ttm_seq if v is not None and v > 0]
    pb_seq_clean = [v for v in pb_seq if v is not None and v > 0]

    if current_pe is None and pe_seq_clean:
        current_pe = pe_seq_clean[-1]
    if current_pb is None and pb_seq_clean:
        current_pb = pb_seq_clean[-1]

    result: dict[str, Any] = {
        "window_label": window_label,
        "n_samples": len(pe_seq_clean),
        "sufficient": len(pe_seq_clean) >= 30,
    }

    # PE
    if pe_seq_clean and current_pe is not None:
        pe_pct = percentile_rank(pe_seq_clean, current_pe)
        pe_median = _median(pe_seq_clean)
        result["pe"] = {
            "current": round(current_pe, 2),
            "pct": round(pe_pct, 2) if pe_pct is not None else None,
            "median": round(pe_median, 2) if pe_median is not None else None,
            "zone": zone_label(pe_pct) if pe_pct is not None else "未知",
            "n_valid": len(pe_seq_clean),
        }
    else:
        result["pe"] = {"current": None, "pct": None, "median": None,
                        "zone": "未知", "n_valid": 0,
                        "reason": "PE 数据为空或无正值"}

    # PB
    if pb_seq_clean and current_pb is not None:
        pb_pct = percentile_rank(pb_seq_clean, current_pb)
        pb_median = _median(pb_seq_clean)
        result["pb"] = {
            "current": round(current_pb, 2),
            "pct": round(pb_pct, 2) if pb_pct is not None else None,
            "median": round(pb_median, 2) if pb_median is not None else None,
            "zone": zone_label(pb_pct) if pb_pct is not None else "未知",
            "n_valid": len(pb_seq_clean),
        }
    else:
        result["pb"] = {"current": None, "pct": None, "median": None,
                        "zone": "未知", "n_valid": 0,
                        "reason": "PB 数据为空或无正值"}

    # PS（可选）
    if ps_seq is not None:
        ps_seq_clean = [v for v in ps_seq if v is not None and v > 0]
        if current_ps is None and ps_seq_clean:
            current_ps = ps_seq_clean[-1]
        if ps_seq_clean and current_ps is not None:
            ps_pct = percentile_rank(ps_seq_clean, current_ps)
            ps_median = _median(ps_seq_clean)
            result["ps"] = {
                "current": round(current_ps, 2),
                "pct": round(ps_pct, 2) if ps_pct is not None else None,
                "median": round(ps_median, 2) if ps_median is not None else None,
                "zone": zone_label(ps_pct) if ps_pct is not None else "未知",
                "n_valid": len(ps_seq_clean),
            }
        else:
            result["ps"] = {"current": None, "pct": None, "median": None,
                           "zone": "未知", "n_valid": 0,
                           "reason": "PS 数据不可得"}
    else:
        result["ps"] = {"available": False, "reason": "PS 序列未传入"}

    # 股息率
    result["dv_ratio"] = round(dv_ratio, 4) if dv_ratio is not None else None

    # 样本不足警告
    result["warnings"] = []
    if not result["sufficient"]:
        result["warnings"].append("样本不足30个交易日，分位计算结果仅供参考")

    # 摘要文本（渲染用）
    result["summary_text"] = _build_summary_text(result)

    return result


def _median(seq: list[float]) -> float | None:
    """中位数。"""
    if not seq:
        return None
    s = sorted(seq)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _build_summary_text(result: dict[str, Any]) -> str:
    """从结构化结果生成估值摘要文本。"""
    lines: list[str] = []

    pe = result.get("pe", {})
    if pe.get("current") is not None:
        pct = pe.get("pct")
        pct_str = f"{result['window_label']} {pct:.1f}% 分位" if pct is not None else "分位不可得"
        median_str = f"（中位数 {pe['median']:.2f}x）" if pe.get("median") is not None else ""
        lines.append(f"PE(TTM): {pe['current']:.2f}x，{pct_str}{median_str}，处于历史{pe.get('zone', '未知')}区间。")
    else:
        lines.append(f"PE(TTM): {pe.get('reason', '不可得')}。")

    pb = result.get("pb", {})
    if pb.get("current") is not None:
        pct = pb.get("pct")
        pct_str = f"{result['window_label']} {pct:.1f}% 分位" if pct is not None else "分位不可得"
        median_str = f"（中位数 {pb['median']:.2f}x）" if pb.get("median") is not None else ""
        lines.append(f"PB: {pb['current']:.2f}x，{pct_str}{median_str}，处于历史{pb.get('zone', '未知')}区间。")
    else:
        lines.append(f"PB: {pb.get('reason', '不可得')}。")

    dv = result.get("dv_ratio")
    if dv is not None:
        # Tushare daily_basic.dv_ratio 为百分比值（如 0.42 表示 0.42%）
        lines.append(f"股息率: {dv:.2f}%（最近交易日）。")
    else:
        lines.append("股息率: 不可得。")

    ps = result.get("ps", {})
    if ps.get("current") is not None:
        pct = ps.get("pct")
        pct_str = f"分位 {pct:.1f}%" if pct is not None else "分位不可得"
        lines.append(f"PS(TTM): {ps['current']:.2f}x，{result['window_label']} {pct_str}。")
    elif ps.get("available") is not False:
        lines.append(f"PS(TTM): {ps.get('reason', '不可得')}。")

    for w in result.get("warnings", []):
        lines.append(f"⚠️ {w}")

    return "\n".join(lines)
