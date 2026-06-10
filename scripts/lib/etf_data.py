"""
ETF 数据模块（MVP 占位）。

MVP 提供：
- is_etf: 判断代码是否为 ETF
- get_etf_stub: 返回基金名称+跟踪指数+基础信息（若可从 akshare 获取）

Phase 6 实现：
- get_etf_info: 发行商、跟踪指数、费率、AUM
- get_premium_discount: 折溢价率
- get_tracking_error: 跟踪误差
- get_holdings: Top10 持仓占比
- get_share_change: 份额变动
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _build_meta(
    source: str | None,
    source_group: str | None,
    fallback_chain: list[str],
    attempted_sources: list[str],
    latency_ms: float,
    success: bool,
) -> dict[str, Any]:
    return {
        "source": source or "none",
        "source_group": source_group or "unknown",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_chain": fallback_chain,
        "confidence": "medium" if source else "low",
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "error_type": None if success else "empty",
    }


# ETF 代码前缀（上海 ETF）
SH_ETF_PREFIXES = ("51", "58")

# 深圳 ETF
SZ_ETF_PREFIXES = ("15", "16", "18")


def is_etf(code: str) -> bool:
    """判断股票代码是否为 ETF 基金。

    Args:
        code: 纯数字代码（如 "510300"）

    Returns:
        bool
    """
    code = str(code).strip().zfill(6)
    return (
        code.startswith(SH_ETF_PREFIXES)
        or code.startswith(SZ_ETF_PREFIXES)
    )


def get_etf_stub(code: str) -> dict[str, Any]:
    """获取 ETF 基本信息（MVP 占位）。

    Phase 6 将实现完整的 ETF 数据采集。

    Args:
        code: ETF 代码

    Returns:
        dict: {etf_code, name, tracking_index, fund_company, phase6_preview, ...}
    """
    code = str(code).strip().zfill(6)
    attempted_sources: list[str] = []
    result: dict[str, Any] = {"etf_code": code}
    source = ""
    source_group = ""
    start = datetime.now(timezone.utc)

    # 尝试从 akshare 获取基本信息
    try:
        import akshare as ak
        attempted_sources.append("akshare")

        # 尝试 ETF 实时行情获取名称
        try:
            etf_df = ak.fund_etf_spot_em()
            if not etf_df.empty:
                match = etf_df[etf_df["代码"] == code]
                if not match.empty:
                    row = match.iloc[0]
                    result["name"] = str(row.get("名称", ""))
                    result["price"] = float(row.get("最新价", 0) or 0)
                    result["change_pct"] = float(row.get("涨跌幅", 0) or 0)
                    result["volume"] = float(row.get("成交量", 0) or 0)
                    result["total_mv"] = float(row.get("总市值", 0) or 0)
                    source = "akshare.fund_etf_spot_em"
                    source_group = "eastmoney"
        except Exception:
            pass

        # 尝试获取跟踪指数
        if result.get("name"):
            try:
                info_df = ak.fund_etf_fund_info_em(fund=code, market="SH" if code.startswith("5") else "SZ")
                if info_df is not None and not info_df.empty:
                    info_row = info_df.iloc[0] if hasattr(info_df, 'iloc') else info_df
                    result["tracking_index"] = str(info_row.get("跟踪标的", info_row.get("跟踪指数", "")))
                    result["fund_company"] = str(info_row.get("基金管理人", ""))
            except Exception:
                pass

    except Exception as e:
        logger.debug("akshare ETF 信息获取失败: %s", e)

    latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

    # Phase 6 预告
    result["phase6_preview"] = (
        "完整 ETF 分析将在 Phase 6 实现，包括：折溢价率、跟踪误差、"
        "AUM 历史、Top10 持仓占比、份额变动趋势。"
    )

    result["_meta"] = _build_meta(
        source=source or None,
        source_group=source_group or None,
        fallback_chain=["akshare.fund_etf_spot_em", "akshare.fund_etf_fund_info_em"],
        attempted_sources=attempted_sources,
        latency_ms=latency,
        success=bool(source),
    )
    return result


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    code = sys.argv[1] if len(sys.argv) > 1 else "510300"
    print(f"is_etf('{code}'): {is_etf(code)}")

    stub = get_etf_stub(code)
    for k, v in stub.items():
        if k != "_meta":
            print(f"  {k}: {v}")
    print(f"  _meta: {stub.get('_meta')}")

    print(f"\nis_etf('600519'): {is_etf('600519')}")
