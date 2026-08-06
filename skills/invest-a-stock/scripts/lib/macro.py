"""宏观数据采集模块（层 5: 宏观层）。

自动采集 PMI/CPI/PPI/LPR 等中国宏观指标，以及 VIX/SOX 等全球指标。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数（日期格式用 skills/lib/dates；避免从 collector 循环导入）
# ---------------------------------------------------------------------------

from .shared_dates import (  # noqa: E402
    shanghai_days_ago as _days_ago,
    shanghai_now as _shanghai_now,
    shanghai_today as _today,
    yyyymmdd_to_iso as _to_iso_date,
)


# ---------------------------------------------------------------------------
# 全球指标采集（FRED / Yahoo Finance）
# ---------------------------------------------------------------------------

def _sanitize_cpi(value: float) -> float | None:
    """R12c: CPI 口径归一与异常拦截。

    - 合理同比区间 (-5, 30) → 直接采用
    - 基期指数口径 (85, 200) → 转换为同比 (value - 100)
    - 其余 → None（异常值，调用方标注「不可靠」）
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if -5 < v < 30:
        return round(v, 2)
    if 85 < v < 200:
        return round(v - 100, 2)
    return None


def _fetch_fred_series(
    series_id: str,
    config: dict,
    lookback_days: int = 90,
) -> tuple[float | None, list[tuple[str, float]]]:
    """从 FRED 抓取单个 series 的日序列。

    Returns:
        (latest_value, [(date, value), ...]) — latest_value 为最近有效值，
        序列按日期升序。失败时返回 (None, [])。
    """
    from . import env

    if not env.is_fred_available(config):
        return None, []

    key = config.get("FRED_API_KEY", "")
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": _to_iso_date(_days_ago(lookback_days)),
        "observation_end": _to_iso_date(_today()),
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("FRED %s fetch failed: %s", series_id, exc)
        return None, []

    out: list[tuple[str, float]] = []
    for obs in payload.get("observations", []):
        val = obs.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((obs.get("date", ""), float(val)))
        except (TypeError, ValueError):
            continue

    if not out:
        return None, []
    latest = out[-1][1]
    return latest, out


