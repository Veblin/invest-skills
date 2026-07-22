"""市场微观结构 — Tier 1 当日快照 + 环境护栏 v1。

v0.2.1：当日快照（无历史分位）。所有 akshare 调用走直连会话。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from _invest_path import ensure_invest_a_scripts_on_path

ensure_invest_a_scripts_on_path()

from lib.nums import safe_float  # noqa: E402
from lib.proxy import akshare_direct_session  # noqa: E402

logger = logging.getLogger(__name__)

# 涨跌停比极端阈值：>5:1 亢奋，<1:5（即 ratio < 0.2）恐慌
_LU_LD_EXTREME_UP = 5.0
_LU_LD_EXTREME_DOWN = 0.2  # 1:5


# ---------------------------------------------------------------------------
# 当日快照
# ---------------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """采集 Tier 1 当日快照：两融、涨跌比、涨跌停比、成交额。

    每个指标独立采集，失败不阻塞其他维度。
    """
    result: dict[str, Any] = {
        "date": date.today().strftime("%Y%m%d"),
        "margin_balance": None,          # 融资余额（亿元）
        "margin_buy_amount": None,        # 融资买入额（亿元）
        "ad_ratio": None,                 # 涨跌比
        "lu_ld_ratio": None,              # 涨跌停比（跌停=0 时为 None，见 lu_ld_note）
        "lu_ld_note": None,               # e.g. "no_limit_down"
        "limit_up_count": None,           # 涨停家数
        "limit_down_count": None,         # 跌停家数
        "total_turnover": None,           # 全市场成交额（亿）
        "label_leverage": None,           # 杠杆标签
        "label_breadth": None,            # 广度标签
        "label_sentiment": None,          # 情绪标签
        "_errors": [],
    }

    _fetch_margin(result)
    _fetch_ad_ratio(result)
    _fetch_limit_pools(result)
    _fetch_turnover(result)
    _compute_labels(result)

    return result


def _fetch_margin(result: dict) -> None:
    """两融余额 + 融资买入额（akshare 直调）。

    单位假设：akshare ``stock_margin_account_info``（东方财富
    RPTA_WEB_MARGIN_DAILYTRADE）的 FIN_BALANCE / FIN_BUY_AMT 已是**亿元**
    （实测约 2.6e4，非元）。阈值 30000 / 12000 均按亿元解读。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_margin_account_info()
        if df is None or df.empty:
            result["_errors"].append("margin: empty response")
            return
        latest = df.iloc[-1]
        # 亿元（见 docstring）；勿再 /1e8
        result["margin_balance"] = safe_float(latest.get("融资余额"))
        result["margin_buy_amount"] = safe_float(latest.get("融资买入额"))
    except Exception as exc:
        logger.warning("margin fetch failed: %s", exc)
        result["_errors"].append(f"margin: {exc}")


def _fetch_ad_ratio(result: dict) -> None:
    """涨跌比（akshare stock_market_activity_legu 快照）。

    返回 key-value 格式（12 行），提取 上涨/下跌 两行。
    """
    try:
        import akshare as ak
        with akshare_direct_session():
            df = ak.stock_market_activity_legu()
        if df is None or df.empty:
            result["_errors"].append("ad_ratio: empty response")
            return
        # key-value 格式：item 列 = 指标名，value 列 = 数值
        kv = dict(zip(df["item"], df["value"]))
        up = safe_float(kv.get("上涨"))
        down = safe_float(kv.get("下跌"))
        if up is not None and down is not None and down > 0:
            result["ad_ratio"] = round(up / down, 4)
    except Exception as exc:
        logger.warning("ad_ratio fetch failed: %s", exc)
        result["_errors"].append(f"ad_ratio: {exc}")


