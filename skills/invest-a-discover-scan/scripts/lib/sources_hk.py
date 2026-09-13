"""港股池数据层（T11-5 / HK-4）——**每透镜可用性显式标注，空透镜不冒充**。

勘察门实测（2026-09-12，见 `host-docs/v0.3.0/round-plans/r5-20260912.md` §1）：

| 探针 | 结果 |
|---|---|
| 腾讯 `r_hk` **批量**取 PE | ✅ **100 个代码一次调用**（URL 长 1021 返回 100 段）→ 2785 只约 **28 次调用** |
| tushare `hk_basic` **全量** | ✅ 2785 行；`list_status` 全 L、`curr_type` 全 HKD、市场 主板 2479 + 创业板 306；⚠️ **无 industry 字段** |
| akshare push2 成分（兜底） | ⏭️ **无需执行**——上两条已给出**零东财依赖**路径，本 skill 明令不引 push2 |
| FRED `DGS10`（US 10Y） | ✅ 最新 2026-09-10 **4.95** |
| 东财港股财务单只耗时 | ✅ **0.21s/只**（3 只 0.63s，各 9 期）→ 418 候选 ≈ 88s |

## 口径偏离声明（须在报告头显式说明）

requirements §3.4 建议 universe 取「港股通/恒指成分 v0 口径」——但那两个源一个需
**push2 域**（本 skill 刻意回避）一个**无源**；本实现改用 **`hk_basic` 全部上市港股（超集）**，
**覆盖不失且零东财依赖**。
"""
from __future__ import annotations

import datetime as _dt
import importlib
import importlib.util
import logging
import math
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BATCH_SIZE = 100          # 腾讯 r_hk 单次可拼的代码数（实测 100 段成功）

warnings: list[str] = []

# 实测调用计数（照 A 侧 `sources.CALL_COUNT` 惯例）——**不得在报告/快照里写死字面量**：
# 快照 `pool_stats` 是回填裁决的锚点，硬编码 `{"hk_basic": 1}` 会让「实际打了几次源」
# 永远无法从留档里复原（HK 路径的 r_hk 是**分批**调用，财务是**逐只**调用）。
CALL_COUNT = {"hk_basic": 0, "r_hk": 0, "hk_financials": 0}
EMPTY_RETURN_COUNT = 0      # 空返回（源返回空表，非异常）——与「标的真的无数据」不可区分


def _note(msg: str) -> None:
    warnings.append(msg)
    logger.warning("%s", msg)


def reset_warnings() -> None:
    global EMPTY_RETURN_COUNT
    warnings.clear()
    EMPTY_RETURN_COUNT = 0
    for k in CALL_COUNT:
        CALL_COUNT[k] = 0


def _load_hk_module(name: str):
    """加载 HK 复用模块，优先使用随 discover-scan 分发包携带的副本。

    主仓库中仍按显式路径复用 `invest-hk-stock` 的 canonical 实现，避免维护两份。
    SkillHub 单包构建会把动态依赖及其闭包置于本目录；此时必须经包相对导入
    取本地副本，不能再假定 sibling skill 存在。
    """
    mod_name = f"discover_hk_{name}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    local = Path(__file__).resolve().with_name(f"{name}.py")
    if local.is_file() and __package__:
        mod = importlib.import_module(f".{name}", package=__package__)
        sys.modules[mod_name] = mod
        return mod

    lib = Path(__file__).resolve().parents[3] / "invest-hk-stock" / "scripts" / "lib"
    spec = importlib.util.spec_from_file_location(mod_name, lib / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"invest-hk-stock lib/{name}.py 缺失（分发形态下不适用港股池）")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------

