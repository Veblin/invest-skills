"""股指期货数据层（v0.2.6 F 系列）——futures_daily 取数与增量回填。

数据源：Tushare fut_daily（settle/oi/oi_chg 齐，首选）+ fut_basic（合约元数据，
一次调用构造当月合约序列）；降级 sina 主力连续（口径不同：main_continuous，
source 字段标注）。现货对齐：stock_zh_index_daily（IF→sh000300 / IH→sh000016 /
IC→sh000905 / IM→sh000852），预计算 basis_pts/basis_pct/oi_change_pct 入库（P0）。

口径注记（调研方案 §2.2）：
- 基差 = settle − 现货指数收盘（15:00/15:30 时点对齐）；sina 降级用 close
- 当月 vs 主力口径不可混用——本表默认当月合约口径；sina 降级为主力连续口径
- 分红：现货为价格指数，6-7 月除权期基差"假收窄"，仅标注不调整
"""

from __future__ import annotations

import logging

from . import store
from .nums import safe_float
from .tushare_client import TushareClient

logger = logging.getLogger(__name__)

# 品种 → 现货指数（akshare stock_zh_index_daily 代码）
INDEX_MAP = {"IF": "sh000300", "IH": "sh000016", "IC": "sh000905", "IM": "sh000852"}


def _make_client() -> TushareClient:
    import os

    from . import env

    cfg = env.get_config()
    token = cfg.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    daily = cfg.get("TUSHARE_DAILY_CALL_LIMIT")
    rate_raw = os.environ.get("TUSHARE_RATE_LIMIT_PER_MINUTE")
    rate = int(rate_raw) if rate_raw and rate_raw.strip().isdigit() else 80
    return TushareClient(token=token, daily_call_limit=daily, rate_limit_per_minute=rate)


def _third_friday(ym: str) -> str:
    """'2608' → 该月第三个周五 'YYYY-MM-DD'（CFFEX 到期日兜底计算）。"""
    import datetime

    y, m = 2000 + int(ym[:2]), int(ym[2:4])
    first = datetime.date(y, m, 1)
    fri = first + datetime.timedelta(days=(4 - first.weekday()) % 7)
    return (fri + datetime.timedelta(days=14)).isoformat()


def contract_series(client: TushareClient) -> dict[str, list[tuple[str, str]]]:
    """fut_basic 元数据 → {IF: [(IF1504.CFX, '2015-04-17'), ...]}（按代码升序）。

    到期日取 last_trade_date；缺失退 last_ddate（金融期货最后交易日=最后交割日）；
    再缺失按 CFFEX 规则兜底计算该合约月第三个周五。当月合约序列据此划分
    窗口：前合约到期日 < date <= 本合约到期日（相邻合约重叠日按到期日
    边界划分，不再按月划分——月内到期日→月末的交易日归下一合约）。
    """
    df = client.query("fut_basic", exchange="CFFEX")
    if df is None or df.empty:
        raise RuntimeError("fut_basic(CFFEX) 无数据")
    series: dict[str, set[tuple[str, str]]] = {}
    for _, r in df.iterrows():
        code = str(r.get("ts_code", ""))
        if len(code) != 10 or code[6:] != ".CFX" or code[:2] not in INDEX_MAP:
            continue
        lt = str(r.get("last_trade_date") or r.get("last_ddate") or "")
        if len(lt) == 8:
            expiry = f"{lt[:4]}-{lt[4:6]}-{lt[6:8]}"
        else:
            expiry = _third_friday(code[2:6])
        series.setdefault(code[:2], set()).add((code, expiry))
    return {sym: sorted(codes) for sym, codes in series.items()}


def fetch_contract(
    client: TushareClient, contract: str,
    window_start: str, window_end: str,
) -> list[dict]:
    """单合约 fut_daily → 当月窗口内 rows（window_start < date <= window_end）。

    window_end = 本合约到期日（last_trade_date）；window_start = 前一合约到期日
    （序列首合约传 start_month 月初前一日，使月初含入）。当月口径唯一性由
    到期日边界保证：相邻合约重叠交易日的行按边界划分，每月 40% 交易日
    不再丢失（原按月过滤：到期日→月末的行属于下月合约却未被下月窗口收留）。
    """
    df = client.query("fut_daily", ts_code=contract)
    rows: list[dict] = []
    for _, r in df.iterrows():
        d = str(r.get("trade_date", ""))
        settle = safe_float(r.get("settle"))
        if len(d) != 8 or settle is None:
            continue
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if not (window_start < iso <= window_end):
            continue
        rows.append({
            "date": iso,
            "symbol": contract[:2],
            "contract": contract,
            "open": safe_float(r.get("open")),
            "high": safe_float(r.get("high")),
            "low": safe_float(r.get("low")),
            "close": safe_float(r.get("close")),
            "settle": settle,
            "oi": safe_float(r.get("oi")),
            "oi_chg": safe_float(r.get("oi_chg")),
            "source": "tushare",
        })
    return rows


