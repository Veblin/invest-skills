"""A 股市场脉冲指标抓取（akshare 直连会话）— 供各 skill 共享。

历史：两融余额抓取在 journal market_microstructure._fetch_margin 与
stock collector/_orchestrate 降级路径各有一份（同 API 同 wrapper）。
统一收敛至此；列解析/错误约定留在调用方。
"""

from __future__ import annotations


def fetch_margin_account_info() -> "pd.DataFrame | None":
    """两融账户余额（akshare stock_margin_account_info）。

    惰性导入 akshare 与 lib.proxy（避免 import 图依赖）；空结果返回 None；
    异常上抛由调用方 try/except 处理。
    """
    import akshare as ak
    from lib.proxy import akshare_direct_session

    with akshare_direct_session():
        df = ak.stock_margin_account_info()
    if df is None or df.empty:
        return None
    return df
