"""沪指波浪回撤位分析 — 验证直播总结"4258→3927 为一浪"的量化锚点
数据: akshare 东财日线; zigzag 摆动点 + 斐波那契回撤位 + 缺口/均线
"""
import sys
import akshare as ak
import pandas as pd

pd.set_option("display.width", 200)


def fetch_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    """腾讯行情日线 (web.ifzq.gtimg.cn) — 直连/代理均可达"""
    import json
    import urllib.request

    # sh000001 / sz399006 / sh000688
    ts_code = "sh" + symbol if symbol.startswith("000") or symbol.startswith("999") else "sz" + symbol
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={ts_code},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},800,qfq"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    rows = data["data"][ts_code]
    key = "day" if "day" in rows else "qfqday"
    cols = ["date", "open", "close", "high", "low", "volume"]
    return pd.DataFrame(rows[key], columns=cols).astype({"open": float, "close": float, "high": float, "low": float, "volume": float})


def zigzag(high: pd.Series, low: pd.Series, threshold: float):
    """摆动点检测: 反向超过 threshold(比例) 才确认前一个极值点。返回 [(idx, price, 'H'|'L')]"""
    n = len(high)
    pivots = []
    trend = 0
    last_idx = 0
    h = high.to_numpy()
    l = low.to_numpy()
    for i in range(1, n):
        if trend >= 0:
            if h[i] > h[last_idx]:
                last_idx = i
            if l[i] < l[last_idx] * (1 - threshold):
                pivots.append((last_idx, h[last_idx], "H"))
                trend = -1
                last_idx = i
                continue
        if trend <= 0:
            if l[i] < l[last_idx]:
                last_idx = i
            if h[i] > l[last_idx] * (1 + threshold):
                pivots.append((last_idx, l[last_idx], "L"))
                trend = 1
                last_idx = i
    return pivots


def fib_levels(high: float, low: float):
    """下跌浪的斐波那契回撤位(自低点向上回撤)"""
    diff = high - low
    return {r: low + diff * r for r in (0.382, 0.5, 0.618)}


def find_gaps(df: pd.DataFrame, lo_date=None, hi_date=None):
    """向上缺口: 今日 low > 昨日 high 且缺量显著"""
    gaps = []
    for i in range(1, len(df)):
        prev_h, cur_l, cur_h = df.iloc[i-1]["high"], df.iloc[i]["low"], df.iloc[i]["high"]
        if cur_l > prev_h:
            gaps.append((df.iloc[i]["date"], prev_h, cur_l, "up"))
        elif cur_h < df.iloc[i-1]["low"]:
            gaps.append((df.iloc[i]["date"], cur_h, df.iloc[i-1]["low"], "down"))
    return gaps


def main():
    start, end = "20251201", "20260812"
    df = fetch_index("000001", start, end)
    df["date"] = pd.to_datetime(df["date"])
    print(f"== 上证指数 {df.iloc[0]['date'].date()} ~ {df.iloc[-1]['date'].date()} 共 {len(df)} 个交易日 ==")

    # 均线
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    last = df.iloc[-1]
    print(f"\n最新收盘 {last['close']:.2f}  ({last['date'].date()})")
    print(f"MA5={last['ma5']:.2f}  收盘vs MA5 偏离 {(last['close']/last['ma5']-1)*100:.2f}%")
    print(f"MA20={last['ma20']:.2f}  收盘vs MA20 偏离 {(last['close']/last['ma20']-1)*100:.2f}%")

    # 摆动点 (3% 阈值)
    pivots = zigzag(df["high"], df["low"], 0.03)
    print(f"\n== zigzag 摆动点 (阈值 3%) ==")
    for idx, price, t in pivots:
        print(f"  {df.iloc[idx]['date'].date()}  {t}  {price:.2f}")

    # 找 4258 附近高点与 3927 附近低点
    print("\n== 波浪结构: 高点~4258 → 低点~3927 ==")
    wave_hi = wave_lo = None
    for i in range(len(pivots) - 1):
        if pivots[i][2] == "H" and abs(pivots[i][1] - 4258) < 30:
            wave_hi = pivots[i]
            # 向后找首个低点
            for j in range(i + 1, len(pivots)):
                if pivots[j][2] == "L":
                    wave_lo = pivots[j]
                    break
            break
    if wave_hi and wave_lo:
        hi_idx, hi_p, _ = wave_hi
        lo_idx, lo_p, _ = wave_lo
        n_days = lo_idx - hi_idx
        drop_pct = (lo_p / hi_p - 1) * 100
        print(f"高点 {hi_p:.2f} ({df.iloc[hi_idx]['date'].date()}) → 低点 {lo_p:.2f} ({df.iloc[lo_idx]['date'].date()})")
        print(f"历时 {n_days} 个交易日, 跌幅 {drop_pct:.2f}%")

        levels = fib_levels(hi_p, lo_p)
        print(f"\n== 该下跌浪的斐波那契回撤位 (自低点反弹回撤比例) ==")
        for r, lv in levels.items():
            pct_off = (last["close"] / lv - 1) * 100
            print(f"  回撤 {r*100:.1f}%  →  {lv:.2f}   (当前收盘距该位 {pct_off:+.2f}%)")
        print(f"  浪低点(0%)   →  {lo_p:.2f}")
        ext_127 = lo_p - (hi_p - lo_p) * 0.272
        ext_161 = lo_p - (hi_p - lo_p) * 0.618
        print(f"  下方延伸 1.272 →  {ext_127:.2f}   1.618 → {ext_161:.2f}")

        # 低点之后的反弹 vs 回撤位
        after = df.iloc[lo_idx:]
        rebound_high = after["high"].max()
        rebound_high_date = after.loc[after["high"].idxmax(), "date"]
        rebound_pct = (rebound_high / lo_p - 1) * 100
        print(f"\n低点后最高反弹至 {rebound_high:.2f} ({rebound_high_date.date()}), 反弹幅度 {rebound_pct:.2f}%")
        for r, lv in levels.items():
            if rebound_high >= lv:
                print(f"  → 已触及回撤 {r*100:.1f}% 位 ({lv:.2f})")
            else:
                print(f"  → 未触及回撤 {r*100:.1f}% 位 ({lv:.2f})")

        # 最近一次摆动 (低点后)
        print(f"\n== 低点之后的摆动结构 ==")
        for idx, price, t in pivots:
            if idx > lo_idx:
                print(f"  {df.iloc[idx]['date'].date()}  {t}  {price:.2f}")
    else:
        print("未在阈值 3% 下找到 ~4258/~3927 摆动点, 尝试 2% 阈值...")
        pivots2 = zigzag(df["high"], df["low"], 0.02)
        for idx, price, t in pivots2:
            print(f"  {df.iloc[idx]['date'].date()}  {t}  {price:.2f}")

    # 缺口
    print("\n== 近期缺口 (12月至今) ==")
    for d, lo, hi, kind in find_gaps(df)[-8:]:
        print(f"  {d.date()}  {'向上' if kind=='up' else '向下'}缺口 {lo:.2f} ~ {hi:.2f}")


if __name__ == "__main__":
    main()
