#!/usr/bin/env python3
"""H1 回测 — 见底日分组强弱（ABCD §3.2 H1 行，预注册见
skills/lib/references/backtest_prereg/H1_预注册.md）。

用法:
  uv run python scripts/backtest_h1.py                # 输出 docs/data/H1_backtest_result.json

成分池：方案 A 申万三级成分（legulegu 直连：半导体材料 850813.SI / 设备 850818.SI；
MLCC/PCB/机器人按 sw_index_third_info 名称匹配，失效退方案 B 人工清单）。
组构造：上证主板（60 前缀）/ 创业板（300/301）/ 科创板（688）。
检验：+5/+10/+20 市场调整超额（全 A 等权）+ 三组两两 permutation（描述性）。
输出: JSON。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen

_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    str(_ROOT / "skills" / "lib"),
    str(_ROOT / "skills"),
    str(_ROOT / "skills" / "invest-a-stock" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest import describe, market_adjusted, permutation_test  # noqa: E402

BOTTOM_DATES = ("2026-07-20", "2026-07-30", "2026-08-03")
HORIZONS = (5, 10, 20)

# 方案 B 人工核验清单（直播方向板块代表标的；输出标注"人工清单"）
MANUAL_POOL = {
    "mlcc": ["300408", "000636", "002859", "603989", "002484", "002138", "600563"],
    "pcb": ["002463", "002916", "600183", "002938", "300476", "603228"],
    "robot": ["002747", "688017", "300124", "603666", "002472", "300023", "688305"],
}

# 申万三级名称关键词（sw_index_third_info 匹配用；失败退人工清单）
SW3_KEYWORDS = {
    "mlcc": ["被动元件", "电子元件"],
    "pcb": ["印制电路板"],
    "robot": ["机器人", "自动化设备"],
}


def fetch_sw3_constituents(industry_code: str) -> list[str]:
    """legulegu 直连申万成分，失败返回 []。

    注意：urllib 裸请求 403（反爬），需 requests + 浏览器 UA；
    6 位数字 token 含指数代码本身，按「非 000xxx/8xxxxx 指数代码」粗滤。
    """
    import requests

    url = f"https://legulegu.com/stockdata/index-composition?industryCode={industry_code}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=30,
        )
        codes = re.findall(r"\b(\d{6})\b", r.text)
        # 过滤指数代码：85xxxx（申万）/93xxxx（中证类）/399xxx（深证指数）/
        # 已知宽基指数代码（000 前缀个股如 000636 保留）
        _INDEX_CODES = {"000001", "000016", "000300", "000905", "000852", "000985", "000688"}
        stock_codes = {
            c for c in codes
            if not c.startswith(("85", "93", "39")) and c != industry_code.split(".")[0]
            and c not in _INDEX_CODES
        }
        return sorted(stock_codes)
    except Exception:
        return []


def fetch_sw3_code_by_name(keywords: list[str]) -> list[str]:
    """sw_index_third_info 名称匹配 → 申万三级代码列表。"""
    import akshare as ak

    try:
        df = ak.sw_index_third_info()
        codes = []
        for _, r in df.iterrows():
            name = str(r.get("指数名称") or r.get("名称") or "")
            code = str(r.get("指数代码") or r.get("代码") or "")
            if any(kw in name for kw in keywords):
                codes.append(code)
        return codes
    except Exception:
        return []


def build_pool() -> dict[str, list[str]]:
    """方向池：{方向: [6 位代码]}（方案 A 为主，方案 B 人工清单兜底）。"""
    pool: dict[str, list[str]] = {}
    # 半导体材料/设备：SW3 直连成分（已验证可用）
    mat = fetch_sw3_constituents("850813.SI")
    eqp = fetch_sw3_constituents("850818.SI")
    pool["semiconductor_materials"] = mat or MANUAL_POOL["mlcc"]  # 兜底语义不同，见标注
    pool["semiconductor_equipment"] = eqp
    # MLCC/PCB/机器人：名称匹配 → legulegu 成分
    for key, keywords in SW3_KEYWORDS.items():
        codes = fetch_sw3_code_by_name(keywords)
        members: set[str] = set()
        for c in codes:
            members.update(fetch_sw3_constituents(f"{c}.SI"))
        pool[key] = sorted(members) or MANUAL_POOL[key]
    # 人工清单合并（方案 B 兜底；报告标注"人工清单+申万成分混合"）
    for key, manual in MANUAL_POOL.items():
        pool[key] = sorted(set(pool.get(key, [])) | set(manual))
    pool["_source_note"] = "申万三级成分（legulegu 直连）+ 人工核验清单兜底（MLCC/PCB/机器人）"
    return pool


def board_of(code: str) -> str | None:
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("60", "601", "603", "605", "600")):
        return "sh_main"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="H1 见底日分组回测")
    parser.add_argument("--out", default=str(_ROOT / "docs" / "data" / "H1_backtest_result.json"))
    args = parser.parse_args()

    from lib import store

    dates = sorted(store.market_daily_dates())
    if dates[-1] < BOTTOM_DATES[-1]:
        print(f"market_daily 最新 {dates[-1]} 早于见底日，请先回填")
        return 1

    pool = build_pool()
    all_codes = sorted({c for k, v in pool.items() if isinstance(v, list) and not k.endswith("_source") for c in v})
    pool["_n_total"] = len(all_codes)

    # 市场等权日收益（同 H2 口径）
    mkt_ret: dict[str, float] = {}
    for d in dates:
        rows = store.load_market_daily(dates=[d])
        pcts = [float(r["pct_chg"]) for r in rows if r.get("pct_chg") is not None]
        if pcts:
            mkt_ret[d] = sum(pcts) / len(pcts)

    # 每只池内股票：见底日收盘 + 前向收益
    results: dict = {
        "meta": {"pool_size": len(all_codes), "bottom_dates": list(BOTTOM_DATES)},
        "groups": {},
        "permutation": {},
    }
    group_rets: dict[str, dict[str, list[float]]] = {}  # (bottom, board) → (horizon → adj rets)
    for d in BOTTOM_DATES:
        rows = store.load_market_daily(dates=[d])
        close_map = {r["ts_code"].split(".")[0]: r.get("close") for r in rows}
        idx = dates.index(d)
        for code in all_codes:
            board = board_of(code)
            if board is None or close_map.get(code) is None:
                continue
            entry = float(close_map[code])
            for h in HORIZONS:
                if idx + h >= len(dates):
                    continue
                future = store.load_market_daily(dates=[dates[idx + h]])
                fc = {r["ts_code"].split(".")[0]: r.get("close") for r in future}.get(code)
                if fc is None:
                    continue
                ev_r = (fc / entry - 1) * 100.0
                mkt_cum = 1.0
                for k in range(1, h + 1):
                    dk = dates[idx + k]
                    if dk not in mkt_ret:
                        break
                    mkt_cum *= (1 + mkt_ret[dk] / 100.0)
                else:
                    adj = ev_r - (mkt_cum - 1) * 100.0
                    key = (d, board)
                    group_rets.setdefault(key, {}).setdefault(h, []).append(adj)

    for (d, board), horizons in sorted(group_rets.items()):
        gkey = f"{d}|{board}"
        results["groups"][gkey] = {}
        for h in HORIZONS:
            vals = horizons.get(h, [])
            if len(vals) < 3:
                results["groups"][gkey][f"+{h}"] = {"n": len(vals), "error": "样本 <3"}
                continue
            desc = describe(vals)
            results["groups"][gkey][f"+{h}"] = {
                "n": desc["n"],
                "mean_pct": desc["mean_daily_pct"],
                "median_pct": desc["median_daily_pct"],
                "win_rate": desc["up_prob"],
            }

    # 三见底日组间 permutation（跨 board 合并：同日全部方向成分）
    bottom_agg: dict[str, dict[int, list[float]]] = {}
    for (d, _b), horizons in group_rets.items():
        for h, vals in horizons.items():
            bottom_agg.setdefault(d, {}).setdefault(h, []).extend(vals)
    for h in HORIZONS:
        results["permutation"][f"+{h}"] = {}
        for a in BOTTOM_DATES:
            for b in BOTTOM_DATES:
                if a >= b:
                    continue
                va = bottom_agg.get(a, {}).get(h, [])
                vb = bottom_agg.get(b, {}).get(h, [])
                if len(va) < 3 or len(vb) < 3:
                    results["permutation"][f"+{h}"][f"{a}_vs_{b}"] = {"error": "样本 <3"}
                    continue
                perm = permutation_test(va, vb, n_perm=2000, seed=42)
                results["permutation"][f"+{h}"][f"{a}_vs_{b}"] = {
                    "n_a": len(va), "n_b": len(vb),
                    "mean_a": sum(va) / len(va), "mean_b": sum(vb) / len(vb),
                    "p_value": perm["p_value"],
                    "note": "n=1 组截面（描述性，功效受限）",
                }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 已写入 {out_path}")
    for g, v in sorted(results["groups"].items()):
        line = f"{g}:"
        for h, hv in v.items():
            if isinstance(hv, dict) and "mean_pct" in hv:
                line += f" {h}: {hv['mean_pct']:+.2f}% (n={hv['n']})"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
