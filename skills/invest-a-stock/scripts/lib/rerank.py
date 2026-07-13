"""确定性证据可信度评分引擎。

参考 last30days-skill lib/rerank.py 的 penalty/bonus 体系。
对每个维度的证据给出 0-100 的可信度分数。
"""

from __future__ import annotations

from typing import Any

# ---- 基础分 ----

BASE_SCORE = 50.0

# ---- Bonus 加分项 ----

CROSS_SOURCE_MATCH = +15.0     # 多源一致
PAID_SOURCE = +10.0            # Tushare 付费/签名数据
PRIMARY_SOURCE = +5.0          # 被选为主源的渠道
RECENT_DATA = +5.0             # 近 30 日数据（vs 滞后）
MULTI_SOURCE_BONUS = +5.0      # 有 ≥2 个有效源

# ---- Penalty 减分项 ----

DATA_CONFLICT = -10.0          # 两源给出不同值
SINGLE_SOURCE = -10.0          # 仅一个源有数据
OUTDATED_PER_YEAR = -5.0       # 过时数据每年 -5
WEBSEARCH_SOURCE = -5.0        # WebSearch 数据可靠性低


def score_evidence(
    *,
    multi_source: bool = False,
    cross_validation_status: str | None = None,
    primary_source: str = "",
    source_count: int = 1,
    data_age_days: float | None = None,
) -> float:
    """对单个维度的证据可信度打分。

    Args:
        multi_source: 是否有多个源成功
        cross_validation_status: "convergence" | "divergence" | None
        primary_source: 主源名称（如 "tushare.daily_basic"）
        source_count: 有数据的源数量
        data_age_days: 数据年龄（天），None 表示未知

    Returns:
        0-100 的可信度分数
    """
    score = BASE_SCORE

    # 多源交叉验证
    if multi_source and cross_validation_status == "convergence":
        score += CROSS_SOURCE_MATCH
    elif multi_source and cross_validation_status == "divergence":
        score += DATA_CONFLICT
    elif source_count < 2:
        score += SINGLE_SOURCE

    # 多源加分
    if source_count >= 2:
        score += MULTI_SOURCE_BONUS

    # 源质量
    if primary_source.startswith("tushare"):
        score += PAID_SOURCE
    elif primary_source == "websearch" or primary_source.startswith("websearch"):
        score += WEBSEARCH_SOURCE

    # 时效性
    if data_age_days is not None:
        if data_age_days <= 30:
            score += RECENT_DATA
        elif data_age_days > 365:
            years = (data_age_days - 365) / 365
            score += OUTDATED_PER_YEAR * min(years, 5)  # 最多扣 25 分

    return max(0.0, min(100.0, score))


def score_from_dimension_meta(meta: dict) -> float:
    """从 legacy dict 的 _meta 字段计算可信度分数。

    Args:
        meta: dimension["_meta"] dict

    Returns:
        0-100 的可信度分数
    """
    multi_source = meta.get("multi_source", False)
    cv_status = meta.get("cross_validation")
    primary_src = meta.get("source", "")
    all_src = meta.get("all_sources", [])
    source_count = meta.get("source_count", len([s for s in all_src if s.get("data_available")]))

    # 数据年龄
    fetched_at = meta.get("fetched_at", "")
    data_age_days = None
    if fetched_at:
        try:
            from datetime import datetime, timezone
            fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            data_age_days = (now - fetched_dt).total_seconds() / 86400
        except (ValueError, TypeError):
            pass

    return score_evidence(
        multi_source=multi_source,
        cross_validation_status=cv_status,
        primary_source=primary_src,
        source_count=source_count,
        data_age_days=data_age_days,
    )


def score_all_dimensions(dimensions: list[dict]) -> dict[str, float]:
    """对所有维度的证据可信度打分。

    Args:
        dimensions: collect_all 输出的 dimensions 列表

    Returns:
        {dimension_display_name: credibility_score} 映射
    """
    scores: dict[str, float] = {}
    for dim in dimensions:
        dn = dim.get("dimension", "")
        display = dim.get("display", dn)
        meta = dim.get("_meta", {})
        if not meta:
            scores[display] = 30.0  # 无 meta → 极低可信
            continue
        scores[display] = score_from_dimension_meta(meta)
    return scores


def to_credibility_label(score: float) -> str:
    """分数 → 可读标签。"""
    if score >= 80:
        return "🟢 高可信"
    if score >= 60:
        return "🟡 中可信"
    if score >= 40:
        return "🟠 低可信"
    return "🔴 极低可信"


def render_credibility_table(scores: dict[str, float]) -> str:
    """渲染可信度评分为 Markdown 表格。"""
    lines = [
        "## 证据可信度评分",
        "",
        "| 维度 | 分数 | 可信度 |",
        "|------|------|--------|",
    ]
    for dim, score in sorted(scores.items(), key=lambda x: -x[1]):
        label = to_credibility_label(score)
        lines.append(f"| {dim} | {score:.0f} | {label} |")
    return "\n".join(lines)
