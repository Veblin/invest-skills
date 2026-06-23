"""规范化数据结构定义。每个数据源的原始结果统一转为以下结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 跨源差异阈值（交叉验证与融合共用）
CROSS_SOURCE_DIFF_THRESHOLD = 0.01
_SCALAR_EPSILON = 1e-9


# ---- 维度标识 ----

DIMENSIONS = {
    "basic_info": "基本信息",
    "financials": "财务报告",
    "quote": "实时行情",
    "shareholders": "十大股东",
    "northbound": "北向资金",
    "kline": "日K线",
    "valuation": "估值分析",
    "research": "机构研报",
    "industry": "行业数据",
}

# research 维度在 to_legacy_dict 之外附加的汇总字段（collect_research）
RESEARCH_SUMMARY_KEYS = (
    "status", "source", "summary_text", "latest_ratings",
    "target_price_range", "eps_forecasts", "profit_forecasts", "company_guidance",
)


def source_confidence(source: str, dimension: str) -> str:
    """按维度与渠道返回置信度，用于主源选择。"""
    if dimension == "quote":
        if source.startswith("tushare."):
            return "high"
        if source == "tencent_finance":
            return "medium"
        return "low"
    if source.startswith("tushare."):
        return "high"
    if source == "baostock.kline":
        return "medium"
    if source.startswith("akshare."):
        return "medium"
    if source == "tencent_finance":
        return "medium"
    return "medium"


_SCALAR_KEYS = (
    "value", "close", "price", "pe", "pe_ttm", "pb", "roe", "eps",
    "net_mf_vol", "change_pct",
)
# 财务/资金流字段可为合法零值；close/price/pe 为 0 通常表示缺失
_ZERO_OK_KEYS = frozenset({"change_pct", "roe", "eps", "net_mf_vol"})


def _numeric_scalar(v: Any) -> float | None:
    """将 int/float 转为 float，排除 bool（bool 是 int 子类）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _scalar_key_usable(key: str, v: float) -> bool:
    return v != 0.0 or key in _ZERO_OK_KEYS


def _extract_scalar(data: Any) -> float | None:
    """从可能的格式（dict/list/scalar）中提取标量用于比较/融合。"""
    num = _numeric_scalar(data)
    if num is not None:
        return num
    if isinstance(data, dict):
        for key in _SCALAR_KEYS:
            v = _numeric_scalar(data.get(key))
            if v is not None and _scalar_key_usable(key, v):
                return v
    if isinstance(data, (list, tuple)) and len(data) == 1:
        return _extract_scalar(data[0])
    if isinstance(data, list) and data:
        last = data[-1]
        if isinstance(last, dict):
            for key in _SCALAR_KEYS:
                v = _numeric_scalar(last.get(key))
                if v is not None and _scalar_key_usable(key, v):
                    return v
    return None


def relative_diff_pct(max_v: float, min_v: float, avg: float) -> float | None:
    """相对差异比例 |max-min|/|avg|；avg 近零时返回 None。"""
    if abs(avg) < _SCALAR_EPSILON:
        return None
    return abs(max_v - min_v) / abs(avg)


# ---- 源结果（单个源的原始输出包装） ----

class SourceResult:
    """单个数据源的采集结果。"""

    __slots__ = (
        "source",       # str: 来源标识（如 "tushare.stock_basic"）
        "data",         # Any: 原始数据（dict 或 list[dict]）
        "dimension",    # str: 维度标识
        "query_params", # str: 调用参数字符串
        "confidence",   # str: "high" | "medium" | "low"
        "success",      # bool
        "latency_ms",   # float
        "error",        # str | None
        "fetched_at",   # str
    )

    def __init__(
        self,
        source: str,
        data: Any,
        dimension: str,
        query_params: str = "",
        confidence: str | None = None,
        latency_ms: float = 0,
        error: str | None = None,
        fetched_at: str | None = None,
    ):
        self.source = source
        self.data = data
        self.dimension = dimension
        self.query_params = query_params
        self.confidence = confidence if confidence is not None else source_confidence(source, dimension)
        self.success = data is not None and error is None
        self.latency_ms = latency_ms
        self.error = error
        from datetime import datetime, timezone
        self.fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "query_params": self.query_params,
            "confidence": self.confidence,
            "success": self.success,
            "fetched_at": self.fetched_at,
            "data_available": self.data is not None,
            "scalar_value": _extract_scalar(self.data),
            "data": self.data,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


# ---- 维度采集结果（维度下全部源合并后） ----

