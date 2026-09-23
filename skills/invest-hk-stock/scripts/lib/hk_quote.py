"""港股实时快照（腾讯 r_hk，v0.2.9 港股数据引入 v1）。

字段下标为 **2026-09-06 实测定稿**（r_hk00700/r_hk01211 双标的逐位对齐；社区流传
下标有 ±1 偏移与字段误标，勿按社区整理修改）：
  [1] 名称 [2] 代码 [3] 现价 [4] 昨收 [5] 今开
  [30] 时间戳(YYYY/MM/DD HH:MM:SS) [31] 涨跌额 [32] 涨跌幅% [33] 最高 [34] 最低
  [35] 收盘(=现价) [36] 成交量(股) [37] 成交额(元，实测=量×均价自洽)
  [39] PE(TTM) [44] 市值(亿 HKD，**语义随标的类型变化**，见下)
  [48] 52周高 [49] 52周低（实测：腾讯 677.7/411.0、比亚迪 114.99/71.4 各自自洽）
注：无 PB 字段（腾讯 [42]=1.76 与真实 PB ~3.1 不符）；PB 走百度/东财估值源。

⚠️ **[44] 不是恒定的「总市值」**（D3，2026-09-17 实测）：纯港股标的（00700）为总市值
（40308.8 亿 HKD，与价×总股本自洽）；**A+H 标的（03750）为 H 股部分市值**（≈1,070
亿 HKD，同刻 A 股口径总市值 14,135 亿 CNY）。两者不可并列或相加，且本引擎未采集
股本结构字段、无法自动判别标的属哪一类 → 使用该字段处**一律带口径注**，
见 ``hk_ah.MCAP_SCOPE_NOTE``。
"""
from __future__ import annotations

import re
from typing import Any

from hk_codes import parse_hk_symbol

TENCENT_HK_URL = "http://qt.gtimg.cn/q=r_hk{sym}"

# 实测定稿下标（勿改）
_IDX = {
    "name": 1, "code": 2, "price": 3, "prev_close": 4, "open": 5,
    "ts": 30, "chg_amt": 31, "chg_pct": 32, "high": 33, "low": 34,
    "close": 35, "volume": 36, "amount": 37, "pe_ttm": 39,
    "mcap_hkd_yi": 44, "high_52w": 48, "low_52w": 49,
}


def parse_tencent_hk(raw: str) -> dict[str, Any]:
    """解析 `v_r_hk00700="..."` 原始响应。字段缺失/异常 → 该键为 None（不虚构）。"""
    m = re.search(r'="([^"]*)"', raw)
    if not m:
        raise ValueError(f"腾讯港股响应格式异常: {raw[:80]!r}")
    parts = m.group(1).split("~")
    out: dict[str, Any] = {}
    for key, idx in _IDX.items():
        val = parts[idx] if idx < len(parts) else ""
        out[key] = val if val not in ("", "-") else None
    # 数字转换（None 保留）
    for key in ("price", "prev_close", "open", "chg_amt", "chg_pct",
                "high", "low", "close", "volume", "amount",
                "pe_ttm", "mcap_hkd_yi", "high_52w", "low_52w"):
        v = out.get(key)
        if v is not None:
            try:
                out[key] = float(v)
            except ValueError:
                out[key] = None
    out["currency"] = "HKD"
    out["source"] = "tencent.r_hk"
    return out


def fetch_quote(sym: str, timeout: float = 15.0) -> dict[str, Any]:
    """拉取腾讯港股快照。失败 → 抛异常由调用方降级。"""
    import requests
    code = parse_hk_symbol(sym)
    resp = requests.get(
        TENCENT_HK_URL.format(sym=code),
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    resp.encoding = "gbk"
    return parse_tencent_hk(resp.text)
