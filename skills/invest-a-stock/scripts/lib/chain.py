"""产业链数据采集模块（层 3+4: 上下游产业链 + 全球同业对标）。

提供行业上下游利润率数据和全球同业对标数据。
当前版本为基础框架，后续迭代扩展具体产业链模板。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_CHAIN_MAP: dict[str, dict[str, Any]] = {
    "新能源汽车": {"position": "中游制造", "upstream": ["锂", "钴", "硅料"], "downstream": ["电站", "电动车"]},
    "电气": {"position": "中游制造", "upstream": ["铜", "铝", "稀土"], "downstream": ["电网", "新能源"]},
    "汽车": {"position": "中游制造", "upstream": ["钢铁", "橡胶", "芯片"], "downstream": ["经销商", "消费者"]},
    "医药": {"position": "中游制造", "upstream": ["原料药", "化工"], "downstream": ["医院", "药店"]},
    "白酒": {"position": "下游消费", "upstream": ["粮食", "包装"], "downstream": ["经销商", "消费者"]},
    "银行": {"position": "金融", "upstream": ["存款"], "downstream": ["贷款企业", "个人"]},
    "房地产": {"position": "中游", "upstream": ["土地", "建材"], "downstream": ["购房者", "物业"]},
    "半导体": {"position": "中游制造", "upstream": ["硅片", "设备"], "downstream": ["电子消费品", "汽车"]},
    "新能源": {"position": "中游制造", "upstream": ["锂", "钴", "硅料"], "downstream": ["电站", "电动车"]},
    "化工": {"position": "上游原料", "upstream": ["原油", "煤炭"], "downstream": ["制药", "塑料"]},
    "钢铁": {"position": "上游原料", "upstream": ["铁矿石", "焦煤"], "downstream": ["建筑", "汽车"]},
    "食品": {"position": "下游消费", "upstream": ["农产品", "包装"], "downstream": ["商超", "消费者"]},
    "计算机": {"position": "中游", "upstream": ["芯片", "软件"], "downstream": ["企业", "政府"]},
    "通信": {"position": "中游", "upstream": ["芯片", "光纤"], "downstream": ["运营商", "消费者"]},
    "电子": {"position": "中游制造", "upstream": ["硅", "稀土"], "downstream": ["手机", "汽车"]},
}

# P1-1: 行业 → 期货品种映射（复用 _match_chain_keyword 做长度降序匹配）
_INDUSTRY_FUTURES_MAP: dict[str, list[tuple[str, str]]] = {
    "化工":       [("原油", "SC"), ("纯碱", "SA"), ("甲醇", "MA"), ("PTA", "TA"), ("聚丙烯", "PP")],
    "玻璃":       [("玻璃", "FG"), ("纯碱", "SA")],
    "玻纤":       [("玻璃", "FG"), ("纯碱", "SA")],
    "钢铁":       [("铁矿石", "I"), ("螺纹钢", "RB"), ("热卷", "HC"), ("焦炭", "J")],
    "有色金属":   [("铜", "CU"), ("铝", "AL"), ("锌", "ZN"), ("镍", "NI"), ("锡", "SN")],
    "煤炭":       [("焦煤", "JM"), ("焦炭", "J"), ("动力煤", "ZC")],
    "石油石化":   [("原油", "SC"), ("燃料油", "FU"), ("沥青", "BU")],
    "新能源汽车": [("碳酸锂", "LC"), ("工业硅", "SI")],
    "造纸":       [("纸浆", "SP")],
}

_GLOBAL_PEER_MAP: dict[str, list[str]] = {
    "汽车": ["Tesla (TSLA)", "Toyota (TM)", "Volkswagen (VOW3.DE)"],
    "半导体": ["NVIDIA (NVDA)", "TSMC (TSM)", "Intel (INTC)"],
    "新能源": ["Tesla (TSLA)", "NextEra Energy (NEE)", "Vestas (VWS.CO)"],
    "银行": ["JPMorgan (JPM)", "Bank of America (BAC)", "HSBC (HSBA.L)"],
    "医药": ["Pfizer (PFE)", "Johnson & Johnson (JNJ)", "Roche (ROG.SW)"],
}


def _match_chain_keyword(industry: str, mapping: dict) -> Any:
    """按关键词长度降序匹配，避免「新能源汽车」误命中「汽车」。"""
    for keyword in sorted(mapping.keys(), key=len, reverse=True):
        if keyword in industry:
            return mapping[keyword]
    return None


def get_futures_for_industry(industry: str) -> list[tuple[str, str]]:
    """根据申万行业名称返回应追踪的期货品种列表。

    复用 _match_chain_keyword() 的长度降序匹配，避免"新能源汽车"
    被"新能源"或"汽车"的映射误命中。
    """
    result = _match_chain_keyword(industry, _INDUSTRY_FUTURES_MAP)
    return result or []


def collect_chain_context(
    symbol: str,
    *,
    industry: str = "",
    basic_data: dict | None = None,
) -> dict[str, Any]:
    """采集产业链上下文数据。

    若已采集 basic_info，可通过 industry/basic_data 传入以避免重复请求。
    """
    if not industry:
        if isinstance(basic_data, dict):
            industry = basic_data.get("industry", "") or basic_data.get("行业", "")
        if not industry:
            from .collector import collect_basic_info
            try:
                basic = collect_basic_info(symbol)
            except Exception as exc:
                logger.debug("chain: basic_info failed for %s: %s", symbol, exc)
                return {"status": "missing", "error": str(exc)}
            data = basic.get("data", {})
            if isinstance(data, dict):
                industry = data.get("industry", "") or data.get("行业", "")

    chain_info = _match_chain_keyword(industry, _CHAIN_MAP)

    result: dict[str, Any] = {
        "status": "ok",
        "industry": industry,
        "chain_position": chain_info["position"] if chain_info else None,
        "upstream": chain_info["upstream"] if chain_info else [],
        "downstream": chain_info["downstream"] if chain_info else [],
        "global_peers": [],
    }

    peers = _match_chain_keyword(industry, _GLOBAL_PEER_MAP)
    if peers:
        result["global_peers"] = peers

    return result
