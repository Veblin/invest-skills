"""概率加权盈亏比计算（v0.2.3 新增）。

复用 valuation.py 的 scenario_fcff() + render_dcf.py 的 DCF 聚合逻辑，
从 collection 自动推导三情景目标价，计算：
  - 上行空间 / 下行空间
  - 概率加权预期回报
  - 盈亏比（≥2:1 为专业买方标准）
  - 是否通过阈值

使用方式:
    from lib.risk_reward import compute_risk_reward

    result = compute_risk_reward(collection)
    # result["risk_reward_ratio"] → 2.1
    # result["meets_threshold"] → True
"""

from __future__ import annotations

from typing import Any

from .nums import ONE_PER_YI

# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------

def calc_risk_reward(
    current_price: float,
    bull_target: float,
    base_target: float,
    bear_target: float,
    bull_prob: float = 0.20,
    base_prob: float = 0.50,
    bear_prob: float = 0.30,
) -> dict[str, Any]:
    """从情景目标价和概率计算盈亏比。

    Args:
        current_price: 当前股价
        bull_target: 乐观情景每股目标价
        base_target: 中性情景每股目标价
        bear_target: 悲观情景每股目标价
        bull_prob: 乐观情景概率（默认 0.20，来自 valuation_calc.py SCENARIOS）
        base_prob: 中性情景概率（默认 0.50）
        bear_prob: 悲观情景概率（默认 0.30）

    Returns:
        dict with keys:
            - upside_pct: 乐观上行百分比
            - downside_pct: 悲观下行百分比（取绝对值）
            - expected_return_pct: 概率加权预期回报
            - risk_reward_ratio: 盈亏比 = (P_bull * upside) / (P_bear * downside)
            - meets_threshold: 是否 ≥ 2:1
            - scenarios: 每个情景的详细信息
    """
    if current_price <= 0:
        return {
            "error": "当前价格 ≤ 0，无法计算盈亏比",
            "upside_pct": None,
            "downside_pct": None,
            "expected_return_pct": None,
            "risk_reward_ratio": None,
            "meets_threshold": False,
            "scenarios": {},
        }

    upside = (bull_target - current_price) / current_price
    downside = (current_price - bear_target) / current_price
    base_return = (base_target - current_price) / current_price

    expected_return = (
        bull_prob * upside
        + base_prob * base_return
        + bear_prob * (-downside)
    )

    if downside > 0 and bear_prob > 0:
        rr_ratio = (bull_prob * upside) / (bear_prob * downside)
    else:
        rr_ratio = float("inf")

    return {
        "current_price": round(current_price, 2),
        "upside_pct": round(upside * 100, 1),
        "downside_pct": round(downside * 100, 1),
        "base_return_pct": round(base_return * 100, 1),
        "expected_return_pct": round(expected_return * 100, 1),
        "risk_reward_ratio": round(rr_ratio, 2),
        "meets_threshold": rr_ratio >= 2.0,
        "threshold": 2.0,
        "scenarios": {
            "bull": {
                "target_price": round(bull_target, 2),
                "probability": f"{bull_prob * 100:.0f}%",
                "return_pct": round(upside * 100, 1),
            },
            "base": {
                "target_price": round(base_target, 2),
                "probability": f"{base_prob * 100:.0f}%",
                "return_pct": round(base_return * 100, 1),
            },
            "bear": {
                "target_price": round(bear_target, 2),
                "probability": f"{bear_prob * 100:.0f}%",
                "return_pct": round(-downside * 100, 1),
            },
        },
    }


# ---------------------------------------------------------------------------
# 从 collection 自动推导
# ---------------------------------------------------------------------------

