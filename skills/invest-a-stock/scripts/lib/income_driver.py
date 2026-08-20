"""R1: 收益驱动假设分类（研究路径分流）。

核心洞见（面基方法论）："不能把基本面的投资手册当成趋势投资的航海指南"——
散户失败根源是流派混搭（趋势起念买入 + 价值理由自我麻醉）。在深挖基本面
之前，先回答"这只股票的收益驱动是什么"，决定用哪套研究框架。

设计约束（观点GPT 裁决）：
- 输出为「收益驱动假设」4 选 1，不是"树/粮/菜"式自动定性（不自动断言
  "没有投资价值"——那会变成研究结论黑箱）
- 必须展示证据表、置信度、反例
- 证据缺失 → 标注「证据缺失：需 WebSearch/公告补充」，不强行归类
- 引擎计算，AI 不心算（P0）
"""

from __future__ import annotations

from lib.nums import safe_float  # canonical（None/NaN/±inf → None）

# 收益驱动假设四分支
DRIVER_GROWTH = "成长兑现"       # 企业增长创造价值增量（树）
DRIVER_VALUE = "估值股息回归"     # 估值修复 + 股息分红（粮）
DRIVER_CYCLE = "周期均值回归"     # 周期波动中盈利均值回归
DRIVER_UNKNOWN = "暂无法判定"     # 证据不足或矛盾

# 判定阈值（引擎规则，集中可调）
_GROWTH_POSITIVE_YEAR_RATIO = 0.6   # 正增长年占比 ≥60% → 成长倾向
_CYCLE_CV_THRESHOLD = 0.5           # 年度净利变异系数 ≥0.5 → 周期特征
_DIV_YEARS_MIN = 3                  # 连续分红年数 ≥3 → 股息回归证据
_CONF_HIGH_EVIDENCE = 3             # 有效证据 ≥3 → 高置信度


def _yearly_net_profit_series(annual_rows: list[dict]) -> list[float]:
    """年度净利序列（升序）。

    按年份字段显式升序归一：报告路径调用者（render_markdown/style_match）
    喂 Tushare financials 原始行（最新在前），income 表路径为升序——不排序
    会导致 deltas/近两年方向从最旧两段计算、方向反转（code-review 修复）。
    """
    rows = sorted(annual_rows, key=lambda r: str(r.get("year") or ""))
    out: list[float] = []
    for r in rows:
        v = safe_float(r.get("net_profit"))
        if v is not None:
            out.append(v)
    return out


def _growth_evidence(annual: list[float]) -> dict:
    """增速持续性：正增长年占比 + 最近两年方向。"""
    if len(annual) < 3:
        return {"available": False, "reason": "年度净利样本 <3 年"}
    deltas = [annual[i] - annual[i - 1] for i in range(1, len(annual))]
    pos = sum(1 for d in deltas if d > 0)
    ratio = pos / len(deltas)
    return {
        "available": True,
        "positive_year_ratio": round(ratio, 2),
        "positive_years": pos,
        "total_deltas": len(deltas),
        "last_two_direction": "up" if deltas[-1] > 0 and deltas[-2] > 0 else (
            "down" if deltas[-1] < 0 and deltas[-2] < 0 else "mixed"),
    }


def _cycle_evidence(annual: list[float]) -> dict:
    """周期特征：年度净利变异系数（≥阈值 → 周期均值回归倾向）。"""
    if len(annual) < 5:
        return {"available": False, "reason": "年度净利样本 <5 年"}
    mean = sum(annual) / len(annual)
    if mean == 0:
        return {"available": False, "reason": "净利均值为 0（微利/亏损边缘）"}
    var = sum((v - mean) ** 2 for v in annual) / len(annual)
    cv = (var ** 0.5) / abs(mean)
    return {
        "available": True,
        "cv": round(cv, 2),
        "negative_years": sum(1 for v in annual if v < 0),
        "n_years": len(annual),
    }


