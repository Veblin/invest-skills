#!/usr/bin/env python3
"""
条件评分检查 — 多维客观条件量化（研究工具，非决策工具）。

四个维度（各 0-25 分，总分 0-100）：
  1. 宏观偏置:   跨资产信号描述风险偏好（risk-on vs risk-off）
  2. 技术位置:   RSI / BOLL / MA偏离 / 近N日涨幅
  3. 资金行为:   超大单/大单净流向
  4. 离散风险:   近期已知事件（IPO/财报/政策等关键词）

总分含义（条件状态描述，非操作建议）:
  <25  : 多重逆风条件
  25-50: 偏谨慎条件
  50-75: 中性混合条件
  >75  : 多维同向偏松

用法:
  uv run python scripts/research/entry_check.py 588000
  uv run python scripts/research/entry_check.py 588000 --events "长鑫科技IPO下周上市,虹吸效应"
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import akshare as ak
import numpy as np
import pandas as pd

_skills_lib = Path(__file__).resolve().parents[2] / "skills" / "lib"
if str(_skills_lib) not in sys.path:
    sys.path.insert(0, str(_skills_lib))

from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

ensure_invest_a_scripts_on_path()

from lib.proxy import akshare_direct_session  # noqa: E402
from lib.technical import boll_latest, rsi_series, sma  # noqa: E402


# ── 1. 宏观偏置（基于跨资产信号） ──────────────────────────

def _macro_score() -> tuple[int, dict]:
    """跨资产信号 → 风险偏好评分 (0-25)"""
    try:
        with akshare_direct_session():
            df = ak.futures_global_spot_em()
    except Exception:
        return 12, {"error": "数据不可用，返回中性 12 分"}

    def _get_change(name_contains: str) -> Optional[float]:
        row = df[df["名称"].str.contains(name_contains, na=False, case=False)]
        if row.empty:
            return None
        val = row["涨跌幅"].dropna()
        return float(val.iloc[0]) if len(val) > 0 else None

    gold = _get_change("COMEX黄金")
    oil = _get_change("NYMEX原油")
    copper = _get_change("COMEX铜")
    vix = _get_change("VIX|恐慌")

    signals = {}
    score = 15  # 中性起点

    # 信号1: 黄金+石油同涨 + 铜跌 = 地缘风险 → 风险厌恶
    if gold is not None and oil is not None and copper is not None:
        signals["gold"] = f"{gold:+.2f}%"
        signals["oil"] = f"{oil:+.2f}%"
        signals["copper"] = f"{copper:+.2f}%"

        if gold > 0.5 and oil > 0.5 and copper < 0:
            score -= 10
            signals["pattern"] = "地缘风险/risk-off"
        elif gold > 0.5 and oil < 0 and copper < -0.5:
            score -= 8
            signals["pattern"] = "纯避险/risk-off"
        elif copper > 0.5 and oil > 0:
            score += 5
            signals["pattern"] = "增长乐观/risk-on"
        elif gold < -0.3 and copper > 0.3:
            score += 8
            signals["pattern"] = "风险偏好/risk-on"
        else:
            signals["pattern"] = "混合信号/中性"
    else:
        score = 12
        signals["pattern"] = "部分数据缺失/默认中性"

    if vix is not None:
        signals["vix_change"] = f"{vix:+.2f}%"

    return max(0, min(25, score)), signals


# ── 2. 技术位置（基于价格 + 均线 + RSI） ──────────────────

def _technical_score(symbol: str) -> tuple[int, dict]:
    """技术位置评分 (0-25)：描述价格相对区间位置，不作操作解读。"""
    try:
        with akshare_direct_session():
            df = ak.fund_etf_hist_em(
                symbol=symbol, period="daily",
                start_date=(datetime.now() - timedelta(days=180)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq",
            )
    except Exception:
        return 12, {"error": "K线获取失败"}

    if df is None or df.empty or "收盘" not in df.columns:
        return 12, {"error": "无数据"}

    closes = df["收盘"].astype(float)
    if len(closes) < 2:
        # 单根 K 线无法计算日涨跌/RSI/MA，中性回退而非 IndexError 崩溃
        return 12, {"error": "K线不足 2 根"}

    latest = closes.iloc[-1]
    prev = closes.iloc[-2]
    closes_list = closes.tolist()

    info = {
        "latest": round(latest, 3),
        "prev": round(prev, 3),
        "day_change": f"{(latest / prev - 1) * 100:+.2f}%",
    }

    score = 15  # 中性起点

    # RSI(6) — 共享引擎 Wilder 平滑（全涨 RSI=100 / 全跌 RSI=0，不丢超买信号）
    rsi6_list = rsi_series(closes_list, 6)
    rsi_last = _safe_float(rsi6_list[-1], None) if rsi6_list else None
    rsi_val = float(rsi_last) if rsi_last is not None else 50
    info["rsi6"] = round(rsi_val, 1)

    if rsi_val > 75:
        score -= 8
        info["rsi_zone"] = "超买"
    elif rsi_val > 65:
        score -= 4
        info["rsi_zone"] = "偏高"
    elif rsi_val < 25:
        score += 8
        info["rsi_zone"] = "超卖"
    elif rsi_val < 35:
        score += 4
        info["rsi_zone"] = "偏低"
    else:
        info["rsi_zone"] = "中性"

    # MA 偏离 — 共享引擎 sma
    ma20_list = sma(closes_list, 20)
    ma20 = _safe_float(ma20_list[-1], None) if ma20_list else None
    if ma20 is not None:
        dev = (latest / ma20 - 1) * 100
        info["ma20_dev"] = f"{dev:+.1f}%"
        if dev > 15:
            score -= 6
        elif dev > 8:
            score -= 3
        elif dev < -15:
            score += 6
        elif dev < -8:
            score += 3

    # 近20日涨跌幅（需 21 根 K 线：今收 vs 20 交易日前）
    if len(closes) >= 21:
        chg20 = (closes.iloc[-1] / closes.iloc[-21] - 1) * 100
        info["chg_20d"] = f"{chg20:+.1f}%"
        if chg20 > 20:
            score -= 5
        elif chg20 > 10:
            score -= 2
        elif chg20 < -20:
            score += 5
        elif chg20 < -10:
            score += 2

    # BOLL 位置 — 共享引擎 boll_latest（总体标准差，与引擎口径一致）
    boll = boll_latest(closes_list, 20, 2.0)
    if (
        boll["upper"] is not None
        and boll["lower"] is not None
        and boll["upper"] > boll["lower"]
    ):
        boll_pos = (latest - boll["lower"]) / (boll["upper"] - boll["lower"])
        info["boll_pos"] = f"{boll_pos:.0%}"
        if boll_pos > 0.9:
            score -= 3
        elif boll_pos < 0.1:
            score += 3

    return max(0, min(25, score)), info


# ── 3. 资金行为（当日 + 近期资金流） ───────────────────────

def _safe_float(value, default: Optional[float] = 0.0) -> Optional[float]:
    """NaN 安全取值：None/缺失/NaN → default；合法数值 → float。

    akshare 空值常为 np.nan，而 `bool(nan)` 为 True，原 `float(x or 0)` 会把
    NaN 透传 → 输出 '+nan亿' / 'nan%'，且缺失资金流被误判为"平衡"。
    """
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if np.isnan(v):
        return default
    return v


def _flow_score(symbol: str) -> tuple[int, dict]:
    """资金流向评分 (0-25)：描述净流入/流出状态。"""
    info = {}
    row = None
    super_large_yi: float | None = None
    large_yi: float | None = None

    # 当日资金流
    try:
        with akshare_direct_session():
            spot = ak.fund_etf_spot_em()
        row = spot[spot["代码"] == symbol]
        if not row.empty:
            r = row.iloc[0]
            # NaN 安全取值：缺失/NaN → None → 标注 not available（而非误判"平衡"）
            super_large = _safe_float(r.get("超大单净流入-净额"), default=None)
            large = _safe_float(r.get("大单净流入-净额"), default=None)
            mid = _safe_float(r.get("中单净流入-净额"), default=None)
            small = _safe_float(r.get("小单净流入-净额"), default=None)

            # Keep raw 亿元 floats for scoring; format only for display
            if super_large is not None:
                super_large_yi = super_large / 1e8
                info["超大单"] = f"{super_large_yi:+.2f}亿"
            else:
                info["超大单"] = "not available"
            if large is not None:
                large_yi = large / 1e8
                info["大单"] = f"{large_yi:+.2f}亿"
            else:
                info["大单"] = "not available"
            if mid is not None:
                info["中单"] = f"{mid/1e8:+.2f}亿"
            else:
                info["中单"] = "not available"
            if small is not None:
                info["小单"] = f"{small/1e8:+.2f}亿"
            else:
                info["小单"] = "not available"
    except Exception:
        pass

    score = 15  # 中性起点

    if super_large_yi is not None and large_yi is not None:
        # 主力态度 = 超大单 + 大单方向（亿元）
        smart = super_large_yi + large_yi
        info["主力净额"] = f"{smart:+.2f}亿"

        if smart > 1:
            score += 6
            info["主力态度"] = "积极流入"
        elif smart > 0:
            score += 3
            info["主力态度"] = "小幅流入"
        elif smart < -1:
            score -= 6
            info["主力态度"] = "积极流出"
        elif smart < 0:
            score -= 3
            info["主力态度"] = "小幅流出"
        else:
            info["主力态度"] = "平衡"

    # 换手率异常（依赖上方 spot 查询成功且命中标的）
    if row is not None and not row.empty:
        turnover = _safe_float(row.iloc[0].get("换手率"), default=None)
        if turnover is not None:
            info["换手率"] = f"{turnover:.1f}%"
            if turnover > 15:
                score -= 3
                info["换手率异常"] = "过高(>15%)"
            elif turnover > 10:
                score -= 1
        else:
            info["换手率"] = "not available"

    return max(0, min(25, score)), info


# ── 4. 离散风险（事件冲击） ────────────────────────────────

def _event_score(user_events: str = "") -> tuple[int, dict]:
    """离散事件风险评分 (0-25)：已知事件关键词越多，分数越低。"""
    info = {}
    score = 20  # 默认接近满分（无已知风险）

    if not user_events:
        info["note"] = "未提供事件，默认无已知风险"
        return score, info

    # 用户提供的事件列表
    events = [e.strip() for e in user_events.split(",") if e.strip()]
    info["events"] = events

    # 关键词风险评级
    risk_keywords = {
        "ipo": -8, "上市": -5, "吸血": -6, "虹吸": -6, "解禁": -8,
        "财报": -3, "业绩": -3, "加息": -7, "cpi": -5, "非农": -4,
        "地缘": -8, "冲突": -8, "制裁": -6, "政策": -5,
        "利好": +3, "回购": +4, "增持": +4, "分红": +2,
    }

    for event in events:
        event_lower = event.lower()
        for kw, delta in risk_keywords.items():
            if kw in event_lower:
                score += delta
                info.setdefault("风险关键词命中", []).append(f"{kw}({delta:+d})")

    return max(0, min(25, score)), info


# ── 主流程 ──────────────────────────────────────────────────

def check(symbol: str, events: str = "") -> dict:
    result = {
        "symbol": symbol,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    scores = {}

    # 1. 宏观
    s, d = _macro_score()
    scores["宏观偏置"] = s
    result["macro"] = {"score": s, **d}

    # 2. 技术
    s, d = _technical_score(symbol)
    scores["技术位置"] = s
    result["technical"] = {"score": s, **d}

    # 3. 资金
    s, d = _flow_score(symbol)
    scores["资金行为"] = s
    result["flow"] = {"score": s, **d}

    # 4. 事件
    s, d = _event_score(events)
    scores["离散风险"] = s
    result["event"] = {"score": s, **d}

    total = sum(scores.values())
    result["total"] = total
    result["scores"] = scores

    # 条件状态（非操作建议）
    if total < 25:
        result["signal"] = "多重逆风条件"
    elif total < 50:
        result["signal"] = "偏谨慎条件"
    elif total < 75:
        result["signal"] = "中性混合条件"
    else:
        result["signal"] = "多维同向偏松"

    return result


def format_report(r: dict) -> str:
    lines = []
    lines.append(f"# 条件评分检查: {r['symbol']} — {r['time']}")
    lines.append(f"## {r['signal']} (总分 {r['total']}/100)")
    lines.append("")

    dims = [
        ("宏观偏置", "macro"),
        ("技术位置", "technical"),
        ("资金行为", "flow"),
        ("离散风险", "event"),
    ]

    for name, key in dims:
        d = r[key]
        s = r["scores"][name]
        bar = "█" * (s // 5) + "░" * (5 - s // 5)
        lines.append(f"### {name}: {s}/25  [{bar}]")
        for k, v in d.items():
            if k != "score":
                lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append(
        "> ⚠️ 研究工具：评分仅描述当前可观测条件，不构成投资建议，"
        "亦不预测未来价格走势。"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="条件评分检查（研究工具，非决策工具）")
    parser.add_argument("symbol", help="ETF 代码，如 588000")
    parser.add_argument("--events", default="", help="近期已知事件，逗号分隔。如 '长鑫IPO上市,虹吸效应'")
    args = parser.parse_args()

    result = check(args.symbol, args.events)
    print(format_report(result))
