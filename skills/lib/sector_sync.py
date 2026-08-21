"""E1 板块同步性引擎（v0.2.7）— 把「板块同涨同跌、α 被 β 淹没」变成可验算数字。

产出 6 个 derived 字段（全部来自学术，不自创）：

| 字段 | 口径 | 实证锚 |
|------|------|--------|
| ``sector_beta_60d`` | 60 交易日窗口 OLS ``r_i = α + β·r_sector + ε`` | Moskowitz-Grinblatt 1999 JF |
| ``sector_r2_60d`` | 同回归的 R² | Morck-Yeung-Yu 2000 JFE |
| ``idio_var_share`` | ``1 − R²``（特质方差占比，「α 还剩多少」的直接测度） | 同上 |
| ``sector_dispersion`` | 板块内成分股当日收益横截面标准差（%） | 同涨同跌的直接度量 |
| ``csad_gamma2`` | ``CSAD_t = γ0 + γ1·|R_m,t| + γ2·R_m,t² + ε`` 的 γ2（显著为负 = 羊群） | Chang-Cheng-Khorana 2000 |
| ``downside_corr_gap`` | ``ρ⁻ − ρ⁺``（250 日窗口，1σ 阈值分组，**Forbes-Rigobon 异方差校正**） | Ang-Chen 2002 JFE; Forbes-Rigobon 2002 JF |

口径注记（实现取舍）：
- **锚定行业指数而非大盘**：科技/电子在中国历史上属 α 主导型行业（市场 β<1），
  用大盘做基准会低估板块效应。全部 6 个字段以**行业指数**为基准；CSAD 的
  ``R_m`` 同样取行业指数（CCK 原版为市场组合，此处按板块口径实现，跨标的可比）。
- 收益一律按**小数**（0.01 = 1%）参与回归/相关；``sector_dispersion`` 输出时
  ×100 转 %。
- 窗口：beta 60 交易日（= 61 个对齐收盘点，D3）；CSAD / 离散度 / 下行相关
  250 交易日（同一成分股收益矩阵复用，D10）。
- 成分股取**当前时点**快照（成分漂移属已知局限——E8 回测须固定时点口径）。
- ``sector_dispersion`` 用总体标准差（pstdev）：板块成分即全体，无抽样含义。
- 1σ 阈值分组：板块指数收益 ``R_m ≤ −σ`` 为下行档、``R_m ≥ +σ`` 为上行档
  （σ 为窗口内板块收益样本标准差）；各档样本 < 20 日 → 不可得（fail loud）。
- Forbes-Rigobon 校正：``ρ_corr = ρ* / sqrt(1 + δ·(1 − ρ*²))``，
  ``δ = Var(R_m|下行)/Var(R_m|上行) − 1``，ρ* 为下行档原始相关（高波市态的
  机械性高相关被剔除）。校正方向：δ>0 时校正值 < 原始值。

措辞约束（C7）：本模块只回答「收益方差的归属」，不回答「基本面是否重要」。
任何面向报告的文案必须带条件限定：「在高同步性板块 + 短窗口（日/周）+
高波动市态下，收益方差由板块成分主导；基本面在中长期、低同步性个股、
横截面定价与剧烈回调尾部中恢复意义」。**禁止写成「基本面不重要」**。

降级（D5 fail loud）：板块指数缺失 / **全部候选**板块成分股 < 20 只 / 窗口样本
不足 → 字段输出 None + ``reasons`` 说明（「不可得」），**绝不给默认值 / 0 / NaN**。
空结果不写缓存（D6）。

数据源（2026-08-21 实测）：
- 东财 BK（主口径）：``stock_board_industry_name_em`` / ``stock_board_industry_hist_em`` /
  ``stock_board_industry_cons_em`` —— 当日 ConnectionError（拒连），历史上亦有
  拒连记录（TLS 指纹/IP 过滤）。
- 申万行业指数（降级口径，§5.3 稳健性变体）：``sw_index_first/second/third_info`` /
  ``index_hist_sw`` / ``index_component_sw`` —— 实测可用。匹配 L3→L2→L1
  （exact 全级优先 → substring 全级）；细分行业成分 < 20 时沿「上级行业」落到
  上级板块（如 801712 玻璃玻纤 L2 仅 16 只 → 建筑材料 L1），锚定在
  ``meta.industry`` / ``index_code`` 如实标注。
- 成分股日线：``stock_zh_a_hist``（东财，主）→ ``stock_zh_a_daily``（sina，降级）—— sina 实测可用。

缓存（DataCache，skills/lib/cache.py）：dimension 约定
``sector_tables``（板块表解析，TTL 1 天）/ ``sector_index_hist`` / ``sector_cons`` /
``sector_cons_kline``（TTL 1 天，盘中自动 ×0.8 收紧、盘后 ×2 放宽）。
``sector_cons_kline`` 另存失败墓碑（双源全挂的代码，短 TTL），防每次采集
重试同一批不可抓代码。
"""

from __future__ import annotations

import contextlib
import logging
import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 公共常量
# ---------------------------------------------------------------------------

SECTOR_SYNC_FIELDS: tuple[str, ...] = (
    "sector_beta_60d",
    "sector_r2_60d",
    "idio_var_share",
    "sector_dispersion",
    "csad_gamma2",
    "downside_corr_gap",
)

# C7 措辞约束：面向报告的文案必须带此条件限定（禁止「基本面不重要」类表述）
SECTOR_SYNC_INTERPRETATION_NOTE = (
    "在高同步性板块 + 短窗口（日/周）+ 高波动市态下，收益方差由板块成分主导；"
    "基本面在中长期、低同步性个股、横截面定价与剧烈回调尾部中恢复意义"
)

