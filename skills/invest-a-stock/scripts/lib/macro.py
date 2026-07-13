"""宏观数据采集模块（层 5: 宏观层）。

自动采集 PMI/CPI/PPI/LPR 等中国宏观指标。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def collect_macro_context(symbol: str = "") -> dict[str, Any]:
    """采集宏观背景数据。返回 {indicator_name: {value, source, signal}} 映射。

    当前支持的指标：
    - PMI: 制造业采购经理指数 (akshare macro_china_pmi)
    - CPI: 居民消费价格指数 (akshare macro_china_cpi)
    - PPI: 工业生产者出厂价格指数 (akshare macro_china_ppi)
    - LPR: 贷款市场报价利率 (akshare macro_china_lpr)

    每个指标采集失败时独立降级，不阻塞其他指标。
    """
    from . import env

    context: dict[str, Any] = {
        "pmi": None,
        "cpi": None,
        "ppi": None,
        "lpr": None,
    }

    if not env.is_akshare_available():
        return {
            "status": "unavailable",
            "error": "akshare 不可用",
            "indicators": context,
        }

    failures: list[str] = []

    # PMI
    try:
        from .proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.macro_china_pmi()
            if df is not None and not df.empty:
                row = df.iloc[-1]
                pmi_val = None
                for col in ["制造业-指数", "制造业"]:
                    v = row.get(col)
                    if v is not None:
                        pmi_val = float(v)
                        break
                if pmi_val is None and len(row) > 1:
                    pmi_val = float(row.iloc[-1])
                if pmi_val is not None:
                    context["pmi"] = {
                        "value": round(pmi_val, 2),
                        "signal": "扩张" if pmi_val >= 50 else "收缩",
                        "source": "akshare.macro_china_pmi",
                    }
        if context["pmi"] is None:
            failures.append("PMI")
    except Exception as exc:
        logger.warning("PMI fetch failed: %s", exc)
        failures.append("PMI")

    # CPI
    try:
        from .proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.macro_china_cpi()
            if df is not None and not df.empty:
                row = df.iloc[-1]
                cpi_val = None
                for col in ["全国-当月", "全国"]:
                    v = row.get(col)
                    if v is not None:
                        cpi_val = float(v)
                        break
                if cpi_val is not None:
                    context["cpi"] = {
                        "value": round(cpi_val, 2),
                        "signal": "通胀" if cpi_val > 3 else ("通缩" if cpi_val < 0 else "温和"),
                        "source": "akshare.macro_china_cpi",
                    }
        if context["cpi"] is None:
            failures.append("CPI")
    except Exception as exc:
        logger.warning("CPI fetch failed: %s", exc)
        failures.append("CPI")

    # PPI
    try:
        from .proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.macro_china_ppi()
            if df is not None and not df.empty:
                row = df.iloc[-1]
                ppi_val = None
                for col in ["全国-当月", "全国"]:
                    v = row.get(col)
                    if v is not None:
                        ppi_val = float(v)
                        break
                if ppi_val is not None:
                    context["ppi"] = {
                        "value": round(ppi_val, 2),
                        "signal": "上行" if ppi_val > 0 else "下行",
                        "source": "akshare.macro_china_ppi",
                    }
        if context["ppi"] is None:
            failures.append("PPI")
    except Exception as exc:
        logger.warning("PPI fetch failed: %s", exc)
        failures.append("PPI")

    # LPR
    try:
        from .proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.macro_china_lpr()
            if df is not None and not df.empty:
                row = df.iloc[-1]
                lpr_1y = None
                for col in ["1年期", "LPR1Y"]:
                    v = row.get(col)
                    if v is not None:
                        lpr_1y = float(v)
                        break
                if lpr_1y is not None:
                    context["lpr"] = {
                        "value": round(lpr_1y, 2),
                        "signal": "偏宽松" if lpr_1y <= 3.5 else ("中性" if lpr_1y <= 4.0 else "偏紧"),
                        "source": "akshare.macro_china_lpr",
                    }
        if context["lpr"] is None:
            failures.append("LPR")
    except Exception as exc:
        logger.warning("LPR fetch failed: %s", exc)
        failures.append("LPR")

    available = sum(1 for v in context.values() if v is not None)
    if failures:
        logger.warning("宏观指标采集失败: %s", ", ".join(failures))
    return {
        "status": "ok" if available > 0 else "all_failed",
        "available_count": available,
        "failed_indicators": failures,
        "indicators": context,
    }


def macro_signal_label(macro: dict) -> str:
    """从宏观数据生成情景标签字符串。"""
    indicators = macro.get("indicators", {})
    parts = []

    pmi = indicators.get("pmi")
    if pmi:
        parts.append(f"PMI {pmi['value']}")

    cpi = indicators.get("cpi")
    if cpi:
        parts.append(f"CPI {cpi['value']:+.1f}%")

    lpr = indicators.get("lpr")
    if lpr:
        parts.append(f"LPR {lpr['value']}%")

    # 政策方向：综合 LPR 与 CPI（LPR 优先，CPI 作为补充信号）
    policy_parts: list[str] = []
    if lpr:
        policy_parts.append(lpr.get("signal", ""))
    if cpi:
        cpi_val = cpi.get("value", 0)
        if cpi_val < 0:
            policy_parts.append("CPI通缩压力")
        elif cpi_val > 3:
            policy_parts.append("CPI通胀压力")
    if policy_parts:
        if len(policy_parts) == 1:
            parts.append(f"→{policy_parts[0]}")
        else:
            parts.append(f"→{'/'.join(p for p in policy_parts if p)}")

    return " + ".join(parts) if parts else "宏观数据不可得"