def compute_dcf_risk_reward(
    collection: dict,
    *,
    rf_override: float | None = None,
    erp_override: float | None = None,
    terminal_g_override: float | None = None,
    probabilities: dict[str, float] | None = None,
) -> dict[str, Any]:
    """从 collection 自动运行 DCF 三情景 → 盈亏比。

    步骤:
        1. index_dimensions → dims
        2. 提取 current_price（kline）
        3. 提取 shares, net_debt
        4. 计算 WACC（默认 beta=1.0, rf=2.5%, erp=6%）
        5. 对 financials 运行 scenario_fcff() × 3
        6. 对每个情景折现 → enterprise_value → per_share target
        7. calc_risk_reward()

    Args:
        collection: collect_all() 返回的 collection dict
        rf_override: 手动指定无风险利率（小数）
        erp_override: 手动指定 ERP（小数）
        terminal_g_override: 手动指定终端增长率（小数）
        probabilities: 三情景概率权重 {"bear":0.3, "base":0.4, "bull":0.3}

    Returns:
        dict with calc_risk_reward() 的输出 + _meta（假设、数据来源）。
        净债务不可得（非有息口径）时返回 {"error": "…每股换算已抑制…"}
        （F0-1 与 render_dcf 同口径），不输出每股目标价。
    """
    from lib.schema import index_dimensions
    from lib.valuation import scenario_fcff, calc_ev_to_equity
    from lib.render_dcf import (
        _dcf_try_wacc,
        _dcf_extract_shares,
        _dcf_extract_net_debt,
        _aggregate_scenario_dcf,
    )
    from lib.technical import sort_kline_asc

    dims = index_dimensions(collection)

    # ---- Step 1: 当前价格 ----
    kline_data = dims.get("kline", {}).get("data")
    if not isinstance(kline_data, list) or not kline_data:
        return {"error": "collection 缺少 kline 数据，无法获取当前价格"}

    k_sorted = sort_kline_asc(kline_data)
    current_price = k_sorted[-1].get("close")
    if current_price is None or float(current_price) <= 0:
        return {"error": "K 线数据无效（无收盘价）"}

    current_price = float(current_price)

    # ---- Step 2: 总股本 + 净债务 ----
    shares, shares_source = _dcf_extract_shares(dims)
    if shares is None:
        return {"error": f"无法获取总股本（{shares_source}），无法将企业价值转为每股价格"}

    financials = dims.get("financials") or {}
    net_debt, nd_source = _dcf_extract_net_debt(financials)

    # ---- Step 3: WACC ----
    market_structure = collection.get("market_structure") or {}

    wacc_result, wacc_missing = _dcf_try_wacc(
        financials, market_structure, kline_data,
        rf_override=rf_override, erp_override=erp_override,
    )
    if wacc_result is None:
        return {"error": f"WACC 计算失败: {', '.join(wacc_missing)}"}

    wacc = wacc_result.get("wacc")
    if wacc is None or wacc <= 0:
        return {"error": f"WACC 无效: {wacc}，来源: {wacc_missing}"}

    # Extract actual rf/erp from WACC result (reflects what was really used,
    # not the fallback default)
    wacc_components = wacc_result.get("components") or {}
    rf_val = wacc_components.get("risk_free_rate", 0.025)
    erp_val = wacc_components.get("erp", 0.06)

    # ---- Step 4: 终端增长率 ----
    terminal_g = terminal_g_override if terminal_g_override is not None else 0.025

    # ---- Step 5: 三情景 FCFF → DCF → 每股目标价 ----
    if net_debt is None:
        # F0-1 同口径：净债务（有息负债 − 货币资金）不可得时，每股价值
        # = (EV − ND)/shares 无法计算——不得用 0 替代（否则目标价被整个
        # 净债务抬高：300750 实测 3528.7 亿 / 24.6 亿股 = 143.44 元/股虚高
        # [来源: Python calc: 3528.7/24.6]）。
        # 与 render_dcf「每股换算已抑制」一致，显式失败而非静默错数。
        return {
            "error": "净债务不可得（有息负债字段未采集），每股换算已抑制——"
            "与 render_dcf 同口径，不输出每股目标价",
            "_meta": {"net_debt_source": nd_source},
        }

    scenarios: dict[str, float] = {}
    scenario_details: dict[str, dict] = {}

    for scenario_name in ("bear", "base", "bull"):
        fcff_result = scenario_fcff(financials, scenario=scenario_name,
                                     probabilities=probabilities)
        if "error" in fcff_result:
            return {
                "error": f"{scenario_name} 情景 FCFF 生成失败: {fcff_result['error']}",
                "scenario": scenario_name,
            }

        yearly_fcff = fcff_result.get("yearly_fcff")
        if not yearly_fcff:
            return {"error": f"{scenario_name} 情景无 yearly_fcff 数据"}

        dcf = _aggregate_scenario_dcf(yearly_fcff, wacc, terminal_g)
        if dcf is None:
            return {"error": f"{scenario_name} 情景 DCF 折现失败（wacc={wacc}, g={terminal_g}）"}

        ev = dcf["enterprise_value"]
        # net_debt 恒非 None（Step 5 前已对 None 显式失败）——不再保留
        # `else 0` 兜底：0 是合法金融值，静默替代会复活「目标价被整个
        # 净债务抬高」的错数模式（D1 家族）
        per_share = calc_ev_to_equity(ev, net_debt, shares)
        if "error" in per_share:
            return {"error": f"{scenario_name} 情景 EV→每股 转换失败: {per_share['error']}"}

        target = per_share["per_share"]
        scenarios[scenario_name] = target
        scenario_details[scenario_name] = {
            "enterprise_value_yi": round(ev / ONE_PER_YI, 2),
            "per_share": round(target, 2),
            "assumptions": fcff_result.get("assumptions", {}),
            "probability": fcff_result.get("probability", 1 / 3),
        }

    # ---- Step 6: 盈亏比 ----
    prob = probabilities or {"bear": 1 / 3, "base": 1 / 3, "bull": 1 / 3}
    result = calc_risk_reward(
        current_price,
        bull_target=scenarios["bull"],
        base_target=scenarios["base"],
        bear_target=scenarios["bear"],
        bull_prob=prob.get("bull", 1 / 3),
        base_prob=prob.get("base", 1 / 3),
        bear_prob=prob.get("bear", 1 / 3),
    )

    # ---- Step 7: 附加元数据 ----
    # 注：net_debt None 已在 Step 5 前显式失败，此路径恒有净债务
    result["_meta"] = {
        "method": "DCF two-stage with scenario_fcff",
        "wacc": round(wacc, 4),
        "wacc_source": wacc_result.get("beta_source", "default 1.0"),
        "wacc_missing_defaults": wacc_missing,
        "terminal_g": terminal_g,
        "shares_source": shares_source,
        "shares": shares,
        "net_debt_yi": round(net_debt / ONE_PER_YI, 2) if net_debt is not None else None,
        "net_debt_source": nd_source,
        "rf": rf_val,
        "erp": erp_val,
        "scenario_details": scenario_details,
        "disclaimer": "三情景假设基于历史财务数据的规则代理（非分析师预测），"
                      "概率权重为默认值，仅供参考，不构成投资建议。",
    }

    return result


