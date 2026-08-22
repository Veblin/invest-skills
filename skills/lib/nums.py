"""Numeric helpers shared across lib modules."""

from __future__ import annotations

from typing import Any

# ── 中文数字单位换算比率（invest-a-stock / invest-a-etf 共用）──────────────
# 方向由运算符决定：÷ 比率得亿/万单位，× 比率得原单位（个/元/股/份）。
# 常见陷阱：万份×元=万元（÷WAN_PER_YI 才得亿元，勿误用 ONE_PER_YI）；
#           千元→亿元 ÷QIAN_PER_YI；万股→股 ×ONE_PER_WAN（勿与 WAN_PER_YI 混淆）。
ONE_PER_YI = 1e8    # 1 亿 = 1e8 个：元→亿元 ÷，亿→元 ×
WAN_PER_YI = 1e4    # 1 亿 = 1e4 万：万元→亿元 ÷，亿→万 ×
QIAN_PER_YI = 1e5   # 1 亿 = 1e5 千：千元→亿元 ÷，亿→千 ×
ONE_PER_WAN = 1e4   # 1 万 = 1e4 个：万股→股 ×，股→万股 ÷


def safe_float(v: Any) -> float | None:
    """安全转为 float；None / NaN / ±inf / 非数字返回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        if f in (float("inf"), float("-inf")):  # inf
            return None
        return f
    except (TypeError, ValueError):
        return None


def parse_shares_wan(raw: Any) -> float | None:
    """解析总股本文本 → 万股（亿股/万股 后缀 + 非数字回退）。

    统一 valuation_calc._parse_shares 与 financial_rigor._parse_share_count
    的漂移副本（review #10）：剥「亿股/万股」后缀、ValueError 回退 safe_float。
    裸数字/整型直通（万股口径）。「股」后缀仅随 亿/万 一并剥离，不单独处理
    （原始数据无裸「股」口径，误乘/误除 1e4 的风险大于收益）。
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).replace(",", "").strip()
    mult = 1.0
    if "亿" in s:
        mult = WAN_PER_YI  # 亿股 → 万股
        s = s.replace("亿股", "").replace("亿", "")
    elif "万" in s:
        s = s.replace("万股", "").replace("万", "")
    try:
        return float(s) * mult
    except ValueError:
        return safe_float(raw)


def coalesce_field(row: dict, *keys: str) -> float | None:
    """取 dict 中第一个非 None 的数值字段（保留负值与 0，避免 `or` 误判）。

    一次只尝试一个 key，命中后返回 safe_float 结果；跳过 NaN/inf/非数字。
    """
    for k in keys:
        v = safe_float(row.get(k))
        if v is not None:
            return v
    return None


def row_value_or_last(row: dict, *keys: str) -> float | None:
    """从 dict 行取第一个非 None 的列值，全部缺省时回退末列值。

    宏观采集行经 df.to_dict("records") 转 dict 后（F0-4），iloc 是 Series
    专属 API——dict 上 iloc[-1] 是 AttributeError（review 二轮 R-13）。
    指标列缺失/改名时取末列兜底（旧 Series.iloc[-1] 语义的 dict 等价）。
    """
    v = coalesce_field(row, *keys)
    if v is not None:
        return v
    vals = list(row.values())
    return safe_float(vals[-1]) if vals else None


def fmt_amount(v: Any, unit: str = "", precision: int = 2) -> str:
    """格式化数值为 亿/万 可读形式，供渲染与标签使用。

    None → "-"；非数字 → str(v)；否则按量级附加 亿/万。
    ``precision`` 控制小数位（如 ``precision=0`` 输出整数，gap-scan 报表用）。
    """
    if v is None:
        return "-"
    f = safe_float(v)
    if f is None:
        return str(v)
    if abs(f) >= ONE_PER_YI:
        return f"{f / ONE_PER_YI:.{precision}f}亿"
    if abs(f) >= ONE_PER_WAN:
        return f"{f / ONE_PER_WAN:.{precision}f}万"
    return f"{f:.{precision}f}{unit}" if unit else f"{f:.{precision}f}"
