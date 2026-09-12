"""股票池构建（DS-1 / T12-2）——纯函数，不联网。

中过滤第 1/2 条（设计 §2.2）：
- 市场 = 沪深主板 + 创业板 + 科创板（**默认排除北交所**，`with_bj=True` 可开）
- 排除 ST / *ST / 退市整理（名称前缀 / 关键字匹配）

机构覆盖池 ~2000+ 的 v0 代理口径即「全 A 主板+创业+科创」。
"""
from __future__ import annotations

_BJ_MARKET = "北交所"
_MISSING_INDUSTRY = "—"


def is_st(name: str) -> bool:
    """ST / *ST / 退市整理 判定。

    「ST」须在**名称开头**（`*ST` 去掉星号后同样成立）；退市整理以「退市」二字识别
    （不用单字「退」——正常名称含「退」不应误杀）。
    """
    if not name:
        return False
    n = str(name).strip()
    return n.replace("*", "").upper().startswith("ST") or "退市" in n


def market_of(row: dict) -> str:
    """市场归类（stock_basic 的 `market` 字段原值；缺失 → 空串）。"""
    return str(row.get("market") or "").strip()


def build_pool(basic_rows: list[dict], *, with_bj: bool = False) -> dict:
    """`stock_basic` 行 → 候选池。

    返回 ``{"rows": [{ts_code,name,industry,market}], "n_total", "n_excluded_st",
    "n_excluded_bj"}``。行业缺失归「—」桶（L1 阶段跳过行业条件并 warning），
    **不因此剔除标的**。

    空输入 raise ``ValueError``（D5：静默返回空池会让报告显示「今日无命中」，
    而这是事实性错误——不是「没有便宜货」，是「没取到数」）。
    """
    if not basic_rows:
        raise ValueError("stock_basic 空返回，无法建池（不是「无候选」而是「未取到数」）")

    rows: list[dict] = []
    n_st = 0
    n_bj = 0
    for r in basic_rows:
        market = market_of(r)
        if not with_bj and market == _BJ_MARKET:
            n_bj += 1
            continue
        if is_st(r.get("name")):
            n_st += 1
            continue
        rows.append({
            "ts_code": str(r.get("ts_code") or ""),
            "name": str(r.get("name") or ""),
            "industry": str(r.get("industry") or _MISSING_INDUSTRY).strip() or _MISSING_INDUSTRY,
            "market": market,
        })
    return {"rows": rows, "n_total": len(basic_rows),
            "n_excluded_st": n_st, "n_excluded_bj": n_bj}


def industry_missing(industry: str) -> bool:
    """行业不可得（L1 行业条件据此跳过并 warning，设计 §5）。"""
    return not industry or industry == _MISSING_INDUSTRY
