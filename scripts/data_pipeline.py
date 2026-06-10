"""
主编排引擎 data_pipeline.py

协调所有采集模块，执行 Step 0-1 的数据采集流程：

- build_collection_plan: 生成查询计划（Step 0）
- collect_all: 七维度并行采集（Step 1）
- collect_dimension: 单维度采集
- save_evidence: 保存原始数据到 evidence/raw.json
- sanitize_meta: _meta 字段安全消毒

依赖：所有 scripts/lib/*.py 模块就绪
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 安全消毒
# ------------------------------------------------------------------

SENSITIVE_KEY_PATTERNS = re.compile(
    r"(token|secret|key|password|authorization|api_key|access_key)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*")
URL_PATTERN = re.compile(r"https?://[^\s<>\"'{}|\\^`\[\]]+")

MAX_ERROR_DETAIL_LENGTH = 300


def sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """对 _meta 字段进行安全消毒。

    消毒内容：
    - 敏感 Key 名检测：字段名含 token/secret/key/password/authorization → [REDACTED]
    - Bearer Token 正则清洗：Bearer xxx → Bearer [REDACTED]
    - URL 脱敏：https://... → [REDACTED_URL]
    - 长度截断：error_detail 超过 300 字符自动截断

    Args:
        meta: 原始 _meta dict

    Returns:
        消毒后的 _meta dict（新对象，不修改原对象）
    """
    sanitized = {}
    for key, value in meta.items():
        # 敏感 Key 名检测
        if isinstance(key, str) and SENSITIVE_KEY_PATTERNS.search(key):
            sanitized[key] = "[REDACTED]"
            continue

        if isinstance(value, str):
            # Bearer Token 清洗
            value = BEARER_PATTERN.sub("Bearer [REDACTED]", value)
            # URL 脱敏
            value = URL_PATTERN.sub("[REDACTED_URL]", value)
            # 长度截断
            if key == "error_detail" and len(value) > MAX_ERROR_DETAIL_LENGTH:
                value = value[:MAX_ERROR_DETAIL_LENGTH] + "...[TRUNCATED]"
            sanitized[key] = value
        elif isinstance(value, dict):
            sanitized[key] = sanitize_meta(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_meta(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            sanitized[key] = value

    return sanitized


# ------------------------------------------------------------------
# 查询计划
# ------------------------------------------------------------------

# 七+1维度定义（与 config/dimension_baselines.yaml 对齐）
# web_research 为 v0.2 新增维度，覆盖行业/舆情/机构的 Web 搜索补充
DIMENSION_MAP = {
    "basic_info": {
        "index": 1,
        "display": "官方基本信息",
        "module": "a_share_data.get_stock_info",
    },
    "fundamental": {
        "index": 2,
        "display": "财务报告",
        "module": "a_share_data.get_financials",
    },
    "industry": {
        "index": 3,
        "display": "行业产业链",
        "module": "industry_chain.get_industry_context",
    },
    "valuation": {
        "index": 4,
        "display": "估值",
        "module": "a_share_data.get_daily_kline + realtime_quote.get_realtime_quote",
    },
    "technical": {
        "index": 4.5,
        "display": "市场状态（技术分析）",
        "module": "a_share_data.get_daily_kline",
    },
    "institutional": {
        "index": 5,
        "display": "机构分析师",
        "module": "a_share_data.get_shareholders + fund_flow.get_northbound_flow",
    },
    "sentiment": {
        "index": 6,
        "display": "情绪舆情",
        "module": "sentiment_data.get_eastmoney_sentiment + news_search.search_stock_news",
    },
    "web_research": {
        "index": 6.5,
        "display": "Web 搜索（产业链/舆情/机构/催化剂）",
        "module": "web_research.collect_web_research",
        "source_type": "web",  # api | web | analyst — 区分数据源类型
    },
    "macro": {
        "index": 7,
        "display": "宏观政策",
        "module": "global_macro.get_macro_snapshot + global_macro.get_china_macro_snapshot",
    },
}

# 默认采集维度（含 web_research）
DEFAULT_DIMS = [
    "basic_info",
    "fundamental",
    "industry",
    "valuation",
    "institutional",
    "sentiment",
    "web_research",
    "macro",
]

# 采集优先级（框架建议顺序）
# web_research 优先级在 sentiment 之后——它为舆情和产业链提供补充信息
COLLECTION_PRIORITY = [
    "fundamental",     # ★★★★★ 核心
    "basic_info",      # ★★★☆☆ 基础
    "industry",        # ★★★★☆ 行业
    "valuation",       # ★★★☆☆ 估值
    "technical",       # ★★★☆☆ 技术
    "institutional",   # ★★★★☆ 机构
    "sentiment",       # ★★★☆☆ 情绪
    "web_research",    # ★★★☆☆ Web搜索（补充产业链/舆情/机构）
    "macro",           # ★★★☆☆ 宏观
]


def build_collection_plan(
    symbol: str,
    asset_type: str | None = None,
    dims: list[str] | None = None,
    name: str = "",
) -> dict[str, Any]:
    """生成采集计划（Step 0 输出）。

    Args:
        symbol: 股票/ETF 代码
        asset_type: "stock" | "hk" | "etf"（None 则自动识别）
        dims: 指定维度列表（None 则全部七个）
        name: 公司名（用于 Web 搜索查询构建）

    Returns:
        dict: {
            symbol, asset_type, market,
            dims_enabled, estimated_tokens,
            modules_to_call, ...
        }
    """
    from scripts.lib.etf_data import is_etf

    symbol = str(symbol).strip()

    # 自动识别 asset_type
    if asset_type is None:
        if is_etf(symbol):
            asset_type = "etf"
        elif len(symbol) <= 5 and not symbol.startswith(("6", "0", "3", "5")):
            asset_type = "hk"
        else:
            asset_type = "stock"

    # 市场
    if asset_type == "hk":
        market = "HK"
    elif asset_type == "etf":
        market = "A-share(ETF)"
    else:
        market = "A-share"

    # 维度
    dims = dims or DEFAULT_DIMS
    if asset_type == "etf":
        dims = ["basic_info"]  # MVP ETF 仅基本信息
    elif asset_type == "hk":
        dims = [d for d in dims if d != "institutional"]  # 港股机构数据有限

    # 模块
    modules_to_call = []
    for dim in dims:
        if dim in DIMENSION_MAP:
            modules_to_call.append({
                "dimension": dim,
                "display": DIMENSION_MAP[dim]["display"],
                "module": DIMENSION_MAP[dim]["module"],
            })

    # Token 估估
    estimated_tokens = len(dims) * 800 + 2000

    # 环境
    env_status = {
        "TUSHARE_TOKEN": bool(os.environ.get("TUSHARE_TOKEN")),
        "FRED_API_KEY": bool(os.environ.get("FRED_API_KEY")),
        "TAVILY_API_KEY": bool(os.environ.get("TAVILY_API_KEY")),
    }

    return {
        "symbol": symbol,
        "asset_type": asset_type,
        "market": market,
        "dims_enabled": dims,
        "dim_count": len(dims),
        "modules_to_call": modules_to_call,
        "estimated_tokens": estimated_tokens,
        "env_status": env_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------
# 单维度采集
# ------------------------------------------------------------------

def collect_dimension(dim: str, symbol: str, context: dict | None = None) -> dict[str, Any]:
    """采集单个维度的数据。

    Args:
        dim: 维度名（basic_info, fundamental, industry, ...）
        symbol: 股票代码
        context: 上下文（如 asset_type, market 等）

    Returns:
        dict: {dimension, data: {...}, _meta, status}
    """
    context = context or {}
    asset_type = context.get("asset_type", "stock")
    start = datetime.now(timezone.utc)

    result: dict[str, Any] = {
        "dimension": dim,
        "display": DIMENSION_MAP.get(dim, {}).get("display", dim),
    }

    try:
        if asset_type == "hk":
            result.update(_collect_hk_dimension(dim, symbol))
        elif asset_type == "etf":
            result.update(_collect_etf_dimension(dim, symbol))
        else:
            result.update(_collect_stock_dimension(dim, symbol))
    except Exception as e:
        logger.error("维度 %s 采集异常: %s", dim, e, exc_info=True)
        result["data"] = None
        result["error"] = str(e)
        result["_meta"] = sanitize_meta({
            "source": "none",
            "source_group": "unknown",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fallback_chain": [],
            "confidence": "low",
            "latency_ms": (datetime.now(timezone.utc) - start).total_seconds() * 1000,
            "success": False,
            "error_type": "parse",
            "error_detail": str(e),
        })

    # 确保 _meta 存在
    if "_meta" not in result:
        result["_meta"] = {
            "source": "none",
            "source_group": "unknown",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fallback_chain": [],
            "confidence": "low",
            "latency_ms": (datetime.now(timezone.utc) - start).total_seconds() * 1000,
            "success": False,
            "error_type": "empty",
        }

    # 安全消毒
    result["_meta"] = sanitize_meta(result["_meta"])

    # 状态
    if result.get("error"):
        result["status"] = "degraded"
    elif result.get("data") is not None:
        result["status"] = "available"
    else:
        result["status"] = "degraded"

    return result


def _collect_stock_dimension(dim: str, symbol: str) -> dict[str, Any]:
    """A 股各维度采集。"""
    if dim == "basic_info":
        from scripts.lib.a_share_data import get_stock_info
        info = get_stock_info(symbol)
        return {"data": {k: v for k, v in info.items() if k != "_meta"}, "_meta": info.get("_meta", {})}

    elif dim == "fundamental":
        from scripts.lib.a_share_data import get_financials, get_governance_signals
        fin = get_financials(symbol)
        gov = get_governance_signals(symbol)
        fin_data = {k: v for k, v in fin.items() if k not in ("_meta", "_tmp_source")}
        gov_data = {k: v for k, v in gov.items() if k != "_meta"}
        return {
            "data": {"financials": fin_data, "governance": gov_data},
            "_meta": fin.get("_meta", {}),
        }

    elif dim == "industry":
        from scripts.lib.industry_chain import get_industry_context
        ctx = get_industry_context(symbol)
        return {"data": {k: v for k, v in ctx.items() if k != "_meta"}, "_meta": ctx.get("_meta", {})}

    elif dim == "valuation":
        from scripts.lib.realtime_quote import get_realtime_quote
        from scripts.lib.a_share_data import get_daily_kline
        quote = get_realtime_quote(symbol)
        kline = get_daily_kline(symbol)
        quote_data = {k: v for k, v in quote.items() if k not in ("_meta",)}
        return {
            "data": {"quote": quote_data, "kline_summary": f"{len(kline.get('data', []))} bars"},
            "_meta": quote.get("_meta", kline.get("_meta", {})),
        }

    elif dim == "technical":
        from scripts.lib.a_share_data import get_daily_kline
        kline = get_daily_kline(symbol)
        df = kline.get("data")
        summary = _compute_technical_summary(df)
        return {
            "data": {"technical_summary": summary},
            "_meta": kline.get("_meta", {}),
            "warning": "技术指标仅用于理解市场状态，不构成交易建议",
        }

    elif dim == "institutional":
        from scripts.lib.a_share_data import get_shareholders, get_institutional_research
        from scripts.lib.fund_flow import get_northbound_flow

        sh = get_shareholders(symbol)
        inst = get_institutional_research(symbol)
        nb = get_northbound_flow(symbol)

        sh_data = {k: v for k, v in sh.items() if k != "_meta"}
        inst_data = {k: v for k, v in inst.items() if k != "_meta"}
        nb_data = {k: v for k, v in nb.items() if k != "_meta"}

        return {
            "data": {
                "shareholders": sh_data,
                "institutional_research": inst_data,
                "northbound_flow": nb_data,
            },
            "_meta": sh.get("_meta", nb.get("_meta", {})),
        }

    elif dim == "sentiment":
        from scripts.lib.sentiment_data import get_eastmoney_sentiment
        from scripts.lib.news_search import search_stock_news

        sent = get_eastmoney_sentiment(symbol)
        news = search_stock_news(symbol)

        sent_data = {k: v for k, v in sent.items() if k != "_meta"}
        news_data = {k: v for k, v in news.items() if k != "_meta"}

        return {
            "data": {"sentiment": sent_data, "news": news_data},
            "_meta": sent.get("_meta", news.get("_meta", {})),
        }

    elif dim == "web_research":
        from scripts.lib.web_research import collect_web_research

        # 传入公司名（从 basic_info 或 context 中取）
        company_name = context.get("name", "")
        user_topic = context.get("topic", None)

        result = collect_web_research(
            symbol,
            name=company_name,
            topic=user_topic,
            dimensions=["industry", "sentiment", "institutional", "catalysts"],
        )
        # Web 搜索维度返回查询模板 + 可信度分级框架
        # 实际搜索调用在 LLM 分析阶段（Step 2）由 Skill 的 WebSearch tool 执行
        return {
            "data": result.get("data", {}),
            "_meta": result.get("_meta", {}),
            "status": result.get("status", "available"),
        }

    elif dim == "macro":
        from scripts.lib.global_macro import get_macro_snapshot, get_china_macro_snapshot, get_fx_rates
        from scripts.lib.commodity_data import get_gold_price, get_crude_price

        macro = get_macro_snapshot()
        china = get_china_macro_snapshot()
        fx = get_fx_rates()
        gold = get_gold_price()
        crude = get_crude_price()

        return {
            "data": {
                "global_macro": {k: v for k, v in macro.items() if k != "_meta"},
                "china_macro": {k: v for k, v in china.items() if k != "_meta"},
                "fx_rates": {k: v for k, v in fx.items() if k != "_meta"},
                "gold": {k: v for k, v in gold.items() if k != "_meta"},
                "crude": {k: v for k, v in crude.items() if k != "_meta"},
            },
            "_meta": macro.get("_meta", fx.get("_meta", {})),
        }

    else:
        return {"data": None, "error": f"未知维度: {dim}", "_meta": {}}


def _collect_hk_dimension(dim: str, symbol: str) -> dict[str, Any]:
    """港股各维度采集。"""
    from scripts.lib.hk_share_data import (
        get_hk_stock_info,
        get_hk_financials,
        get_hk_governance_signals,
        get_hk_daily_kline,
    )

    if dim == "basic_info":
        info = get_hk_stock_info(symbol)
        return {"data": {k: v for k, v in info.items() if k != "_meta"}, "_meta": info.get("_meta", {})}

    elif dim == "fundamental":
        fin = get_hk_financials(symbol)
        return {"data": {k: v for k, v in fin.items() if k != "_meta"}, "_meta": fin.get("_meta", {})}

    elif dim in ("valuation", "technical"):
        kline = get_hk_daily_kline(symbol)
        data = {k: v for k, v in kline.items() if k not in ("_meta", "data")}
        if dim == "technical":
            summary = _compute_technical_summary(kline.get("data"))
            data["technical_summary"] = summary
            data["warning"] = "技术指标仅用于理解市场状态，不构成交易建议"
        return {"data": data, "_meta": kline.get("_meta", {})}

    elif dim == "industry":
        from scripts.lib.industry_chain import get_industry_context
        ctx = get_industry_context(symbol)
        return {"data": {k: v for k, v in ctx.items() if k != "_meta"}, "_meta": ctx.get("_meta", {})}

    elif dim == "institutional":
        gov = get_hk_governance_signals(symbol)
        return {
            "data": {
                "governance": {k: v for k, v in gov.items() if k != "_meta"},
                "note": "港股机构持仓数据需通过港交所披露易获取（Phase 6 扩展）",
            },
            "_meta": gov.get("_meta", {}),
        }

    elif dim == "sentiment":
        from scripts.lib.news_search import search_stock_news
        news = search_stock_news(symbol)
        return {"data": {"news": {k: v for k, v in news.items() if k != "_meta"}}, "_meta": news.get("_meta", {})}

    elif dim == "macro":
        # 港股宏观与 A 股相同
        return _collect_stock_dimension("macro", symbol)

    return {"data": None, "error": f"港股不支持维度: {dim}", "_meta": {}}


def _collect_etf_dimension(dim: str, symbol: str) -> dict[str, Any]:
    """ETF 各维度采集（MVP 仅 basic_info）。"""
    from scripts.lib.etf_data import get_etf_stub

    if dim == "basic_info":
        stub = get_etf_stub(symbol)
        return {"data": {k: v for k, v in stub.items() if k != "_meta"}, "_meta": stub.get("_meta", {})}

    return {
        "data": None,
        "note": "ETF 此维度分析将在 Phase 6 实现",
        "_meta": {},
    }


def _compute_technical_summary(df: Any) -> dict[str, Any]:
    """从 K 线 DataFrame 计算技术摘要（仅状态描述，不生成交易信号）。"""
    if df is None:
        return {"error": "无K线数据"}
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame) and not df.empty:
            # 收盘价列名处理
            close_col = None
            for c in ["close", "Close", "收盘", "收盘价"]:
                if c in df.columns:
                    close_col = c
                    break
            if close_col is None:
                return {"error": "K线数据中未找到收盘价列", "columns": list(df.columns)}

            close = df[close_col].astype(float)
            latest_close = float(close.iloc[-1])

            # 均线
            ma5 = float(close.tail(5).mean()) if len(close) >= 5 else None
            ma10 = float(close.tail(10).mean()) if len(close) >= 10 else None
            ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
            ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None

            # 均线位置
            position = "above_all" if ma60 and latest_close > ma60 else (
                "below_ma60" if ma60 else "unknown"
            )

            # MACD
            macd = None
            if len(close) >= 26:
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                dif = ema12 - ema26
                dea = dif.ewm(span=9, adjust=False).mean()
                macd_bar = 2 * (dif - dea)
                macd = {
                    "DIF": round(float(dif.iloc[-1]), 4),
                    "DEA": round(float(dea.iloc[-1]), 4),
                    "MACD_bar": round(float(macd_bar.iloc[-1]), 4),
                    "DIF_above_zero": float(dif.iloc[-1]) > 0,
                    "DIF_above_DEA": float(dif.iloc[-1]) > float(dea.iloc[-1]),
                }

            return {
                "latest_close": latest_close,
                "MA5": round(ma5, 2) if ma5 else None,
                "MA10": round(ma10, 2) if ma10 else None,
                "MA20": round(ma20, 2) if ma20 else None,
                "MA60": round(ma60, 2) if ma60 else None,
                "price_vs_MA60": position,
                "MACD": macd,
                "data_points": len(close),
                "note": "技术指标仅描述市场状态，不构成交易建议",
            }
    except Exception as e:
        logger.warning("技术摘要计算失败: %s", e)
        return {"error": str(e)}

    return {"error": "无有效K线数据"}


# ------------------------------------------------------------------
# collect_all — 主编排入口
# ------------------------------------------------------------------

def collect_all(
    symbol: str,
    *,
    dims: list[str] | None = None,
    with_macro: bool = False,
    name: str = "",
    topic: str | None = None,
    use_cache: bool = False,
    max_workers: int = 8,
) -> dict[str, Any]:
    """七维度并行采集主编排。

    Args:
        symbol: 股票代码
        dims: 维度列表（None 则全七维+web）
        with_macro: 是否强制包含宏观
        name: 公司名（用于 Web 搜索查询构建）
        topic: 用户额外指定的研究主题
        use_cache: 是否尝试从 evidence/ 加载缓存
        max_workers: 并行线程数

    Returns:
        dict: {
            symbol, asset_type, fetched_at,
            dimensions: [{dimension, display, data, _meta, status}, ...],
            collection_summary: {total, available, degraded, missing}
        }
    """
    from scripts.lib.data_utils import load_cached, normalize_code
    from scripts.lib.etf_data import is_etf

    symbol = normalize_code(symbol)
    asset_type = "etf" if is_etf(symbol) else (
        "hk" if (len(symbol) <= 5 and not symbol.startswith(("6","0","3","5"))) else "stock"
    )

    # 尝试缓存
    if use_cache:
        cached = load_cached(symbol)
        if cached:
            logger.info("从缓存加载 %s", symbol)
            return cached

    # 确定维度
    if dims is None:
        dims = DEFAULT_DIMS.copy()
    if with_macro and "macro" not in dims:
        dims = list(dims) + ["macro"]

    if asset_type == "etf":
        dims = ["basic_info"]
    elif asset_type == "hk":
        dims = [d for d in dims if d not in ("institutional", "web_research")]

    # 按优先级排序
    priority_order = {d: i for i, d in enumerate(COLLECTION_PRIORITY)}
    dims = sorted(dims, key=lambda d: priority_order.get(d, 99))

    context = {
        "asset_type": asset_type,
        "symbol": symbol,
        "name": name,
        "topic": topic,
    }
    start = datetime.now(timezone.utc)

    # 并行采集
    dimensions = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(dims))) as pool:
        futures = {
            pool.submit(collect_dimension, dim, symbol, context): dim
            for dim in dims
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                dimensions.append(result)
            except Exception as e:
                dim = futures[future]
                logger.error("维度 %s 采集崩溃: %s", dim, e)
                dimensions.append({
                    "dimension": dim,
                    "display": DIMENSION_MAP.get(dim, {}).get("display", dim),
                    "data": None,
                    "error": str(e),
                    "status": "missing",
                    "_meta": sanitize_meta({
                        "source": "none",
                        "source_group": "unknown",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "fallback_chain": [],
                        "confidence": "low",
                        "latency_ms": (datetime.now(timezone.utc) - start).total_seconds() * 1000,
                        "success": False,
                        "error_type": "parse",
                        "error_detail": str(e),
                    }),
                })

    # 汇总
    available = sum(1 for d in dimensions if d.get("status") == "available")
    degraded = sum(1 for d in dimensions if d.get("status") == "degraded")
    missing = sum(1 for d in dimensions if d.get("status") == "missing")
    total = len(dimensions)

    latency = (datetime.now(timezone.utc) - start).total_seconds()

    final_result = {
        "symbol": symbol,
        "asset_type": asset_type,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions,
        "collection_summary": {
            "total_dimensions": total,
            "available": available,
            "degraded": degraded,
            "missing": missing,
            "latency_seconds": round(latency, 1),
        },
    }

    # 保存缓存
    if use_cache:
        try:
            from scripts.lib.data_utils import save_cache
            save_cache(final_result, symbol)
        except Exception:
            pass

    return final_result


# ------------------------------------------------------------------
# save_evidence
# ------------------------------------------------------------------

def save_evidence(result: dict[str, Any], out_dir: str = "evidence") -> str:
    """将采集结果保存为 evidence/raw.json。

    Args:
        result: collect_all 的返回值
        out_dir: 输出目录

    Returns:
        输出文件路径
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    symbol = result.get("symbol", "unknown")
    out_file = out_path / f"raw_{symbol}.json"

    # 消毒后写入
    sanitized = _json_safe_sanitize(result)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2, default=str)

    logger.info("Evidence saved: %s", out_file)
    return str(out_file)