def fetch_hk_universe() -> list[dict]:
    """`hk_basic` 全量 → ``[{ts_code, symbol, name, market, currency}]``。

    带 7 天缓存（同 A 侧的 `stock_basic` 惯例）；**空结果不写缓存**（D6）。
    """
    from cache import DataCache

    cache = DataCache()
    hit = cache.get("discover_hk_basic", "all", max_age_seconds=7 * 86400)
    if hit and isinstance(hit.get("rows"), list) and hit["rows"]:
        return hit["rows"]

    # ⚠️ 必须走 `sources.client()` 的**进程内单例**：`TushareClient.__init__` 会重置
    # 实例级限流状态（`_call_timestamps`/`_daily_calls`），每次新建实例 = 限流器清零
    # → 全速突发（**R4 评审实测的限流根因**，见 `sources.client()` docstring）。
    # 同进程内 A 股管线先跑时尤其明显：HK 分支另起实例会绕开 A 侧已积累的节流状态。
    import sources as _sources

    CALL_COUNT["hk_basic"] += 1
    df = _sources.client().query("hk_basic")
    if df is None or df.empty:
        raise RuntimeError("hk_basic 空返回——港股 universe 不可得")
    rows: list[dict] = []
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code") or "")
        if not ts_code.endswith(".HK"):
            continue
        if str(r.get("list_status") or "L") != "L":
            continue
        rows.append({
            "ts_code": ts_code,
            "symbol": ts_code.split(".")[0],          # 5 位（腾讯 r_hk 用 5 位）
            "name": str(r.get("name") or ""),
            "market": str(r.get("market") or ""),
            "currency": str(r.get("curr_type") or ""),
            # ⚠️ hk_basic **无 industry 字段** → 行业透镜不可得（见 lens_availability）
            "industry": None,
        })
    if not rows:
        raise RuntimeError("hk_basic 无有效上市港股行")
    cache.set("discover_hk_basic", "all", {"rows": rows},
              ttl_seconds=7 * 86400, source="tushare.hk_basic")
    return rows


# ---------------------------------------------------------------------------
# 横截面（腾讯 r_hk 批量）
# ---------------------------------------------------------------------------

def fetch_hk_quote_batch(symbols: list[str]) -> dict[str, dict]:
    """腾讯 `r_hk` **批量**行情 → ``{symbol: {price, pe_ttm, mcap_hkd_yi, ...}}``。

    分批（`BATCH_SIZE`）+ 单批失败即记 warning 并跳过（**不阻断整轮**）；
    解析复用 invest-hk-stock 的 `hk_quote.parse_tencent_hk`（单一实现）。
    """
    import urllib.request

    hk_quote = _load_hk_module("hk_quote")
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[i: i + BATCH_SIZE]
        CALL_COUNT["r_hk"] += 1
        url = "https://qt.gtimg.cn/q=" + ",".join(f"r_hk{s}" for s in chunk)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="replace")
        except Exception as exc:  # noqa: BLE001 —— 单批降级，不阻断整轮
            _note(f"腾讯 r_hk 批次 {i // BATCH_SIZE + 1} 失败（{type(exc).__name__}）→ 该批跳过")
            continue
        for seg in raw.split(";"):
            if "=" not in seg or "~" not in seg:
                continue
            try:
                q = hk_quote.parse_tencent_hk(seg.strip())
            except Exception:  # noqa: BLE001 —— 单段解析失败跳过
                continue
            code = str(q.get("code") or "").lstrip("hk")
            if code:
                out[code] = q
    return out


# ---------------------------------------------------------------------------
# 财务（东财港股，需直连上下文）
# ---------------------------------------------------------------------------

def fetch_hk_financials(symbol: str) -> list[dict]:
    """东财港股财务（近若干期）。

    ⚠️ **口径**（与 A 侧不可直接比）：
    - **无扣非概念**（`hk_financials` docstring）→ 净利用 `HOLDER_PROFIT`（归母）
    - `ROE_AVG` 实测为**年度 ROE**（该接口返回年报行，108 行全 `DATE_TYPE_CODE=001`）；
      若出现中期行由 `annualized_roe` 按报告期年化后再比
    - 披露节奏**以年报为主**（无季报、无强制业绩预告）
    """
    global EMPTY_RETURN_COUNT
    hk_fin = _load_hk_module("hk_financials")
    CALL_COUNT["hk_financials"] += 1
    try:
        from lib.proxy import akshare_direct_session

        with akshare_direct_session():
            rows = hk_fin.fetch_financials(symbol) or []
            if not rows:
                EMPTY_RETURN_COUNT += 1
            return rows
    except Exception as exc:  # noqa: BLE001 —— 单标的降级
        _note(f"{symbol} 东财港股财务不可得（{type(exc).__name__}）")
        return []


# ---------------------------------------------------------------------------
# 每透镜可用性（HK-4 验收核心：**每透镜标可用性、空透镜不冒充**）
# ---------------------------------------------------------------------------

