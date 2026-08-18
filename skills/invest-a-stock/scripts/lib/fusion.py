"""多源加权 RRF (Reciprocal Rank Fusion) 融合引擎。

将多源数据从「并排展示」变成「加权融合+差异标记」。
融合可以在 collector 层（有原始 SourceResult）或使用 legacy dict 格式完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import (
    _CV_L2_FIELDS,
    _extract_l2_scalar,
    _extract_l2_scalar_or_fallback,
    _extract_scalar,
    relative_diff_pct,
)

# R12h（决策 C5 影响面修正）：共识带判定不再共享 CROSS_SOURCE_DIFF_THRESHOLD——
# 该阈值已放宽至 5%（跨源差异标注口径），若共用会把 strong 共识带拉宽至 5%、moderate 至 25%，
# 击穿 test_fusion.py 存量语义。此处定义自有常量，保持报告「strong ≤1% / moderate ≤5%」语义不变。
_FUSION_STRONG_MAX = 0.01
_FUSION_MODERATE_MAX = 0.05


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
    if max_diff <= _FUSION_STRONG_MAX:
        return "strong"
    if max_diff <= _FUSION_MODERATE_MAX:
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
            else:
                # R12h（决策 C5）：L2 维度非主源只接受原始 data 的白名单
                # 提取（allow_scalar_fallback=False，绝不回退存储的 scalar_value
                # ——可能是旧 to_dict 键序提取的 PE，600206 实证 140.16，注入后
                # 经 _auto_cross_validate 裸标量短路混入市值交叉验证 divergence
                # 104.3%；无白名单数据 → 不注入，口径一致性优先，宁可单源）。
                data = _extract_l2_scalar_or_fallback(
                    s.get("data"), s.get("scalar_value"), name,
                    allow_scalar_fallback=False)
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
            # 融合值口径与跨源差异标注一致（C5）：估值维度用市值键——
            # _DIM_SCALAR_KEYS 键序让 pe_ttm 先命中，而 _auto_cross_validate
            # 用 _CV_L2_FIELDS 市值键，同一报告曾出现融合值=PE、差异标注=市值。
            # 必须用 _extract_l2_scalar 而非 _extract_scalar：后者对裸标量
            # 短路（_numeric_scalar(data) 直接返回）绕过显式 keys，会把 PE
            # 混入市值融合（600206 实证：445.71 市值 vs 140.16 PE → 融合值
            # 322.78 不可用）。源头已封堵：SourceResult.to_dict 对 L2 维度
            # 按白名单提取，dimension_results_from_legacy 只接受白名单数据；
            # 此处兜底防裸标量绕过。_extract_l2_scalar 仅认白名单字段
            # （dict/list），裸标量返回 None。
            if dim_name == "valuation":
                # 显式枚举而非 `dim_name in _CV_L2_FIELDS`：白名单新增第三维时
                # 不得被静默切换到 L2 全扫描口径（code-review）。financials
                # 必须走 _extract_scalar：_DIM_SCALAR_KEYS["financials"] 含 eps
                # 且 latest_only 口径，与 _CV_L2_FIELDS["financials"]（无 eps、
                # 全表扫描）不同（见 test_fusion.py 存量语义），不可改查表。
                v = _extract_l2_scalar(src.data, _CV_L2_FIELDS[dim_name])
            else:
                v = _extract_scalar(src.data, dim_name)
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
            # R12h（决策 C5）：L2 维度优先按原始 data 白名单提取（忽略存储的
            # 旧 scalar_value，旧 to_dict 键序提取的 PE 140.16）；无 data 时
            # 回退 scalar_value（兼容手写/旧快照格式，to_dict 修复后已口径正确）。
            sv = _extract_l2_scalar_or_fallback(
                s.get("data"), s.get("scalar_value"), dim_name,
                allow_scalar_fallback=True)
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