def _fcf_evidence(fin_rows: list[dict]) -> dict:
    """FCF 持续性（fcff 或 OCF-cap_ex，按财年取年报期值，正数占比）。

    口径：fina_indicator 为累计 YTD（0331/0630/0930/1231 逐期累加），季报行与
    年报行等权会高估正数占比——全年 OCF 为负但 Q2 后累计转正的年份会贡献
    3 条正行；与 quality_check._metric_fcf_5y（e07fe41）同型：每年只取 1231
    年报期（全年数），同一年重复行后覆盖前。
    """
    by_year: dict[str, float] = {}
    for r in fin_rows:
        ed = str(r.get("end_date") or "")
        if not ed.endswith("1231"):
            continue
        v = safe_float(r.get("fcff"))
        if v is None:
            v = safe_float(r.get("fcfe"))
        if v is None and r.get("ocf") is not None and r.get("cap_ex") is not None:
            ocf = safe_float(r.get("ocf"))
            capex = safe_float(r.get("cap_ex"))
            if ocf is not None and capex is not None:
                v = ocf - capex
        if v is not None:
            by_year[ed[:4]] = v
    years = [by_year[k] for k in sorted(by_year)]
    if len(years) < 3:
        return {"available": False, "reason": "FCF 年报样本 <3 年"}
    pos = sum(1 for v in years if v > 0)
    return {
        "available": True,
        "positive_ratio": round(pos / len(years), 2),
        "positive_periods": pos,
        "n_periods": len(years),
        "latest": years[-1],
    }


def _dividend_evidence(div_years: int | None, div_yield: float | None) -> dict:
    """分红历史（连续分红年数 + 股息率）。"""
    if div_years is None and div_yield is None:
        return {"available": False, "reason": "分红数据未提供"}
    return {
        "available": True,
        "div_years": div_years,
        "div_yield": div_yield,
    }


def _refinancing_evidence(refi_times: int | None) -> dict:
    """再融资历史（增发/配股次数，稀释视角的反例证据）。"""
    if refi_times is None:
        return {"available": False, "reason": "再融资数据未提供"}
    return {"available": True, "refi_times": refi_times}


