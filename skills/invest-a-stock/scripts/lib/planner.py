"""采集计划生成器。输入 symbol + intent → 输出 AnalysisPlan。

参考 last30days planner.py 的 intent → scope 模式。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from .report_snapshot import REPORT_DEPENDENCIES, plan_digest

__all__ = ["ModuleConfig", "AnalysisPlan", "INTENT_PRESETS", "generate_plan",
           "REPORT_DEPENDENCIES"]

# 依赖合同的唯一定义在 `report_snapshot`（读取侧同用一份，避免两处漂移）；
# 此处仅 re-export 供 plan 载荷与调用方使用。


@dataclass
class ModuleConfig:
    """单个模块的采集配置。"""
    module_id: str
    priority: int       # 1-5, 1 最高
    weight: float       # 在最终综合中的权重
    depth: str          # "quick" | "normal" | "deep"
    min_sources: int    # 最少源数


@dataclass
class AnalysisPlan:
    """采集分析计划。"""
    symbol: str
    intent: str
    modules: list[ModuleConfig] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 报告模式是合同的一部分：brief 与 full 的必需字段不同，同一 symbol 的
    # 两种模式不应得到同一个 plan_hash（原实现硬编码 "full"，等于谎报）。
    mode: str = "full"

    def to_dict(self) -> dict:
        payload = {
            "symbol": self.symbol,
            "intent": self.intent,
            "modules": [
                {
                    "module_id": m.module_id,
                    "priority": m.priority,
                    "weight": m.weight,
                    "depth": m.depth,
                    "min_sources": m.min_sources,
                }
                for m in self.modules
            ],
            "notes": self.notes,
            "report_contract": {
                "version": 1,
                "mode": self.mode,
                "dependencies": REPORT_DEPENDENCIES,
                "source_policy": {"financials": "L2_parallel_dual_source",
                                  "valuation": "L2_parallel_dual_source",
                                  "quote": "L3_cascade_first_success",
                                  "kline": "L3_cascade_first_success"},
                "snapshot": "collect_report_ready_then_read_by_id",
                "freshness": "preserve_fetched_at_and_each_source_as_of",
            },
        }
        payload["plan_hash"] = plan_digest(payload)
        return payload

    def dimension_list(self) -> list[str]:
        """提取计划中的维度列表（按 priority 排序）。"""
        sorted_modules = sorted(self.modules, key=lambda m: m.priority)
        return [m.module_id for m in sorted_modules]


# ---- Intent Presets ----

def _m(module_id: str, priority: int, weight: float, depth: str = "normal",
       min_sources: int = 1) -> ModuleConfig:
    return ModuleConfig(module_id=module_id, priority=priority,
                        weight=weight, depth=depth, min_sources=min_sources)


INTENT_PRESETS: dict[str, AnalysisPlan] = {
    "deep_analysis": AnalysisPlan(
        symbol="",
        intent="deep_analysis",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("valuation", priority=1, weight=1.0, depth="deep", min_sources=2),
            _m("financials", priority=1, weight=1.0, depth="deep", min_sources=2),
            _m("segments", priority=2, weight=0.7, depth="normal", min_sources=1),  # A2: 分部构成
            _m("kline", priority=1, weight=0.8, depth="deep", min_sources=2),
            _m("basic_info", priority=2, weight=0.5, depth="quick", min_sources=1),
            _m("shareholders", priority=2, weight=0.6, depth="normal", min_sources=1),
            _m("northbound", priority=2, weight=0.6, depth="normal", min_sources=1),
            _m("research", priority=3, weight=0.4, depth="normal", min_sources=1),
        ],
    ),
    "quick_check": AnalysisPlan(
        symbol="",
        intent="quick_check",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("valuation", priority=1, weight=0.8, depth="quick", min_sources=1),
            _m("financials", priority=2, weight=0.5, depth="quick", min_sources=1),
            _m("kline", priority=2, weight=0.5, depth="quick", min_sources=1),
        ],
    ),
    "catalyst_monitor": AnalysisPlan(
        symbol="",
        intent="catalyst_monitor",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("research", priority=1, weight=0.8, depth="normal", min_sources=1),
            _m("northbound", priority=2, weight=0.6, depth="quick", min_sources=1),
            _m("kline", priority=2, weight=0.5, depth="quick", min_sources=1),
        ],
    ),
    "compare": AnalysisPlan(
        symbol="",
        intent="compare",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("valuation", priority=1, weight=1.0, depth="normal", min_sources=2),
            _m("financials", priority=1, weight=1.0, depth="normal", min_sources=1),
        ],
    ),
    "sentiment_deep": AnalysisPlan(
        symbol="",
        intent="sentiment_deep",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("research", priority=1, weight=0.9, depth="normal", min_sources=1),
            _m("basic_info", priority=1, weight=0.5, depth="quick", min_sources=1),
            _m("industry", priority=1, weight=0.7, depth="deep", min_sources=1),
            _m("kline", priority=2, weight=0.5, depth="normal", min_sources=1),
            _m("northbound", priority=2, weight=0.6, depth="quick", min_sources=1),
        ],
        notes=[
            "Load references/sentiment.md",
            "Template C (SentimentCard) = 卖方研报，非社媒舆情",
        ],
    ),
    "financials_deep": AnalysisPlan(
        symbol="",
        intent="financials_deep",
        modules=[
            _m("financials", priority=1, weight=1.0, depth="deep", min_sources=2),
            _m("segments", priority=2, weight=0.8, depth="normal", min_sources=1),  # A2: 分部构成
            _m("valuation", priority=1, weight=1.0, depth="deep", min_sources=2),
            _m("quote", priority=1, weight=0.6, depth="quick", min_sources=1),
            _m("holder_changes", priority=2, weight=0.7, depth="normal", min_sources=1),
            _m("basic_info", priority=2, weight=0.4, depth="quick", min_sources=1),
        ],
        notes=["Load references/financials.md"],
    ),
    "game_theory": AnalysisPlan(
        symbol="",
        intent="game_theory",
        modules=[
            _m("quote", priority=1, weight=1.0, depth="quick", min_sources=1),
            _m("shareholders", priority=1, weight=0.8, depth="normal", min_sources=1),
            _m("northbound", priority=1, weight=0.9, depth="normal", min_sources=1),
            _m("holder_changes", priority=1, weight=0.8, depth="normal", min_sources=1),
            _m("kline", priority=1, weight=0.6, depth="normal", min_sources=1),
            _m("basic_info", priority=2, weight=0.4, depth="quick", min_sources=1),
        ],
        notes=[
            "Load references/game-theory.md",
            "Collect: participant_behavior_scan (market_structure sealed before render)",
        ],
    ),
}


def generate_plan(symbol: str, intent: str = "deep_analysis",
                  mode: str = "full") -> AnalysisPlan:
    """根据意图生成采集计划。`mode` 记入报告合同（见 `AnalysisPlan.mode`）。"""
    preset = INTENT_PRESETS.get(intent)
    if preset is None:
        raise ValueError(f"未知意图 '{intent}'。可用: {list(INTENT_PRESETS.keys())}")
    plan = AnalysisPlan(
        symbol=symbol,
        intent=intent,
        modules=deepcopy(preset.modules),
        notes=list(preset.notes),
        mode=mode or "full",
    )
    return plan
