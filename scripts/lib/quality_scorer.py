"""
数据质量评模块。

独立于采集层和渲染层，从 config/ YAML 读取评规则。

分阶段启用：
  MVP: _meta 四字段 + 单一来源标注 + Layer 降级提示
  v0.2: 维度 ★ 展示 + 时效性标注
  v0.3: 交叉验证加分 + 分歧告警
  v1.0: 与 DSA 式 0-100 内部分对接 prompt 护栏
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ------------------------------------------------------------------
# Data Classes
# ------------------------------------------------------------------

@dataclass
class DimensionQuality:
    """单个维度的数据质量评估结果。"""
    dimension_index: int | float
    display_name: str
    stars: float
    baseline_stars: float
    adjustments: list[str] = field(default_factory=list)
    status: str = "available"  # "available" | "degraded" | "missing"


@dataclass
class ReportQuality:
    """整体报告的数据质量评估。"""
    overall_stars: float
    tier: str           # "excellent"|"good"|"fair"|"limited"|"none"
    tier_display: str
    dimension_qualities: dict[str, DimensionQuality] = field(default_factory=dict)


# ------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------

_config_cache: dict[str, Any] = {}


def _load_config(filename: str) -> dict[str, Any]:
    """懒加载 config/ 下的 YAML 文件。"""
    if filename in _config_cache:
        return _config_cache[filename]

    config_dir = Path(__file__).parent.parent.parent / "config"
    path = config_dir / filename
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _config_cache[filename] = data
    return data


def _get_source_config(source_name: str) -> dict[str, Any]:
    """获取单个数据源的配置。"""
    cfg = _load_config("source_credibility.yaml")
    sources = cfg.get("sources", {})
    return sources.get(source_name, {})


def _get_dimension_config(dim_name: str) -> dict[str, Any]:
    """获取单个维度的基准配置。"""
    cfg = _load_config("dimension_baselines.yaml")
    dims = cfg.get("dimensions", {})
    return dims.get(dim_name, {})


def _get_quality_tiers() -> dict[str, Any]:
    """获取质量等级映射。"""
    cfg = _load_config("dimension_baselines.yaml")
    return cfg.get("quality_tiers", {})


# ------------------------------------------------------------------
# 维度质量计算
# ------------------------------------------------------------------

def calculate_dimension_quality(
    dim_name: str,
    meta_list: list[dict[str, Any]],
    raw_data: dict | None = None,
) -> DimensionQuality:
    """计算单个维度的数据质量。

    Args:
        dim_name: 维度名（如 "fundamental"）
        meta_list: 该维度所有数据源的 _meta 列表
        raw_data: 原始数据（用于行数、时效性等额外判断）

    Returns:
        DimensionQuality
    """
    dim_cfg = _get_dimension_config(dim_name)
    baseline_stars = dim_cfg.get("baseline_stars", 3)
    display_name = dim_cfg.get("display_name", dim_name)
    dim_index = dim_cfg.get("index", 0)

    adjustments: list[str] = []

    if not meta_list or all(not m.get("success") for m in meta_list):
        return DimensionQuality(
            dimension_index=dim_index,
            display_name=display_name,
            stars=0,
            baseline_stars=baseline_stars,
            adjustments=["所有数据源失败"],
            status="missing",
        )

    # 取最可靠源
    successful = [m for m in meta_list if m.get("success")]
    if not successful:
        return DimensionQuality(
            dimension_index=dim_index,
            display_name=display_name,
            stars=0.5,
            baseline_stars=baseline_stars,
            adjustments=["无成功数据源"],
            status="missing",
        )

    best = successful[0]
    source_name = best.get("source", "unknown")
    source_cfg = _get_source_config(source_name)
    source_stars = source_cfg.get("stars", 3)
    source_tier = source_cfg.get("tier", "C")

    # 基准星 = 数据源可信度星
    stars = float(source_stars)

    # 单一来源标注
    independent_sources = _count_independent_sources(successful)
    if independent_sources == 1:
        adjustments.append("单一来源")
    elif independent_sources >= 3:
        stars = min(stars + 1.0, 5.0)
        adjustments.append(f"多源交叉验证({independent_sources}源)")

    # 时效性检查
    timeliness = _check_timeliness(dim_name, best, raw_data)
    if timeliness:
        adjustments.append(timeliness["label"])
        stars += timeliness.get("penalty", 0)

    # 准入检查
    min_viable = dim_cfg.get("min_viable_sources", {})
    if min_viable.get("min_tier_s_or_a") and source_tier not in ("S", "A"):
        adjustments.append(f"数据源等级不足({source_tier})")
        stars = max(stars - 0.5, 0.5)

    # 限制范围
    stars = max(0, min(5.0, round(stars, 1)))

    # 状态
    if stars >= baseline_stars:
        status = "available"
    elif stars >= 1:
        status = "degraded"
    else:
        status = "missing"

    return DimensionQuality(
        dimension_index=dim_index,
        display_name=display_name,
        stars=stars,
        baseline_stars=baseline_stars,
        adjustments=adjustments,
        status=status,
    )


def _count_independent_sources(meta_list: list[dict]) -> int:
    """统计独立数据源数量（不同 source_group 算独立）。"""
    groups = set()
    for m in meta_list:
        group = m.get("source_group", "unknown")
        if group and group != "unknown":
            groups.add(group)
    return len(groups) if groups else 1


def _check_timeliness(
    dim_name: str,
    meta: dict,
    raw_data: dict | None,
) -> dict[str, Any] | None:
    """检查数据时效性。"""
    cross_cfg = _load_config("cross_validation_rules.yaml")
    timeliness_cfg = cross_cfg.get("global", {}).get("timeliness", {}).get("windows", {})

    # 财报时效
    if dim_name == "fundamental":
        fin_cfg = timeliness_cfg.get("financial_report", {})
        fetched_at = meta.get("fetched_at", "")
        if fetched_at:
            try:
                dt = datetime.fromisoformat(fetched_at)
                age_days = (datetime.now(timezone.utc) - dt).days
                if age_days > fin_cfg.get("stale_penalty_stars", 90) * 2:
                    return {"label": "财报数据可能过期", "penalty": fin_cfg.get("stale_penalty_stars", -0.5)}
            except Exception:
                pass

    # 新闻时效
    if dim_name in ("sentiment",):
        news_cfg = timeliness_cfg.get("news_sentiment", {})
        return None  # MVP 不做细致时效检查

    return None


# ------------------------------------------------------------------
# 综合质量计算
# ------------------------------------------------------------------

def calculate_overall_quality(
    dimension_results: dict[str, DimensionQuality],
) -> ReportQuality:
    """加权汇总各维度质量，输出综合评级。

    Args:
        dimension_results: {dim_name: DimensionQuality}

    Returns:
        ReportQuality
    """
    cfg = _load_config("dimension_baselines.yaml")
    dims_cfg = cfg.get("dimensions", {})
    tiers = _get_quality_tiers()

    total_weight = 0.0
    weighted_score = 0.0

    for dim_name, dq in dimension_results.items():
        dim_cfg = dims_cfg.get(dim_name, {})
        weight = dim_cfg.get("internal_weight", 10)
        total_weight += weight
        weighted_score += dq.stars * weight

    if total_weight == 0:
        return ReportQuality(
            overall_stars=0,
            tier="none",
            tier_display="综合数据质量: 不可用",
            dimension_qualities=dimension_results,
        )

    avg_stars = round(weighted_score / total_weight, 1)

    # 内部评分 (0-100)
    internal_score = avg_stars / 5.0 * 100

    # 匹配等级
    tier = "none"
    tier_display = "综合数据质量: 不可用"
    for name, tcfg in sorted(tiers.items(), key=lambda x: -x[1].get("min_internal_score", 0)):
        if internal_score >= tcfg.get("min_internal_score", 0):
            tier = name
            tier_display = tcfg.get("display", name)
            break

    return ReportQuality(
        overall_stars=avg_stars,
        tier=tier,
        tier_display=tier_display,
        dimension_qualities=dimension_results,
    )


# ------------------------------------------------------------------
# 从 collect_all 结果计算
# ------------------------------------------------------------------

def evaluate_collection_result(collection: dict[str, Any]) -> tuple[dict[str, DimensionQuality], ReportQuality]:
    """从 data_pipeline.collect_all() 的结果计算质量。

    Args:
        collection: collect_all 的返回值

    Returns:
        (dimension_qualities, report_quality)
    """
    dimensions = collection.get("dimensions", [])
    dim_qualities: dict[str, DimensionQuality] = {}

    for dim in dimensions:
        dim_name = dim.get("dimension", "")
        meta = dim.get("_meta", {})
        dq = calculate_dimension_quality(
            dim_name=dim_name,
            meta_list=[meta] if meta else [],
            raw_data=dim.get("data"),
        )
        dim_qualities[dim_name] = dq

    rq = calculate_overall_quality(dim_qualities)
    return dim_qualities, rq


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Mock 测试
    mock_meta = {
        "source": "akshare.stock_financial_abstract",
        "source_group": "eastmoney",
        "success": True,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    dq = calculate_dimension_quality("fundamental", [mock_meta])
    print(f"维度: {dq.display_name}")
    print(f"  星级: {'★' * int(dq.stars)}{'☆' * (5 - int(dq.stars))} ({dq.stars})")
    print(f"  基准: {dq.baseline_stars}")
    print(f"  调整: {dq.adjustments}")
    print(f"  状态: {dq.status}")

    rq = calculate_overall_quality({"fundamental": dq})
    print(f"\n综合: {rq.overall_stars}★ ({rq.tier} — {rq.tier_display})")
