"""market_daily 取数与增量回填（v0.2.6 全市场分位数据层）。

数据源：Tushare pro.daily（全市场日线，含 amount）+ pro.daily_basic（turnover_rate），
2000 积分档可用；复用 TushareClient（80/min 自节流）。
落库：store.save_market_daily（merge 幂等）——同一天重复拉取不产生重复行。
断点续跑：ensure_market_daily 只补 store 中缺失的交易日。

口径注记（D4）：pro.daily 只含在交易中的股票，退市股缺失 → 分位与 H2 事件
统计存在幸存者偏差，消费方报告须注明。
"""

from __future__ import annotations

import logging

from . import store
from .nums import safe_float
from .tushare_client import TushareClient

logger = logging.getLogger(__name__)


def _make_client() -> TushareClient:
    import os

    from . import env  # 惰性导入：无 token 环境（D13 测试）不触发配置加载

    cfg = env.get_config()
    token = cfg.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置（env.get_config 无 token）")
    # 回填场景可经 env 抬高限额（Tushare 2000 积分档 daily 类接口 500/min）：
    # TUSHARE_DAILY_CALL_LIMIT=5000 TUSHARE_RATE_LIMIT_PER_MINUTE=300
    daily = cfg.get("TUSHARE_DAILY_CALL_LIMIT")  # env.py 已解析（默认 None → 客户端默认 500）
    rate_raw = os.environ.get("TUSHARE_RATE_LIMIT_PER_MINUTE")
    rate = int(rate_raw) if rate_raw and rate_raw.strip().isdigit() else 80
    return TushareClient(token=token, daily_call_limit=daily, rate_limit_per_minute=rate)


def fetch_market_day(date: str) -> list[dict]:
    """拉取单日全市场行（pro.daily + daily_basic 按 ts_code 合并 turnover_rate）。

    date: 'YYYYMMDD'（Tushare 口径）。返回标准化 rows（date 归一为 YYYY-MM-DD）。
    两个接口任一失败即抛（调用方决定降级）。
    """
    client = _make_client()
    daily_df = client.query("daily", trade_date=date)
    if daily_df is None or daily_df.empty:
        raise RuntimeError(f"pro.daily({date}) 无数据")
    basic_df = client.query("daily_basic", trade_date=date)
    daily = daily_df.to_dict("records")
    turnover = {
        r.get("ts_code"): safe_float(r.get("turnover_rate"))
        for r in (basic_df.to_dict("records") if basic_df is not None and not basic_df.empty else [])
    }
    rows: list[dict] = []
    for r in daily:
        ts_code = r.get("ts_code")
        close = safe_float(r.get("close"))
        if not ts_code or close is None:
            continue
        rows.append(
            {
                "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
                "ts_code": ts_code,
                "open": safe_float(r.get("open")),
                "high": safe_float(r.get("high")),
                "low": safe_float(r.get("low")),
                "close": close,
                "pre_close": safe_float(r.get("pre_close")),
                "pct_chg": safe_float(r.get("pct_chg")),
                "vol": safe_float(r.get("vol")),
                "amount": safe_float(r.get("amount")),
                "turnover_rate": turnover.get(ts_code),
            }
        )
    return rows


def ensure_market_daily(
    until_date: str | None = None,
    max_missing: int = 25,
    from_date: str | None = None,
) -> dict:
    """增量回填至指定日（默认最近交易日），只补缺失交易日。

    until_date: 'YYYYMMDD'；from_date: 起始日（全历史回填用，缺省最近 30 个交易日）；
    max_missing: 单次最多补 N 日（防配额打爆）。
    返回 {fetched: [...], failed: {date: err}, skipped_existing: int}。
    单日失败不阻塞后续日（断点续跑靠 store 已有日期集合）。
    """
    from .trade_cal import fetch_trade_cal, last_trade_dates  # 惰性导入

    if from_date:
        end = until_date or last_trade_dates(1)[0]
        trade_dates, _ = fetch_trade_cal(from_date, end)
    else:
        trade_dates = last_trade_dates(30)  # 最近 30 个交易日
    if until_date:
        trade_dates = [d for d in trade_dates if d <= until_date]
    existing = store.market_daily_dates()  # ISO YYYY-MM-DD
    # trade_cal 为 YYYYMMDD → 归一 ISO 后再比对，防格式不匹配导致重复拉取
    missing = [
        d for d in trade_dates
        if f"{d[:4]}-{d[4:6]}-{d[6:8]}" not in existing
    ][-max_missing:]

    fetched: list[str] = []
    failed: dict[str, str] = {}
    for d in missing:
        try:
            rows = fetch_market_day(d)
            store.save_market_daily(rows)
            fetched.append(d)
        except Exception as exc:  # noqa: BLE001 — 逐日容错，断点续跑
            failed[d] = str(exc)
            logger.warning("market_daily fetch failed %s: %s", d, exc)
    return {
        "fetched": fetched,
        "failed": failed,
        "skipped_existing": len(trade_dates) - len(missing),
        "latest": store.latest_market_daily_date(),
    }


def pctile_as_of_rows(days: int = 25) -> list[dict]:
    """分位计算所需的最近 N 个交易日全市场行（读取层，非取数层）。

    若库内不足 2 个交易日 → 返回 []（调用方降级 None+note，D5 由调用方处理）。
    """
    dates = sorted(store.market_daily_dates())
    if len(dates) < 2:
        return []
    recent = dates[-days:]
    return store.load_market_daily(dates=recent)