def fetch_index_close_map() -> dict[str, dict[str, float]]:
    """{IF: {date: close}, ...} 四大宽基现货收盘（akshare stock_zh_index_daily）。"""
    import akshare as ak

    out: dict[str, dict[str, float]] = {}
    for sym, code in INDEX_MAP.items():
        df = ak.stock_zh_index_daily(symbol=code)
        closes: dict[str, float] = {}
        for _, r in df.iterrows():
            d = str(r["date"])[:10]
            c = safe_float(r.get("close"))
            if d and c is not None:
                closes[d] = c
        out[sym] = closes
    return out


def compute_basis(rows: list[dict], index_closes: dict[str, float]) -> list[dict]:
    """现货对齐 + 预计算 basis_pts/basis_pct/oi_change_pct（P0：引擎算好入库）。"""
    out: list[dict] = []
    for r in rows:
        sym = r["symbol"]
        idx = index_closes.get(r["date"])
        price = r["settle"] if r.get("settle") is not None else r.get("close")
        if idx is None or price is None:
            continue
        r = dict(r)
        r["basis_pts"] = round(price - idx, 2)
        r["basis_pct"] = round((price - idx) / idx * 100, 4) if idx > 0 else None
        if r.get("oi_chg") is not None and r.get("oi") is not None and r["oi"] > 0:
            prev_oi = r["oi"] - r["oi_chg"]
            if prev_oi > 0:
                pct = r["oi_chg"] / prev_oi * 100
                # 到期日 OI 归零的机械塌缩（≤−99%）不计入持仓变化（复利链会被清零）
                r["oi_change_pct"] = round(pct, 4) if pct > -99 else None
            else:
                r["oi_change_pct"] = None
        else:
            r["oi_change_pct"] = None
        out.append(r)
    return out


def compound_oi_change(
    vals: list[float | None], *, window: int = 20, min_valid: int = 18,
) -> float | None:
    """尾部 window 个日环比（oi_change_pct）复利合成 N 日持仓变化。

    口径唯一实现（run_f3 / 各消费方共用，禁止复制粘贴）：
    None 或 <= -99（到期日 OI 归零机械塌缩掩码）不计入因子也不计入有效数；
    有效因子数 < min_valid → None（防全缺失窗口伪造 0 变化）。
    返回原始百分比（不 round；调用方按需 round）。
    """
    w = vals[-window:]
    prod = 1.0
    cnt = 0
    for v in w:
        if v is not None and v > -99.0:  # NaN 比较恒 False → 自然排除
            prod *= 1.0 + v / 100.0
            cnt += 1
    if cnt < min_valid:
        return None
    return (prod - 1.0) * 100.0


def fetch_sina_fallback() -> list[dict]:
    """降级：sina 主力连续（close 口径，无结算价/换月标注——source 字段标记）。"""
    import akshare as ak

    rows: list[dict] = []
    for sym in ("IF0", "IH0", "IC0", "IM0"):
        df = ak.futures_main_sina(symbol=sym)
        for _, r in df.iterrows():
            d = str(r["日期"])[:10]
            close = safe_float(r.get("收盘价"))
            if not d or close is None:
                continue
            rows.append({
                "date": d,
                "symbol": sym[:2],
                "contract": "main_continuous",
                "open": safe_float(r.get("开盘价")),
                "high": safe_float(r.get("最高价")),
                "low": safe_float(r.get("最低价")),
                "close": close,
                "settle": None,
                "oi": safe_float(r.get("持仓量")),
                "oi_chg": None,
                "source": "sina",
            })
    return rows


