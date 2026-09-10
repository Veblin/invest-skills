#!/usr/bin/env python3
"""商品期货→股票联动扫描（invest-a-futures-link）。

对 references/commodity_map.yaml 每条链：取期货主力连续与映射股票近 ~40 交易日
收盘，计算 20 日/5 日变化率 + 当前价 vs MA20 位置，输出三层对照表：
期货方向 vs 股票方向 → 共振（同向）/ 背离（反向）/ 分化（期货动股票不动）。

用法：
    cd "${INVEST_SKILLS_ROOT:-.}" && uv run python skills/invest-a-futures-link/scripts/futures_link.py
    # 输出 reports/commodity-link/{YYYY-MM-DD}.md

参数：
    --map PATH       映射表路径（默认 references/commodity_map.yaml）
    --no-out         不落盘
    --out-dir PATH   输出目录（默认 reports/commodity-link）

口径与边界：
    - 数据源：akshare futures_main_sina（期货）+ stock_zh_a_daily（新浪日线，股票）
    - 外盘（ICE/LME/CMX）未接入 → 外盘-内盘-股票三层中的外盘层暂缺，SKILL 流程
      用 L2 WebSearch 人工补充；本脚本只做「内盘期货 vs 股票」
    - relation=cost_inverse 的链（期货为成本端）输出标注「反向链」，方向判定取反
    - P0：全部变化率/统计由 pandas 计算，禁止心算；数值仅供研究，无买卖建议

退出码：0 正常（含部分链解析失败）；2 致命错误。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys

import yaml


def _ensure_lib_on_path() -> None:
    """共享库引导（R1 审查 F3）：单体仓库取 skills/lib；包内运行取 <pkg>/scripts/lib。

    包内共享模块会被 builder 重写为 ``lib.<name>`` 并拷入 ``scripts/lib``。
    """
    here = pathlib.Path(__file__).resolve()
    for cand in (here.parents[2] / "lib", here.parents[1] / "scripts" / "lib"):
        if cand.is_dir():
            sys.path.insert(0, str(cand))
            return


_ensure_lib_on_path()
from skill_paths import default_out_dir, default_reference  # noqa: E402

# 默认 map：两种布局下均为 <skill>/references/commodity_map.yaml（原 parents[3]
# 假设在包内运行时指向包外 → 首次运行 FATAL）
_DEFAULT_MAP = default_reference(__file__, "references/commodity_map.yaml")

# ---------- 纯计算 ----------

def series_stats(closes: list[float]) -> dict:
    """收盘序列 → {last, chg5_pct, chg20_pct, vs_ma20_pct}。P0：pandas 计算。

    样本不足：5 日变化率需 ≥6 点、20 日需 ≥21 点，不足返回 None 字段。
    """
    import pandas as pd

    if not closes:
        return {"last": None, "chg5_pct": None, "chg20_pct": None, "vs_ma20_pct": None}
    s = pd.Series(closes, dtype=float).dropna()   # R0 审查：NaN 收盘（停牌/缺行）不得渲染 +nan
    if s.empty:
        return {"last": None, "chg5_pct": None, "chg20_pct": None, "vs_ma20_pct": None}
    out: dict = {"last": round(float(s.iloc[-1]), 2)}
    out["chg5_pct"] = round(float(s.iloc[-1] / s.iloc[-6] - 1) * 100, 2) if len(s) >= 6 else None
    out["chg20_pct"] = round(float(s.iloc[-1] / s.iloc[-21] - 1) * 100, 2) if len(s) >= 21 else None
    ma20 = s.tail(20).mean()
    out["vs_ma20_pct"] = round(float(s.iloc[-1] / ma20 - 1) * 100, 2) if len(s) >= 20 else None
    return out


_FLAT_BAND = 0.5  # |变化率| <= 0.5% 视为平盘（死区），避免单侧比较误标背离


def _dir(x: float) -> str:
    """变化率方向三态：up / down / flat（±0.5% 死区内为 flat）。"""
    if x > _FLAT_BAND:
        return "up"
    if x < -_FLAT_BAND:
        return "down"
    return "flat"


def judge(fut: dict, stk: dict, inverse: bool) -> str:
    """方向判定：取 20 日变化率（不足取 5 日），三态方向组合判共振/背离/分化。

    inverse=True（cost_inverse 链）时股票方向取反后再判。
    死区语义（v0.3.0 修复，review F3）：±0.5% 内平盘不参与方向——
    平盘 vs 强动 = 分化（非背离），两侧均平盘 = 平盘。
    """
    f = fut["chg20_pct"] if fut["chg20_pct"] is not None else fut["chg5_pct"]
    s = stk["chg20_pct"] if stk["chg20_pct"] is not None else stk["chg5_pct"]
    if f is None or s is None:
        return "样本不足"
    s_eff = -s if inverse else s
    df_, ds = _dir(f), _dir(s_eff)
    if df_ == "flat" and ds == "flat":
        return "平盘"
    if df_ == ds:  # up/up 或 down/down
        return f"共振{'↑' if df_ == 'up' else '↓'}{'(成本反向)' if inverse else ''}"
    if df_ in ("up", "down") and ds in ("up", "down"):
        return "背离"
    return "分化"  # 单侧平盘

# ---------- 取数 ----------

def fetch_futures(ak, symbol: str) -> list[float]:
    """新浪主力连续收盘近 45 点；取数异常（网络/限流 SSL EOF）返回 []——
    单链失败不得中止全扫（R0 审查 F2：原实现无守卫，一条链异常裸崩全进程）。"""
    try:
        df = ak.futures_main_sina(symbol=symbol)
        if df is None or df.empty:
            return []
        return [float(v) for v in df["收盘价"].tolist()[-45:]]
    except Exception:  # noqa: BLE001 — 网络异常按空链降级，由 warnings 呈现
        return []


def resolve_relation(chain: dict, stock: dict) -> str:
    """链内成员 relation 可覆盖链默认（R0 审查 F11）：原油链上游(中石油) vs
    炼化(中石化/荣盛) 方向相反，单一链级 relation 无法表达混合成员。"""
    return str(stock.get("relation") or chain.get("relation") or "positive")


_TS_CLIENT = None  # 模块级缓存（R0 审查：原实现每次兜底调用新建 TushareClient）


def _ts_client():
    """惰性加载 TushareClient；不可用返回 None（哨兵 False 防重复尝试）。"""
    global _TS_CLIENT
    if _TS_CLIENT is None:
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
            from invest_path import ensure_invest_a_scripts_on_path  # noqa: PLC0415

            ensure_invest_a_scripts_on_path()
            from lib.tushare_client import TushareClient  # noqa: PLC0415

            _TS_CLIENT = TushareClient()
        except Exception:  # noqa: BLE001
            _TS_CLIENT = False
    return _TS_CLIENT or None


def fetch_stock(ak, code: str) -> tuple[list[float], str]:
    """returns (closes, source)，source ∈ {"新浪", "tushare", "none"}。

    新浪日线优先，失败降级 tushare daily（同口径不复权收盘；2026-09-08 新浪对
    连续请求限流 SSL EOF，故加兜底 + 0.4s 间隔）。逐行标注实际数据源
    （R0 审查 F14：降级 tushare 时报告不得仍署「新浪」）。
    """
    import time

    sym = ("sz" if code.startswith(("0", "3")) else "sh") + code
    try:
        df = ak.stock_zh_a_daily(symbol=sym, start_date=(_dt.date.today() - _dt.timedelta(days=120)).strftime("%Y%m%d"),
                                 end_date=_dt.date.today().strftime("%Y%m%d"))
        closes = [float(v) for v in df["close"].tolist()[-45:]] if df is not None and not df.empty else []
        if len(closes) >= 6:
            time.sleep(0.4)
            return closes, "新浪"
    except Exception:
        pass
    client = _ts_client()
    if client is None:
        return [], "none"
    try:
        ts_code = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
        df = client.query("daily", fields="trade_date,close",
                          ts_code=ts_code,
                          start_date=(_dt.date.today() - _dt.timedelta(days=120)).strftime("%Y%m%d"),
                          end_date=_dt.date.today().strftime("%Y%m%d"))
        if df is None or df.empty:
            return [], "none"
        return [float(v) for v in df.sort_values("trade_date")["close"].tolist()[-45:]], "tushare"
    except Exception:
        return [], "none"



# ---------- 渲染 ----------

def render_md(rows: list[dict], warnings: list[str], date: str) -> str:
    lines = [
        f"# 🔗 商品期货 → 股票联动扫描 — {date}",
        "",
        "> 数据源：期货 futures_main_sina（新浪）；股票 stock_zh_a_daily（新浪）为主、"
        "tushare daily 兜底——实际源逐行在「股票源」列标注。",
        "> 变化率 = 最新收盘 vs 5/20 个交易日前收盘（pandas 计算）。",
        "> 反向链（成本端）：期货涨对股票为负向，方向判定已取反标注；混合链成员级覆盖。",
        "> 外盘层（ICE/LME/CMX）未接入——需外盘对照时用 L2 检索人工补。",
        "> 研究工具，非决策工具，不含任何买卖建议。",
        "",
        "| 链 | 商品 | 期货20d% | 期货5d% | 期货vs MA20% | 股票 | 股票20d% | 股票5d% "
        "| 股票vs MA20% | 股票源 | 判定 |",
        "|----|------|---------|---------|-------------|------|---------|---------|"
        "-------------|--------|------|",
    ]
    degraded = False
    for r in rows:
        mark = " *" if r.get("window5d") else ""
        degraded = degraded or bool(r.get("window5d"))
        lines.append(
            f"| {r['label']} | {r['commodity']} | {_fmt(r['fut']['chg20_pct'])} "
            f"| {_fmt(r['fut']['chg5_pct'])} | {_fmt(r['fut']['vs_ma20_pct'])} "
            f"| {r['stock']} | {_fmt(r['stk']['chg20_pct'])} | {_fmt(r['stk']['chg5_pct'])} "
            f"| {_fmt(r['stk']['vs_ma20_pct'])} | {r.get('stk_src', '-')} "
            f"| {r['judge']}{mark} |"
        )
    if degraded:
        lines.append("")
        lines.append("> 判定带 * = 该行样本不足 20 个交易日，降级为 5 日口径（已标注，非 20 日结论）。")
    if warnings:
        lines.append("")
        lines.append("**⚠️ 取数失败（需检查网络或更新 commodity_map.yaml）：** " + "、".join(warnings))
    lines.append("")
    lines.append("> 声明：本报告为价格联动事实快照，不构成投资建议。")
    return "\n".join(lines)


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", type=str, default=_DEFAULT_MAP)
    ap.add_argument("--no-out", action="store_true")
    ap.add_argument("--out-dir", type=str, default=default_out_dir(__file__, "commodity-link"))
    args = ap.parse_args()

    try:
        return _run(args)
    except (FileNotFoundError, yaml.YAMLError, KeyError, TypeError, ImportError, OSError) as exc:
        # 致命错误（映射表缺失/损坏/结构错）：按 docstring 契约返回 2，不裸 traceback
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    with open(args.map, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    chains = cfg["chains"]

    import akshare as ak

    today = _dt.date.today().strftime("%Y-%m-%d")
    rows: list[dict] = []
    warnings: list[str] = []
    stk_cache: dict[str, tuple[list[float], str]] = {}   # 同码跨链不重复取数（002714 等）
    for ch in chains:
        try:
            fut = series_stats(fetch_futures(ak, ch["symbol"]))
        except Exception as exc:  # noqa: BLE001 — 双重保险：单链期货异常不阻断全扫（契约 0）
            warnings.append(f"{ch['label']}（期货取数异常: {type(exc).__name__}）")
            fut = series_stats([])
        for s in ch["stocks"]:
            if s["code"] not in stk_cache:
                stk_cache[s["code"]] = fetch_stock(ak, s["code"])
            closes, src = stk_cache[s["code"]]
            stk = series_stats(closes)
            inverse = resolve_relation(ch, s) == "cost_inverse"
            if fut["last"] is None or stk["last"] is None:
                warnings.append(f"{ch['label']}-{s['name']}")
            verd = judge(fut, stk, inverse)
            degraded = verd != "样本不足" and (
                fut["chg20_pct"] is None or stk["chg20_pct"] is None
            )
            rows.append({
                "label": ch["label"], "commodity": ch["commodity"], "fut": fut,
                "stock": f"{s['name']}({s['code']})", "stk": stk,
                "judge": verd, "stk_src": src, "window5d": degraded,
                "rel": "反向链" if inverse else "",
            })

    md = render_md(rows, warnings, today)
    if args.no_out:
        print(md)
    else:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{today}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"\n已落盘: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
