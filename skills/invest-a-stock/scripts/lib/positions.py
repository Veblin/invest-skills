"""持仓位置状态卡（P-1，v0.2.9）。

隔离纪律（host-docs/v0.2.9/deep-research/00-research-summary-2026-09-06.md §2-P
+ p-domain-behavioral-foundations-2026-09-05.md §2/§6）：
- 本模块只产"状态"，不产任何判断/建议/动作文本；
- 成本是计算输入，**永不进入输出行**（输出只含档位标签与派生量）——
  成本字段的显著性本身就是处置效应放大器（Frydman & Wang 2020, JF）。
- 展示层（调用方）负责弱显著渲染；档位分界为常量，调整只改 POSITION_BANDS。
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any


class PositionError(ValueError):
    """位置卡输入非法。"""


# 四档分界（纯位置描述，非建议触发阈值）：深亏 / 浅亏 / 浮盈 / 浮盈厚
POSITION_BANDS: dict[str, str] = {
    "deep_loss": "深亏",
    "loss": "浅亏",
    "gain": "浮盈",
    "gain_thick": "浮盈厚",
    "unknown": "位置不可判",
}
_LOSS_FLOOR = -0.20   # ≤ 此值 → deep_loss（含边界）
_GAIN_THICK = 0.30    # > 此值 → gain_thick


def band_for_pnl(pnl_pct: float | None) -> str:
    if pnl_pct is None or (isinstance(pnl_pct, float) and math.isnan(pnl_pct)):
        return "unknown"
    if pnl_pct <= _LOSS_FLOOR:
        return "deep_loss"
    if pnl_pct <= 0.0:
        return "loss"
    if pnl_pct <= _GAIN_THICK:
        return "gain"
    return "gain_thick"


def _days_between(a: str, b: str) -> int:
    try:
        d0 = _dt.date.fromisoformat(a)
        d1 = _dt.date.fromisoformat(b)
    except ValueError as exc:
        raise PositionError(f"日期须为 YYYY-MM-DD: {exc}") from exc
    return (d1 - d0).days


def build_position_row(*, symbol: str, price: float | None,
                       cost: float | None, buy_date: str | None,
                       today: str, name: str | None = None,
                       weight: float | None = None) -> dict[str, Any]:
    """单标的位置状态行。cost 仅作计算输入，不进输出。

    Returns keys: symbol, name, weight, pnl_pct, band, holding_days, note
    """
    if not symbol.strip():
        raise PositionError("symbol 为空")
    pnl_pct: float | None = None
    holding_days: int | None = None
    note: str | None = None
    # NaN 守卫（code-review max F4）：isnan(cost)<=0 恒 False 会穿透旧校验
    if isinstance(cost, float) and math.isnan(cost):
        raise PositionError(f"{symbol}: cost 为 NaN")
    if isinstance(price, float) and math.isnan(price):
        note = "现价为 NaN，仅能确认持仓事实"
        price = None
    if cost is None or price is None:
        note = note or "缺成本或现价，仅能确认持仓事实（无法判盈亏档位）"
    else:
        if cost <= 0:
            raise PositionError(f"{symbol}: cost 须为正数")
        if price <= 0:
            note = "现价非正，仅能确认持仓事实"
        else:
            pnl_pct = price / cost - 1.0
    if buy_date:
        holding_days = _days_between(buy_date, today)
        if holding_days is not None and holding_days < 0:
            note = "buy_date 晚于 today，请检查买入日期"
            holding_days = None
    return {
        "symbol": symbol,
        "name": name,
        "weight": weight,
        "pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
        "band": band_for_pnl(pnl_pct),
        "holding_days": holding_days,
        "note": note,
    }


def _a_share_symbol_ok(sym: str) -> bool:
    """A 股代码形态校验（review2 A-4/HK-3 防错路由）：6 位纯数字，或带 sh/sz/bj
    前缀的 6 位（sh600176）。港股 5 位码/字母代码 → False（不得喂 A 股 get_kline——
    共享 codes.zfill(6) 会把 00700 静默路由到 000700）。"""
    s = sym.lower()
    for pre in ("sh", "sz", "bj"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.isdigit() and len(s) == 6


def _validate_p1_fields(h: dict) -> tuple[dict, str | None]:
    """P-1 字段语义校验（单行）。返回 (h, error_note|None) —— h 原样返回（本函数
    不做字段修正，修正语义在调用侧），error_note 为校验结论（None=通过）。

    校验失败 → 返回错误 note（调用方整行降级），**不 raise**——review2 A-4 定稿：
    load_holdings 已宽容，P-1 语义校验在此逐行执行，坏行降级不阻塞整表。
    """
    cost = h.get("cost")
    if cost is not None:
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            return h, f"cost 非数值（{type(cost).__name__}），本行位置状态不可判"
        if math.isnan(cost) or math.isinf(cost) or cost <= 0:
            return h, "cost 非正数/NaN/Infinity，本行位置状态不可判"
    bd = h.get("buy_date")
    if bd is not None:
        if not isinstance(bd, str):
            return h, "buy_date 非字符串，本行持仓天数不可判"
        try:
            _dt.date.fromisoformat(bd)
        except ValueError:
            return h, f"buy_date 非真实日期（{bd}），本行持仓天数不可判"
    return h, None


def build_position_rows_from_holdings(holdings: list[dict], today: str | None = None) -> list[dict[str, Any]]:
    """holdings → 位置状态行。现价取最近收盘（K 线统一前复权，仅作位置参考）；
    不可得 → price=None（档位 unknown）。网络失败单标的降级，不阻塞整表。

    code-review max 两轮修复：
    - today 默认上海历 shanghai_today()（本地钟 UTC+8 以西 00:00-08:00 差一天）
    - 仅对"有 cost 的行"按 symbol 去重拉取一次 K 线（无 cost 行档位恒 unknown，不拉网络）
    - 记录现价所属交易日，窗口内停牌（现价陈旧 >3 自然日）时 note 标注
    - review2 A-4：P-1 字段逐行语义校验，非法行整行降级（note），不 raise 不静默
    - review2 HK-3：非 A 股 6 位码（港股 5 位等）不拉 A 股 K 线（防 zfill 错路由），
      仅确认持仓事实
    """
    from ._invest_path import ensure_skills_lib_on_path
    ensure_skills_lib_on_path()
    from data_bridge import get_kline  # noqa: E402
    from .shared_dates import shanghai_days_ago as _days_ago, shanghai_today

    today = today or shanghai_today()
    validated: list[tuple[dict, str | None]] = [_validate_p1_fields(h) for h in holdings]

    price_by_sym: dict[str, tuple[float | None, str | None]] = {}   # (price, price_date)
    fetch_syms = sorted({
        str(h.get("symbol", "")).strip()
        for h, err in validated
        if err is None
        and str(h.get("symbol", "")).strip()
        and h.get("cost") is not None
        and _a_share_symbol_ok(str(h.get("symbol", "")).strip())
    })
    for sym in fetch_syms:
        price, pdate = None, None
        try:
            kdim = get_kline(sym, start_date=_days_ago(10))
            data = kdim.get("data") if isinstance(kdim, dict) else None
            if isinstance(data, list) and data:
                last = max(data, key=lambda r: str(r.get("trade_date") or ""))
                pdate = str(last.get("trade_date") or "")
                raw = last.get("close")
                price = float(raw) if raw is not None else None
        except Exception:
            price = None
        price_by_sym[sym] = (price, pdate)

    rows: list[dict[str, Any]] = []
    # v0.3.0 B1：与 rows **同步**收集对应 holding。此前 `_carry_paper_keys` 按
    # 下标 zip 未过滤的 holdings，而下面遇到空 symbol 行就 `continue`（空 symbol
    # 是 `load_holdings` 明确支持的形态：现金/占位/坏行）→ 从该行起全体错位，
    # 后续每行继承**上一持仓**的 kind/account/tag，`_is_paper` 与
    # `disposition_hint` 的 paper 结论随之出错。**不可改用 symbol 键**：journal
    # 侧同 symbol 多批次（分批建仓），键不唯一。
    paper_holdings: list[dict] = []
    for h, err in validated:
        sym = str(h.get("symbol", "")).strip()
        if not sym:
            continue
        note_pre = err
        if err is None and not _a_share_symbol_ok(sym):
            note_pre = "非 A 股 6 位代码（如港股），本表仅确认持仓事实，档位以相应市场工具为准"
        price, pdate = price_by_sym.get(sym, (None, None))
        if note_pre:
            # 校验失败/非 A 股行：绕过 build_position_row 的常规计算（字符串 cost 等
            # 会 TypeError），以 None 输入构造同构降级行（band unknown），note 置校验结论
            # ——与 build_position_row 输出 schema 单源，避免平行字面量漂移
            row = build_position_row(
                symbol=sym, price=None, cost=None, buy_date=None,
                today=today, name=h.get("name"), weight=h.get("weight"),
            )
            row["note"] = note_pre
            rows.append(row)
            paper_holdings.append(h)
            continue
        row = build_position_row(
            symbol=sym, price=price, cost=h.get("cost"), buy_date=h.get("buy_date"),
            today=today, name=h.get("name"), weight=h.get("weight"),
        )
        if price is not None and pdate:
            try:
                stale_days = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(pdate)).days
            except ValueError:
                stale_days = 0
            if stale_days > 3:
                row["note"] = (row.get("note") or "").strip() + f"；现价截至 {pdate}（或停牌/数据陈旧）"
        rows.append(row)
        paper_holdings.append(h)
    # R-C03：模拟/观察仓标识须随行携带（否则 `_is_paper` 在生产路径恒 False）
    return _carry_paper_keys(rows, paper_holdings)


def _fmt_weight(raw: Any) -> str:
    """weight 渲染归一（code-review max F2）：支持 fraction(0.4)、'40%' 字符串、
    裸整数 40（百分比直觉写法，>1 按 /100 显示——不静默渲染 '4000%'）。解析失败 → '—'。"""
    if raw is None:
        return "—"
    if isinstance(raw, str):
        s = raw.strip()
        if s.endswith("%"):
            try:
                return f"{float(s[:-1].strip()) / 100:.0%}"
            except ValueError:
                return "—"
        try:
            raw = float(s)
        except ValueError:
            return "—"
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return "—"
    if math.isnan(raw):
        return "—"
    if raw > 1.0:
        return f"{raw / 100:.0%}"   # 40 → 40%（百分比单位直觉）
    return f"{raw:.0%}"


# --- R-C03 权重最大持仓的处置效应弱提示 ---------------------------------------
# 证据边界（必须随文案走）：Sui-Wang 2025 的组合权重层证据**仅限处置效应**；
# 权重非随机决定 → **相关非因果**，不得泛化到过度交易等其它偏差。
_PAPER_KEYS = ("kind", "account", "asset_type", "tag", "type")
_PAPER_TOKENS = ("模拟", "观察", "paper", "simulated", "watch")


def _weight_num(v: Any) -> float | None:
    """权重 → **归一化 fraction**（0–1）；不可解析 → None。

    ⚠️ 归一化口径必须与 `_fmt_weight`（渲染侧）**逐字一致**：裸整数 >1 视为
    百分数直觉写法（40 → 0.40）。原实现直接 `float(v)` 返回 40.0 → `disposition_hint`
    会挑**错**「权重最大」持仓（40.0 > 0.6 假象），并把 100 倍权重交给消费方
    （`{:.0%}` 渲染成 4000%）。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100.0
            except ValueError:
                return None
        try:
            v = float(s)
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
        return None
    return float(v) / 100.0 if v > 1.0 else float(v)