def _json_safe_sanitize(obj: Any) -> Any:
    """递归处理对象使其 JSON 可序列化，同时消毒敏感信息。"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            # 敏感 Key 检测
            if isinstance(k, str) and SENSITIVE_KEY_PATTERNS.search(k):
                result[k] = "[REDACTED]"
            else:
                result[k] = _json_safe_sanitize(v)
        return result
    elif isinstance(obj, list):
        return [_json_safe_sanitize(item) for item in obj]
    elif isinstance(obj, str):
        s = BEARER_PATTERN.sub("Bearer [REDACTED]", obj)
        s = URL_PATTERN.sub("[REDACTED_URL]", s)
        return s
    elif hasattr(obj, "isoformat"):
        return obj.isoformat()
    elif hasattr(obj, "item"):  # numpy types
        return obj.item()
    return obj


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m scripts.data_pipeline --test 600519 [--render]")
        sys.exit(0)

    if sys.argv[1] == "--test" and len(sys.argv) > 2:
        symbol = sys.argv[2]
        do_render = "--render" in sys.argv

        print(f"=== 数据采集计划: {symbol} ===\n")
        plan = build_collection_plan(symbol)
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))

        print(f"\n=== 开始采集: {symbol} ===\n")
        t0 = time.time()
        result = collect_all(symbol)
        elapsed = time.time() - t0

        print(f"采集完成，耗时 {elapsed:.1f}s")
        summary = result["collection_summary"]
        print(f"维度: {summary['total_dimensions']} total, "
              f"{summary['available']} available, "
              f"{summary['degraded']} degraded, "
              f"{summary['missing']} missing")

        for dim in result["dimensions"]:
            status_icon = {"available": "✅", "degraded": "⚠️", "missing": "❌"}.get(dim.get("status", ""), "?")
            meta = dim.get("_meta", {})
            source = meta.get("source", "none")
            print(f"  {status_icon} {dim['display']} ({dim['dimension']}) — 来源: {source}")

        # 保存证据
        evidence_path = save_evidence(result)
        print(f"\n证据已保存: {evidence_path}")

        # 渲染（如果 --render）
        if do_render:
            print("\n=== 渲染报告 ===\n")
            try:
                from scripts.lib.report_render import render_report
                report = render_report(
                    dimensions=result["dimensions"],
                    metadata={
                        "symbol": symbol,
                        "asset_type": result["asset_type"],
                        "fetched_at": result["fetched_at"],
                        "sources": [
                            d.get("_meta", {}).get("source", "unknown")
                            for d in result["dimensions"]
                        ],
                    },
                )
                print(report[:2000])
                if len(report) > 2000:
                    print("\n... (报告截断，完整输出请查看 --render 全量)")
            except ImportError:
                print("report_render 模块尚未实现（Phase 2）")
    else:
        print("Usage: python -m scripts.data_pipeline --test 600519 [--render]")