# ---------------------------------------------------------------------------
# Markdown 格式化
# ---------------------------------------------------------------------------

def format_risk_reward_table(result: dict) -> str:
    """将 calc_risk_reward() 或 compute_dcf_risk_reward() 的结果格式化为 Markdown 表格。"""
    if "error" in result:
        return f"❌ **盈亏比计算失败**: {result['error']}"

    rr = result
    rr_ratio_val = rr['risk_reward_ratio']
    ratio_display = f"{rr_ratio_val:.1f}" if rr_ratio_val != float('inf') else '∞'
    lines = [
        f"## 多情景盈亏比分析",
        "",
        f"| 指标 | 数值 |",
        f"|------|:-----|",
        f"| 当前价格 | {rr['current_price']:.2f} 元 |",
        f"| 上行空间（乐观） | +{rr['upside_pct']}% |",
        f"| 下行空间（悲观） | −{rr['downside_pct']}% |",
        f"| 中性回报 | {rr['base_return_pct']:+.1f}% |",
        f"| 概率加权预期回报 | {rr['expected_return_pct']:+.1f}% |",
        f"| **盈亏比** | **{ratio_display}:1** |",
        f"| 阈值 | ≥ 2:1 |",
        f"| 判定 | {'✅ 通过' if rr['meets_threshold'] else '❌ 未通过'} |",
        "",
        "### 情景明细",
        "",
        "| 情景 | 概率 | 目标价 | 涨跌幅 |",
        "|------|:---:|:-----:|:-----:|",
    ]

    for key in ("bull", "base", "bear"):
        s = rr["scenarios"].get(key, {})
        lines.append(
            f"| {key} | {s.get('probability', '?')} | "
            f"{s.get('target_price', '?')} 元 | "
            f"{s.get('return_pct', 0):+.1f}% |"
        )

    # 附加 DCF 方法学元数据
    meta = rr.get("_meta", {})
    if meta:
        lines.extend([
            "",
            "### 计算方法",
            "",
            f"- WACC: {meta.get('wacc', '?')}（beta={meta.get('wacc_source', '?')}）",
            f"- 终端增长率: {meta.get('terminal_g', '?')}",
            f"- 总股本: {meta.get('shares', '?')} 股（{meta.get('shares_source', '?')}）",
            f"- 净债务: {meta['net_debt_yi'] if meta.get('net_debt_yi') is not None else '?'} 亿",
            f"- 无风险利率: {meta.get('rf', '?')} | ERP: {meta.get('erp', '?')}",
            "",
            f"> ⚠️ {meta.get('disclaimer', '')}",
        ])

    return "\n".join(lines)
