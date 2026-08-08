"""R5 行业景气状态卡 — 五维规则引擎 + 行业采集。

五维：估值分位 / 盈利趋势 / 相对强度 / 资金流 / 政策证据。

规则纪律（决策记录 U4）：
- 有效维 <3 → 输出「数据不完整（有效维度 N/5）」+ 缺失维度清单，**不做状态结论**、不自动降级猜测
- 方向投票仅盈利趋势/相对强度/资金流三票；估值分位只定语境；政策证据独立呈现
- 方向冲突按优先级裁决（盈利趋势 > 资金流 > 相对强度），非多数投票；仅全部方向维缺失/中性才「无法定论」
- 各维度独立呈现，禁止单维下结论；状态卡是研究判断（可解释状态），不携带仓位/交易含义

引擎分层：
- `industry_climate_card(dims)` 纯规则引擎（可单测，零网络）
- `build_industry_climate(industry)` 五维采集（akshare，各维独立降级为无效维度）
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CLIMATE_PCT_LOW = 0.30
CLIMATE_PCT_HIGH = 0.70
CLIMATE_MIN_VALID = 3

# 相对强度窗口（交易日）：RS 方向反映近期相对强度，而非 2005 年起的累计表现
RS_WINDOW = 120

_DIM_ORDER = ("估值分位", "盈利趋势", "相对强度", "资金流", "政策证据")
_DIRECTION_DIMS = ("盈利趋势", "相对强度", "资金流")

# 方向冲突优先级（STEP 4：盈利趋势 > 资金流 > 相对强度）
_DIRECTION_PRIORITY = ("盈利趋势", "资金流", "相对强度")

LAW6A_DISCLAIMER = "状态卡为研究判断（可解释状态），不构成操作信号，也不携带任何仓位/交易含义。"


# ---------------------------------------------------------------------------
# 纯规则引擎（可单测）
# ---------------------------------------------------------------------------

def industry_climate_card(dims: list[dict]) -> dict:
    """五维规则判定（无网络）。

    Args:
        dims: 每项 {name, value, source, valid}；name 限五维之一
              value 约定：
                估值分位: 0-1 分位值（行业 PE 分布内）
                盈利趋势/相对强度: "up" / "down"
                资金流: "in" / "out"
                政策证据: "有" / "无" / "未查"（valid 仅在有官方来源时 True）

    Returns:
        {
            state: 复苏/扩张/降温/收缩/无法定论/数据不完整,
            valid_count, missing_dims, direction, context,
            policy_note, disclaimer, dims: [...]
        }
    """
    by_name = {}
    for d in dims:
        name = str(d.get("name", ""))
        if name in _DIM_ORDER:
            by_name[name] = d

    def _valid(name: str) -> bool:
        d = by_name.get(name)
        return bool(d and d.get("valid"))

    # STEP 1 有效方向维
    dir_dims = [n for n in _DIRECTION_DIMS if _valid(n)]
    valid_total = sum(1 for n in _DIM_ORDER if _valid(n))
    missing = [n for n in _DIM_ORDER if not _valid(n)]

    # STEP 2 有效维总数 <3 → 数据不完整（U4，不做状态结论）
    if valid_total < CLIMATE_MIN_VALID:
        return {
            "state": "数据不完整",
            "valid_count": valid_total,
            "missing_dims": missing,
            "direction": None,
            "context": None,
            "policy_note": None,
            "dims": [dict(by_name[n]) for n in _DIM_ORDER if n in by_name],
        }

    # STEP 3 有效方向维 <2 → 无法定论（方向证据不足）
    if len(dir_dims) < 2:
        return {
            "state": "无法定论",
            "valid_count": valid_total,
            "missing_dims": missing,
            "direction": None,
            "context": None,
            "policy_note": None,
            "dims": [dict(by_name[n]) for n in _DIM_ORDER if n in by_name],
        }

    # STEP 4 方向 = 按冲突优先级裁决（盈利趋势 > 资金流 > 相对强度），非多数投票
    # 资金流维度取值 in/out 与方向维 up/down 归一化；优先级最高的非中性维先决，
    # 2-1/1-1 均按优先级而非票数；仅全部方向维缺失/中性才留空 → 无法定论
    ups = [n for n in dir_dims if by_name[n].get("value") in ("up", "in")]
    downs = [n for n in dir_dims if by_name[n].get("value") in ("down", "out")]
    conflict = bool(ups and downs)
    direction = None
    for n in _DIRECTION_PRIORITY:
        if not _valid(n):
            continue
        v = by_name[n].get("value")
        if v in ("up", "in"):
            direction = "up"
            break
        if v in ("down", "out"):
            direction = "down"
            break

    # STEP 5 语境 = 估值分位档（<30% 低 / ≥70% 高 / 其余含无效中）
    pct = by_name["估值分位"].get("value") if _valid("估值分位") else None
    try:
        pct_f = float(pct)
        context = "low" if pct_f < CLIMATE_PCT_LOW else ("high" if pct_f >= CLIMATE_PCT_HIGH else "mid")
    except (TypeError, ValueError):
        context = "mid"

    # STEP 6 真值表
    state: str
    if direction is None:
        state = "无法定论"
    elif direction == "up":
        state = "复苏" if context == "low" else "扩张"
    else:
        state = "降温" if context == "high" else "收缩"

    # 政策证据：独立呈现，任何情况下不改变状态判定
    policy_note = None
    policy = by_name.get("政策证据")
    if policy and policy.get("valid") and policy.get("value") == "有":
        policy_note = "政策加持（需人工核验强度）"

    return {
        "state": state,
        "valid_count": valid_total,
        "missing_dims": missing,
        "direction": direction,
        "context": context,
        "policy_note": policy_note,
        "conflict": conflict,
        "dims": [dict(by_name[n]) for n in _DIM_ORDER if n in by_name],
    }


# ---------------------------------------------------------------------------
# 五维采集（akshare，各维独立降级）
# ---------------------------------------------------------------------------

def _ak() -> "Any":
    import akshare as ak
    return ak


def _sw_tables() -> dict:
    """申万一级+二级行业表（sw_index_first_info / sw_index_second_info，官方源）。

    Returns:
        {"first": [rows], "second": [rows], "map": {行业名: (6位代码, layer)}}
        layer ∈ {"first", "second"}——同名时一级优先，二级仅补一级缺失的名称（如「半导体」）。
        失败返回 {"first": [], "second": [], "map": {}}。
    """
    try:
        ak = _ak()
        tables: dict[str, list[dict]] = {"first": [], "second": []}
        df1 = ak.sw_index_first_info()
        if df1 is not None and not df1.empty:
            tables["first"] = df1.to_dict("records")
        df2 = ak.sw_index_second_info()
        if df2 is not None and not df2.empty:
            tables["second"] = df2.to_dict("records")
        mapping: dict[str, tuple[str, str]] = {}
        for layer in ("first", "second"):
            for r in tables[layer]:
                code = str(r.get("行业代码") or "").replace(".SI", "")
                name = str(r.get("行业名称") or "")
                if code and name and name not in mapping:
                    mapping[name] = (code, layer)
        return {**tables, "map": mapping}
    except Exception as exc:
        logger.warning("climate[sw_tables]: %s", exc)
        return {"first": [], "second": [], "map": {}}


def _dim_valuation(industry: str) -> dict:
    """估值分位：行业 TTM PE 在其所属申万层级（一级/二级）全体行业分布内的分位。

    2026-08-06 探测确认：swsindex 官方源代理可达；EM push2 系不可达 → 不用 cninfo 表。
    """
    name = "估值分位"
    try:
        tables = _sw_tables()
        entry = tables["map"].get(industry)
        if not entry:
            return {"name": name, "value": None, "source": "申万行业 PE", "valid": False}
        code, layer = entry
        rows = tables[layer]
        target = None
        pes: list[float] = []
        for r in rows:
            nm = str(r.get("行业名称") or "")
            pe = r.get("TTM(滚动)市盈率")
            if pe is None:
                continue
            try:
                pes.append(float(pe))
            except (TypeError, ValueError):
                continue
            if nm == industry:
                target = float(pe)
        if target is None or not pes:
            return {"name": name, "value": None, "source": "申万行业 PE", "valid": False}
        below = sum(1 for p in pes if p < target)
        pct = below / len(pes)
        layer_label = "一级" if layer == "first" else "二级"
        return {"name": name, "value": round(pct, 3),
                "source": f"申万{layer_label}行业 TTM PE 分布", "valid": True}
    except Exception as exc:
        logger.warning("climate[估值分位] %s: %s", industry, exc)
        return {"name": name, "value": None, "source": "申万行业 PE", "valid": False}


def _dim_earnings(industry: str) -> dict:
    """盈利趋势：申万行业指数月线方向（index_hist_sw，官方源）。

    2026-08-06 探测确认：index_hist_sw 支持 day/week/month（无 quarter），
    月收益方向即盈利趋势代理；东财 stock_board_industry_hist_em 的 period 不支持"季度"且当前不可达。
    """
    name = "盈利趋势"
    try:
        entry = _sw_tables()["map"].get(industry)
        if not entry:
            return {"name": name, "value": None, "source": "申万行业指数月线", "valid": False}
        code, _ = entry
        ak = _ak()
        df = ak.index_hist_sw(symbol=code, period="month")
        if df is None or df.empty:
            return {"name": name, "value": None, "source": "申万行业指数月线", "valid": False}
        close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
        if not close_col:
            return {"name": name, "value": None, "source": "申万行业指数月线", "valid": False}
        vals = df[close_col].astype(float).tolist()
        if len(vals) < 2:
            return {"name": name, "value": None, "source": "申万行业指数月线", "valid": False}
        direction = "up" if vals[-1] >= vals[-2] else "down"
        return {"name": name, "value": direction, "source": "申万行业指数月线方向", "valid": True}
    except Exception as exc:
        logger.warning("climate[盈利趋势] %s: %s", industry, exc)
        return {"name": name, "value": None, "source": "申万行业指数月线", "valid": False}


def _dim_rs(industry: str) -> dict:
    """相对强度：申万行业指数（日线）vs 沪深300（新浪 sh000300）的 RS。

    仅取近 RS_WINDOW 个交易日计算方向——全历史（2005 起）累计涨跌会掩盖近期
    走弱信号（长端涨 300%、近 12 月下跌的板块仍会恒投 up 票）。
    """
    name = "相对强度"
    try:
        entry = _sw_tables()["map"].get(industry)
        if not entry:
            return {"name": name, "value": None, "source": "申万行业指数/沪深300", "valid": False}
        code, _ = entry
        ak = _ak()
        ind = ak.index_hist_sw(symbol=code, period="day")
        bench = ak.stock_zh_index_daily(symbol="sh000300")
        if ind is None or ind.empty or bench is None or bench.empty:
            return {"name": name, "value": None, "source": "申万行业指数/沪深300", "valid": False}
        ind_col = "close" if "close" in ind.columns else ("收盘" if "收盘" in ind.columns else None)
        if not ind_col:
            return {"name": name, "value": None, "source": "申万行业指数/沪深300", "valid": False}
        ind_closes = ind[ind_col].astype(float).tolist()
        bench_closes = bench["close"].astype(float).tolist()
        # 近期窗口：不足窗口长度时取全部可用（relative_strength 仍会做长度对齐）
        ind_closes = ind_closes[-RS_WINDOW:]
        bench_closes = bench_closes[-RS_WINDOW:]
        from lib.technical import relative_strength
        rs = relative_strength(ind_closes, bench_closes)
        if "rs_latest" not in rs:
            return {"name": name, "value": None, "source": "申万行业指数/沪深300", "valid": False}
        direction = "up" if rs["rs_latest"] > rs["rs_start"] else "down"
        return {"name": name, "value": direction,
                "source": f"申万行业指数 vs 沪深300（近 {RS_WINDOW} 交易日）",
                "window": RS_WINDOW, "valid": True}
    except Exception as exc:
        logger.warning("climate[相对强度] %s: %s", industry, exc)
        return {"name": name, "value": None, "source": "申万行业指数/沪深300", "valid": False}


def _dim_flow(industry: str) -> dict:
    """资金流：行业资金净流入方向（同花顺 stock_fund_flow_industry，净额列）。

    2026-08-06 探测确认：走 data.10jqka.com.cn 代理可达；symbol 支持 即时/3日/5日/10日/20日。
    """
    name = "资金流"
    try:
        ak = _ak()
        df = ak.stock_fund_flow_industry(symbol="即时")
        if df is None or df.empty:
            return {"name": name, "value": None, "source": "同花顺行业资金流", "valid": False}
        rows = df.to_dict("records")
        for r in rows:
            nm = str(r.get("行业") or "")
            if nm == industry:
                v = r.get("净额")
                if v is None:
                    return {"name": name, "value": None, "source": "同花顺行业资金流", "valid": False}
                return {"name": name, "value": "in" if float(v) >= 0 else "out",
                        "source": "同花顺行业资金流（净额）", "valid": True}
        return {"name": name, "value": None, "source": "同花顺行业资金流", "valid": False}
    except Exception as exc:
        logger.warning("climate[资金流] %s: %s", industry, exc)
        return {"name": name, "value": None, "source": "同花顺行业资金流", "valid": False}


def _dim_policy() -> dict:
    """政策证据：引擎不自动判定；由采集层（SOP-M1/WebSearch）引用官方文件填入。

    无官方来源时固定「未查」（valid=False，不参与有效维计数）。
    """
    return {"name": "政策证据", "value": "未查", "source": "官方来源（人工/WebSearch 填入）", "valid": False}


def build_industry_climate(industry: str) -> dict:
    """五维采集 + 规则判定。任一维失败 → 无效维度（U4 降级），不阻塞。"""
    dims = [
        _dim_valuation(industry),
        _dim_earnings(industry),
        _dim_rs(industry),
        _dim_flow(industry),
        _dim_policy(),
    ]
    card = industry_climate_card(dims)
    card["industry"] = industry
    card["disclaimer"] = LAW6A_DISCLAIMER
    return card


def format_climate_card(card: dict) -> str:
    """人类可读输出（CLI / 报告引用）。"""
    industry = card.get("industry", "")
    state = card.get("state", "?")
    lines = [f"行业景气状态卡（R5）: {industry}"]
    if card.get("direction") and card.get("context"):
        ctx_label = {"low": "估值低位", "mid": "估值中位", "high": "估值高位"}.get(card["context"], "")
        lines.append(f"状态: {state}（方向 {'↑' if card['direction'] == 'up' else '↓'} × {ctx_label}）")
    else:
        lines.append(f"状态: {state}")
    for d in card.get("dims", []):
        name = d.get("name", "?")
        if not d.get("valid"):
            lines.append(f"  - {name}: 无效维度 [来源: {d.get('source', '')}]")
            continue
        value = d.get("value")
        if name == "估值分位":
            value_s = f"{float(value) * 100:.0f}% 分位" if value is not None else "—"
        elif name == "资金流":
            value_s = "净流入" if value == "in" else "净流出"
        elif name == "政策证据":
            value_s = str(value)
        else:
            value_s = "↑" if value == "up" else "↓"
        lines.append(f"  - {name}: {value_s} [来源: {d.get('source', '')}]")
    if card.get("valid_count", 0) < CLIMATE_MIN_VALID or card.get("state") == "无法定论":
        lines.append(f"数据不完整（有效维度 {card.get('valid_count', 0)}/5）— 缺失: " +
                     "、".join(card.get("missing_dims", [])))
    if card.get("policy_note"):
        lines.append(f"  - 政策证据: {card['policy_note']}")
    lines.append(card.get("disclaimer", LAW6A_DISCLAIMER))
    return "\n".join(lines)