def ensure_futures_daily(
    start_month: str = "2015-04", max_contracts: int = 200, *, force: bool = False,
) -> dict:
    """回填/增量：已入库合约仅回填尾部缺失窗口（断点续跑）。返回
    {fetched, failed, skipped, source}。

    force=True 全量重建（finding #1 数据修复用——旧表按月划分含每月 40%
    交易日洞，必须重建）：逐合约取数暂存内存，取数全部结束后才
    clear + 写回——tushare 主源不可用、或 max_contracts < 所需合约数时
    **清空前即中止**，旧数据保留（先清库后验源的旧实现会把 9258 行
    settle 口径数据毁掉）。

    逐合约失败不推进窗口起点：同品种下一合约上市早于前合约到期，
    其窗口覆盖失败合约缺口（失败记录 failed，后续增量运行重试）。

    Tushare 主源整体失败 → sina 降级（fill-only：仅补缺失日期，绝不覆盖
    已有行，source='sina' 标注——merge COALESCE 逐列覆盖会把 close 口径的
    基差写进 settle 口径的 tushare 行，杂交口径必须禁止）。
    """
    import datetime

    # start_month "2015-04" → "1504"（与合约代码月份段同格式，字符串可比）
    start_ym = start_month.replace("-", "")[2:]
    first_start = (datetime.date.fromisoformat(f"{start_month}-01")
                   - datetime.timedelta(days=1)).isoformat()
    existing: set[str] = set() if force else store.futures_contracts()
    skipped = 0
    last_seen: dict[str, str] = {}
    if not force:
        dates_by_sym = store.futures_dates_by_symbol()
        last_seen = {sym: max(ds) for sym, ds in dates_by_sym.items() if ds}
    try:
        client = _make_client()
        series = contract_series(client)
        if force:
            # 清空前预检：force + cap 不足会静默截断（尾部品种表已清空却
            # 0 合约入库，failed={} 退出码 0）→ 必须中止并保留旧数据
            needed = sum(1 for contracts in series.values()
                         for c, _e in contracts if c[2:6] >= start_ym)
            if max_contracts < needed:
                return {"fetched": [], "failed": {}, "skipped": 0, "source": "tushare",
                        "error": (f"force 重建需要 {needed} 个合约 > "
                                  f"max_contracts={max_contracts}，未清空现有数据")}
        index_closes = fetch_index_close_map()
        fetched: list[str] = []
        failed: dict[str, str] = {}
        staged: list[dict] = []  # force：暂存内存，全部取数成功后 clear + 写
        capped = False
        for sym, contracts in series.items():
            prev_lt = first_start  # 首合约窗口起点 = 起始月月初前一日（月初含入）
            for contract, expiry in contracts:
                if contract[2:6] < start_ym:
                    continue
                ws = prev_lt
                if contract in existing:
                    # 已入库合约仅回填尾部：窗口起点 = max(前合约到期日,
                    # 该品种最新入库日)——到期日前新增交易日不再永久缺失
                    ws = max(prev_lt, last_seen.get(sym, ""))
                if ws >= expiry:
                    skipped += 1
                    prev_lt = expiry
                    continue
                if len(fetched) >= max_contracts:
                    capped = True
                    break
                try:
                    rows = fetch_contract(client, contract, ws, expiry)
                    rows = compute_basis(rows, index_closes.get(sym, {}))
                except Exception as exc:  # noqa: BLE001 — 逐合约容错
                    failed[contract] = str(exc)
                    logger.warning("futures fetch failed %s: %s", contract, exc)
                    continue  # 不推进 prev_lt：下一合约窗口覆盖失败合约缺口
                if rows:
                    if force:
                        staged.extend(rows)
                    else:
                        store.save_futures_daily(rows)
                    fetched.append(contract)
                    last_seen[sym] = max(last_seen.get(sym, ""),
                                         max(r["date"] for r in rows))
                else:
                    skipped += 1
                prev_lt = expiry
            if capped:
                break
        if force and staged:
            store.clear_futures_daily()
            store.save_futures_daily(staged)
        return {"fetched": fetched, "failed": failed, "skipped": skipped,
                "source": "tushare"}
    except Exception as exc:  # noqa: BLE001 — 主源整体失败 → sina 降级
        # force 清空只在取数成功后执行——此处旧数据完整保留
        logger.warning("Tushare futures failed (%s), falling back to sina", exc)
        rows = fetch_sina_fallback()
        if not rows:
            return {"fetched": [], "failed": {"tushare_all": str(exc), "sina_all": "降级亦无数据"},
                    "skipped": skipped, "source": "sina", "error": str(exc)}
        # sina 降级：现货对齐逐品种（close 口径）
        idx_all = fetch_index_close_map()
        combined: list[dict] = []
        for r in rows:
            sym = r["symbol"]
            closes = idx_all.get(sym, {})
            price = r["close"]
            idx = closes.get(r["date"])
            if idx is not None and price is not None:
                r2 = dict(r)
                r2["basis_pts"] = round(price - idx, 2)
                r2["basis_pct"] = round((price - idx) / idx * 100, 4)
                combined.append(r2)
        # fill-only：已有日期的 tushare 行保持不动，sina 仅补缺（逐行口径自洽）
        existing_by_sym = store.futures_dates_by_symbol()
        combined = [r for r in combined
                    if r["date"] not in existing_by_sym.get(r["symbol"], set())]
        if combined:
            store.save_futures_daily(combined)
        return {"fetched": [f"sina_fill:{len(combined)}"], "failed": {"tushare_all": str(exc)},
                "skipped": skipped, "source": "sina", "error": str(exc)}