_BETA_WINDOW_DAYS = 60      # 60 日窗口（= 61 个对齐收盘点，D3）
_LONG_WINDOW_DAYS = 250     # CSAD / 离散度 / 下行相关共用长窗
_MIN_CONSTITUENTS = 20      # 验收 #2：成分股 < 20 → 不可得
_MIN_REGIME_DAYS = 20       # 1σ 分组后下行/上行各档最少样本日
_MIN_CSAD_DAYS = 30         # CSAD 回归最少样本日
_MIN_CORR_DAYS = 30         # 下行相关窗口最少样本日
_KLINE_FETCH_DAYS = 400     # 成分股日线抓取窗口（自然日，覆盖 ~270 交易日）
_CACHE_TTL_SECONDS = 86400  # 1 天（日频数据；盘中 TTL ×0.8 由 DataCache 处理）
_MAX_WORKERS = 8            # 成分股日线抓取并发数
_WARMUP_FETCH_BUDGET = 20   # F1 冷缓存门控：成分股日线现场补抓预算（只），缺口 > 预算视为冷缓存
_MIN_KLINE_ROWS = 30        # 成分股日线最少可信行数：EM 非空但少于此 → 触发 sina 兜底（截断响应/新股）
_TOMBSTONE_TTL_SECONDS = 1800  # 失败墓碑 TTL（30 分钟）：不可抓代码短期内不再重试，过期自然恢复


# ---------------------------------------------------------------------------
# 数据层：单一 akshare 入口
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _direct_session():
    """akshare 直连会话（东财需直连）。lib.proxy 不可用时退化为裸调用。"""
    try:
        from lib.proxy import akshare_direct_session  # type: ignore
    except ImportError:
        try:
            from proxy import akshare_direct_session  # type: ignore
        except ImportError:
            yield
            return
    with akshare_direct_session():
        yield


def _ak_fetch(func_name: str, *args: Any, **kwargs: Any) -> Any:
    """单一 akshare 调用入口（惰性导入 + 直连会话 + socket 超时兜底）。

    测试 patch 本函数即可完全隔离网络（D13：mock 打在定义模块命名空间）。
    """
    import socket

    if socket.getdefaulttimeout() is None:
        socket.setdefaulttimeout(30)  # 防挂起（akshare 多数接口不设显式 timeout）
    import akshare as ak

    fn = getattr(ak, func_name, None)
    if fn is None:
        raise ValueError(f"akshare 无函数 {func_name}")
    with _direct_session():
        return fn(*args, **kwargs)


def _ak_df_rows(df: Any) -> list[dict]:
    """DataFrame → records；None/空 → []。"""
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _norm_date(raw: Any) -> str:
    """日期归一化 → YYYYMMDD（YYYY-MM-DD / YYYYMMDD / YYYY.MM.DD）；不可解析返回 ''。"""
    s = str(raw or "").strip()
    s = s.replace("-", "").replace(".", "").replace("/", "")
    return s[:8] if len(s) >= 8 and s[:8].isdigit() else ""


def _default_cache() -> Any:
    """DataCache 默认单例（lib.cache → 裸 cache 降级，与既有懒导入模式一致）。"""
    try:
        from lib.cache import default_cache  # type: ignore
    except ImportError:
        from cache import default_cache  # type: ignore
    return default_cache()


def _shanghai_today() -> str:
    """上海时区今日（YYYYMMDD）——采集管线统一口径；date.today() 是本机时区。"""
    try:
        from lib.dates import shanghai_today  # type: ignore
    except ImportError:
        from dates import shanghai_today  # type: ignore
    return shanghai_today()


def _shanghai_days_ago(n: int) -> str:
    """上海时区 N 天前（YYYYMMDD）。"""
    try:
        from lib.dates import shanghai_days_ago  # type: ignore
    except ImportError:
        from dates import shanghai_days_ago  # type: ignore
    return shanghai_days_ago(n)


def _cached_sector_table(name: str, fetch_fn: Callable[[], Any], *, cache: Any) -> list[dict]:
    """板块表（行业名→代码映射）缓存：dimension ``sector_tables``，TTL 1 天。

    probe 与 compute 各自解析锚定板块时共用同一份表——首次抓取后 1 天内
    （含跨进程）零重复网络；失败/空表不缓存（D6）。
    """
    cached = cache.get("sector_tables", name)
    if isinstance(cached, list) and cached:
        return cached
    rows = _ak_df_rows(fetch_fn())
    if not rows:
        return []
    cache.set("sector_tables", name, rows, ttl_seconds=_CACHE_TTL_SECONDS,
              source=f"akshare.{name}")
    return rows


# ---------------------------------------------------------------------------
# 行业 → 板块指数解析（东财 BK 主口径 → 申万行业指数降级）
# ---------------------------------------------------------------------------

def _match_name(names: list[str], hint: str) -> str | None:
    """板块名匹配：exact → 包含（与 collector/_sources 行业匹配同型）。"""
    hint = hint.strip()
    for n in names:
        if n == hint:
            return n
    for n in names:
        if n and (hint in n or n in hint):
            return n
    return None


def _resolve_em_board(industry_hint: str, *, cache: Any = None) -> dict[str, str] | None:
    """东财 BK 板块：stock_board_industry_name_em → {provider, index_name, index_code}。"""
    if cache is None:
        cache = _default_cache()
    try:
        rows = _cached_sector_table(
            "stock_board_industry_name_em",
            lambda: _ak_fetch("stock_board_industry_name_em"), cache=cache)
    except Exception as exc:
        logger.info("sector_sync: EM board list unavailable: %s", exc)
        return None
    names = [str(r.get("板块名称", "")).strip() for r in rows]
    matched = _match_name(names, industry_hint)
    if not matched:
        return None
    code = next(
        (str(r.get("板块代码", "")).strip() for r in rows
         if str(r.get("板块名称", "")).strip() == matched), "")
    return {"provider": "em_bk", "index_name": matched, "index_code": code}


_SW_LEVEL_FUNCS = (
    ("sw_index_third_info", "L3"),
    ("sw_index_second_info", "L2"),
    ("sw_index_first_info", "L1"),
)
_SW_LEVEL_ORDER = ("L3", "L2", "L1")


