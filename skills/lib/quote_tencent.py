"""腾讯 qt.gtimg.cn 行情 — 全库唯一解析实现（v0.2.7 收敛三份拷贝）。

历史：v0.2.7 前同一逻辑三份拷贝，路由规则与单位语义互有出入（字段漂移风险）：
  - invest-a-etf ``etf_data._q_tencent_etf_quote`` — 路由 ("5","6","9")→sh，
    唯一记录了成交额单位换算（万元→元，F1-1）；字段：价/涨跌幅/量/额
  - invest-a-stock ``valuation_calc._get_quote_tencent`` — 路由 ("6","68")→sh
    （"68" 冗余，已含于 "6"）；字段另含 pe(39)/pb(46)/total_mv(45)
  - invest-a-stock ``collector._sources._q_tencent_quote`` — 路由 ("6","9")→sh
    + 北交所豁免（4/8/920 前缀不请求，防误路由到别的公司）；字段最全

统一裁决（v0.2.7）：
  - 市场路由取三者并集：5/6/9 → sh（"5" 仅 etf 份有，沪 ETF/LOF 51xxxx 必需；
    "9" 覆盖 900xxx 沪 B；"68" 被 "6" 覆盖无需单列）；其余 → sz；
    4/8/920 前缀（北交所/老三板）→ 跳过不请求（取 _sources 的豁免，防
    sz8xxxxx 命中旧三板返回**别家公司**报价——误路由数据比缺失危害更大）
  - 字段集取三者并集；单位换算语义显式化（见下）
  - 载荷有效性：split("~") ≥ 46 字段（覆盖下标 45）；price 缺失视为无效
    （三份中两份以 price 为有效性门槛，统一后全库一致）

单位语义（qt.gtimg.cn split("~") 下标，最高维护风险点，显式记录）：
  - 下标 3  最新价（元）
  - 下标 6  成交量（手；1 手 = 100 股。三份旧拷贝均未换算，保持原样）
  - 下标 32 涨跌幅（%）
  - 下标 33/34 最高/最低（元）
  - 下标 37 成交额（**万元**）→ amount 统一 ×1e4 转元（F1-1：东财 spot 口径为元，
    不换算则同一字段随数据源差 10⁴ 倍；实测 p[37]=323831 vs spot 3,238,306,187）
  - 下标 38 换手率（%）
  - 下标 39 市盈率（TTM 口径，消费方各自命名：pe_ratio / pe_dynamic）
  - 下标 45 总市值（**亿元**）→ total_mv_yi 保持亿元**不换算**（键名显式带单位，
    防与元口径混用；旧注释 "腾讯 field45 返回亿元，无需转换" 移入本模块）
  - 下标 46 市净率（仅 len > 46 时可用）
"""

from __future__ import annotations

from typing import Any

from nums import safe_float  # skills/lib 共享数值库（经 _invest_path 引导后可达）

__all__ = [
    "TENCENT_QUOTE_URL",
    "TENCENT_TIMEOUT_SECONDS",
    "AMOUNT_WAN_TO_YUAN",
    "IDX_PRICE",
    "IDX_CHANGE_PCT",
    "IDX_VOLUME",
    "IDX_HIGH",
    "IDX_LOW",
    "IDX_AMOUNT_WAN",
    "IDX_TURNOVER_RATE",
    "IDX_PE",
    "IDX_TOTAL_MV_YI",
    "IDX_PB",
    "is_tencent_unsupported",
    "tencent_market",
    "build_tencent_quote_url",
    "parse_tencent_quote",
    "fetch_tencent_quote",
]

TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q="
TENCENT_TIMEOUT_SECONDS = 5.0

# 下标 37 成交额单位：万元 → 元（见模块 docstring 单位语义）
AMOUNT_WAN_TO_YUAN = 1e4

# qt.gtimg.cn 响应 "v_<code>=\"...~...~...\"" 的 split("~") 下标
IDX_PRICE = 3
IDX_VOLUME = 6
IDX_CHANGE_PCT = 32
IDX_HIGH = 33
IDX_LOW = 34
IDX_AMOUNT_WAN = 37
IDX_TURNOVER_RATE = 38
IDX_PE = 39
IDX_TOTAL_MV_YI = 45
IDX_PB = 46

# 北交所/老三板：腾讯无覆盖，跳过（不误路由）
_NORTH_EXCHANGE_PREFIXES = ("4", "8", "920")
# 沪市前缀：6=沪主板/科创板(688)、5=沪 ETF/LOF(51xxxx 等)、9=沪 B(900xxx)
_SH_MARKET_PREFIXES = ("5", "6", "9")

# 腾讯「数据不可用」占位标记 → None（与真实 0 区分，D1：0.0 是合法值）
_UNAVAILABLE_MARKERS = ("--", "N/A", "", "—")

# 有效载荷最少字段数：需覆盖下标 45（total_mv）；下标 46（pb）仅在更长时可用
_MIN_FIELDS = 46