def classify_income_driver(
    annual_rows: list[dict],
    fin_rows: list[dict] | None = None,
    *,
    div_years: int | None = None,
    div_yield: float | None = None,
    refi_times: int | None = None,
    industry: str | None = None,
) -> dict:
    """R1: 收益驱动假设分类。

    Args:
        annual_rows: [{year, net_profit}] 年度净利序列（income 表口径）
        fin_rows: financials 记录（含 fcff/fcfe/ocf/cap_ex）
        div_years: 连续分红年数（None = 未提供 → 证据缺失标注）
        div_yield: 当前股息率（小数，None = 未提供）
        refi_times: 近 5 年再融资次数（None = 未提供）
        industry: 行业名（银行/非银金融等金融行业对成长分支减权——银行
            年年名义正增长但增速个位数，非"成长兑现"逻辑）

    Returns:
        {
          "driver": 4 选 1,
          "confidence": 高/中/低,
          "evidence": {growth: {...}, cycle: {...}, fcf: {...},
                       dividend: {...}, refi: {...}},
          "counter_evidence": [...],
          "missing_evidence": [...],
        }
    """
    annual = _yearly_net_profit_series(annual_rows)
    ev_growth = _growth_evidence(annual)
    ev_cycle = _cycle_evidence(annual)
    ev_fcf = _fcf_evidence(fin_rows or [])
    ev_div = _dividend_evidence(div_years, div_yield)
    ev_refi = _refinancing_evidence(refi_times)

    evidence = {
        "growth": ev_growth,
        "cycle": ev_cycle,
        "fcf": ev_fcf,
        "dividend": ev_div,
        "refi": ev_refi,
    }

    # 有效证据计数（置信度基础）
    valid = [k for k, v in evidence.items() if v.get("available")]
    confidence = (
        "高" if len(valid) >= _CONF_HIGH_EVIDENCE else (
            "中" if len(valid) == 2 else "低")
    )

    # 缺失证据（需 WebSearch/公告补充）
    missing = [k for k, v in evidence.items() if not v.get("available")
               and k in ("dividend", "refi")]

    # 各分支证据强度（引擎规则，非 AI 定性）
    # F2-1: 成长分支加入增速量级约束——银行等「年年正增长但增速个位数」
    # 的标的，正增长年占比的证据贡献按近年年化净利增速缩放（年化 <8% 时
    # 衰减；下限 0.15 防全灭）。窗口取最近 3 年（全窗口 CAGR 会被早年
    # 高增长抬高，招行 2015→2025 全窗口 ~10% 但近 3 年 ~3%）。
    _cagr_window = annual[-4:] if len(annual) >= 4 else annual
    _has_loss_year = any(v <= 0 for v in _cagr_window)
    annual_cagr_pct: float | None = None
    if len(_cagr_window) >= 2:
        if not _has_loss_year:
            # 全窗口 CAGR：全程盈利才可算——任一端亏损时负数底数的
            # 小数次幂返回复数，min/max 比较直接 TypeError 崩掉整个渲染链
            # （亏损期标的走 report/classify 即炸）。
            _years = len(_cagr_window) - 1
            if _years >= 1:
                annual_cagr_pct = ((_cagr_window[-1] / _cagr_window[0]) ** (1 / _years) - 1) * 100
        elif len(_cagr_window) >= 3 and _cagr_window[-2] > 0:
            # 窗口含亏损年：全窗口 CAGR 不可算。用最近一年增速近似增速量级
            # （分母须为正；终点亏损时增速为负 → 落 scale 下限 0.15）——
            # 否则"亏损恢复"标的拿全权重、稳健正增长标的反而被 F2-1 衰减
            # （不对称：恢复≠成长兑现）。
            annual_cagr_pct = (_cagr_window[-1] / _cagr_window[-2] - 1) * 100
    growth_scale = 1.0
    if annual_cagr_pct is not None:
        growth_scale = min(1.0, max(0.15, annual_cagr_pct / 8.0))
    if _has_loss_year:
        # 「恢复≠成长兑现」：窗口含亏损年时再封顶 0.5——高增速恢复年
        # （如 -50→200→210→600，单年 185.7%）按 /8 会 cap 到 1.0 满权重，
        # 绕过量级约束（review 二轮 live repro）。
        growth_scale = min(growth_scale, 0.5)

    growth_score = 0.0
    if ev_growth.get("available"):
        growth_score += ev_growth["positive_year_ratio"] * growth_scale
        if ev_growth.get("last_two_direction") == "up":
            growth_score += 0.2
    if ev_fcf.get("available"):
        growth_score += ev_fcf["positive_ratio"] * 0.3
    # F2-1: 金融行业成长分支减权——银行名义正增长≠成长兑现驱动
    if industry in ("银行", "非银金融", "保险", "证券", "多元金融"):
        growth_score *= 0.5

    value_score = 0.0
    if ev_cycle.get("available"):
        value_score += (1 - min(ev_cycle["cv"] / _CYCLE_CV_THRESHOLD, 1.0)) * 0.5
        if ev_cycle["negative_years"] == 0:
            value_score += 0.2
    if ev_div.get("available"):
        if (ev_div.get("div_years") or 0) >= _DIV_YEARS_MIN:
            value_score += 0.3
        # F2-1: 高股息率（≥4%）给股息回归分支额外加权（红利型标的）
        if (ev_div.get("div_yield") or 0) >= 0.04:
            value_score += 0.3
    if ev_fcf.get("available"):
        value_score += ev_fcf["positive_ratio"] * 0.2

    cycle_score = 0.0
    if ev_cycle.get("available"):
        cycle_score += min(ev_cycle["cv"] / _CYCLE_CV_THRESHOLD, 1.0)
        if ev_cycle["negative_years"] > 0:
            cycle_score += 0.3

    # 判定：分差显著时取最高分；不显著（<0.15）→ 暂无法判定
    scores = {
        DRIVER_GROWTH: round(growth_score, 3),
        DRIVER_VALUE: round(value_score, 3),
        DRIVER_CYCLE: round(cycle_score, 3),
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    second_score = ranked[1][1]

    # R1b: 单证据 + 低置信度 → 强制「暂无法判定」（避免 3 年样本单靠增速
    # 就断言"成长兑现"——证据不足时研究路径本身就不应被锁定）
    if (
        top_score < 0.3
        or (top_score - second_score) < 0.15
        or (len(valid) == 1 and confidence == "低")
    ):
        driver = DRIVER_UNKNOWN
    else:
        driver = top_name

    # 反例列表（与主假设矛盾的证据）
    counter: list[str] = []
    if driver == DRIVER_GROWTH and ev_fcf.get("available") and ev_fcf["positive_ratio"] < 0.4:
        counter.append(f"FCF 为正占比 {ev_fcf['positive_ratio']:.0%}，成长含金量存疑（利润先行现金流滞后）")
    if driver == DRIVER_GROWTH and (ev_refi.get("refi_times") or 0) >= 2:
        counter.append(f"再融资 {ev_refi['refi_times']} 次，增长依赖外部融资稀释（窗口口径见 --refi-times 传入值）")
    if driver == DRIVER_VALUE and ev_cycle.get("available") and ev_cycle["cv"] >= _CYCLE_CV_THRESHOLD:
        counter.append(f"净利变异系数 {ev_cycle['cv']:.2f} 偏高，'价值'特征可能实为周期")
    if driver == DRIVER_CYCLE and ev_growth.get("available") and ev_growth["positive_year_ratio"] >= _GROWTH_POSITIVE_YEAR_RATIO:
        counter.append(f"正增长年占比 {ev_growth['positive_year_ratio']:.0%} 较高，可能处于周期上行而非纯周期波动")
    if driver == DRIVER_UNKNOWN and ev_growth.get("available"):
        counter.append("分支证据接近（分差 <0.15），无法显著区分成长/价值/周期")

    return {
        "driver": driver,
        "confidence": confidence,
        "scores": scores,
        "evidence": evidence,
        "counter_evidence": counter,
        "missing_evidence": missing,
    }


def format_classify_result(result: dict) -> str:
    """R1: classify 命令文本渲染（引擎输出，AI 直接引用）。"""
    lines = [
        f"【收益驱动假设（R1）】 {result['driver']}（置信度: {result['confidence']}）",
        "  证据表（引擎计算）:",
    ]
    ev = result["evidence"]
    g = ev["growth"]
    if g.get("available"):
        lines.append(
            f"    - 增长持续性: 正增长年占比 {g['positive_year_ratio']:.0%}"
            f"（{g['positive_years']}/{g['total_deltas']}，近两年 {g['last_two_direction']}）")
    else:
        lines.append(f"    - 增长持续性: 不可得（{g.get('reason', '')}）")
    c = ev["cycle"]
    if c.get("available"):
        lines.append(
            f"    - 周期特征: 年度净利变异系数 {c['cv']:.2f}（亏损年 {c['negative_years']}/{c['n_years']}）")
    else:
        lines.append(f"    - 周期特征: 不可得（{c.get('reason', '')}）")
    f = ev["fcf"]
    if f.get("available"):
        lines.append(f"    - FCF: 为正占比 {f['positive_ratio']:.0%}（{f['positive_periods']}/{f['n_periods']} 期）")
    else:
        lines.append(f"    - FCF: 不可得（{f.get('reason', '')}）")
    d = ev["dividend"]
    if d.get("available"):
        lines.append(f"    - 分红: 连续 {d.get('div_years') or 0} 年（股息率 {d.get('div_yield') or '—'}）")
    else:
        lines.append("    - 分红: 未提供（需公告补充）")
    r = ev["refi"]
    if r.get("available"):
        lines.append(f"    - 再融资: {r.get('refi_times') or 0} 次")
    else:
        lines.append("    - 再融资: 未提供（需公告补充）")
    if result["scores"]:
        lines.append("  分支得分: " + " | ".join(
            f"{k} {v}" for k, v in result["scores"].items()))
    if result["counter_evidence"]:
        lines.append("  反例:")
        for item in result["counter_evidence"]:
            lines.append(f"    - {item}")
    if result["missing_evidence"]:
        lines.append("  🔍 证据缺失（需 WebSearch/公告补充）: " + "、".join(result["missing_evidence"]))
    lines.append("  （研究路径分流参考——该假设决定分析重点与待验证问题，非投资建议）")
    return "\n".join(lines)