def _resolve_sw_candidates(industry_hint: str, *, cache: Any = None) -> list[dict[str, str]]:
    """申万行业指数候选：L3 → L2 → L1 逐级（exact 全级优先 → substring 全级）。

    返回最细粒度的命中 + 其祖先链（L2 的「上级行业」→ L1 等，递归向上）：
    细分行业成分常 < 20（如 801712 玻璃玻纤仅 16 只，实测 2026-08-21），
    由 compute 的候选链按成分数自动落到上级行业（建筑材料等 L1），
    锚定结果在 meta.index_name 如实标注。祖先链只从**命中行**出发，
    候选规模有界（L1 ≤ 31 个），避免全表展开造成无谓网络调用。
    """
    hint = (industry_hint or "").strip()
    if not hint:
        return []
    if cache is None:
        cache = _default_cache()
    # 逐级拉表（走表缓存；失败跳过——其余表已找到的匹配不因限流整体丢失）
    levels: dict[str, list[dict]] = {}
    for func, level in _SW_LEVEL_FUNCS:
        try:
            rows = _cached_sector_table(
                func, lambda f=func: _ak_fetch(f), cache=cache)
        except Exception as exc:
            logger.info("sector_sync: %s unavailable: %s", func, exc)
            continue
        if rows:
            levels[level] = rows
    if not levels:
        return []

    candidates: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    matched_names: set[str] = set()

    def _add(n: str, code_raw: Any) -> None:
        n = (n or "").strip()
        code = str(code_raw or "").strip().split(".")[0]
        if n and code and code not in seen_codes:
            seen_codes.add(code)
            candidates.append({"provider": "sw", "index_name": n, "index_code": code})

    def _add_ancestors(name: str) -> None:
        """命中行业沿「上级行业」递归向上追加（L3→L2→L1，成分不足时的降级锚）。"""
        for level in _SW_LEVEL_ORDER:
            for r in levels.get(level, []):
                if str(r.get("行业名称", "")).strip() != name:
                    continue
                parent = str(r.get("上级行业", "") or "").strip()
                if not parent or parent in matched_names:
                    continue
                for p_level in _SW_LEVEL_ORDER:
                    for r2 in levels.get(p_level, []):
                        if str(r2.get("行业名称", "")).strip() == parent:
                            _add(parent, r2.get("行业代码", ""))
                            matched_names.add(parent)
                            _add_ancestors(parent)
                            return

    # exact 全级优先 → substring 全级（L3 → L2 → L1）
    for exact_only in (True, False):
        for level in _SW_LEVEL_ORDER:
            for r in levels.get(level, []):
                n = str(r.get("行业名称", "")).strip()
                if not n:
                    continue
                if n != hint and (exact_only or not (hint in n or n in hint)):
                    continue
                _add(n, r.get("行业代码", ""))
                matched_names.add(n)

    # 祖先链：仅从命中行出发（候选规模有界）
    for name in list(matched_names):
        _add_ancestors(name)
    return candidates


def _resolve_provider_candidates(industry_hint: str, *, cache: Any = None) -> list[dict[str, str]]:
    """行业提示 → 候选板块指数（按主口径优先，成分不足时自动落到上级行业）。

    顺序：东财 BK（若匹配）→ 申万 L3/L2/L1 命中（最细优先）→ 申万祖先链。
    compute 按序尝试：指数历史 + 成分股 ≥ 20 的第一个候选即为锚定板块。
    """
    hint = (industry_hint or "").strip()
    if not hint:
        return []
    if cache is None:
        cache = _default_cache()
    candidates: list[dict[str, str]] = []
    em = _resolve_em_board(hint, cache=cache)
    if em is not None:
        candidates.append(em)
    for cand in _resolve_sw_candidates(hint, cache=cache):
        if cand not in candidates:
            candidates.append(cand)
    return candidates


# ---------------------------------------------------------------------------
# 板块指数历史 / 成分股 / 成分股日线（全部走 DataCache）
# ---------------------------------------------------------------------------