def _fetch_limit_pools(result: dict) -> None:
    """涨跌停池：涨停数（stock_zt_pool_em）+ 跌停数（stock_zt_pool_dtgc_em）。"""
    today = date.today().strftime("%Y%m%d")
    # 涨停
    try:
        import akshare as ak
        with akshare_direct_session():
            df_up = ak.stock_zt_pool_em(date=today)
        if df_up is not None and not df_up.empty:
            result["limit_up_count"] = len(df_up)
    except Exception as exc:
        logger.warning("limit_up fetch failed: %s", exc)
        result["_errors"].append(f"limit_up: {exc}")

    # 跌停
    try:
        import akshare as ak
        with akshare_direct_session():
            df_dn = ak.stock_zt_pool_dtgc_em(date=today)
        if df_dn is not None and not df_dn.empty:
            result["limit_down_count"] = len(df_dn)
    except Exception as exc:
        logger.warning("limit_down fetch failed: %s", exc)
        result["_errors"].append(f"limit_down: {exc}")

    # 计算涨跌停比（禁止 float("inf") — 会破坏 JSON）
    up = result.get("limit_up_count")
    dn = result.get("limit_down_count")
    if up is not None and dn is not None and dn > 0:
        result["lu_ld_ratio"] = round(up / dn, 4)
        result["lu_ld_note"] = None
    elif up is not None and up > 0 and (dn is None or dn == 0):
        result["lu_ld_ratio"] = None
        result["lu_ld_note"] = "no_limit_down"  # 无量跌停 → 极端看多


def _fetch_turnover(result: dict) -> None:
    """全市场成交额（上交所 + 深交所）。"""
    try:
        import akshare as ak
        with akshare_direct_session():
            sse = ak.stock_sse_summary()
            szse = ak.stock_szse_summary()
        sse_amount = safe_float(sse.iloc[0].get("成交金额", 0)) if sse is not None and not sse.empty else 0
        szse_amount = safe_float(szse.iloc[0].get("成交金额", 0)) if szse is not None and not szse.empty else 0
        if sse_amount or szse_amount:
            result["total_turnover"] = round((sse_amount + szse_amount) / 1e8, 2)  # 元→亿
    except Exception as exc:
        logger.warning("turnover fetch failed: %s", exc)
        result["_errors"].append(f"turnover: {exc}")


def _is_extreme_sentiment_up(snap: dict) -> bool:
    """涨跌停比极端亢奋：ratio > 5 或无跌停（lu_ld_note=no_limit_down）。"""
    if snap.get("lu_ld_note") == "no_limit_down":
        return True
    lr = snap.get("lu_ld_ratio")
    if lr is None:
        return False
    try:
        return float(lr) > _LU_LD_EXTREME_UP
    except (ValueError, TypeError):
        return False


def _compute_labels(result: dict) -> None:
    """基于当日快照计算启发式环境标签（无历史分位，仅当日绝对值+方向）。"""
    # 杠杆标签
    # 单位：亿元（akshare eastmoney FIN_BALANCE 已是亿元，见 _fetch_margin）
    margin = result.get("margin_balance")
    if margin is not None:
        # v0.2.1 当日快照无历史序列；标签来自当日绝对水平 + 融资买入额方向
        buy = result.get("margin_buy_amount")
        if margin > 30000:  # 亿元
            base = "高杠杆"
        elif margin < 12000:  # 亿元
            base = "冰点"
        else:
            base = "中性"

        # 买入额/余额：偏冷时中性区间发出「去杠杆」以便 badge/护栏一致
        if buy is not None and margin > 0:
            pct = buy / margin
            if pct > 0.08:
                result["label_leverage"] = f"{base} 偏热"
            elif pct < 0.03:
                if base == "中性":
                    result["label_leverage"] = "中性 去杠杆"
                else:
                    result["label_leverage"] = f"{base} 偏冷"
            else:
                result["label_leverage"] = base
        else:
            result["label_leverage"] = base

    # 广度标签
    ad = result.get("ad_ratio")
    if ad is not None:
        if ad < 0.6:
            result["label_breadth"] = "极冷"
        elif ad < 0.8:
            result["label_breadth"] = "偏冷"
        elif ad <= 1.5:
            result["label_breadth"] = "正常"
        elif ad <= 2.0:
            result["label_breadth"] = "偏暖"
        else:
            result["label_breadth"] = "极热"

    # 情绪标签
    lr = result.get("lu_ld_ratio")
    ld = result.get("limit_down_count")
    if lr is not None or result.get("lu_ld_note") == "no_limit_down":
        if ld is not None and ld > 50:
            result["label_sentiment"] = "局部恐慌"
        elif _is_extreme_sentiment_up(result):
            result["label_sentiment"] = "极端亢奋"
        elif lr is not None and lr < 0.25:
            result["label_sentiment"] = "恐慌"
        elif lr is not None and lr < 0.6:
            result["label_sentiment"] = "偏冷"
        elif lr is not None and lr <= 3.0:
            result["label_sentiment"] = "正常"
        elif lr is not None:
            result["label_sentiment"] = "偏热"


