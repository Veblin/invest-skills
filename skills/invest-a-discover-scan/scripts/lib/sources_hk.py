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

import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BATCH_SIZE = 100          # 腾讯 r_hk 单次可拼的代码数（实测 100 段成功）

warnings: list[str] = []


def _note(msg: str) -> None:
    warnings.append(msg)
    logger.warning("%s", msg)


def reset_warnings() -> None:
    warnings.clear()


def _load_hk_module(name: str):
    """按**显式路径**加载 invest-hk-stock 的 lib 模块（复用其客户端与解析器）。

    仿 `invest_path.load_gap_scan_module` 的既有惯例：不复制解析逻辑，
    缺失时**显式抛错**而非静默降级（换源/搬迁时能立刻发现）。
    """
    mod_name = f"discover_hk_{name}"
    mod = sys.modules.get(mod_name)
    if mod is not None:
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

    from lib.tushare_client import TushareClient

    df = TushareClient().query("hk_basic")
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
    - `ROE_AVG` 是**期间 ROE**（中报值为半年 ROE，**非年化**）；A 侧用 `roe_yearly` 年化
    - 披露节奏 = **年报 + 中报**（无季报、无强制业绩预告）
    """
    hk_fin = _load_hk_module("hk_financials")
    try:
        from lib.proxy import akshare_direct_session

        with akshare_direct_session():
            return hk_fin.fetch_financials(symbol) or []
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
    ("L3 利差（EY − rf）", "可用（口径改 US 10Y）",
     "HKD 钉住美元 → 中国 10Y 口径不当；改用 FRED `DGS10`（实测 2026-09-10 4.95）"),
    ("L3 预告增速", "不可得",
     "港股**无强制业绩预告**（`SKILL.md:74`）；tushare `forecast` 为 A 股专用"),
    ("质量门 净利 ≥ 0", "可用（口径放宽）",
     "东财 `HOLDER_PROFIT`（归母）；**港股无扣非概念** → 相对 A 侧为口径放宽"),
    ("质量门 ROE ≥ 8%", "可用（口径注记）",
     "东财 `ROE_AVG` 为**期间 ROE**（中报非年化），节奏为年报+中报 → **不可与 A 侧年化值直接比**"),
    ("L2 自身历史分位", "不可得", "A 侧 v0.1 亦未接；港股序列源仅单标的（百度）"),
)


def lens_availability() -> list[dict]:
    """每透镜的可用性（供报告头逐条标注）。"""
    return [{"lens": a, "status": b, "basis": c} for a, b, c in LENS_AVAILABILITY]