def _validated_dated_closes(raw: Any) -> list[tuple[str, float]] | None:
    """校验缓存读出的 [(date, close), ...]；非法/空 → None（视作未缓存）。"""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[tuple[str, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        d, c = item
        d8 = _norm_date(d)
        c = _to_float(c)
        if len(d8) == 8 and c is not None:
            out.append((d8, c))
    return out or None


def _to_float(v: Any) -> float | None:
    """数值化（None/NaN/±inf/非数字 → None）；不依赖 lib.nums 的裸导入路径。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _fetch_index_history(provider: dict[str, str], *, cache: Any,
                         days: int = _KLINE_FETCH_DAYS) -> list[tuple[str, float]] | None:
    """板块指数日线（升序 [(YYYYMMDD, close)]）。

    em_bk: stock_board_industry_hist_em（板块名）→ sw: index_hist_sw（裸代码，取尾部）。
    缓存 dimension ``sector_index_hist``，symbol = 指数代码（EM BK 代码 / 申万代码不冲突）。
    """
    key = provider["index_code"]
    cached = cache.get("sector_index_hist", key)
    if cached is not None:
        out = _validated_dated_closes(cached)
        if out is not None:
            # 缓存读同现场路径：排序 + 尾部裁剪（sorted() 不污染缓存对象——
            # DataCache 对 list 返回引用；幂等修复修复前写入的乱序/超窗条目）
            return sorted(out)[-days:]
    if provider["provider"] == "em_bk":
        sd = _shanghai_days_ago(days)
        ed = _shanghai_today()
        df = _ak_fetch("stock_board_industry_hist_em",
                       symbol=provider["index_name"], period="日k",
                       start_date=sd, end_date=ed, adjust="")
        rows = _ak_df_rows(df)
        closes: list[tuple[str, float]] = []
        for r in rows:
            d8 = _norm_date(r.get("日期") or r.get("trade_date") or r.get("date"))
            c = _to_float(r.get("收盘") or r.get("close"))
            if d8 and c is not None:
                closes.append((d8, c))
        closes.sort()
        closes = closes[-days:]
    else:
        df = _ak_fetch("index_hist_sw", symbol=key, period="day")
        rows = _ak_df_rows(df)
        closes = []
        for r in rows:
            d8 = _norm_date(r.get("日期") or r.get("trade_date") or r.get("date"))
            c = _to_float(r.get("收盘") or r.get("close"))
            if d8 and c is not None:
                closes.append((d8, c))
        # F6：先排序再取尾部——申万返回全历史（1999 起），且接口不保证升序
        # （同仓 _orchestrate.py:1976 也防御性先排），乱序时尾部切片才有效
        closes.sort()
        closes = closes[-days:]
    if not closes:
        return None
    cache.set("sector_index_hist", key, closes, ttl_seconds=_CACHE_TTL_SECONDS,
              source=f"akshare.{'stock_board_industry_hist_em' if provider['provider'] == 'em_bk' else 'index_hist_sw'}")
    return closes


def _fetch_constituents(provider: dict[str, str], *, cache: Any) -> list[str] | None:
    """板块成分股代码（6 位、去重）。缓存 dimension ``sector_cons``。"""
    key = provider["index_code"]
    cached = cache.get("sector_cons", key)
    if cached is not None and isinstance(cached, list) and cached:
        return list(cached)
    if provider["provider"] == "em_bk":
        df = _ak_fetch("stock_board_industry_cons_em", symbol=provider["index_name"])
        rows = _ak_df_rows(df)
        codes = [str(r.get("代码", "")).strip() for r in rows]
    else:
        df = _ak_fetch("index_component_sw", symbol=key)
        rows = _ak_df_rows(df)
        codes = [str(r.get("证券代码", "")).strip() for r in rows]
    seen: list[str] = []
    for raw in codes:
        c = "".join(ch for ch in raw if ch.isdigit())
        if len(c) == 6 and c not in seen:
            seen.append(c)
    if not seen:
        return None
    cache.set("sector_cons", key, seen, ttl_seconds=_CACHE_TTL_SECONDS,
              source=f"akshare.{'stock_board_industry_cons_em' if provider['provider'] == 'em_bk' else 'index_component_sw'}")
    return seen


def _sina_symbol_prefix(code: str) -> str:
    """sina 代码前缀：6xx → sh，0/3xx → sz，其余（北交所）无 sina 日线。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return ""


def _fetch_constituent_kline(code: str, *, cache: Any,
                             days: int = _KLINE_FETCH_DAYS) -> list[tuple[str, float]] | None:
    """单个成分股日线（qfq，升序 [(YYYYMMDD, close)]）。

    降级链：stock_zh_a_hist（东财）→ stock_zh_a_daily（sina）。
    缓存 dimension ``sector_cons_kline``，TTL 1 天——185 只成分股逐只抓取
    5-10 分钟，必须走缓存（同板块多标的复用同一份矩阵）。
    双源全挂写失败墓碑（短 TTL）：近期重复 collect 不再重试同一批失败代码
    （东财拒连 + 北交所无 sina 日线是常态组合），过期后自然恢复重试。
    """
    cached = cache.get("sector_cons_kline", code)
    if isinstance(cached, dict) and cached.get("failed"):
        return None  # 失败墓碑未过期：本次跳过（不发网络）
    if cached is not None:
        out = _validated_dated_closes(cached)
        if out is not None:
            return sorted(out)[-days:]
    sd = _shanghai_days_ago(days)
    ed = _shanghai_today()
    em_closes: list[tuple[str, float]] = []
    try:
        df = _ak_fetch("stock_zh_a_hist", symbol=code, period="daily",
                       start_date=sd, end_date=ed, adjust="qfq")
        rows = _ak_df_rows(df)
        for r in rows:
            d8 = _norm_date(r.get("日期") or r.get("trade_date") or r.get("date"))
            c = _to_float(r.get("收盘") or r.get("close"))
            if d8 and c is not None:
                em_closes.append((d8, c))
    except Exception as exc:
        logger.debug("sector_sync cons %s EM kline failed: %s", code, exc)
    # 截断响应（限流只回少数几行）同样不可信：非空但行数不足也走 sina 兜底
    em_ok = len(em_closes) >= _MIN_KLINE_ROWS
    sina_closes: list[tuple[str, float]] = []
    if not em_ok:
        prefix = _sina_symbol_prefix(code)
        if not prefix:
            logger.info("sector_sync cons %s: 北交所代码无 sina 日线，跳过", code)
        else:
            try:
                df = _ak_fetch("stock_zh_a_daily", symbol=f"{prefix}{code}",
                               start_date=sd, end_date=ed, adjust="qfq")
                rows = _ak_df_rows(df)
                for r in rows:
                    d8 = _norm_date(r.get("date") or r.get("trade_date") or r.get("日期"))
                    c = _to_float(r.get("close") or r.get("收盘"))
                    if d8 and c is not None:
                        sina_closes.append((d8, c))
            except Exception as exc:
                logger.debug("sector_sync cons %s sina kline failed: %s", code, exc)
    # 取较长者（EM 健康时不发多余 sina 请求；EM 截断时 sina 更长则用 sina）
    if len(sina_closes) > len(em_closes):
        closes, source = sina_closes, "akshare.stock_zh_a_daily"
    else:
        closes, source = em_closes, "akshare.stock_zh_a_hist"
    if not closes:
        cache.set("sector_cons_kline", code, {"failed": True},
                  ttl_seconds=_TOMBSTONE_TTL_SECONDS, source="failed")
        return None  # D6：空结果不缓存（失败墓碑除外，见 docstring）
    closes.sort()
    cache.set("sector_cons_kline", code, closes, ttl_seconds=_CACHE_TTL_SECONDS, source=source)
    return closes


# ---------------------------------------------------------------------------
# 纯计算层（无网络；全部可独立复算）
# ---------------------------------------------------------------------------

def _returns(dates: list[str], closes_by_date: dict[str, float]) -> list[float | None]:
    """连续对齐日期的简单收益序列（小数）。收盘缺失/零 → None。"""
    out: list[float | None] = []
    for i in range(1, len(dates)):
        prev = closes_by_date.get(dates[i - 1])
        cur = closes_by_date.get(dates[i])
        # F2：prev==0 与 cur==0 均拒绝——零收盘不是「停牌」，是数据异常，
        # cur==0 会注入 -100% 收益（0/prev−1）污染 OLS/CSAD/下行相关
        if prev is None or cur is None or prev == 0 or cur == 0:
            out.append(None)
        else:
            out.append(cur / prev - 1.0)
    return out


def _guarded_metric(name: str, fn: Callable[[], dict]) -> dict:
    """数学字段调用守卫：ValueError → 该字段 available=False（D5 逐字段
    fail loud），其余已算好的字段保留——异常不再击穿整个 sector_sync。"""
    try:
        return fn()
    except ValueError as exc:
        return {"available": False, "reason": f"{name} 计算异常: {exc}"}


def _paired(a: list[float | None], b: list[float | None]) -> tuple[list[float], list[float]]:
    """剔除任一侧 None 的对齐对（用于 beta / 相关系数）。"""
    pa: list[float] = []
    pb: list[float] = []
    for x, y in zip(a, b):
        if x is not None and y is not None:
            pa.append(x)
            pb.append(y)
    return pa, pb


def sector_beta_stats(stock_returns: list[float], sector_returns: list[float]) -> dict:
    """60 日窗口 OLS β + R²（复用 skills/lib/stats.py:109 calc_beta，只读）。"""
    try:
        from lib.stats import calc_beta  # type: ignore
    except ImportError:
        from stats import calc_beta  # type: ignore  # skills/lib 裸路径
    res = calc_beta(stock_returns, sector_returns)
    beta = res.get("beta")
    if beta is None:  # D1：β 可合法为 0.0，必须显式 None 判定
        return {"available": False, "reason": res.get("error", "calc_beta 失败")}
    return {
        "available": True,
        "beta": float(beta),
        "r_squared": float(res["r_squared"]),
        "observations": int(res["observations"]),
    }


def cross_sectional_dispersion_pct(returns_by_day: dict[str, list[float]]) -> dict:
    """板块内成分股当日收益横截面标准差（%）。

    returns_by_day: 结束交易日（YYYYMMDD）→ 当日成分股收益（小数）列表。
    取窗口末日横截面；总体标准差（pstdev）：板块成分即全体。末日有效成分
    < 20 → 不可得。ISO 日期串 max() 即窗口末日（F3 起以交易日为键）。
    """
    if not returns_by_day:
        return {"available": False, "reason": "成分股收益矩阵为空"}
    last_day = max(returns_by_day)
    rets = returns_by_day[last_day]
    if len(rets) < _MIN_CONSTITUENTS:
        return {"available": False,
                "reason": f"窗口末日有效成分股不足（{len(rets)} < {_MIN_CONSTITUENTS}）"}
    return {
        "available": True,
        "dispersion_pct": round(statistics.pstdev(rets) * 100.0, 2),
        "n_constituents": len(rets),
    }


def _solve_linear(aug: list[list[float]]) -> list[float]:
    """高斯消元（部分主元）解 n×n 线性方程组；奇异 → ValueError。"""
    n = len(aug)
    a = [row[:] for row in aug]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-15:
            raise ValueError("矩阵奇异（X'X 不可逆）")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / a[i][i]
    return x


def _ols_with_se(x_rows: list[list[float]], y: list[float]) -> dict:
    """列归一化 OLS（各列按 L2 范数缩放后解正态方程）。

    必要性：CSAD 回归的 X 列为 [1, |R_m|, R_m²]，量级差 ~1e6（R_m² ≈ 1e-6），
    未缩放 X'X 条件数 ~1e10，高斯消元在反解 (X'X)⁻¹ 时精度塌陷（实测返回 0）。
    列缩放后 X'X 对角元 = 1、非对角元为相关系数，条件数收敛到个位数。

    Returns:
        {"coef": [γ0, γ1, γ2], "se": [σ0, σ1, σ2]（n−k 自由度，缩放回原量纲）,
         "rss": float, "n": int}
    """
    n = len(y)
    k = len(x_rows[0])
    scales = [math.sqrt(sum(x_rows[i][j] ** 2 for i in range(n))) for j in range(k)]
    if any(s == 0 for s in scales):
        # 零范数列（如窗口内板块收益恒 0 → |R_m|、R_m² 两列全零）不可缩放：
        # 除零会抛 ZeroDivisionError 穿透 csad_regression 的 ValueError 捕获，
        # 击穿整个 sector_sync——归为奇异输入（ValueError），逐字段 fail loud
        raise ValueError("X 含零范数列（列全为 0），回归不可辨识")
    xs = [[x_rows[i][j] / scales[j] for j in range(k)] for i in range(n)]
    xtx = [[sum(xs[i][a] * xs[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xty = [sum(xs[i][a] * y[i] for i in range(n)) for a in range(k)]
    coef_scaled = _solve_linear([xtx[row] + [xty[row]] for row in range(k)])
    coef = [coef_scaled[j] / scales[j] for j in range(k)]
    resid = [y[i] - sum(coef[a] * x_rows[i][a] for a in range(k)) for i in range(n)]
    rss = sum(e * e for e in resid)
    s2 = rss / (n - k)
    se: list[float | None] = []
    for j in range(k):
        # (X'X)⁻¹ 对角元：解 X'X·x = e_j（缩放系统，良态）。
        # RHS 是**单值** per-row（[1.0 if row == j else 0.0]）——曾误用整列
        # 列表追加成 6 列矩阵，消元只读前 4 列 → j≥1 时 RHS 全零，SE 塌成 None
        # （真实数据 CSAD t 统计量丢失，2026-08-21 实盘复算发现）。
        inv_col = _solve_linear([xtx[row] + [1.0 if row == j else 0.0]
                                 for row in range(k)])
        diag = inv_col[j]
        se.append(math.sqrt(s2 * diag) / scales[j] if s2 > 0 and diag > 0 else None)
    return {"coef": coef, "se": se, "rss": rss, "n": n}


def csad_regression(index_returns: dict[str, float],
                    returns_by_day: dict[str, list[float]]) -> dict:
    """CSAD 回归（CCK 2000，板块口径）。

    ``CSAD_t = γ0 + γ1·|R_m,t| + γ2·R_m,t² + ε``，R_m 取行业指数收益。
    每日有效成分 < 20 的交易日剔除（D4：聚合标注覆盖范围）；样本 < 30 → 不可得。
    输出 γ2 + 标准 OLS t 统计量（列归一化 OLS，见 _ols_with_se）。

    F3 对齐：两侧均以**结束交易日**（YYYYMMDD）为键——指数收益与被解释的
    成分股横截面天然按交易日配对，任一缺失的交易日只剔除自身观察，不位移
    后续样本（旧实现按压缩后位置枚举，缺一日即全局错位）。
    """
    xs: list[list[float]] = []
    ys: list[float] = []
    for d in sorted(index_returns):
        rm = index_returns[d]
        rets = returns_by_day.get(d)
        if rets is None or len(rets) < _MIN_CONSTITUENTS:
            continue
        csad = sum(abs(r - rm) for r in rets) / len(rets)
        xs.append([1.0, abs(rm), rm * rm])
        ys.append(csad)
    if len(ys) < _MIN_CSAD_DAYS:
        return {"available": False,
                "reason": f"CSAD 有效样本不足（{len(ys)} < {_MIN_CSAD_DAYS}，"
                          f"每日需 ≥ {_MIN_CONSTITUENTS} 只成分股）"}
    try:
        fit = _ols_with_se(xs, ys)
    except ValueError as exc:
        return {"available": False, "reason": f"CSAD 回归奇异: {exc}"}
    n = fit["n"]
    gamma2 = fit["coef"][2]
    se_gamma2 = fit["se"][2]
    t_stat = (gamma2 / se_gamma2) if (se_gamma2 is not None and se_gamma2 > 1e-12) else None
    return {
        "available": True,
        "gamma2": gamma2,
        "t_stat": t_stat,
        "n_days": n,
        "r_squared": 1.0 - fit["rss"] / (sum(y * y for y in ys) - n * (sum(ys) / n) ** 2)
        if n > 1 and (sum(y * y for y in ys) - n * (sum(ys) / n) ** 2) > 0 else None,
    }


def _pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson 相关系数；任一侧零方差 → None。"""
    n = len(a)
    if n < 2:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def downside_correlation_gap(stock_returns: list[float],
                             index_returns: list[float]) -> dict:
    """下行相关差 ρ⁻(FR 校正) − ρ⁺（Ang-Chen 2002；Forbes-Rigobon 2002）。

    1σ 阈值分组（250 日窗口）：R_m ≤ −σ 为下行档、R_m ≥ +σ 为上行档
    （σ = 窗口内板块收益样本标准差，两档之外的日子不参与）。
    Forbes-Rigobon 异方差校正（剔除高波市态机械性高相关）：

        ρ_corr = ρ* / sqrt(1 + δ·(1 − ρ*²))，δ = Var(R_i|下行)/Var(R_i|上行) − 1

    其中 ρ* 为下行档原始相关（高波档），上行档方差为参照；δ 用**个股自身**收益
    的档内方差比（教科书口径：FR 校正目标是剔除「个股自身高波动造成的高相关
    假象」，市场波动聚类不是该资产的异方差；2026-08-21 用户裁决，指数口径
    gap 与个股口径差距可达 250 倍）。δ>0 时校正值 < 原始值。
    任一侧样本 < 20 日 / 方差为零 → 不可得（fail loud，不给默认值）。
    """
    n = min(len(stock_returns), len(index_returns))
    if n < _MIN_CORR_DAYS:
        return {"available": False, "reason": f"窗口样本不足（{n} < {_MIN_CORR_DAYS}）"}
    srets = stock_returns[:n]
    mrets = index_returns[:n]
    sigma = statistics.stdev(mrets) if n >= 2 else 0.0
    if sigma <= 0:
        return {"available": False, "reason": "板块指数窗口内无波动（σ=0）"}
    down_idx = [i for i in range(n) if mrets[i] <= -sigma]
    up_idx = [i for i in range(n) if mrets[i] >= sigma]
    if len(down_idx) < _MIN_REGIME_DAYS or len(up_idx) < _MIN_REGIME_DAYS:
        return {"available": False,
                "reason": f"1σ 分组样本不足（下行 {len(down_idx)} 日 / 上行 {len(up_idx)} 日"
                          f"，需各 ≥ {_MIN_REGIME_DAYS} 日）"}
    rho_minus = _pearson([srets[i] for i in down_idx], [mrets[i] for i in down_idx])
    rho_plus = _pearson([srets[i] for i in up_idx], [mrets[i] for i in up_idx])
    if rho_minus is None or rho_plus is None:
        return {"available": False,
                "reason": "某档收益零方差，相关系数不可得（下行或上行档无波动）"}
    var_down = statistics.variance([srets[i] for i in down_idx])
    var_up = statistics.variance([srets[i] for i in up_idx])
    if var_up <= 0 or var_down <= 0:
        return {"available": False, "reason": "档内个股收益方差为零，FR 校正不可用"}
    delta = var_down / var_up - 1.0
    # Forbes-Rigobon 校正：ρ*² = (1+δ)ρ²/(1+δρ²) 的反解
    # ρ_corr = ρ*/sqrt(1 + δ(1 − ρ*²))（δ > −1 恒成立：两方差均为正）
    denom = 1.0 + delta * (1.0 - rho_minus * rho_minus)
    if denom <= 0:  # 数值保护（理论不可达）
        return {"available": False, "reason": f"FR 校正分母异常（δ={delta:.4f}）"}
    rho_minus_corr = rho_minus / math.sqrt(denom)
    return {
        "available": True,
        "gap": rho_minus_corr - rho_plus,
        "rho_minus_raw": rho_minus,
        "rho_minus_corr": rho_minus_corr,
        "rho_plus": rho_plus,
        "n_down": len(down_idx),
        "n_up": len(up_idx),
        "delta": delta,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _dated_closes_from_kline(rows: Iterable[dict]) -> list[tuple[str, float]]:
    """K 线行 → 升序 [(YYYYMMDD, close)]；close 非法行剔除；同日期保留末行。"""
    out: dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        d8 = _norm_date(r.get("trade_date") or r.get("date") or r.get("日期"))
        c = _to_float(r.get("close") if r.get("close") is not None else r.get("收盘"))
        if d8 and c is not None:
            out[d8] = c
    return sorted(out.items())


def _unavailable(reason: str) -> dict[str, Any]:
    """全字段不可得骨架（D5 fail loud；绝不含默认数值）。"""
    return {
        "symbol": "",
        "available": False,
        "provider": None,
        "industry": None,
        "index_code": None,
        "n_constituents": 0,
        "n_constituents_with_kline": 0,
        "window_days": 0,
        "window_start": None,
        "window_end": None,
        "fields": {f: None for f in SECTOR_SYNC_FIELDS},
        "meta": {},
        "reasons": {"_all": reason},
    }


def _empty_success_skeleton(symbol: str) -> dict[str, Any]:
    """成功路径骨架：reasons 为空 dict（无失败字段时不输出多余键）。"""
    out = _unavailable("")
    out["symbol"] = symbol
    out["reasons"] = {}
    return out


def compute_sector_sync(symbol: str, *, industry_hint: str = "",
                        stock_kline: list[dict] | None = None,
                        cache: Any = None,
                        max_workers: int = _MAX_WORKERS,
                        anchor_override: dict | None = None) -> dict[str, Any]:
    """对单个标的计算 6 个板块同步性字段。

    Args:
        symbol: 标的代码（6 位）。
        industry_hint: 行业名提示（basic_info 的「行业」字段）；缺失 → 不可得
            （行业分类缺失，验收 #4）。
        stock_kline: 标的日 K 线行（trade_date/date/日期 + close/收盘）；
            由调用方（collector）传入已采集数据。
        cache: DataCache 实例；None → 模块默认缓存。
        max_workers: 成分股日线抓取并发数。
        anchor_override: probe 解析出的锚定板块（{provider, index_name,
            index_code}）；给定则跳过候选解析——probe 与 compute 之间数据源
            可达性翻转时不再二次解析触发全量冷抓。

    Returns:
        dict：见 _unavailable() 骨架 + available 时填充 fields/meta/reasons。
        任一字段不可得时为 None + reasons 说明，**绝不给默认值/0/NaN**。
    """
    if cache is None:
        cache = _default_cache()

    out = _empty_success_skeleton(symbol)
    reasons = out["reasons"]

    # 1. 个股 K 线 → 对齐日期（免费检查，先于任何网络调用）
    stock_closes = _dated_closes_from_kline(stock_kline or [])
    if len(stock_closes) < _BETA_WINDOW_DAYS + 1:
        reasons["_all"] = (f"个股 K 线不足（{len(stock_closes)} 个收盘点，"
                           f"需 ≥ {_BETA_WINDOW_DAYS + 1}）")
        return out
    stock_by_date = dict(stock_closes)
    stock_dates = set(stock_by_date)

    # 2. 行业 → 板块指数（东财 BK → 申万 L1；表解析走缓存，probe 同享；
    #    probe 已解析出锚定时直接复用，不二次解析）
    if anchor_override is not None:
        candidates = [anchor_override]
    else:
        candidates = _resolve_provider_candidates(industry_hint, cache=cache)
    if not candidates:
        if not (industry_hint or "").strip():
            reasons["_all"] = "行业分类缺失（basic_info 无行业字段），无法锚定板块指数"
        else:
            reasons["_all"] = (f"板块指数不可得（行业「{industry_hint}」未匹配到"
                               f"东财 BK 板块或申万 L1 行业，或板块列表源不可达）")
        return out

    # 3. 依次尝试候选板块（主口径优先；EM 失败自然落到申万；细分行业
    #    成分不足时落到其上级行业——如玻璃玻纤 L2 16 只 → 建筑材料 L1）
    provider = None
    index_closes: list[tuple[str, float]] | None = None
    codes: list[str] | None = None
    for cand in candidates:
        try:
            idx = _fetch_index_history(cand, cache=cache)
            cons = _fetch_constituents(cand, cache=cache) if idx else None
        except Exception as exc:
            logger.warning("sector_sync provider %s failed: %s", cand.get("provider"), exc)
            continue
        if idx and cons and len(cons) >= _MIN_CONSTITUENTS:
            provider, index_closes, codes = cand, idx, cons
            break
    if provider is None or index_closes is None or codes is None:
        reasons["_all"] = ("板块指数或成分股不可得（东财 BK 与申万各层级均失败，"
                           "或全部候选成分股 < 20）")
        return out

    out["provider"] = provider["provider"]
    out["industry"] = provider["index_name"]
    out["index_code"] = provider["index_code"]
    out["n_constituents"] = len(codes)

    # 4. 对齐窗口：板块指数日期 ∩ 个股日期
    idx_by_date = dict(index_closes)
    common = [d for d, _ in index_closes if d in stock_dates]
    if len(common) < _BETA_WINDOW_DAYS + 1:
        reasons["_all"] = (f"板块指数与个股对齐交易日不足（{len(common)}"
                           f" < {_BETA_WINDOW_DAYS + 1}）")
        return out

    # 5. beta 窗口（60 收益 = 61 点，D3）
    beta_dates = common[-_BETA_WINDOW_DAYS - 1:]
    stock_rets, idx_rets = _paired(
        _returns(beta_dates, stock_by_date), _returns(beta_dates, idx_by_date))
    beta_res = _guarded_metric("beta 回归",
                               lambda: sector_beta_stats(stock_rets, idx_rets))
    if beta_res["available"]:
        out["fields"]["sector_beta_60d"] = round(beta_res["beta"], 2)
        # r2 / 特质方差互补（和恒为 1），同用 4 位精度——2 位会把 0.996→1.0、
        # 0.004→0.0，把真实微弱信号归零（fail loud 契约：0.0 必须是真的 0）
        out["fields"]["sector_r2_60d"] = round(beta_res["r_squared"], 4)
        out["fields"]["idio_var_share"] = round(1.0 - beta_res["r_squared"], 4)
        out["meta"]["sector_beta_60d"] = {"observations": beta_res["observations"]}
    else:
        reason = beta_res["reason"]
        reasons["sector_beta_60d"] = reason
        reasons["sector_r2_60d"] = reason
        reasons["idio_var_share"] = reason

    # 6. 长窗口（CSAD / 离散度 / 下行相关）：250 交易日
    long_dates = common[-_LONG_WINDOW_DAYS - 1:]
    out["window_days"] = len(long_dates) - 1
    out["window_start"] = long_dates[0]
    out["window_end"] = long_dates[-1]

    stock_rets_long, idx_rets_long = _paired(
        _returns(long_dates, stock_by_date), _returns(long_dates, idx_by_date))

    # 指数收益按结束交易日索引（F3：CSAD 以交易日对齐——压缩后位置会错位）
    idx_ret_by_date: dict[str, float] = {}
    for t in range(1, len(long_dates)):
        prev_d, cur_d = long_dates[t - 1], long_dates[t]
        p, c = idx_by_date.get(prev_d), idx_by_date.get(cur_d)
        if p is not None and c is not None and p != 0 and c != 0:
            idx_ret_by_date[cur_d] = c / p - 1.0

    # 成分股收益矩阵（逐股抓取 + 缓存；D10 一次抓取服务三个字段）
    # F3：与指数同以结束交易日为键；F2：零收盘（cur==0）一并拒绝
    returns_by_day: dict[str, list[float]] = {}
    n_with_kline = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_constituent_kline, c, cache=cache): c for c in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                closes = fut.result()
            except Exception as exc:
                logger.warning("sector_sync cons %s kline fetch failed: %s", code, exc)
                continue
            if not closes:
                continue
            n_with_kline += 1
            cmap = dict(closes)
            for t in range(1, len(long_dates)):
                prev_d, cur_d = long_dates[t - 1], long_dates[t]
                p, c = cmap.get(prev_d), cmap.get(cur_d)
                if p is None or c is None or p == 0 or c == 0:
                    continue
                returns_by_day.setdefault(cur_d, []).append(c / p - 1.0)
    out["n_constituents_with_kline"] = n_with_kline

    # 6a. 板块内离散度（%）
    disp_res = _guarded_metric(
        "板块离散度", lambda: cross_sectional_dispersion_pct(returns_by_day))
    if disp_res["available"]:
        out["fields"]["sector_dispersion"] = disp_res["dispersion_pct"]
        out["meta"]["sector_dispersion"] = {
            "n_constituents": disp_res["n_constituents"],
            # 横截面实际日期（= 末日有效成分股所在交易日）：盘中采集时成分股
            # 日线常滞后指数一日，标注当日会错标——与 window_end 分开报告
            "date": max(returns_by_day) if returns_by_day else long_dates[-1],
        }
    else:
        reasons["sector_dispersion"] = disp_res["reason"]

    # 6b. CSAD 回归（γ2）
    csad_res = _guarded_metric(
        "CSAD 回归", lambda: csad_regression(idx_ret_by_date, returns_by_day))
    if csad_res["available"]:
        # 4 位精度：γ2 量级 ~1e-2~1e-3，2 位会把 ±0.003 归零丢符号（下游以
        # gamma2<0 判羊群，-0.0 会误读为无羊群）
        out["fields"]["csad_gamma2"] = round(csad_res["gamma2"], 4)
        t_val = csad_res.get("t_stat")
        out["meta"]["csad_gamma2"] = {
            "n_days": csad_res["n_days"],
            "t_stat": round(t_val, 3) if t_val is not None else None,
        }
    else:
        reasons["csad_gamma2"] = csad_res["reason"]

    # 6c. 下行相关差（Forbes-Rigobon 校正）
    down_res = _guarded_metric(
        "下行相关", lambda: downside_correlation_gap(stock_rets_long, idx_rets_long))
    if down_res["available"]:
        out["fields"]["downside_corr_gap"] = round(down_res["gap"], 4)
        out["meta"]["downside_corr_gap"] = {
            "n_down": down_res["n_down"],
            "n_up": down_res["n_up"],
            "delta": round(down_res["delta"], 3),
            "rho_minus_raw": round(down_res["rho_minus_raw"], 3),
            "rho_minus_corr": round(down_res["rho_minus_corr"], 3),
            "rho_plus": round(down_res["rho_plus"], 3),
        }
    else:
        reasons["downside_corr_gap"] = down_res["reason"]

    out["available"] = all(out["fields"][f] is not None for f in SECTOR_SYNC_FIELDS)
    return out


def probe_sector_cache_warmth(industry_hint: str, *, cache: Any = None) -> dict:
    """F1 冷缓存门控探测：锚定板块成分股日线的缓存覆盖缺口。

    默认采集不得被成分股逐只抓取（5-10 分钟）阻塞：本探测先解析锚定板块
    （板块表走 ``sector_tables`` 缓存 1 天 TTL；指数历史/成分列表各走缓存——
    暖缓存下探测 0 网络），再统计成分股 kline 缓存缺口。缺口 ≤
    _WARMUP_FETCH_BUDGET 视为温热（现场补抓成本有界）；否则 cold + reason，
    由调用方跳过计算并 fail loud。

    返回值含 ``anchor``（解析出的锚定板块）：compute 以 anchor_override 直接
    复用，不再二次解析——probe 与 compute 之间数据源可达性翻转（东财拒连是
    文档化常态）时不会退化为全量冷抓。miss 判定与 compute 的读取校验
    （_validated_dated_closes）同口径：结构损坏/短序列条目同样计 miss；
    失败墓碑既非 miss 也非 valid（compute 侧不会为其发网络）。
    """
    if cache is None:
        cache = _default_cache()
    candidates = _resolve_provider_candidates(industry_hint, cache=cache)
    for cand in candidates:
        try:
            idx = _fetch_index_history(cand, cache=cache)
            cons = _fetch_constituents(cand, cache=cache) if idx else None
        except Exception as exc:
            logger.warning("sector_sync warmth probe %s failed: %s",
                           cand.get("provider"), exc)
            continue
        if not (idx and cons and len(cons) >= _MIN_CONSTITUENTS):
            continue
        miss = valid = 0
        for c in cons:
            raw = cache.get("sector_cons_kline", c)
            if isinstance(raw, dict) and raw.get("failed"):
                continue  # 失败墓碑：非 miss 非 valid
            if _validated_dated_closes(raw) is not None:
                valid += 1
            else:
                miss += 1
        if miss <= _WARMUP_FETCH_BUDGET:
            return {"warm": True, "miss": miss, "total": len(cons),
                    "valid": valid, "reason": None, "anchor": cand}
        return {
            "warm": False,
            "miss": miss,
            "total": len(cons),
            "valid": valid,
            "reason": (f"板块成分股日线缓存未预热（{valid}/{len(cons)} 只已缓存，"
                       f"需现场补抓 {miss} 只 > 预算 {_WARMUP_FETCH_BUDGET} 只），默认采集跳过；"
                       f"首次请用 --force-sector-sync 强制预热（约 5-10 分钟）"),
            "anchor": cand,
        }
    # 无锚定板块 / 全部候选解析失败：anchor=None + 真实 reason。调用方据此
    # 跳过 compute（fail-fast，不再给二次解析冷抓机会），下一次采集自然重试。
    hint = (industry_hint or "").strip()
    if not hint:
        reason = "行业分类缺失（basic_info 无行业字段），无法锚定板块指数"
    else:
        reason = (f"板块指数不可得（行业「{industry_hint}」未匹配到"
                  f"东财 BK 板块或申万 L1 行业，或板块列表源不可达）")
    return {"warm": True, "miss": 0, "total": 0, "valid": 0,
            "reason": reason, "anchor": None}