# ---------------------------------------------------------------------------
# 环境护栏 v1（确定性规则，只追加 blind_spots，不改写评级，不输出仓位数字）
# ---------------------------------------------------------------------------

def apply_env_guardrail(evaluation_json: dict, snap: dict | None = None) -> dict:
    """在评估结果上追加环境盲点提示。

    3 条 v1 规则：
    1. 去杠杆趋势 → 追加"流动性收紧"盲点
    2. 涨跌停比 >5:1 或 <1:5 → 追加"情绪回归"盲点
    3. 涨跌比 <0.6 → 追加"指数失真"盲点

    不改写 dimensions.*.level，不输出仓位/买卖建议数字。
    """
    if snap is None:
        return evaluation_json

    blind_spots: list[dict] = evaluation_json.get("blind_spots", [])
    if not isinstance(blind_spots, list):
        blind_spots = []

    # 规则 1：去杠杆趋势
    label_lev = snap.get("label_leverage", "")
    if "偏冷" in str(label_lev) or "去杠杆" in str(label_lev):
        blind_spots.append({
            "rule": "deleveraging",
            "note": (
                "融资余额处于偏低水平或呈下降趋势"
                "——你的假设是否纳入了去杠杆环境下流动性收紧的可能？"
            ),
        })

    # 规则 2：涨跌停比极端（>5:1 或 <1:5；无跌停视同极端亢奋）
    if _is_extreme_sentiment_up(snap):
        lr = snap.get("lu_ld_ratio")
        if snap.get("lu_ld_note") == "no_limit_down":
            ratio_desc = "无跌停（涨停>0）"
        else:
            try:
                ratio_desc = f"{float(lr):.1f}:1"
            except (TypeError, ValueError):
                ratio_desc = "极端"
        blind_spots.append({
            "rule": "extreme_sentiment_up",
            "note": (
                f"涨跌停比 {ratio_desc}，处于极端亢奋区间"
                "——情绪回归均值时，你的入场价可能包含情绪溢价。"
            ),
        })
    else:
        lr = snap.get("lu_ld_ratio")
        if lr is not None:
            try:
                lr_val = float(lr)
                if lr_val < _LU_LD_EXTREME_DOWN:
                    blind_spots.append({
                        "rule": "extreme_sentiment_down",
                        "note": (
                            f"涨跌停比 {lr_val:.2f}:1，处于恐慌区间"
                            "——跌停潮下部分标的可能无法成交，名义仓位 ≠ 可退出仓位。"
                        ),
                    })
            except (ValueError, TypeError):
                pass

    # 规则 3：涨跌比 <0.6
    ad = snap.get("ad_ratio")
    if ad is not None and ad < 0.6:
        blind_spots.append({
            "rule": "market_breadth",
            "note": (
                f"涨跌比 {ad:.2f}，大多数股票在跌"
                "——指数可能被权重股拉偏，你的标的真实跌幅可能更大。"
            ),
        })

    evaluation_json["blind_spots"] = blind_spots
    return evaluation_json

