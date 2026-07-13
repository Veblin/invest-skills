"""多源加权 RRF (Reciprocal Rank Fusion) 融合引擎。

将多源数据从「并排展示」变成「加权融合+差异标记」。
融合可以在 collector 层（有原始 SourceResult）或使用 legacy dict 格式完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import (
    CROSS_SOURCE_DIFF_THRESHOLD,
    _extract_scalar,
    relative_diff_pct,
)


RRF_K = 60

# 各数据源质量权重（用于 RRF 排序加权）
SOURCE_QUALITY = {
    "tushare": 0.95,
    "akshare": 0.75,
    "baostock": 0.70,
    "tencent_finance": 0.65,
    "websearch": 0.50,
}


@dataclass
class FusedDataPoint:
    """单维度多源融合结果。"""
    dimension: str
    fused_value: float | None
    source_values: dict[str, float] = field(default_factory=dict)
    source_weights: dict[str, float] = field(default_factory=dict)
    consensus: str = "weak"        # "strong" | "moderate" | "weak"
    max_diff_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "fused_value": self.fused_value,
            "source_values": dict(self.source_values),
            "source_weights": dict(self.source_weights),
            "consensus": self.consensus,
            "max_diff_pct": self.max_diff_pct,
        }


def _source_weight(source_name: str) -> float:
    """根据源名获取质量权重（按前缀匹配）。"""
    for prefix, weight in sorted(SOURCE_QUALITY.items(), key=lambda x: -len(x[0])):
        if source_name.startswith(prefix):
            return weight
    return 0.50  # 未知源默认权重


def _consensus_from_diff(max_diff: float) -> str:
    if max_diff <= CROSS_SOURCE_DIFF_THRESHOLD:
        return "strong"
    if max_diff <= CROSS_SOURCE_DIFF_THRESHOLD * 5:
        return "moderate"
    return "weak"


def weighted_rrf_for_dimension(
    dimension: str,
    sources: dict[str, float | None],
) -> FusedDataPoint | None:
    """对单维度多源数据做加权 RRF 融合。"""
    valid = {k: v for k, v in sources.items() if v is not None}
    if not valid:
        return None

    if len(valid) == 1:
        src, val = next(iter(valid.items()))
        return FusedDataPoint(
            dimension=dimension,
            fused_value=val,
            source_values=dict(valid),
            source_weights={src: 1.0},
            consensus="weak",
            max_diff_pct=0.0,
        )

    sorted_sources = sorted(valid.items(), key=lambda x: _source_weight(x[0]), reverse=True)

    rrf_scores: dict[str, float] = {}
    for rank_i, (src, _val) in enumerate(sorted_sources, start=1):
        rrf_scores[src] = _source_weight(src) / (RRF_K + rank_i)

    total_rrf = sum(rrf_scores.values())
    weights = {src: s / total_rrf for src, s in rrf_scores.items()} if total_rrf > 0 else {}

    fused_val = sum(val * weights.get(src, 0) for src, val in valid.items())

    vals = list(valid.values())
    max_v, min_v = max(vals), min(vals)
    avg_v = sum(vals) / len(vals)
    max_diff = relative_diff_pct(max_v, min_v, avg_v) or 0.0
    consensus = _consensus_from_diff(max_diff)

    return FusedDataPoint(
        dimension=dimension,
        fused_value=round(fused_val, 4),
        source_values=dict(valid),
        source_weights=weights,
        consensus=consensus,
        max_diff_pct=round(max_diff * 100, 2),
    )


def dimension_results_from_legacy(dimensions: list[dict]) -> dict[str, Any]:
    """从 legacy dict 重建 DimensionResult，供 fuse_from_source_results 使用。"""
    from .schema import DimensionResult, SourceResult

    out: dict[str, Any] = {}
    for dim in dimensions:
        if not dim:
            continue
        name = dim.get("dimension", "")
        meta = dim.get("_meta", {})
        primary_data = dim.get("data")
        primary_source = meta.get("source", "none")
        if primary_source.startswith("merged:"):
            primary_source = ""
        src_list: list[SourceResult] = []
        for s in meta.get("all_sources", []):
            src_name = s.get("source", "")
            if not src_name:
                continue
            if primary_source and src_name == primary_source and primary_data is not None:
                data = primary_data
            elif s.get("scalar_value") is not None:
                data = s["scalar_value"]
            else:
                data = None
            src_list.append(SourceResult(
                src_name, data, name,
                query_params=s.get("query_params", ""),
                confidence=s.get("confidence"),
                error=s.get("error"),
                fetched_at=s.get("fetched_at"),
            ))
        if src_list:
            out[name] = DimensionResult(name, src_list)
    return out


def fuse_from_source_results(
    dim_results: dict[str, Any],
) -> dict[str, FusedDataPoint]:
    """从原始 DimensionResult 对象做融合（与 legacy 路径共用 schema._extract_scalar）。"""
    from .schema import DimensionResult

    fusion_results: dict[str, FusedDataPoint] = {}
    for dim_name, dim_result in dim_results.items():
        if not isinstance(dim_result, DimensionResult):
            continue
        sources: dict[str, float | None] = {}
        for src in dim_result.all_sources:
            if src.data is None:
                continue
            v = _extract_scalar(src.data)
            if v is not None:
                sources[src.source] = v
        if sources:
            fp = weighted_rrf_for_dimension(dim_name, sources)
            if fp:
                fusion_results[dim_name] = fp
    return fusion_results


def fuse_from_legacy_dicts(dimensions: list[dict]) -> dict[str, FusedDataPoint]:
    """从 legacy dict 格式做融合（读取 SourceResult.to_dict 注入的 scalar_value）。"""
    fusion_results: dict[str, FusedDataPoint] = {}
    for dim in dimensions:
        if not dim:
            continue
        dim_name = dim.get("dimension", "")
        meta = dim.get("_meta", {})
        all_src = meta.get("all_sources", [])
        if not all_src:
            continue
        sources: dict[str, float | None] = {}
        for s in all_src:
            src_name = s.get("source", "")
            sv = s.get("scalar_value")
            if sv is None:
                continue
            try:
                sources[src_name] = float(sv)
            except (TypeError, ValueError):
                continue
        if sources:
            fp = weighted_rrf_for_dimension(dim_name, sources)
            if fp:
                fusion_results[dim_name] = fp
    return fusion_results


def fusion_results_to_dict(fusion: dict[str, FusedDataPoint]) -> dict[str, dict]:
    """将 FusedDataPoint 映射转为可 JSON 序列化的 dict。"""
    return {k: v.to_dict() if isinstance(v, FusedDataPoint) else v for k, v in fusion.items()}
