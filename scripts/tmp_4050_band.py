"""4050 关口带历史触达行为统计 — 验证'4050 作为参考位有效'"""
import json
import urllib.request
import pandas as pd

url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,2024-01-01,2026-08-12,800,qfq"
with urllib.request.urlopen(url, timeout=30) as r:
    data = json.loads(r.read().decode("utf-8"))
rows = data["data"]["sh000001"]
key = "day" if "day" in rows else "qfqday"
df = pd.DataFrame(rows[key], columns=["date", "open", "close", "high", "low", "volume"])
df["date"] = pd.to_datetime(df["date"])
df = df.astype({"open": float, "close": float, "high": float, "low": float})

# 1. 区间中值计算
print("== 区间中值 ==")
print(f"(3900+4200)/2 = {(3900+4200)/2:.1f}")
print(f"3900 是100整数倍: {3900%100==0}  4200 是100整数倍: {4200%100==0}  4050 是50整数倍: {4050%50==0}")

# 2. 4050 关口带定义 ±0.5%
band_lo, band_hi = 4050 * 0.995, 4050 * 1.005
print(f"\n== 关口带: [{band_lo:.2f}, {band_hi:.2f}] == 全样本 {len(df)} 交易日 ({df.iloc[0]['date'].date()} ~ {df.iloc[-1]['date'].date()})")

# 3. 触达统计: 收盘进入带内 或 盘中触及带内
close_hits = df[(df["close"] >= band_lo) & (df["close"] <= band_hi)]
touch_hits = df[(df["high"] >= band_lo) & (df["low"] <= band_hi)]
print(f"收盘落在带内: {len(close_hits)} 日")
print(f"盘中触及带内: {len(touch_hits)} 日")

# 4. 触达后行为: 收盘进入带内的日子, 看随后 1/3/5 日
print("\n== 收盘进入带内后 1/3/5 日行为 ==")
for name, hits in (("收盘入带", close_hits), ("盘中触带", touch_hits)):
    fwd = {1: [], 3: [], 5: []}
    broke_up = 0
    for idx in hits.index:
        for n in (1, 3, 5):
            if idx + n < len(df):
                fwd[n].append(df.iloc[idx + n]["close"] / df.iloc[idx]["close"] - 1)
        # 5 日内是否上破带上沿
        if idx + 5 < len(df):
            if df.iloc[idx + 1 : idx + 6]["high"].max() > band_hi:
                broke_up += 1
    n_eff = len(hits)
    print(f"\n-- {name} (n={n_eff}) --")
    for n in (1, 3, 5):
        if fwd[n]:
            s = pd.Series(fwd[n])
            print(f"  后{n}日: 均值 {s.mean()*100:+.2f}%  中位数 {s.median()*100:+.2f}%  胜率 {(s>0).mean()*100:.0f}%  最大 {s.max()*100:+.1f}%  最小 {s.min()*100:+.1f}%")
    print(f"  5日内上破带上沿 {broke_up}/{n_eff} = {broke_up/n_eff*100:.0f}%")

# 5. 触达明细 (最近 10 次)
print("\n== 最近 8 次收盘进入带内明细 ==")
for idx in close_hits.index[-8:]:
    r_ = df.iloc[idx]
    print(f"  {r_['date'].date()}  收盘 {r_['close']:.2f}  (高 {r_['high']:.2f} / 低 {r_['low']:.2f})")

# 6. 当前状态
last = df.iloc[-1]
print(f"\n== 当前 ==")
print(f"最新收盘 {last['close']:.2f} ({last['date'].date()}), 距 4050: {(last['close']/4050-1)*100:+.2f}%")
print(f"距关口带下沿 {band_lo:.2f}: {(last['close']/band_lo-1)*100:+.2f}%")

# 7. 无条件基线（E-001 对照基准：全样本 5 日持有收益）
#    scenario-plans.md E-001 基线「胜率 55.18% / 均值 +0.26%」来源
fwd5 = [df.iloc[i + 5]["close"] / df.iloc[i]["close"] - 1 for i in range(len(df) - 5)]
fwd5_s = pd.Series(fwd5)
print(f"\n== 无条件基线（5 日持有，n={len(fwd5)}）==")
print(f"  均值 {fwd5_s.mean()*100:+.2f}%  胜率 {(fwd5_s>0).mean()*100:.2f}%")
print(f"  收盘入带后 5 日差 = 入带均值 - 基线均值（与 E-001 对照口径一致）")
