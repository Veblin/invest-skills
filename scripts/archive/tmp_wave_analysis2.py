"""沪指波浪分析第 2 轮 — 两段下跌浪的斐波回撤 + 三个见底日 + 缺口/均线/BOLL + 双创对照"""
import json
import urllib.request
import pandas as pd


def fetch_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    ts_code = "sh" + symbol if symbol.startswith(("000", "999")) else "sz" + symbol
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={ts_code},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},800,qfq"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    rows = data["data"][ts_code]
    key = "day" if "day" in rows else "qfqday"
    df = pd.DataFrame(rows[key], columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df.astype({"open": float, "close": float, "high": float, "low": float, "volume": float})


def fib_of(down_wave_high: float, down_wave_low: float, current: float, label: str):
    diff = down_wave_high - down_wave_low
    print(f"\n== {label} (高 {down_wave_high:.2f} → 低 {down_wave_low:.2f}, 幅度 {(down_wave_low/down_wave_high-1)*100:.2f}%) ==")
    for r in (0.382, 0.5, 0.618, 0.786):
        lv = down_wave_low + diff * r
        print(f"  回撤 {r*100:.1f}% → {lv:8.2f}   (当前 {current:.2f} 距该位 {(current/lv-1)*100:+.2f}%)")
    # 延伸位 (向下)
    for r in (1.0, 1.272, 1.618):
        print(f"  向下延伸 {r:.3f} → {down_wave_low - diff * r:.2f}")
    return diff


def main():
    start, end = "20260601", "20260812"
    sh = fetch_index("000001", start, end)
    sh["ma5"] = sh["close"].rolling(5).mean()
    sh["ma20"] = sh["close"].rolling(20).mean()
    mid = sh["close"].rolling(20).mean()
    std = sh["close"].rolling(20).std()
    sh["boll_up"] = mid + 2 * std
    sh["boll_dn"] = mid - 2 * std
    last = sh.iloc[-1]

    print(f"== 沪指最新: 收盘 {last['close']:.2f} ({last['date'].date()}) ==")
    print(f"MA5={last['ma5']:.2f}  MA20={last['ma20']:.2f}  收盘距MA20 {(last['close']/last['ma20']-1)*100:+.2f}%")
    print(f"BOLL: 上轨 {last['boll_up']:.2f} / 中轨 {mid.iloc[-1]:.2f} / 下轨 {last['boll_dn']:.2f}")
    pos = (last["close"] - last["boll_dn"]) / (last["boll_up"] - last["boll_dn"]) * 100
    print(f"BOLL 位置 {pos:.1f}% (0%=下轨 100%=上轨)")

    # 三见底日验证: 找 7/17-7/21, 7/28-8/4 区间内每日 low
    print("\n== 见底日区间逐日 low ==")
    for tag, lo, hi in (("7/20 见底", "2026-07-16", "2026-07-22"),
                        ("7/30 见底", "2026-07-28", "2026-08-03"),
                        ("8/3 见底", "2026-07-31", "2026-08-05")):
        seg = sh[(sh["date"] >= lo) & (sh["date"] <= hi)]
        if len(seg):
            print(f"  {tag}: " + " ".join(f"{d.date().strftime('%m-%d')}:{l:.0f}" for d, l in zip(seg["date"], seg["low"])))

    # 波浪2: 4175.35 -> 3745.17
    d3 = fib_of(4175.35, 3745.17, last["close"], "第二段下跌浪(6/23高 4175.35 → 7/17低 3745.17)")
    # 当前反弹幅度
    print(f"\n当前反弹 3745.17→{last['close']:.2f} = {(last['close']/3745.17-1)*100:.2f}%")
    print(f"反弹回撤占比 = {(last['close']-3745.17)/d3*100:.1f}%")
    # 5浪杀跌目标 (以 4258.86→3927.85 一浪 = 331.01 为标尺)
    w1 = 4258.86 - 3927.85
    print(f"\n== 若走第5浪(最差走势): 以3745.17为第4浪低点(实测7/17), 5浪目标 ==")
    for k in (0.618, 1.0, 1.272, 1.618):
        print(f"  5浪 = {k}×一浪({w1:.1f}) → {3745.17 - w1*k:.2f}")
    print(f"  5浪 = 一浪等长 → {3745.17 - w1:.2f}")

    # 缺口位置
    print("\n== 缺口 vs 当前收盘 ==")
    for i in range(1, len(sh)):
        prev, cur = sh.iloc[i - 1], sh.iloc[i]
        if cur["low"] > prev["high"]:
            print(f"  {cur['date'].date()} 向上缺口 {prev['high']:.2f}~{cur['low']:.2f} (距收盘 {(prev['high']/last['close']-1)*100:+.2f}%)")
        elif cur["high"] < prev["low"]:
            print(f"  {cur['date'].date()} 向下缺口 {cur['high']:.2f}~{prev['low']:.2f} (距收盘 {(cur['high']/last['close']-1)*100:+.2f}%)")

    # 双创对照
    print("\n" + "=" * 50)
    for sym, name, doc_level in (("399006", "创业板指", "3750-3800 (布林下轨)"), ("000688", "科创50", "1750-1850")):
        df = fetch_index(sym, "20260401", end)
        df["ma20"] = df["close"].rolling(20).mean()
        mid2 = df["close"].rolling(20).mean()
        std2 = df["close"].rolling(20).std()
        df["boll_dn"] = mid2 - 2 * std2
        l2 = df.iloc[-1]
        pos2 = (l2["close"] - l2["boll_dn"]) / (2 * std2.iloc[-1]) * 100
        print(f"\n== {name} 最新收盘 {l2['close']:.2f} ({l2['date'].date()}) 文档预期: {doc_level} ==")
        print(f"  MA20={l2['ma20']:.2f} 偏离 {(l2['close']/l2['ma20']-1)*100:+.2f}%  距20日线 {(l2['close']/l2['ma20']-1)*100:+.2f}%")
        print(f"  BOLL下轨 {l2['boll_dn']:.2f}, 位置 {pos2:.1f}%")


if __name__ == "__main__":
    main()