def is_tencent_unsupported(symbol: str) -> bool:
    """北交所代码（4/8/920 前缀，含 .BJ/.NQ 后缀）→ 腾讯无覆盖。

    与 codes.exchange_code 的北交所规则一致（4xxxxx/8xxxxx 老三板、920xxx
    北交所新股）。此前市场启发式 'sh' if startswith(("6","9")) 会把 920xxx
    路由到 sh920xxx（请求不存在）、把 8xxxxx 路由到 sz8xxxxx（可能命中旧
    三板而返回**别家公司**的报价）——误路由数据比缺失数据危害更大，明确
    跳过并标注不可得。
    """
    s = str(symbol or "").strip().split(".")[0]
    return s.startswith(_NORTH_EXCHANGE_PREFIXES)


def tencent_market(symbol: str) -> str | None:
    """统一市场路由：北交所（4/8/920）→ None；5/6/9 → sh；其余 → sz。

    v0.2.7 裁决：取 _sources 最全版本（含北交所豁免）并补入 etf 份独有的
    "5" 前缀（沪 ETF/LOF 51xxxx 必须路由 sh，否则请求不存在的 sz5xxxxx）。
    空代码 → None（D5：空输入不发起无意义请求）。
    """
    s = str(symbol or "").strip().split(".")[0]
    if not s:
        return None
    if s.startswith(_NORTH_EXCHANGE_PREFIXES):
        return None
    return "sh" if s.startswith(_SH_MARKET_PREFIXES) else "sz"


def build_tencent_quote_url(symbol: str) -> str | None:
    """按统一路由构造请求 URL；北交所返回 None（不发起请求）。

    代码统一取 "." 前纯数字段（旧 _sources 直接把带后缀 symbol 拼进 URL，
    形如 ``sh600176.SH`` 必失败——收敛后为行为改进）。
    """
    mkt = tencent_market(symbol)
    if mkt is None:
        return None
    code = str(symbol).strip().split(".")[0]
    return f"{TENCENT_QUOTE_URL}{mkt}{code}"


def _parse_field(fields: list[str], idx: int) -> float | None:
    """字段 → float；不可用标记/越界 → None（与真实 0 区分，D1）。"""
    if len(fields) <= idx:
        return None
    val = fields[idx]
    if val is None or val in _UNAVAILABLE_MARKERS:
        return None
    return safe_float(val)


def _parse_amount_yuan(fields: list[str]) -> float | None:
    """成交额（下标 37，万元）→ 元。唯一单位换算点，显式 ×1e4（F1-1）。"""
    amt_wan = _parse_field(fields, IDX_AMOUNT_WAN)
    if amt_wan is None:
        return None
    return amt_wan * AMOUNT_WAN_TO_YUAN


def parse_tencent_quote(text: str) -> dict[str, Any] | None:
    """解析 qt.gtimg.cn 响应文本 → 超集字段 dict（纯函数，无网络）。

    返回键（单位显式）：
      price（元）/ change_pct（%）/ high（元）/ low（元）/ volume（手）/
      amount（**元**，下标 37 万元 ×1e4）/ turnover_rate（%）/
      pe_ratio（TTM）/ total_mv_yi（**亿元**，不换算）/ pb

    无效载荷返回 None：无 "~"、字段数 < 46、price 缺失（不可用标记或越界）。
    """
    if not text or "~" not in text:
        return None
    fields = text.split("~")
    if len(fields) < _MIN_FIELDS:
        return None
    price = _parse_field(fields, IDX_PRICE)
    if price is None:
        return None  # 无价格的快照不算有效行情（三份旧拷贝中两份以此为门槛）
    return {
        "price": price,
        "change_pct": _parse_field(fields, IDX_CHANGE_PCT),
        "high": _parse_field(fields, IDX_HIGH),
        "low": _parse_field(fields, IDX_LOW),
        "volume": _parse_field(fields, IDX_VOLUME),
        "amount": _parse_amount_yuan(fields),
        "turnover_rate": _parse_field(fields, IDX_TURNOVER_RATE),
        "pe_ratio": _parse_field(fields, IDX_PE),
        "total_mv_yi": _parse_field(fields, IDX_TOTAL_MV_YI),
        "pb": _parse_field(fields, IDX_PB),
    }


def fetch_tencent_quote(
    symbol: str, *, session: Any = None, timeout: float = TENCENT_TIMEOUT_SECONDS
) -> dict[str, Any] | None:
    """网络获取 + 解析；北交所/HTTP 非 200/无效载荷 → None。

    - ``session`` 可注入（测试 mock 用；消费方各自持有自己的 mock 缝）：
      缺省时惰性导入 ``lib.proxy.no_proxy_session``（强制直连，防 Clash/
      VPN 拦截国内金融域名；与 etf/_sources 旧行为一致）。
    - 传输层异常（requests 连接失败等）**上抛**，由调用方按其失败契约处理
      （etf→None、valuation→error dict、_sources 沿旧行为由 collector 捕获）。
    """
    url = build_tencent_quote_url(symbol)
    if url is None:
        return None
    if session is None:
        from lib.proxy import no_proxy_session

        with no_proxy_session() as sess:
            r = sess.get(url, timeout=timeout)
    else:
        r = session.get(url, timeout=timeout)
    if r.status_code != 200:
        return None
    return parse_tencent_quote(getattr(r, "text", "") or "")
