"""
数据工具模块 — 基础设施层

提供：
- bypass_proxy_session: 绕过系统代理的 requests Session
- normalize_code: 统一代码格式处理
- extract_metric: 宽表转窄表（从 stock_financial_abstract 产出中提取指标）
- curl_fetch: curl subprocess 回退（当 Python requests 被 proxy 阻断时）
- compute_cagr / compute_yoy: 财务同比计算
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 代理绕过
# ─────────────────────────────────────────────────────────────

def _clean_proxy_env() -> dict[str, str]:
    """从 os.environ 中移除所有代理相关变量，返回 clean 的副本。"""
    proxy_keys = [
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy",
    ]
    return {k: os.environ.pop(k, "") for k in proxy_keys}


def bypass_proxy_session(timeout: int = 15, retries: int = 3) -> requests.Session:
    """创建一个绕过系统代理、带重试的 requests Session。

    Args:
        timeout: 单次请求超时秒数
        retries: 失败重试次数

    Returns:
        配置好的 requests.Session（trust_env=False）
    """
    from requests.adapters import HTTPAdapter, Retry

    session = requests.Session()
    session.trust_env = False  # 关键：不读取系统代理设置
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    })

    retry_strategy = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def curl_fetch(url: str, timeout: int = 15) -> str | None:
    """当 Python requests 被代理阻断时，用 curl subprocess 回退获取数据。

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        HTTP 响应 body 文本，失败返回 None
    """
    try:
        # 用 env -i 确保不继承任何代理环境变量
        result = subprocess.run(
            ["env", "-i", "HOME", os.environ.get("HOME", ""),
             "PATH", os.environ.get("PATH", ""),
             "curl", "-s", "--connect-timeout", str(timeout),
             "--max-time", str(timeout),
             "-H", "User-Agent: Mozilla/5.0",
             url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        else:
            logger.warning("curl fallback failed: rc=%s stderr=%s", result.returncode, result.stderr[:200])
            return None
    except Exception as e:
        logger.warning("curl fallback exception: %s", e)
        return None


def fetch_json_via_fallback(url: str, timeout: int = 15) -> dict | None:
    """获取 JSON 数据的容错请求：优先 requests（无代理），失败则 curl。

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        解析后的 JSON dict，失败返回 None
    """
    # 尝试 1: requests 绕过代理
    try:
        session = bypass_proxy_session(timeout=timeout, retries=2)
        r = session.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json(object)  # type: ignore[return-value]
    except Exception as e:
        logger.debug("requests bypass failed for %s: %s", url[:80], e)

    # 尝试 2: curl fallback
    raw = curl_fetch(url, timeout=timeout)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("curl response for %s is not valid JSON: %s", url[:80], e)

    return None


# ─────────────────────────────────────────────────────────────
# 代码归一化
# ─────────────────────────────────────────────────────────────

# 东方财富 secid 前缀
EM_MARKET_MAP = {
    "6": "1",  # 上海主板
    "0": "0",  # 深圳主板
    "3": "0",  # 深圳创业板
    "9": "1",  # 上海科创板（部分）
}

# 交易所后缀
EXCHANGE_SUFFIX = {
    "6": ".SH",
    "9": ".SH",
    "0": ".SZ",
    "3": ".SZ",
    "8": ".BJ",
    "4": ".BJ",
}


def normalize_code(code: str) -> str:
    """标准化 A 股代码为6位纯数字。

    可接受：600519、SZ300328、300328.SZ、sh600519

    >>> normalize_code("300328")
    '300328'
    >>> normalize_code("SZ300328")
    '300328'
    >>> normalize_code("600519.SH")
    '600519'
    """
    code = str(code).strip().upper()
    # 去前缀
    for prefix in ("SZ", "SH", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    # 去后缀
    for suffix in (".SZ", ".SH", ".BJ"):
        if code.endswith(suffix):
            code = code[:-len(suffix)]
            break
    return code.zfill(6)


def to_eastmoney_secid(code: str) -> str:
    """转为东方财富 secid 格式：0.300328（深交所）/ 1.600519（上交所）。

    >>> to_eastmoney_secid("300328")
    '0.300328'
    >>> to_eastmoney_secid("600519")
    '1.600519'
    """
    code = normalize_code(code)
    market = EM_MARKET_MAP.get(code[0], "0")
    return f"{market}.{code}"


def to_ts_code(code: str) -> str:
    """转为 Tushare/baostock 格式：300328.SZ。

    >>> to_ts_code("300328")
    '300328.SZ'
    """
    code = normalize_code(code)
    suffix = EXCHANGE_SUFFIX.get(code[0], ".SH")
    return f"{code}{suffix}"


# ─────────────────────────────────────────────────────────────
# 财务指标提取（宽表 → 窄表）
# ─────────────────────────────────────────────────────────────

def extract_metric(
    fin_df: pd.DataFrame,
    metric_name: str,
    year: int | None = None,
    quarter: str | None = None,
) -> float | None:
    """从 akshare stock_financial_abstract 宽表中提取指定指标。

    宽表格式：行 = 指标名，列 = 报告期日期（如 20251231）

    Args:
        fin_df: stock_financial_abstract 的返回值
        metric_name: 指标名（如 "归母净利润"、"毛利率"、"净资产收益率(ROE)"）
        year: 年度编号（如 2025），None 则返回最新一期
        quarter: 季报编号（如 "20250630"），与 year 互斥

    Returns:
        指标数值；找不到则返回 None

    >>> # fin_df = ak.stock_financial_abstract(symbol="300328")
    >>> # roe = extract_metric(fin_df, "净资产收益率(ROE)", year=2025)
    """
    try:
        # 找到指标行
        mask = fin_df["指标"] == metric_name
        if not mask.any():
            # 尝试模糊匹配
            for col_name in fin_df["指标"].unique():
                if metric_name in str(col_name):
                    mask = fin_df["指标"] == col_name
                    break
        if not mask.any():
            return None

        # 确定目标列
        if quarter:
            target_col = str(quarter)
        elif year:
            target_col = str(year) + "1231"
        else:
            # 最新一期：取最后一列包含数字值的列
            date_cols = [c for c in fin_df.columns if isinstance(c, str) and re.match(r"^\d{8}$", str(c))]
            if not date_cols:
                return None
            target_col = sorted(date_cols)[-1]
            # 如果是未来日期，取前一个
            import datetime
            today = datetime.date.today().strftime("%Y%m%d")
            if target_col > today:
                valid_cols = [c for c in date_cols if c <= today]
                target_col = valid_cols[-1] if valid_cols else target_col

        if target_col not in fin_df.columns:
            return None

        val = fin_df[mask][target_col].values[0]
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def extract_metric_series(
    fin_df: pd.DataFrame,
    metric_name: str,
    years: list[int] | None = None,
) -> dict[int, float | None]:
    """提取多年时间序列指标。

    Args:
        fin_df: stock_financial_abstract 的返回值
        metric_name: 指标名
        years: 目标年份列表，默认 [2021, 2022, 2023, 2024, 2025]

    Returns:
        {2021: 12345.6, 2022: 23456.7, ...}
    """
    if years is None:
        import datetime
        current = datetime.date.today().year
        years = list(range(current - 4, current + 1))

    return {y: extract_metric(fin_df, metric_name, year=y) for y in years}


def extract_all_metrics(
    fin_df: pd.DataFrame,
    metrics: list[str],
    years: list[int] | None = None,
) -> dict[str, dict[int, float | None]]:
    """批量提取多个指标的多年序列。

    >>> fin = ak.stock_financial_abstract("300328")
    >>> data = extract_all_metrics(fin, ["归母净利润", "毛利率", "ROE"])
    >>> data["毛利率"][2025]
    """
    return {m: extract_metric_series(fin_df, m, years=years) for m in metrics}


# ─────────────────────────────────────────────────────────────
# 衍生指标计算
# ─────────────────────────────────────────────────────────────

def compute_yoy(current: float | None, previous: float | None) -> float | None:
    """计算同比增长率（%）。

    >>> compute_yoy(17.26, 16.52)
    4.48
    """
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def compute_cagr(values: list[float | None]) -> float | None:
    """计算 CAGR（复合年增长率）（%）。

    Args:
        values: 按时间顺序排列的值列表（如 [v2021, v2022, v2023, v2024, v2025]）

    >>> compute_cagr([10.70, 16.16, 17.07, 16.52, 17.26])
    12.63
    """
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return None
    first, last = valid[0], valid[-1]
    if first <= 0 or last <= 0:
        return None
    n = len(valid) - 1
    return round(((last / first) ** (1 / n) - 1) * 100, 2)


def financial_highlight_signals(
    fin_df: pd.DataFrame,
    years: list[int] | None = None,
) -> dict[str, Any]:
    """自动扫描财务指标，标记 highlight 和 red flag 信号。

    Returns:
        {
            "highlights": ["营收连续5年增长", ...],
            "red_flags": ["扣非净利润连续5年为负", "流动比率<1.0"],
            "trends": {"revenue_cagr_5y": 12.6, "gross_margin_trend": "declining"},
        }
    """
    if years is None:
        import datetime
        current = datetime.date.today().year
        years = list(range(current - 4, current + 1))

    highlights: list[str] = []
    red_flags: list[str] = []
    trends: dict[str, Any] = {}

    # 营收
    revenue = extract_metric_series(fin_df, "营业总收入", years=years)
    rev_values = [v for v in revenue.values() if v is not None]
    if len(rev_values) >= 3:
        cagr = compute_cagr(rev_values)
        if cagr is not None:
            trends["revenue_cagr_5y"] = cagr
            if cagr > 10:
                highlights.append(f"营收近{len(years)}年 CAGR {cagr:.1f}%，保持双位数增长")

    # 归母净利润
    profit = extract_metric_series(fin_df, "归母净利润", years=years)
    neg_count = sum(1 for v in profit.values() if v is not None and v < 0)
    if neg_count >= 4:
        red_flags.append(f"归母净利润近{neg_count}年为负")

    # 扣非净利润
    ded_profit = extract_metric_series(fin_df, "扣非净利润", years=years)
    ded_neg = sum(1 for v in ded_profit.values() if v is not None and v < 0)
    if ded_neg >= 4:
        red_flags.append(f"扣非净利润连续{ded_neg}年为负")

    # 毛利率趋势
    gross_margin = extract_metric_series(fin_df, "毛利率", years=years)
    gm_values = [(y, v) for y, v in gross_margin.items() if v is not None]
    if len(gm_values) >= 3:
        first_gm = gm_values[0][1]
        last_gm = gm_values[-1][1]
        if first_gm and last_gm:
            change = last_gm - first_gm
            if change < -3:
                trends["gross_margin_trend"] = "declining_sig"
                red_flags.append(f"毛利率从{years[0]}年 {first_gm:.1f}% 降至{years[-1]}年 {last_gm:.1f}%，下滑显著")
            elif change < -1:
                trends["gross_margin_trend"] = "declining"
            elif change > 3:
                trends["gross_margin_trend"] = "improving"

    # 流动比率
    current_ratio = extract_metric(fin_df, "流动比率")
    if current_ratio is not None and current_ratio < 1.0:
        red_flags.append(f"流动比率 {current_ratio:.2f}，低于安全线 1.0")

    # 资产负债率趋势
    debt_ratio = extract_metric_series(fin_df, "资产负债率", years=years)
    dr_values = [(y, v) for y, v in debt_ratio.items() if v is not None]
    if len(dr_values) >= 2:
        first_dr = dr_values[0][1]
        last_dr = dr_values[-1][1]
        if first_dr and last_dr:
            dr_change = last_dr - first_dr
            if dr_change > 15:
                red_flags.append(f"资产负债率从{years[0]}年 {first_dr:.1f}% 升至{max(years)}年 {last_dr:.1f}%，大幅攀升")
            if last_dr > 60:
                red_flags.append(f"资产负债率 {last_dr:.1f}%，超过 60%")

    # ROE
    roe = extract_metric_series(fin_df, "净资产收益率(ROE)", years=years)
    neg_roe = sum(1 for v in roe.values() if v is not None and v < 0)
    if neg_roe >= 2:
        red_flags.append(f"ROE 连续{neg_roe}年为负，资本回报率低")

    return {
        "highlights": highlights,
        "red_flags": red_flags,
        "trends": trends,
    }


# ─────────────────────────────────────────────────────────────
# 缓存
# ─────────────────────────────────────────────────────────────

def get_evidence_dir(base_dir: str = "evidence") -> str:
    """获取/创建 evidence 目录。"""
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def load_cached(symbol: str, base_dir: str = "evidence") -> dict | None:
    """从缓存加载采集结果。"""
    import json as json_mod
    cache_file = os.path.join(base_dir, f"raw_{normalize_code(symbol)}.json")
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json_mod.load(f)
    except Exception:
        return None


def save_cache(data: dict, symbol: str, base_dir: str = "evidence") -> str:
    """保存采集结果到缓存。"""
    import json as json_mod
    os.makedirs(base_dir, exist_ok=True)
    cache_file = os.path.join(base_dir, f"raw_{normalize_code(symbol)}.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json_mod.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return cache_file