class DimensionResult:
    """一个维度的完整采集结果（合并所有源）。"""

    __slots__ = (
        "dimension",     # str
        "display",       # str
        "primary_data",  # Any: 最优源的数据
        "primary_source", # str: 最优源名称
        "all_sources",   # list[SourceResult]
        "multi_source",  # bool: 是否有多个源成功
        "status",        # str: "available" | "partial" | "missing"
        "_primary",      # SourceResult | None
        "cross_validation",  # CrossValidation | None
    )

    @staticmethod
    def _select_primary(all_sources: list[SourceResult]) -> SourceResult | None:
        conf_rank = {"high": 3, "medium": 2, "low": 1}
        primary: SourceResult | None = None
        for src in all_sources:
            if src.data is None:
                continue
            if primary is None:
                primary = src
            elif conf_rank.get(src.confidence, 0) > conf_rank.get(primary.confidence, 0):
                primary = src
        return primary

    def __init__(self, dimension: str, all_sources: list[SourceResult]):
        self.dimension = dimension
        self.display = DIMENSIONS.get(dimension, dimension)
        self.all_sources = all_sources

        primary = self._select_primary(all_sources)
        self._primary = primary

        if primary is not None:
            self.primary_data = primary.data
            self.primary_source = primary.source
            self.multi_source = sum(1 for s in all_sources if s.data is not None) > 1
            failures = sum(1 for s in all_sources if not s.success)
            self.status = "available" if failures == 0 else "partial"
        else:
            self.primary_data = None
            self.primary_source = "none"
            self.multi_source = False
            self.status = "missing"

        self.cross_validation = None
        if self.multi_source and len(self.all_sources) >= 2:
            self.cross_validation = _auto_cross_validate(self.dimension, self.all_sources)

    def to_legacy_dict(self) -> dict:
        """转为 collector.py 的旧版 dict 格式（兼容 render.py）。"""
        primary_meta = self._best_meta()
        all_src_dicts = [s.to_dict() for s in self.all_sources]
        primary_meta["all_sources"] = all_src_dicts
        primary_meta["multi_source"] = self.multi_source
        primary_meta["source_count"] = sum(1 for s in self.all_sources if s.data is not None)
        primary_meta["cross_validation"] = self.cross_validation.status if self.cross_validation else None
        primary_meta["cross_validation_detail"] = (
            self.cross_validation.detail if self.cross_validation else None
        )
        return {
            "dimension": self.dimension,
            "display": self.display,
            "data": self.primary_data,
            "status": self.status,
            "error": None if self.primary_data is not None else self._best_error_message(),
            "_meta": primary_meta,
        }

    def _best_error_message(self) -> str:
        """从所有失败源的错误中提取最可操作的消息（而非泛化提示）。

        优先选取已知的阻断消息（如东方财富封锁、连接拒绝等），
        其次选取第一个非空错误。"""
        # 注意：不包含 ProxyError — 本地代理配置错误也可能产生 ProxyError，
        # 不应自动归因于东方财富封锁。
        actionable_keywords = (
            "东方财富", "East Money", "eastmoney",
            "拒绝连接", "主动拒绝", "Connection aborted",
            "ConnectionError",
        )
        # 第一轮：找包含可操作关键词的错误
        for s in self.all_sources:
            if s.error and any(kw in s.error for kw in actionable_keywords):
                return s.error
        # 第二轮：取第一个有意义的错误
        for s in self.all_sources:
            if s.error:
                return s.error
        return "所有数据源均不可得"

    def _best_meta(self) -> dict:
        primary = self._primary
        if primary is not None:
            return {
                "source": primary.source,
                "query_params": primary.query_params,
                "confidence": primary.confidence,
                "fetched_at": primary.fetched_at,
                "success": True,
                "latency_ms": primary.latency_ms,
                "source_group": primary.source.split(".")[0] if "." in primary.source else primary.source,
                "fallback_chain": [],
            }
        return {
            "source": "none",
            "query_params": "",
            "confidence": "low",
            "fetched_at": "",
            "success": False,
            "latency_ms": 0,
            "source_group": "unknown",
            "fallback_chain": [],
        }


# ---- R-01: 自动交叉验证 ----

def _auto_cross_validate(dimension: str, sources: list[SourceResult]) -> CrossValidation | None:
    """自动检测多源数据差异。 >1% 差异 → divergence，否则 → convergence。

    只对数值型维度做检测。返回 None 表示不适合交叉验证（如非数值维度）。
    """
    values = []
    for s in sources:
        if s.data is None:
            continue
        v = _extract_scalar(s.data)
        if v is not None:
            values.append((s.source, v))
    if len(values) < 2:
        return None

    max_v, min_v = max(v for _, v in values), min(v for _, v in values)
    avg = sum(v for _, v in values) / len(values)
    diff_pct = relative_diff_pct(max_v, min_v, avg)
    if diff_pct is None:
        return None

    if diff_pct > CROSS_SOURCE_DIFF_THRESHOLD:
        return CrossValidation(
            status="divergence",
            code=f"{dimension}_diff",
            data_pair=f"{min_v:.2f} vs {max_v:.2f}",
            detail=f"跨源差异 {diff_pct * 100:.1f}%",
            reliability="引擎自动检测",
        )
    return CrossValidation(
        status="convergence",
        code=f"{dimension}_agree",
        data_pair=f"{avg:.2f}",
        detail=f"N={len(values)} 源一致",
        reliability="引擎自动检测",
    )


# ---- v0.1.3 动态投研内核数据结构 ----

CVStatus = Literal["convergence", "divergence", "gap"]

_CV_ICONS: dict[str, str] = {"convergence": "🟢", "divergence": "🟡", "gap": "🔴"}
_CV_LABELS: dict[str, str] = {"convergence": "印证", "divergence": "分歧", "gap": "缺口"}


@dataclass
class DriverFactor:
    """多因子驱动矩阵单行（模块 2）。"""
    category: str
    signal: str
    direction: str
    strength: str
    source: str

    def to_matrix_row(self) -> str:
        return (
            f"| {self.category} | {self.signal} | {self.direction} | "
            f"{self.strength} | {self.source} |"
        )


@dataclass
class CrossValidation:
    """交叉验证块（CV-1 … CV-7）。"""
    status: CVStatus
    code: str
    data_pair: str
    detail: str
    reliability: str

    def title(self) -> str:
        if self.code and self.data_pair:
            return f"{self.code} {self.data_pair}"
        return self.code or self.data_pair

    def to_markdown(self) -> str:
        icon = _CV_ICONS.get(self.status, "🔴")
        label = _CV_LABELS.get(self.status, self.status)
        return (
            f"{icon} **{label}（{self.title()}）** — {self.detail}\n"
            f"  可靠性: {self.reliability}"
        )


@dataclass
class ProbabilityStructure:
    """左/右概率结构（模块 6，LAW 16）。"""
    left_items: list[str] = field(default_factory=list)
    right_items: list[str] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)
    watch_nodes: list[str] = field(default_factory=list)
