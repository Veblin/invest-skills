"""数据源与降级链（DS-1 / T12-2，设计 §5）——叶子取数层。

数据面**全程走 tushare**（东财域在部分网络环境下不可达；本 skill 内不引入东财依赖）。
所有叶子函数失败即 raise，由上层降级链捕获并逐项记 warning（禁止静默跳过）。

T0 勘察实测（2026-09-12，`round-plans/r4-20260912.md` §1）：
- `daily_basic` 全市场：5550 行 / 0.30s
- `stock_basic`：5562 行（主板 3195 / 创业板 1407 / 科创板 617 / 北交所 343）
- `fina_indicator`：2000 分档**可用**；**只能按 `ts_code`**（`period=` / 日期区间均被拒）；
  单次约 40ms；返回帧含重复行（由 `quality.latest_report` 去重）
- `forecast`：需 `ann_date` 或 `ts_code`（日期区间被拒）→ 只按候选逐个取
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path

logger = logging.getLogger(__name__)

BASIC_TTL_DAYS = 7          # stock_basic 缓存（行业/名称/状态，月更量级）

# 叶子取数调用计数（预算式接口调用计数，设计 §7）
CALL_COUNT = {"daily_basic": 0, "fina_indicator": 0, "forecast": 0, "stock_basic": 0}

warnings: list[str] = []    # 源级失败逐项登记（调用方取走后自行清空）


def _note(msg: str) -> None:
    warnings.append(msg)
    logger.warning("%s", msg)


def reset_warnings() -> None:
    global EMPTY_RETRY_COUNT
    warnings.clear()
    EMPTY_RETRY_COUNT = 0
    for k in CALL_COUNT:
        CALL_COUNT[k] = 0


def aggregate_warnings(msgs: list[str]) -> list[str]:
    """同样消息合并为「…（×N）」——逐条刷 400 行会淹没真正的降级信号。"""
    from collections import Counter

    counts = Counter(msgs)
    return [f"{m}（×{c}）" if c > 1 else m for m, c in counts.items()]


def has_token() -> bool:
    """是否有 Tushare token（无 → CLI 退出码 4）。"""
    import os

    if os.environ.get("TUSHARE_TOKEN"):
        return True
    try:
        from lib.env import get_config

        return bool((get_config() or {}).get("TUSHARE_TOKEN"))
    except Exception:  # noqa: BLE001 —— 配置读取失败按「无 token」处理（退 4 更诚实）
        return False


_CLIENT = None


def client():
    """Tushare 客户端**进程内单例**（复用 invest-a-stock 的 canonical 实现）。

    ⚠️ 必须单例：`TushareClient.__init__` 会重置**实例级**限流状态
    （`_call_timestamps` / `_daily_calls`），而 `query()` 的自节流正是读这两个字段。
    每次新建实例 = 限流器清零 → 403 候选构造 400+ 实例 → 全速突发。
    （R4 评审实测：这正是「连发空返回、单发正常」的根因。）
    """
    global _CLIENT
    if _CLIENT is None:
        from lib.tushare_client import TushareClient

        _CLIENT = TushareClient()
    return _CLIENT


def latest_trade_date() -> str:
    """最近交易日（YYYYMMDD）。不可得 → 估算并 warning（不静默给当日）。"""
    try:
        from lib.trade_cal import last_trade_dates

        days = last_trade_dates(1)
        if days:
            return days[0]
    except Exception as exc:  # noqa: BLE001
        _note(f"交易日历不可得（{type(exc).__name__}）→ 退化为自然日估算")
    import datetime as _dt

    return _dt.date.today().strftime("%Y%m%d")


def _cache():
    """共享 DataCache（canonical 在 `skills/lib/cache.py`，**顶层导入** `cache`）。

    ⚠️ 不是 `lib.cache`——`lib` 包是 invest-a-stock 的 `scripts/lib`，其中无 cache.py。
    """
    from cache import DataCache

    return DataCache()


def fetch_stock_basic() -> list[dict]:
    """上市股票基础信息（含 industry/market/name）。7 天 TTL 缓存。

    D6：**空结果不写缓存**（否则一次失败会污染后续 7 天）。
    """
    cache = _cache()
    hit = cache.get("discover_stock_basic", "all", max_age_seconds=BASIC_TTL_DAYS * 86400)
    if hit and isinstance(hit.get("rows"), list) and hit["rows"]:
        return hit["rows"]
    CALL_COUNT["stock_basic"] += 1
    df = client().query("stock_basic", list_status="L")
    if df is None or df.empty:
        raise RuntimeError("stock_basic 空返回——全市场基础信息不可得")
    rows = df.to_dict("records")
    cache.set("discover_stock_basic", "all", {"rows": rows},
              ttl_seconds=BASIC_TTL_DAYS * 86400, source="tushare.stock_basic")
    return rows


def fetch_daily_basic(trade_date: str) -> list[dict]:
    """全市场当日 PE/PB/市值/换手（1 次调用）。"""
    CALL_COUNT["daily_basic"] += 1
    df = client().query("daily_basic", trade_date=trade_date)
    if df is None or df.empty:
        raise RuntimeError(f"daily_basic 空返回（trade_date={trade_date}）——全市场数据不可得")
    return df.to_dict("records")


_RETRY_SLEEP_SEC = 0.6      # 突发限流后的退避（设计 §7：重试 ≤1 次）
EMPTY_RETRY_COUNT = 0       # 因空返回触发的重试次数（诊断用）


def _rolling_window(years: int = 3) -> tuple[str, str]:
    """滚动查询窗（近 N 年 → 今天）。**不硬编码年份**：窗口右端固定会让
    「最近一期」随时间推移静默变陈旧，而质量闸门照常放行（陈旧与「确实没更新」
    不可区分）。"""
    import datetime as _dt

    from dates import shanghai_today

    t = shanghai_today()
    today = _dt.date(int(t[:4]), int(t[4:6]), int(t[6:8]))
    return ((today - _dt.timedelta(days=365 * years)).strftime("%Y%m%d"),
            today.strftime("%Y%m%d"))


def fetch_fina_indicator(ts_code: str, *, start_date: str | None = None,
                         end_date: str | None = None) -> list[dict]:
    """单标的财务指标（只能按 ts_code——无全市场批量形态，见模块 docstring）。

    ⚠️ **空返回 ≠ 该标的无数据**：403 只候选连发时 tushare 会突发限流，
    同一代码单独调用则正常（2026-09-12 实测：连发 200 次出现 34 次空返回，
    其中部分是限流、部分是标的本身无数据，且 `last_error` 均为 None——
    两者在当前客户端**不可区分**）。故按设计 §7 退避重试 ≤1 次；
    仍空则计入 ``EMPTY_RETRY_COUNT`` 供上层聚合报告（**不逐条刷 warning**）。
    """
    global EMPTY_RETRY_COUNT
    if start_date is None or end_date is None:
        start_date, end_date = _rolling_window()
    cli = client()
    for attempt in (0, 1):
        CALL_COUNT["fina_indicator"] += 1
        df = cli.query("fina_indicator", ts_code=ts_code,
                       start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            return df.to_dict("records")
        if attempt == 0:
            EMPTY_RETRY_COUNT += 1
            import time as _t

            _t.sleep(_RETRY_SLEEP_SEC)
    return []


def fetch_forecast(ts_code: str) -> list[dict]:
    """单标的事业绩预告（按 ts_code；日期区间查询会被 API 拒绝）。"""
    CALL_COUNT["forecast"] += 1
    try:
        df = client().query("forecast", ts_code=ts_code)
    except Exception as exc:  # noqa: BLE001 —— 预告是可选增强，失败不阻断
        _note(f"forecast 取数失败（{ts_code}）：{type(exc).__name__}")
        return []
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def rf_10y_pct() -> tuple[float | None, str]:
    """中国 10Y 国债收益率（**百分数**，如 1.73）→ ``(值, 来源)``。

    消费侧为 L3 利差透镜（`gap = EY − (rf + 2pp)`）。不可得 → ``(None, "")``，
    由调用方降级并记 warning（设计 §5）。
    """
    try:
        from _invest_path import ensure_invest_a_scripts_on_path

        ensure_invest_a_scripts_on_path()
        from lib.proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.bond_zh_us_rate(start_date="20240101")
        if df is None or getattr(df, "empty", True):
            raise RuntimeError("bond_zh_us_rate 空返回")
        col = "中国国债收益率10年"
        if col not in df.columns:
            raise RuntimeError(f"bond_zh_us_rate 缺列 {col}（列名漂移？）")
        for val in reversed(df[col].tolist()):
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f == f and f > 0:
                return f, "akshare.bond_zh_us_rate 中国国债收益率10年"
        raise RuntimeError("bond_zh_us_rate 无有效数值")
    except Exception as exc:  # noqa: BLE001 —— 降级：L3 仅保留 g_implied 项
        _note(f"中国 10Y 国债收益率不可得（{type(exc).__name__}）→ L3 利差项降级")
        return None, ""


def rf_10y_usd() -> tuple[float | None, str]:
    """美国 10Y 国债收益率（**百分数**）→ ``(值, 来源)``——**港股池用**。

    HKD 钉住美元，故港股池的无风险利率应取 US 10Y 而非中国 10Y（口径不当）。
    取 FRED ``DGS10``（需 ``FRED_API_KEY``）；不可得 → ``(None, "")`` 并记 warning。
    """
    try:
        import json as _json
        import urllib.request

        from lib.env import get_config

        key = (get_config() or {}).get("FRED_API_KEY")
        if not key:
            raise RuntimeError("无 FRED_API_KEY")
        url = (f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10"
               f"&api_key={key}&file_type=json&sort_order=desc&limit=5")
        with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 固定官方主机
            obs = _json.loads(resp.read().decode("utf-8")).get("observations") or []
        for o in obs:
            try:
                f = float(o.get("value"))
            except (TypeError, ValueError):
                continue
            if f == f and f > 0:
                return f, f"FRED.DGS10（US 10Y，{o.get('date')}）"
        raise RuntimeError("DGS10 无有效观测")
    except Exception as exc:  # noqa: BLE001
        _note(f"US 10Y 不可得（{type(exc).__name__}）→ 港股池 L3 利差项降级")
        return None, ""


def market_form_context() -> dict:
    """L4 市场语境（pulse `market_form` 快照）——**不进规则**，仅作报告头注记。

    设计 §2.1：观测标签非信号（Kirby 2023：状态标签不蕴含收益可预测性）。
    `market_form` 由 R-A01 落地；未落地/不可得时返回 available=False（**不编造**）。
    """
    try:
        local = Path(__file__).resolve().with_name("market_microstructure.py")
        lib = Path(__file__).resolve().parents[3] / "invest-a-journal" / "scripts" / "lib"
        name = "discover_market_microstructure"
        mod = sys.modules.get(name)
        if mod is None:
            # SkillHub 分发包把这个动态依赖放在本目录。与 HK loader 一样，CLI
            # 顶层导入时须人为建立包命名空间，才能让打包后模块的相对导入生效。
            target = local if local.is_file() else lib / "market_microstructure.py"
            mod_name = "discover_market_bundle.market_microstructure" if local.is_file() else name
            if local.is_file() and "discover_market_bundle" not in sys.modules:
                pkg = types.ModuleType("discover_market_bundle")
                pkg.__path__ = [str(local.parent)]
                pkg.__package__ = "discover_market_bundle"
                sys.modules["discover_market_bundle"] = pkg
            if not local.is_file():
                s = str(lib)
                if s not in sys.path:
                    sys.path.insert(0, s)
            spec = importlib.util.spec_from_file_location(mod_name, target)
            if spec is None or spec.loader is None:
                raise ImportError("market_microstructure.py 缺失")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            sys.modules[name] = mod
        snap = mod.latest_snapshot()
        label = (snap or {}).get("env_label")
        if isinstance(label, str):
            import json as _json

            try:
                label = _json.loads(label)
            except _json.JSONDecodeError:
                label = {}
        raw = (label or {}).get("market_form") if isinstance(label, dict) else None
        if isinstance(raw, dict):
            # env_label 里存的是完整 dict（form/sub_form/context/vector/kirby_note）——
            # 取展示用的短标签，**不要把整个 dict 插进报告行**
            label_txt = raw.get("form")
            if raw.get("sub_form"):
                label_txt = f"{label_txt}（{raw['sub_form']}）"
            if raw.get("context"):
                label_txt = f"{label_txt} · {raw['context']}"
            if not label_txt:
                return {"available": False, "note": "市场形态标签为空"}
            return {"available": True, "market_form": label_txt,
                    "note": "事后标注，不蕴含收益可预测性（Kirby 2023）"}
        if not raw:
            return {"available": False, "note": "市场形态标签未落地（R-A01）或历史快照为空"}
        return {"available": True, "market_form": str(raw),
                "note": "事后标注，不蕴含收益可预测性（Kirby 2023）"}
    except Exception as exc:  # noqa: BLE001 —— 注记项，失败不影响清单
        return {"available": False, "note": f"市场语境不可得（{type(exc).__name__}）"}