def _fetch_sox_via_yahoo() -> float | None:
    """从 Yahoo Finance v8 API 抓取 SOX（费城半导体指数）最新价。

    免费、无需 API Key。失败返回 None。
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?interval=1d&range=5d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("SOX Yahoo fetch failed: %s", exc)
        return None

    try:
        result = payload["chart"]["result"][0]
        price = result["meta"]["regularMarketPrice"]
        return float(price)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("SOX Yahoo parse failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 主采集与标签函数
# ---------------------------------------------------------------------------

def collect_macro_context(symbol: str = "") -> dict[str, Any]:
    """采集宏观背景数据。返回 {indicator_name: {value, source, signal}} 映射。

    当前支持的指标：
    - PMI: 制造业采购经理指数 (akshare macro_china_pmi)
    - CPI: 居民消费价格指数 (akshare macro_china_cpi)
    - PPI: 工业生产者出厂价格指数 (akshare macro_china_ppi)
    - LPR: 贷款市场报价利率 (akshare macro_china_lpr)
    - VIX: CBOE 波动率指数 (FRED VIXCLS)
    - SOX: 费城半导体指数 (Yahoo Finance ^SOX)

    每个指标采集失败时独立降级，不阻塞其他指标。
    """
    from . import env

    context: dict[str, Any] = {
        "pmi": None,
        "cpi": None,
        "ppi": None,
        "lpr": None,
        "money_supply": None,
        "loan": None,
        "vix": None,
        "sox": None,
    }

    failures: list[str] = []
    # 中国指标依赖 akshare；全球指标（VIX/SOX）独立，akshare 不可用不阻塞。
    akshare_ok = env.is_akshare_available()
    if not akshare_ok:
        failures.extend(["PMI", "CPI", "PPI", "LPR"])
        logger.warning("akshare 不可用，跳过中国宏观指标，继续采集 VIX/SOX")
    else:
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
                        # R12c: 口径归一 + 合理性校验。akshare macro_china_cpi 末行
                        # 可能返回同比%（如 0.3）或基期指数口径（如 107.1）——
                        # 实测 2026-08 曾把 107.1 误当同比渲染为 "CPI +107.1%"。
                        cpi_clean = _sanitize_cpi(cpi_val)
                        if cpi_clean is not None:
                            context["cpi"] = {
                                "value": cpi_clean,
                                "signal": "通胀" if cpi_clean > 3 else ("通缩" if cpi_clean < 0 else "温和"),
                                "source": "akshare.macro_china_cpi",
                            }
                        else:
                            logger.warning("CPI raw value %s outside sane range; marked unreliable", cpi_val)
                            context["cpi"] = {"value": None, "signal": "不可靠", "source": "akshare.macro_china_cpi"}
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

        # M2 货币供应量同比增速（信用脉冲参考）
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_money_supply()
                if df is not None and not df.empty:
                    # 注意：此 API 返回降序（最新在前），用 iloc[0]
                    row = df.iloc[0]
                    m2_yoy = None
                    for col in ["货币和准货币(M2)-同比增长"]:
                        v = row.get(col)
                        if v is not None:
                            m2_yoy = float(v)
                            break
                    if m2_yoy is not None:
                        # 信用脉冲信号：M2 同比 − 名义GDP增速(估~5%)
                        credit_pulse = round(m2_yoy - 5.0, 1)
                        if m2_yoy > 12:
                            signal = "宽松"
                        elif m2_yoy > 8:
                            signal = "偏松"
                        elif m2_yoy >= 5:
                            signal = "稳健"
                        else:
                            signal = "偏紧"
                        context["money_supply"] = {
                            "value": round(m2_yoy, 2),
                            "signal": signal,
                            "credit_pulse": credit_pulse,
                            "source": "akshare.macro_china_money_supply",
                        }
            if context["money_supply"] is None:
                failures.append("M2")
        except Exception as exc:
            logger.warning("M2 fetch failed: %s", exc)
            failures.append("M2")

        # 新增人民币贷款（月度）
        try:
            if context.get("money_supply") is not None:  # 共用 akshare session
                from .proxy import akshare_direct_session

                with akshare_direct_session():
                    import akshare as ak

                    df = ak.macro_rmb_loan()
                    if df is not None and not df.empty:
                        row = df.iloc[-1]
                        loan_val = None
                        loan_yoy_str = row.get("新增人民币贷款-同比", "")
                        for col in ["新增人民币贷款-总额"]:
                            v = row.get(col)
                            if v is not None:
                                loan_val = float(v)
                                break
                        if loan_val is not None:
                            # 同比增速（去掉 % 符号）
                            loan_yoy = None
                            if isinstance(loan_yoy_str, str) and "%" in loan_yoy_str:
                                try:
                                    loan_yoy = float(loan_yoy_str.replace("%", ""))
                                except ValueError:
                                    pass
                            context["loan"] = {
                                "value": round(loan_val, 2),
                                "yoy": loan_yoy,
                                "signal": "扩张" if loan_val > 15000 else ("正常" if loan_val > 5000 else "收缩"),
                                "source": "akshare.macro_rmb_loan",
                            }
            if context["loan"] is None:
                failures.append("Loan")
        except Exception as exc:
            logger.warning("Loan fetch failed: %s", exc)
            failures.append("Loan")

    # VIX (CBOE Volatility Index via FRED)
    try:
        config = env.get_config()
        latest, _ = _fetch_fred_series("VIXCLS", config, lookback_days=30)
        if latest is not None:
            if latest < 15:
                vix_signal = "低波"
            elif latest < 25:
                vix_signal = "正常"
            elif latest < 35:
                vix_signal = "偏高"
            else:
                vix_signal = "恐慌"
            context["vix"] = {
                "value": round(latest, 2),
                "signal": vix_signal,
                "source": "FRED.VIXCLS",
            }
        if context["vix"] is None:
            failures.append("VIX")
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
        failures.append("VIX")

    # SOX (Philadelphia Semiconductor Index via Yahoo Finance)
    try:
        sox_val = _fetch_sox_via_yahoo()
        if sox_val is not None:
            context["sox"] = {
                "value": round(sox_val, 2),
                "signal": "",
                "source": "YahooFinance.^SOX",
            }
        if context["sox"] is None:
            failures.append("SOX")
    except Exception as exc:
        logger.warning("SOX fetch failed: %s", exc)
        failures.append("SOX")

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
    """从宏观数据生成情景标签字符串。

    格式: PMI X.X + CPI +X.X% + LPR X.X% →信号 | VIX X.X 等级 SOX X,XXX
    左侧为国内宏观，右侧（| 之后）为全球风险/AI需求指标。
    """
    indicators = macro.get("indicators", {})
    parts: list[str] = []

    pmi = indicators.get("pmi")
    if pmi:
        parts.append(f"PMI {pmi['value']}")

    cpi = indicators.get("cpi")
    if cpi and cpi.get("value") is not None:
        parts.append(f"CPI {cpi['value']:+.1f}%")

    lpr = indicators.get("lpr")
    if lpr:
        parts.append(f"LPR {lpr['value']}%")

    m2 = indicators.get("money_supply")
    if m2:
        cp = m2.get("credit_pulse")
        cp_str = f" 脉冲{cp:+.1f}%" if cp is not None else ""
        parts.append(f"M2 {m2['value']}%{cp_str}")

    loan = indicators.get("loan")
    if loan:
        loan_val = loan.get("value", 0)
        loan_fmt = f"{loan_val:.0f}亿" if loan_val >= 10000 else f"{loan_val/10000:.1f}万亿"
        parts.append(f"信贷 {loan_fmt}")

    # 政策方向：综合 LPR 与 CPI（LPR 优先，CPI 作为补充信号）
    policy_parts: list[str] = []
    if lpr:
        policy_parts.append(lpr.get("signal", ""))
    if cpi:
        cpi_val = cpi.get("value")
        if cpi_val is None:
            pass  # R12c: 异常值拦截后（signal=不可靠），不参与政策方向判定
        elif cpi_val < 0:
            policy_parts.append("CPI通缩压力")
        elif cpi_val > 3:
            policy_parts.append("CPI通胀压力")
    if policy_parts:
        if len(policy_parts) == 1:
            parts.append(f"→{policy_parts[0]}")
        else:
            parts.append(f"→{'/'.join(p for p in policy_parts if p)}")

    # ---- 全球指标（｜分隔）----
    global_parts: list[str] = []

    vix = indicators.get("vix")
    if vix:
        vix_val = vix.get("value")
        vix_signal = vix.get("signal", "")
        if vix_val is not None and vix_signal:
            global_parts.append(f"VIX {vix_val} {vix_signal}")
        elif vix_val is not None:
            global_parts.append(f"VIX {vix_val}")

    sox = indicators.get("sox")
    if sox:
        sox_val = sox.get("value")
        if sox_val is not None:
            sox_fmt = f"{sox_val:,.0f}" if sox_val >= 1000 else str(sox_val)
            global_parts.append(f"SOX {sox_fmt}")

    china_part = " + ".join(parts) if parts else ""
    global_part = " ".join(global_parts) if global_parts else ""

    if china_part and global_part:
        return f"{china_part} | {global_part}"
    elif global_part:
        return f"宏观数据不可得 | {global_part}"
    else:
        return china_part if china_part else "宏观数据不可得"