LENS_AVAILABILITY: tuple[tuple[str, str, str], ...] = (
    ("L1 pe_grank（横截面分位）", "可用",
     "universe 重定义为**港股池正 PE 子总体**（非全 A）——口径已随 rules_version bump"),
    ("L1 ind_rk（行业内排名）", "不可得",
     "`hk_basic` **无 industry 字段**（实测）；港股无行业分类源已接入 → 条件跳过 + warning"),
    # ⚠️ 本表是**静态可用性说明**，不得写死行情数值：曾在此写「实测 2026-09-10 4.95」，
    # 该字面量会随每次渲染进入报告并**永久陈旧**（P0：报告数字须来自引擎字段或 calc）。
    # 实时值由渲染侧的 L3 行给出（带 [来源:] 标签）。
    ("L3 利差（EY − rf）", "可用（口径改 US 10Y）",
     "HKD 钉住美元 → 中国 10Y 口径不当；改用 FRED `DGS10`（实时值见下方 L3 行）"),
    ("L3 预告增速", "不可得",
     "港股**无强制业绩预告**（`SKILL.md:74`）；tushare `forecast` 为 A 股专用"),
    ("质量门 净利 ≥ 0", "可用（口径放宽）",
     "东财 `HOLDER_PROFIT`（归母）；**港股无扣非概念** → 相对 A 侧为口径放宽"),
    ("质量门 ROE ≥ 8%", "可用（**按报告期口径年化归一后比较**）",
     "东财 `ROE_AVG` 即**年度 ROE**（实测该接口返回年报行，`DATE_TYPE_CODE=001`）；"
     "若出现中期行（002）则按报告期年化 ×2 后再比。**财年各异**（6 月财年 00016 等、"
     "3 月财年 09988）——故不按日历后缀判口径；**与 A 侧 `roe_yearly` 同义但来源不同**"),
    ("L2 自身历史分位", "不可得", "A 侧 v0.1 亦未接；港股序列源仅单标的（百度）"),
)


def _period_months(fin_row: dict) -> float | None:
    """报告期长度（月，按 30.44 天/月折算）；起止任一缺失或不可解析 → None。"""
    start = str(fin_row.get("period_start") or "")[:10]
    end = str(fin_row.get("report_date") or "")[:10]
    if len(start) < 10 or len(end) < 10:
        return None
    try:
        a = _dt.date.fromisoformat(start)
        b = _dt.date.fromisoformat(end)
    except ValueError:
        return None
    return (b - a).days / 30.44


def annualized_roe(fin_row: dict) -> float | None:
    """东财 `ROE_AVG` → **年化** ROE（与 A 侧 `roe_yearly` 同义）。

    ⚠️ **判据是报告期，不是日历后缀**（2026-09-13 实测纠错）：
    本 skill 用的东财接口返回**年报行**（12 只样本 × 9 期 = 108 行全为
    `DATE_TYPE_CODE=001`），`ROE_AVG` 即年度 ROE。而各公司财年不同——
    6 月财年（00016/00017/00083/00659）的年报 `report_date` 正是 `06-30`、
    3 月财年（09988）是 `03-31`。**按日历后缀判中报会把它们的年度 ROE 翻倍**
    （新地真实 3.42% → 误算 6.85%），反之 12 月财年公司不受影响——即错得**不对称**。

    判据优先级：① `report_type`（`002` 中报 → ×2；`001` 年报 → 原值）；
    ② 缺失时用**报告期长度**（≤7 个月 → ×2；≥9 个月 → 原值）；
    ③ 两者都不可得 → **不年化**（宁可保守，也不按日历猜口径）。

    ⚠️ NaN / ±Inf 一律 → None（三态，见 `quality._num`）：Inf 会**恒 > 阈值**直接放行。
    """
    if not isinstance(fin_row, dict):
        return None
    raw = fin_row.get("roe")
    try:
        f = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    if f is None or f != f or math.isinf(f):
        return None
    rtype = str(fin_row.get("report_type") or "").strip()
    if rtype == "002":
        factor = 2.0
    elif rtype == "001":
        factor = 1.0
    else:
        months = _period_months(fin_row)
        factor = 2.0 if (months is not None and months <= 7.0) else 1.0
    return f * factor


def lens_availability() -> list[dict]:
    """每透镜的可用性（供报告头逐条标注）。"""
    return [{"lens": a, "status": b, "basis": c} for a, b, c in LENS_AVAILABILITY]