def _is_paper(row: dict) -> bool:
    """模拟/观察仓判定（容错多键名）。"""
    for k in _PAPER_KEYS:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).lower()
        if any(tok in s for tok in _PAPER_TOKENS):
            return True
    return False


def _carry_paper_keys(rows: list[dict[str, Any]],
                      holdings: list[dict]) -> list[dict[str, Any]]:
    """把 holdings 行中的**模拟/观察仓标识键**带进位置行（就地）。

    ⚠️ `build_position_row` 只输出 symbol/name/weight/pnl_pct/band/holding_days/note，
    把 holdings 的 `kind`/`account`/`asset_type`/`tag`/`type` 全部丢掉 → `_is_paper`
    在**生产路径**上恒 False，「模拟/观察仓单独标注」的 R-C03 分支不可达
    （此前只有手工构造 row 的测试能触发它）。按**下标**对齐（构造器逐行产出，顺序保持）。
    """
    for row, h in zip(rows, holdings or []):
        if not isinstance(h, dict):
            continue
        for k in _PAPER_KEYS:
            if h.get(k) is not None and row.get(k) is None:
                row[k] = h[k]
    return rows


def disposition_hint(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """对**组合权重最大**持仓给处置效应**弱提示**（R-C03）。

    权重全缺或全为 0 → ``None``（不给提示，也不臆造「最大」）。
    返回 ``{symbol, name, weight, paper, note}``；``note`` 为合规文案（非建议）。
    """
    weighted = [(r, _weight_num(r.get("weight"))) for r in rows or []]
    weighted = [(r, w) for r, w in weighted if w is not None and w > 0]
    if not weighted:
        return None
    top, w = max(weighted, key=lambda x: x[1])
    paper = _is_paper(top)
    # 弱显著样式：不用 ⚠️（那是强信号），强调项用**加粗**
    note = ("处置效应相关**弱提示**（Sui-Wang 2025：组合权重越大处置效应越强）。"
            "权重非随机决定——**相关非因果**；本提示**不泛化**到过度交易等其它偏差，"
            "也不构成任何操作建议。")
    if paper:
        note += (" 模拟/观察仓同样适用（Sui-Wang 2025 同文：模拟账户的偏差已存在）"
                 "——**stakes 低 ≠ 无偏差**。")
    return {"symbol": top.get("symbol"), "name": top.get("name"),
            "weight": w, "paper": paper, "note": note}


def position_table(rows: list[dict[str, Any]]) -> str:
    """渲染位置表（弱显著：档位中文 + 天数，不带盈亏数值与成本）。"""
    head = "| 标的 | 名称 | 档位 | 持仓天数 | 持仓占比 | 备注 |"
    sep = "|---|---|---|---|---|---|"
    lines = [head, sep]
    for r in rows:
        w = _fmt_weight(r.get("weight"))
        days = f"{r['holding_days']} 天" if r["holding_days"] is not None else "—"
        lines.append(
            f"| {r['symbol']} | {r.get('name') or '—'} | "
            f"{POSITION_BANDS.get(r['band'], r['band'])} | {days} "
            f"| {w} | {r.get('note') or ''} |"
        )
    lines.append("")
    lines.append("*位置状态表仅描述持仓事实（档位/天数/占比），不构成任何操作建议。*")
    # R-C03：权重最大持仓的处置效应**弱提示**——独立成行（不写进表格单元格），
    # 弱显著样式（斜体，非 ⚠️ 强信号）
    hint = disposition_hint(rows)
    if hint:
        lines.append(f"*{hint['note']}*")
    return "\n".join(lines)
