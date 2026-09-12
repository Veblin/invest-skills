"""A/H 比价透镜（T11-4 / HK-2）。

同公司 A/H 两地价差与溢价率——**研究视角参考，非套利信号**（requirements §3.2）。

```
溢价率 = A价 / (H价 × 汇率) − 1        # A价 CNY、H价 HKD、汇率 CNY per HKD
```

汇率口径（2026-09-12 实测，勿按直觉改）：
- 首选 akshare ``currency_boc_sina(symbol="港币")`` 的 ``央行中间价``——⚠️ **每 100 港元**
  计价（86.384 → 0.86384），漏除 100 会把溢价率放大近百倍；该列缺失时退
  ``中行折算价``（同为每 100 港元口径），并在 ``source`` 注明实际所用列。
- 降级 FRED ``DEXCHUS / DEXHKUS`` 交叉（CNY per HKD = CNY per USD ÷ HKD per USD）——
  日频序列**滞后约 1 周**（2026-09-12 实测最新 2026-09-04），故只作降级并标注滞后。

币种纪律（v1 既有）：东财 CURRENCY 字段对 A+H 公司**不可靠**（比亚迪 H 实测为 CNY
报表值而字段标 HKD）→ 本模块不依赖该字段，A 价与 H 价分别来自各自市场的行情源。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PER_100_HKD = 100.0   # 中行牌价口径：每 100 港元
_BOC_COLUMNS = ("央行中间价", "中行折算价")   # 按优先级
_BOC_LOOKBACK_DAYS = 30        # 显式回溯窗：不传日期时该接口返回的默认窗口**不是最新数据**
_FX_STALE_WARN_DAYS = 10       # 源日期距今超过此值 → 标注「数据陈旧」
_FX_STALE_FAIL_DAYS = 30       # 超过此值 → 视为不可得，转下一级源（防用 3 年前的汇率算溢价）


def premium_pct(a_price, h_price, fx_cny_per_hkd) -> float | None:
    """A/H 溢价率（%）= ``A / (H × 汇率) − 1``；任一输入非正/非数值 → None。

    返回 None 即「不可得」——调用方须出三态标注，**不得**伪造 0%（那会被读成
    「两地平价」这一事实断言）。数值计算全部在本函数内完成（P0）。
    """
    try:
        a = float(a_price) if a_price is not None else None
        h = float(h_price) if h_price is not None else None
        fx = float(fx_cny_per_hkd) if fx_cny_per_hkd is not None else None
    except (TypeError, ValueError):
        return None
    if a is None or h is None or fx is None:
        return None
    if a <= 0 or h <= 0 or fx <= 0:
        return None
    return (a / (h * fx) - 1.0) * 100.0


def _lag_days(date_iso: str | None) -> int | None:
    """源日期距**北京今天**的自然日数；不可解析 → None（不阻断）。"""
    if not date_iso:
        return None
    try:
        import datetime as _dt

        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from dates import shanghai_today

        t = shanghai_today()
        today = _dt.date(int(t[:4]), int(t[4:6]), int(t[6:8]))
        return (today - _dt.date.fromisoformat(date_iso)).days
    except Exception:  # noqa: BLE001 —— 陈判断失败不应阻断汇率使用
        return None


def _boc_row_date(row) -> str | None:
    """行内日期 → YYYY-MM-DD（字符串/date/datetime 均可）；不可解析 → None。"""
    v = row.get("日期")
    if v is None:
        return None
    s = str(v)[:10]
    try:
        import datetime as _dt

        return _dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _fetch_boc_raw(days_back: int = _BOC_LOOKBACK_DAYS):
    """akshare 中行牌价原始帧（按日期升序；失败 → None）。

    ⚠️ **必须显式传日期区间**（2026-09-12 真机实测教训）：不传日期时该接口返回的
    默认窗口不是最新数据——首次实现漏传，取到 **2023-11-10** 的中间价（0.91928）
    并据此算出「-13.42% 溢价」，而正确汇率（≈0.8638）下是 -7.8%：量级正确但结论
    反向的静默陈旧。另：帧序不保证，取用前一律按日期排序。
    """
    try:
        import datetime as _dt

        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from dates import shanghai_today
        from lib.proxy import akshare_direct_session

        today = _dt.date.fromisoformat(
            f"{shanghai_today()[:4]}-{shanghai_today()[4:6]}-{shanghai_today()[6:8]}")
        with akshare_direct_session():
            import akshare as ak

            df = ak.currency_boc_sina(
                symbol="港币",
                start_date=(today - _dt.timedelta(days=days_back)).strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
            )
        if df is None or getattr(df, "empty", True):
            return None
        try:
            return df.sort_values("日期").reset_index(drop=True)
        except Exception:  # noqa: BLE001 —— 列名漂移时原样返回，交由下游三态
            logger.warning("中行牌价帧无 日期 列，无法排序")
            return df
    except Exception as exc:  # noqa: BLE001 —— 降级链第一级，失败继续
        logger.warning("中行牌价取数失败：%s", exc)
        return None


def _fetch_fred_cross() -> dict | None:
    """FRED 交叉：CNY per HKD = DEXCHUS ÷ DEXHKUS（最新观测）；失败 → None。"""
    try:
        import json as _json
        import urllib.request

        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from lib.env import get_config

        key = (get_config() or {}).get("FRED_API_KEY")
        if not key:
            return None

        def _latest(series: str) -> tuple[str, float] | None:
            url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series}"
                   f"&api_key={key}&file_type=json&sort_order=desc&limit=1")
            with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 固定官方主机
                obs = _json.loads(resp.read().decode("utf-8")).get("observations") or []
            if not obs:
                return None
            from lib.nums import safe_float

            v = safe_float(obs[0].get("value"))
            return (str(obs[0].get("date")), v) if v else None

        cny = _latest("DEXCHUS")     # CNY per USD
        hkd = _latest("DEXHKUS")     # HKD per USD
        if not cny or not hkd or hkd[1] <= 0:
            return None
        return {"rate": cny[1] / hkd[1], "date": min(cny[0], hkd[0]),
                "source": "FRED DEXCHUS/DEXHKUS（交叉换算）"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED 汇率交叉失败：%s", exc)
        return None


def fetch_fx_hkd_cny() -> dict:
    """港币兑人民币汇率 → ``{rate, date, source, note}``；全失败 → ``rate=None``。

    ``rate`` 为 **CNY per HKD**（如 0.86384）。三态语义：rate=None 即不可得，
    调用方须标注原因，不得用 1.0 之类的占位值。
    """
    frame = _fetch_boc_raw()
    if frame is not None and not getattr(frame, "empty", True):
        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from lib.nums import safe_float

        row = frame.iloc[-1]
        date = _boc_row_date(row)
        lag = _lag_days(date)
        if lag is not None and lag > _FX_STALE_FAIL_DAYS:
            # 取到的是**过期窗口**（真机踩过：默认窗返回 2023 年数据）——宁可判不可得，
            # 也不用陈旧汇率算出一个看起来合理、方向可能相反的溢价率。
            logger.warning("中行牌价最新行 %s 滞后 %d 天，不采用", date, lag)
        else:
            for col in _BOC_COLUMNS:
                per100 = safe_float(row.get(col))
                if per100 and per100 > 0:
                    note = "中国银行外汇牌价口径；央行中间价为当日 9:15 发布"
                    if lag is not None and lag > _FX_STALE_WARN_DAYS:
                        note = f"⚠️ 源数据陈旧：最新行 {date}（滞后 {lag} 天）——{note}"
                    return {"rate": per100 / _PER_100_HKD, "date": date,
                            "source": f"中行牌价 {col}（每 100 港元 ÷100）",
                            "note": note}
            logger.warning("中行牌价帧无可用汇率列（列名漂移？）")

    fred = _fetch_fred_cross()
    if fred:
        fred["note"] = (f"中行牌价不可得，降级 FRED 交叉（{fred['date']} 口径，"
                        "日频序列**滞后约 1 周**，非当日汇率）")
        return fred

    return {"rate": None, "date": None, "source": None,
            "note": "汇率不可得：中行牌价与 FRED 交叉均失败——本次不出溢价率"}
