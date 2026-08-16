"""R10/R12g-B 风格-标的匹配引擎（三态判定 + 用户风格档案）。

三态（决策 U2，R10 验收）：
- **匹配**：价值→估值股息回归；成长→成长兑现；趋势→暂无法判定（已告知信息深度不足）；事件驱动→暂无法判定
- **中性**：风格未填 / R1 为「暂无法判定」（且无 journal 冲突）
- **混搭风险**：该标的有 journal 记录 且 Q1 驱动逻辑与 R1 假设冲突

混搭提示为**固定模板**（非 AI 撰写）；风格判定只影响研究框架提示，不改报告模块输出
（避免滑向交易指导，LAW 6 + B 裁决：研究工具 ≠ 交易指导）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 风格 → R1 收益驱动假设 的匹配对
MATCHING_PAIRS: dict[str, str] = {
    "价值": "估值股息回归",
    "成长": "成长兑现",
    "趋势": "暂无法判定",
    "事件驱动": "暂无法判定",
}

# journal Q1 驱动逻辑 → 风格家族（风格↔Q1 显式映射表，C0 同步）
JOURNAL_STYLE_MAP: dict[str, str | None] = {
    "均值回归": "价值",
    "趋势跟随": "趋势",
    "政策催化": "事件驱动",
    "产业转型": None,  # 无风格对应 → 中性
}

# R1 收益驱动假设 → 风格家族
DRIVER_STYLE_MAP: dict[str, str | None] = {
    "估值股息回归": "价值",
    "成长兑现": "成长",
    "周期均值回归": "价值",
    "暂无法判定": None,
}

_STYLE_FILE = "user_style.json"


def format_match_hint(driver: str, journal_driver: str) -> str:
    """混搭提示固定模板（非 AI 撰写，R10 验收原文）。"""
    return (
        f"该标的收益驱动为「{driver}」（引擎），你的 journal 记录本次决策驱动逻辑为"
        f"「{journal_driver}」——两者指向不同方法论，注意起念与持有论证的一致性"
        "（面基方法论：'不能把基本面的投资手册当成趋势投资的航海指南'）。"
    )


def match_style(
    style: str | None,
    driver: str | None,
    journal_driver: str | None = None,
) -> dict:
    """三态判定（纯函数，可单测）。

    Returns: {"state": "匹配"|"中性"|"混搭风险", "reason": str, "hint": str|None}
    """
    driver = driver or ""

    # 1. 混搭风险：journal Q1 与 R1 假设冲突（两者均可映射到风格家族且不同）
    if journal_driver:
        js = JOURNAL_STYLE_MAP.get(journal_driver)
        ds = DRIVER_STYLE_MAP.get(driver)
        if js and ds and js != ds:
            return {
                "state": "混搭风险",
                "reason": f"journal Q1=「{journal_driver}」 vs R1=「{driver}」指向不同方法论",
                "hint": format_match_hint(driver, journal_driver),
            }

    # 2. 匹配
    if style and style in MATCHING_PAIRS and driver == MATCHING_PAIRS[style]:
        note = ""
        if style in ("趋势", "事件驱动"):
            note = "（已告知信息深度不足：R1 暂无法判定，自上而下视角更稳健）"
        return {"state": "匹配", "reason": f"{style} × {driver}{note}", "hint": None}

    # 3. 中性：风格未填 / R1 暂无法判定 / 未定义组合不自动推断
    if not style:
        reason = "风格未填写（中性，不自动推断）"
    elif not driver or driver == "暂无法判定":
        reason = f"R1=「{driver or '—'}」（中性，不自动推断）"
    else:
        reason = f"{style} × {driver}（未定义映射，中性，不自动推断）"
    return {"state": "中性", "reason": reason, "hint": None}


# ---------------------------------------------------------------------------
# 用户风格档案（env.STORE_DIR/user_style.json；写失败 → 会话内存降级）
# ---------------------------------------------------------------------------

def _style_path() -> Path:
    from lib.env import STORE_DIR
    return STORE_DIR / _STYLE_FILE


def load_style() -> str | None:
    """读取风格档案；缺失/损坏 → None（中性）。"""
    try:
        p = _style_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        style = data.get("style")
        return style if isinstance(style, str) and style else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# cmd_report 装配
# ---------------------------------------------------------------------------

def _driver_from_collection(collection: dict) -> str | None:
    """复用 R1 classify 逻辑：从 financials 年报期净利提取收益驱动假设。"""
    try:
        from lib.income_driver import classify_income_driver
        fin = (collection.get("dimensions") or [])
        rows = []
        industry: str | None = None
        for dim in fin:
            name = dim.get("dimension")
            if name == "financials" and isinstance(dim.get("data"), list):
                rows = dim["data"]
            if name == "basic_info" and industry is None:
                bdata = dim.get("data")
                if isinstance(bdata, list):
                    for br in bdata:
                        if isinstance(br, dict) and br.get("industry"):
                            industry = str(br.get("industry"))
                            break
                elif isinstance(bdata, dict) and bdata.get("industry"):
                    industry = str(bdata.get("industry"))
        annual: list[dict] = []
        for r in rows:
            ed = str(r.get("end_date", ""))
            npv = r.get("net_profit")
            if ed.endswith("1231") and npv is not None:
                annual.append({"year": ed, "net_profit": float(npv)})
        if len(annual) < 3:
            return None
        result = classify_income_driver(annual, rows, industry=industry)
        return result.get("driver") or None
    except Exception:
        return None


def _journal_driver(symbol: str) -> str | None:
    """读取「当前标的」最近一条 Q1 记录的 driver（不得用其他标的记录替代）。"""
    try:
        from db import search_by_symbol
        rows = search_by_symbol(symbol)
        if not rows:
            return None
        return rows[0].get("driver") or None
    except Exception:
        return None


def assemble_style_match(collection: dict, symbol: str) -> dict | None:
    """cmd_report 装配：driver（R1 引擎）+ style（档案）+ journal_driver（同标的 Q1）。

    任一环节失败 → 返回 None（报告不受影响）。
    """
    try:
        driver = _driver_from_collection(collection)
        style = load_style()
        journal_driver = _journal_driver(symbol)
        m = match_style(style, driver, journal_driver)
        return {
            "style": style,
            "driver": driver,
            "journal_driver": journal_driver,
            **m,
        }
    except Exception as exc:
        logger.warning("assemble_style_match failed: %s", exc)
        return None
