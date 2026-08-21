"""宏观数据采集模块（层 5: 宏观层）。

自动采集 PMI/CPI/PPI/LPR 等中国宏观指标，以及 VIX/SOX 等全球指标。
v0.2.7 E2/E3：新增 12 个 FRED 跨资产 series + NY Fed ACMTP10 周频 + 序列消费。

E2 明确排除（探测确认不可得，勿尝试实现）：
- 非美 30Y 主权债：FRED 无该 OECD series，akshare bond_investing_global 已从 1.18.64 移除
- 钨价：akshare 全包源码无「钨」字
- WGC 全球央行购金：gold.org 下载端点 Cloudflare 拦截（中国央行口径可用
  akshare macro_china_foreign_exchange_gold 替代）
- 粮食/化肥指数（PFOODINDEXM 等）：技术上可得，与 A 股个股研究距离过远，不纳入

已知数据源陷阱（写死注释，防后人踩坑）：
1. 美国财政部 mfh.txt 停更于 2023-03（HTTP 200 但数据停在 2023-01）——
   如做 TIC 必须走 cslt.zip 或 FRED FORTREASPOS* 系列
2. Yahoo XAUUSD=X 已 404 移除，国际金价用 GC=F；FRED 无黄金价格 series
3. ACMTP10 不在 FRED 公开目录（fredgraph.csv?id=ACMTP10 实测 404），
   须从 NY Fed 官网取（见 _fetch_acm_term_premia）
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable

from ._invest_path import ensure_skills_lib_on_path  # noqa: E402

ensure_skills_lib_on_path()

from cache import default_cache  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数（日期格式用 skills/lib/dates；避免从 collector 循环导入）
# ---------------------------------------------------------------------------

from .shared_dates import (  # noqa: E402
    latest_month_row as _latest_month_row,
    shanghai_days_ago as _days_ago,
    shanghai_now as _shanghai_now,
    shanghai_today as _today,
    yyyymmdd_to_iso as _to_iso_date,
)
from lib.nums import row_value_or_last, safe_float as _safe_num  # noqa: E402


# ---------------------------------------------------------------------------
# 全球指标采集（FRED / Yahoo Finance）
# ---------------------------------------------------------------------------

def _sanitize_cpi(value: float) -> float | None:
    """R12c: CPI 口径归一与异常拦截。

    - 合理同比区间 (-5, 30) → 直接采用
    - 基期指数口径 (85, 200) → 转换为同比 (value - 100)
    - 其余 → None（异常值，调用方标注「不可靠」）
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if -5 < v < 30:
        return round(v, 2)
    if 85 < v < 200:
        return round(v - 100, 2)
    return None


def _fetch_fred_series(
    series_id: str,
    config: dict,
    lookback_days: int = 90,
) -> tuple[float | None, list[tuple[str, float]]]:
    """从 FRED 抓取单个 series 的日序列。

    Returns:
        (latest_value, [(date, value), ...]) — latest_value 为最近有效值，
        序列按日期升序。失败时返回 (None, [])。
    """
    from . import env

    if not env.is_fred_available(config):
        return None, []

    key = config.get("FRED_API_KEY", "")
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": _to_iso_date(_days_ago(lookback_days)),
        "observation_end": _to_iso_date(_today()),
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("FRED %s fetch failed: %s", series_id, exc)
        return None, []

    out: list[tuple[str, float]] = []
    for obs in payload.get("observations", []):
        val = obs.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((obs.get("date", ""), float(val)))
        except (TypeError, ValueError):
            continue

    if not out:
        return None, []
    latest = out[-1][1]
    return latest, out


def _fetch_sox_via_yahoo() -> float | None:
    """从 Yahoo Finance v8 API 抓取 SOX（费城半导体指数）最新价。

    免费、无需 API Key。失败返回 None。
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?interval=1d&range=5d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("SOX Yahoo fetch failed: %s", exc)
        return None

    try:
        result = payload["chart"]["result"][0]
        price = result["meta"]["regularMarketPrice"]
        return float(price)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("SOX Yahoo parse failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# E2 v0.2.7: 跨资产锚 series（12 个 FRED + NY Fed ACMTP10 周频）
#
# 纳入清单（host-docs/v0.2.7/requirements-integrated-v0.2.7.md §1.4 裁决：
# 规格书「9 个」为错误计数，以表格为准 = 12 个 FRED series + 1 个 ACM 周频）：
#   日频 8 个: DGS30 / DGS10 / DFII10 / T10Y2Y / T5YIE / DTWEXBGS /
#              DCOILBRENTEU / DEXCHUS
#   月频 4 个: IRLTLT01{GB,DE,FR,JP}M156N（英/德/法/日 10Y，滞后约 2.5 个月，
#              C6 逐国主权债复算的数据基础）
# 阈值信号仅作标签提示，非交易信号；档位依据 2026-08 市况常识划分。
# ---------------------------------------------------------------------------

# 模块级缓存（skills/lib/cache.py DataCache）。测试须 patch 为 tmp 目录，
# 避免 mock 采集结果写入真实缓存（见 tests/test_macro_extended.py 的 autouse fixture）。
_macro_cache = default_cache()

# 日频 series：key → (series_id, 显示名, 阈值信号函数, lookback 天, TTL 秒)
_FRED_DAILY_SPECS: dict[str, dict[str, Any]] = {
    "dgs10": {
        "series_id": "DGS10", "label": "DGS10",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "高位" if v >= 4.5 else ("低位" if v < 3.5 else "中位"),
    },
    "dgs30": {
        "series_id": "DGS30", "label": "DGS30",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "高位" if v >= 5.0 else ("低位" if v < 4.0 else "中位"),
    },
    "dfii10": {
        "series_id": "DFII10", "label": "DFII10",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "负实际利率" if v < 0 else (
            "低实际利率" if v < 1.0 else ("中性" if v < 2.0 else "高实际利率")),
    },
    "t10y2y": {
        "series_id": "T10Y2Y", "label": "T10Y2Y",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "倒挂" if v < 0 else ("平坦" if v < 0.5 else "正常"),
    },
    "t5yie": {
        "series_id": "T5YIE", "label": "T5YIE",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "低通胀预期" if v < 2.0 else (
            "正常" if v < 2.5 else "高通胀预期"),
    },
    "dtwexbgs": {
        "series_id": "DTWEXBGS", "label": "DTWEXBGS",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "",  # 广义美元指数（2006=100），无绝对阈值 → 仅展示
    },
    "dcoilbrenteu": {
        "series_id": "DCOILBRENTEU", "label": "DCOILBRENTEU",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "低位" if v < 60 else ("中性" if v < 90 else "高位"),
    },
    "dexchus": {
        "series_id": "DEXCHUS", "label": "DEXCHUS",
        "lookback_days": 90, "ttl_seconds": 6 * 3600,
        "signal": lambda v: "人民币偏强" if v < 6.8 else (
            "正常" if v <= 7.3 else "人民币偏弱"),
    },
}

# 月频主权债 series（C6 数据基础）：key → (series_id, 显示名)
_FRED_SOVEREIGN_SPECS: dict[str, dict[str, Any]] = {
    "sovereign_gb10y": {
        "series_id": "IRLTLT01GBM156N", "label": "GB10Y",
        "lookback_days": 10000, "ttl_seconds": 12 * 3600,  # ~27 年，覆盖 20 年窗口 + 2007 峰值
        "signal": lambda v: "",
    },
    "sovereign_de10y": {
        "series_id": "IRLTLT01DEM156N", "label": "DE10Y",
        "lookback_days": 10000, "ttl_seconds": 12 * 3600,
        "signal": lambda v: "",
    },
    "sovereign_fr10y": {
        "series_id": "IRLTLT01FRM156N", "label": "FR10Y",
        "lookback_days": 10000, "ttl_seconds": 12 * 3600,
        "signal": lambda v: "",
    },
    "sovereign_jp10y": {
        "series_id": "IRLTLT01JPM156N", "label": "JP10Y",
        "lookback_days": 10000, "ttl_seconds": 12 * 3600,
        "signal": lambda v: "",
    },
}

# E3 需要全序列的额外拉取（与 collect 的 90 天窗口不同 key，缓存独立）：
_DGS30_FULL_LOOKBACK_DAYS = 9125   # ~25 年（分位/距高点窗口）
_DGS30_FULL_TTL_SECONDS = 12 * 3600


def _fred_cache_symbol(series_id: str, lookback_days: int, config: dict) -> str:
    """缓存键（含 FRED key 哈希前 8 位）。

    隔离理由：测试以 mock key（如 "a"*32）全量 mock _fetch_fred_series 时，
    若键不含 key 会把假值写入真实缓存污染生产路径（test_v015_fixes 即如此
    mock）；真实 key 与测试 key 哈希不同 → 天然隔离。
    """
    key = config.get("FRED_API_KEY", "")
    digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:8]
    return f"fred:{series_id}:{lookback_days}:{digest}"


def _fetch_fred_series_cached(
    series_id: str,
    config: dict,
    lookback_days: int,
    ttl_seconds: int,
) -> tuple[float | None, list[tuple[str, float]]]:
    """_fetch_fred_series + DataCache 包裹。

    - D6: 空结果不写缓存
    - D7: 返回的 series 列表与缓存条目共享引用，调用方只读
    - 返回 (latest_value, [(date, value), ...])，失败返回 (None, [])。
    """
    symbol = _fred_cache_symbol(series_id, lookback_days, config)
    cached = _macro_cache.get("macro", symbol, max_age_seconds=ttl_seconds)
    if cached is not None:
        # JSON 缓存读写后元组变列表 → 归一为元组，保持与直取路径同型
        series = [tuple(x) for x in (cached.get("series") or [])]
        return cached.get("value"), series
    value, series = _fetch_fred_series(series_id, config, lookback_days=lookback_days)
    if not series:
        return None, []
    _macro_cache.set(
        "macro", symbol,
        {"value": value, "series": series},
        ttl_seconds=ttl_seconds,
        source=f"FRED.{series_id}",
    )
    return value, series


def _as_of_date(series: list[tuple[str, float]], freq: str) -> str:
    """序列末观测日期；月频取 YYYY-MM（staleness 标注用）。"""
    if not series:
        return ""
    last = series[-1][0]
    if freq == "monthly":
        return last[:7]
    return last


def _lag_note(as_of: str, freq: str) -> str:
    """月频/周频数据的截至日期 + 滞后标注（E5: 月频海外数据强制 staleness）。

    月频发布惯例：数据月末生成、次月中旬发布，再加约 0.5 个月口径 →
    「滞后约 2.5 个月」即月差 + 0.5。实际滞后以 Python 计算为准（P0）。
    """
    if not as_of:
        return "数据不可得"
    if freq == "monthly":
        try:
            asof = datetime.strptime(as_of, "%Y-%m")
        except ValueError:
            return f"月频，截至 {as_of}"
        now = _shanghai_now()
        months = (now.year - asof.year) * 12 + (now.month - asof.month)
        return f"月频，截至 {as_of}，滞后约 {months + 0.5:g} 个月"
    if freq == "weekly":
        return f"周更新，截至 {as_of}"
    return f"截至 {as_of}"


def _new_nyfed_session() -> Any:
    """NY Fed 直连 Session：trust_env=False 强制绕过 Clash/代理。

    实测：10MB xls 经系统代理 urllib 下载 30s 超时 / SSL EOF，直连稳定。
    requests 为项目锁定依赖（pyproject dependencies），非新增依赖。
    """
    import requests  # noqa: PLC0415

    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {"http": None, "https": None}
    return sess


def _fetch_acm_term_premia(_session: Any = None) -> tuple[float | None, str | None]:
    """NY Fed ACM 10Y 期限溢价（ACMTP10，周更新）。

    数据源陷阱（E2 规格必写注释）：
    - ACMTP10 不在 FRED 公开目录（fredgraph.csv?id=ACMTP10 实测 404）
    - 规格称「周频 xlsx」：实测官方文件为 .xls（CDFV2，约 10MB），含
      'ACM Monthly'（月频）与 'ACM Daily'（日频）两表，官网每周更新一次
      （FRED 历史 ACMTP10 亦为周频）→ 取 'ACM Daily' 表 ACMTP10 列末行
    - 主路径失败降级到 acmPlot_data.csv（月频，TERMYld 列 = ACMTP10 月频，
      stdlib csv 解析）；两者皆失败返回 (None, None)
    - 无需 API key；依赖 xlrd（akshare 的锁定传递依赖，见 uv.lock，非新增依赖）
    - _session 为测试注入点（D13: mock 可验证生效，不打全局 patch）
    """
    if _session is None:
        _session = _new_nyfed_session()
    xls_url = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"
    try:
        resp = _session.get(xls_url, timeout=60)
        resp.raise_for_status()
        value, as_of = _acm_parse_xls(resp.content)
        if value is not None:
            return value, as_of
    except Exception as exc:
        logger.warning("ACMTP10 xls fetch failed: %s", exc)
    csv_url = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv"
    try:
        resp = _session.get(csv_url, timeout=60)
        resp.raise_for_status()
        return _acm_parse_csv(resp.text)
    except Exception as exc:
        logger.warning("ACMTP10 csv fallback failed: %s", exc)
        return None, None


def _acm_parse_xls(data: bytes) -> tuple[float | None, str | None]:
    """解析 ACMTermPremium.xls（CDFV2）'ACM Daily' 表 ACMTP10 列末行。"""
    try:
        import xlrd  # noqa: PLC0415 — akshare 锁定传递依赖（uv.lock），非新增依赖

        wb = xlrd.open_workbook(file_contents=data)
        sh = wb.sheet_by_name("ACM Daily")
        hdr = sh.row_values(0)
        date_i = hdr.index("DATE")
        tp_i = hdr.index("ACMTP10")
        row = sh.row_values(sh.nrows - 1)
        return float(row[tp_i]), _nyfed_date_to_iso(str(row[date_i]))
    except Exception as exc:
        logger.warning("ACMTP10 xls parse failed: %s", exc)
        return None, None


def _acm_parse_csv(text: str) -> tuple[float | None, str | None]:
    """解析 acmPlot_data.csv（月频降级路径）。列: RunDates,TERMYld,ACMFITYld,GSWYld。"""
    try:
        rows = list(csv.DictReader(text.splitlines()))
    except Exception as exc:
        logger.warning("ACMTP10 csv parse failed: %s", exc)
        return None, None
    if not rows:
        return None, None
    last = rows[-1]
    try:
        return float(last["TERMYld"]), _nyfed_date_to_iso(last["RunDates"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("ACMTP10 csv row parse failed: %s", exc)
        return None, None


def _nyfed_date_to_iso(raw: str) -> str | None:
    """NY Fed 日期格式（'31-Jul-2026'）→ ISO（'2026-07-31'）。"""
    try:
        return datetime.strptime(raw.strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _fetch_acm_term_premia_cached() -> tuple[float | None, str | None]:
    """_fetch_acm_term_premia + DataCache（10MB xls 下载，12h TTL；D6 空结果不缓存）。"""
    symbol = "nyfed:acm_tp10:v1"
    cached = _macro_cache.get("macro", symbol, max_age_seconds=12 * 3600)
    if cached is not None:
        return cached.get("value"), cached.get("as_of")
    value, as_of = _fetch_acm_term_premia()
    if value is None:
        return None, None
    _macro_cache.set(
        "macro", symbol, {"value": value, "as_of": as_of},
        ttl_seconds=12 * 3600, source="NYFed.ACMTermPremium",
    )
    return value, as_of


def _fetch_us_curve_akshare() -> dict[str, float]:
    """无 FRED key 时的美债曲线降级：akshare bond_zh_us_rate。

    一表覆盖中美 2/5/10/30 年全曲线，当日可得——**比 FRED 新约一个交易日**
    （FRED 约美东 11am 发布、滞后一日）。取含美国 10Y 值的最近一行
    （实测 2026-08-20 行美国列为 NaN，须跳过）。返回 {"dgs10":..., "dgs30":...,
    "t10y2y":...}；失败返回 {}（调用方记 failures）。
    """
    try:
        from .proxy import akshare_direct_session

        with akshare_direct_session():
            import akshare as ak

            df = ak.bond_zh_us_rate()
    except Exception as exc:
        logger.warning("bond_zh_us_rate fallback fetch failed: %s", exc)
        return {}
    if df is None or df.empty:
        return {}
    records = df.to_dict("records")
    for row in reversed(records):  # 实测表为升序——从尾行找最近含 US 10Y 的完整行
        us10 = row.get("美国国债收益率10年")
        us30 = row.get("美国国债收益率30年")
        us2 = row.get("美国国债收益率2年")
        # NaN 不是 None（pandas 空列返回 nan）——用 _safe_num 拒绝（D1/D5）
        dgs10 = _safe_num(us10)
        dgs30 = _safe_num(us30)
        us2v = _safe_num(us2)
        if dgs10 is None or dgs30 is None or us2v is None:
            continue
        return {
            "dgs10": dgs10,
            "dgs30": dgs30,
            "t10y2y": dgs10 - us2v,  # P0: Python 计算，口径与展示的 10Y/2Y 一致
        }
    return {}


# ---------------------------------------------------------------------------
# 主采集与标签函数
# ---------------------------------------------------------------------------

def collect_macro_context(symbol: str = "") -> dict[str, Any]:
    """采集宏观背景数据。返回 {indicator_name: {value, source, signal}} 映射。

    当前支持的指标：
    - 中国: PMI/CPI/PPI/LPR (akshare)、M2/新增贷款 (akshare)、
      money_supply/loan（信用脉冲参考）
    - 全球: VIX (FRED VIXCLS)、SOX (Yahoo ^SOX)
    - E2 v0.2.7 跨资产锚: DGS10/DGS30/DFII10/T10Y2Y/T5YIE/DTWEXBGS/
      DCOILBRENTEU/DEXCHUS（FRED 日频）、IRLTLT01{GB,DE,FR,JP}M156N
      （英德法日 10Y，月频滞后约 2.5 个月）、ACMTP10（NY Fed 周更新）
    - 降级: 无 FRED key 时 dgs10/dgs30/t10y2y 走 akshare bond_zh_us_rate
      （当日可得，较 FRED 新约 1 个交易日）；其余无等价免费源 → None

    每个指标采集失败时独立降级，不阻塞其他指标。
    """
    from . import env

    context: dict[str, Any] = {
        "pmi": None,
        "cpi": None,
        "ppi": None,
        "lpr": None,
        "money_supply": None,
        "loan": None,
        "vix": None,
        "sox": None,
        # E2 v0.2.7: 新 series 键预置 None —— 失败时保持显式 None 而非缺键，
        # 调用方 `context[key] is None` 检查不依赖异常路径（KeyError 兜底不可靠）
        **{k: None for k in _FRED_DAILY_SPECS},
        **{k: None for k in _FRED_SOVEREIGN_SPECS},
        "acm_tp10": None,
    }

    failures: list[str] = []
    # 中国指标依赖 akshare；全球指标（VIX/SOX）独立，akshare 不可用不阻塞。
    akshare_ok = env.is_akshare_available()
    if not akshare_ok:
        failures.extend(["PMI", "CPI", "PPI", "LPR"])
        logger.warning("akshare 不可用，跳过中国宏观指标，继续采集 VIX/SOX")
    else:
        # PMI
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_pmi()
                if df is not None and not df.empty:
                    # F0-4: akshare 序列最新在前，iloc[-1] 会取到 2008 年最旧行；
                    # 按「月份」列取最新期行。
                    row = _latest_month_row(df.to_dict("records"))
                    pmi_val = None
                    for col in ["制造业-指数", "制造业"]:
                        v = row.get(col)
                        if v is not None:
                            pmi_val = float(v)
                            break
                    if pmi_val is None:
                        # row 已由 df.to_dict("records") 转为 dict（F0-4），
                        # iloc 是 Series 专属 API——取末列值兜底
                        # （review 二轮 R-13：收敛到 nums.row_value_or_last 可单测）
                        pmi_val = row_value_or_last(row)
                    if pmi_val is not None:
                        context["pmi"] = {
                            "value": round(pmi_val, 2),
                            "signal": "扩张" if pmi_val >= 50 else "收缩",
                            "source": "akshare.macro_china_pmi",
                        }
            if context["pmi"] is None:
                failures.append("PMI")
        except Exception as exc:
            logger.warning("PMI fetch failed: %s", exc)
            failures.append("PMI")

        # CPI
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_cpi()
                if df is not None and not df.empty:
                    row = _latest_month_row(df.to_dict("records"))
                    cpi_val = None
                    for col in ["全国-当月", "全国"]:
                        v = row.get(col)
                        if v is not None:
                            cpi_val = float(v)
                            break
                    if cpi_val is not None:
                        # R12c: 口径归一 + 合理性校验。akshare macro_china_cpi 末行
                        # 可能返回同比%（如 0.3）或基期指数口径（如 107.1）——
                        # 实测 2026-08 曾把 107.1 误当同比渲染为 "CPI +107.1%"。
                        cpi_clean = _sanitize_cpi(cpi_val)
                        if cpi_clean is not None:
                            context["cpi"] = {
                                "value": cpi_clean,
                                "signal": "通胀" if cpi_clean > 3 else ("通缩" if cpi_clean < 0 else "温和"),
                                "source": "akshare.macro_china_cpi",
                            }
                        else:
                            logger.warning("CPI raw value %s outside sane range; marked unreliable", cpi_val)
                            context["cpi"] = {"value": None, "signal": "不可靠", "source": "akshare.macro_china_cpi"}
            if context["cpi"] is None:
                failures.append("CPI")
        except Exception as exc:
            logger.warning("CPI fetch failed: %s", exc)
            failures.append("CPI")

        # PPI
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_ppi()
                if df is not None and not df.empty:
                    row = _latest_month_row(df.to_dict("records"))
                    ppi_val = None
                    for col in ["全国-当月", "全国"]:
                        v = row.get(col)
                        if v is not None:
                            ppi_val = float(v)
                            break
                    if ppi_val is not None:
                        context["ppi"] = {
                            "value": round(ppi_val, 2),
                            "signal": "上行" if ppi_val > 0 else "下行",
                            "source": "akshare.macro_china_ppi",
                        }
            if context["ppi"] is None:
                failures.append("PPI")
        except Exception as exc:
            logger.warning("PPI fetch failed: %s", exc)
            failures.append("PPI")

        # LPR
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_lpr()
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    lpr_1y = None
                    for col in ["1年期", "LPR1Y"]:
                        v = row.get(col)
                        if v is not None:
                            lpr_1y = float(v)
                            break
                    if lpr_1y is not None:
                        context["lpr"] = {
                            "value": round(lpr_1y, 2),
                            "signal": "偏宽松" if lpr_1y <= 3.5 else ("中性" if lpr_1y <= 4.0 else "偏紧"),
                            "source": "akshare.macro_china_lpr",
                        }
            if context["lpr"] is None:
                failures.append("LPR")
        except Exception as exc:
            logger.warning("LPR fetch failed: %s", exc)
            failures.append("LPR")

        # M2 货币供应量同比增速（信用脉冲参考）
        try:
            from .proxy import akshare_direct_session

            with akshare_direct_session():
                import akshare as ak

                df = ak.macro_china_money_supply()
                if df is not None and not df.empty:
                    # 注意：此 API 返回降序（最新在前），用 iloc[0]
                    row = df.iloc[0]
                    m2_yoy = None
                    for col in ["货币和准货币(M2)-同比增长"]:
                        v = row.get(col)
                        if v is not None:
                            m2_yoy = float(v)
                            break
                    if m2_yoy is not None:
                        # 信用脉冲信号：M2 同比 − 名义GDP增速(估~5%)
                        credit_pulse = round(m2_yoy - 5.0, 1)
                        if m2_yoy > 12:
                            signal = "宽松"
                        elif m2_yoy > 8:
                            signal = "偏松"
                        elif m2_yoy >= 5:
                            signal = "稳健"
                        else:
                            signal = "偏紧"
                        context["money_supply"] = {
                            "value": round(m2_yoy, 2),
                            "signal": signal,
                            "credit_pulse": credit_pulse,
                            "source": "akshare.macro_china_money_supply",
                        }
            if context["money_supply"] is None:
                failures.append("M2")
        except Exception as exc:
            logger.warning("M2 fetch failed: %s", exc)
            failures.append("M2")

        # 新增人民币贷款（月度）
        try:
            if context.get("money_supply") is not None:  # 共用 akshare session
                from .proxy import akshare_direct_session

                with akshare_direct_session():
                    import akshare as ak

                    df = ak.macro_rmb_loan()
                    if df is not None and not df.empty:
                        row = df.iloc[-1]
                        loan_val = None
                        loan_yoy_str = row.get("新增人民币贷款-同比", "")
                        for col in ["新增人民币贷款-总额"]:
                            v = row.get(col)
                            if v is not None:
                                loan_val = float(v)
                                break
                        if loan_val is not None:
                            # 同比增速（去掉 % 符号）
                            loan_yoy = None
                            if isinstance(loan_yoy_str, str) and "%" in loan_yoy_str:
                                try:
                                    loan_yoy = float(loan_yoy_str.replace("%", ""))
                                except ValueError:
                                    pass
                            context["loan"] = {
                                "value": round(loan_val, 2),
                                "yoy": loan_yoy,
                                "signal": "扩张" if loan_val > 15000 else ("正常" if loan_val > 5000 else "收缩"),
                                "source": "akshare.macro_rmb_loan",
                            }
            if context["loan"] is None:
                failures.append("Loan")
        except Exception as exc:
            logger.warning("Loan fetch failed: %s", exc)
            failures.append("Loan")

    # VIX (CBOE Volatility Index via FRED)
    try:
        config = env.get_config()
        latest, _ = _fetch_fred_series("VIXCLS", config, lookback_days=30)
        if latest is not None:
            if latest < 15:
                vix_signal = "低波"
            elif latest < 25:
                vix_signal = "正常"
            elif latest < 35:
                vix_signal = "偏高"
            else:
                vix_signal = "恐慌"
            context["vix"] = {
                "value": round(latest, 2),
                "signal": vix_signal,
                "source": "FRED.VIXCLS",
            }
        if context["vix"] is None:
            failures.append("VIX")
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
        failures.append("VIX")

    # SOX (Philadelphia Semiconductor Index via Yahoo Finance)
    try:
        sox_val = _fetch_sox_via_yahoo()
        if sox_val is not None:
            context["sox"] = {
                "value": round(sox_val, 2),
                "signal": "",
                "source": "YahooFinance.^SOX",
            }
        if context["sox"] is None:
            failures.append("SOX")
    except Exception as exc:
        logger.warning("SOX fetch failed: %s", exc)
        failures.append("SOX")

    # ---- E2 v0.2.7: 跨资产锚 series（12 个 FRED + NY Fed ACMTP10）----
    # 单个 series 失败独立降级，不阻塞其余（与既有 8 指标同模式）。
    # 无 FRED key → 美债曲线（DGS10/DGS30/T10Y2Y）走 akshare bond_zh_us_rate
    # 降级（当日可得，较 FRED 新约 1 个交易日）；DFII10/T5YIE/DTWEXBGS/
    # DCOILBRENTEU/DEXCHUS/英德法日 10Y 无等价免费源 → 记 failures，不填充。
    # 月频/周频数据强制输出截至日期 + 滞后标注（E5 staleness）。
    config = env.get_config()
    if env.is_fred_available(config):
        for key, spec in _FRED_DAILY_SPECS.items():
            try:
                latest, series = _fetch_fred_series_cached(
                    spec["series_id"], config,
                    spec["lookback_days"], spec["ttl_seconds"],
                )
                if latest is not None:
                    as_of = _as_of_date(series, "daily")
                    context[key] = {
                        "value": round(latest, 2),
                        "signal": spec["signal"](latest),
                        "source": f"FRED.{spec['series_id']}",
                        "as_of": as_of,
                        "lag_note": "FRED 日频，发布滞后约 1 个交易日（美东 11am）",
                    }
                if context[key] is None:
                    failures.append(spec["label"])
            except Exception as exc:
                logger.warning("%s fetch failed: %s", spec["series_id"], exc)
                failures.append(spec["label"])
        for key, spec in _FRED_SOVEREIGN_SPECS.items():
            try:
                latest, series = _fetch_fred_series_cached(
                    spec["series_id"], config,
                    spec["lookback_days"], spec["ttl_seconds"],
                )
                if latest is not None:
                    as_of = _as_of_date(series, "monthly")
                    context[key] = {
                        "value": round(latest, 2),
                        "signal": spec["signal"](latest),
                        "source": f"FRED.{spec['series_id']}",
                        "as_of": as_of,
                        "lag_note": _lag_note(as_of, "monthly"),
                        "frequency": "monthly",
                    }
                if context[key] is None:
                    failures.append(spec["label"])
            except Exception as exc:
                logger.warning("%s fetch failed: %s", spec["series_id"], exc)
                failures.append(spec["label"])
    else:
        # 降级路径：akshare bond_zh_us_rate 覆盖美债 2/5/10/30 年全曲线，
        # 当日可得，较 FRED 新约一个交易日（FRED 约美东 11am 发布、滞后一日）。
        curve = _fetch_us_curve_akshare()
        for key in ("dgs10", "dgs30", "t10y2y"):
            spec = _FRED_DAILY_SPECS[key]
            v = curve.get(key)
            if v is not None:
                context[key] = {
                    "value": round(v, 2),
                    "signal": spec["signal"](v),
                    "source": "akshare.bond_zh_us_rate",
                    "as_of": "",
                    "lag_note": "akshare 当日可得，较 FRED 新约 1 个交易日",
                }
            else:
                failures.append(spec["label"])
        for key, spec in _FRED_DAILY_SPECS.items():
            if key in ("dgs10", "dgs30", "t10y2y"):
                continue
            failures.append(spec["label"])
        for key, spec in _FRED_SOVEREIGN_SPECS.items():
            failures.append(spec["label"])

    # ACMTP10（NY Fed，无需 key，独立于 FRED key）
    try:
        acm_val, acm_as_of = _fetch_acm_term_premia_cached()
        if acm_val is not None:
            context["acm_tp10"] = {
                "value": round(acm_val, 2),
                "signal": "负" if acm_val < 0 else ("正常" if acm_val < 1.0 else "偏高"),
                "source": "NYFed.ACMTermPremium(ACMTP10)",
                "as_of": acm_as_of or "",
                "lag_note": _lag_note(acm_as_of or "", "weekly"),
                "frequency": "weekly",
            }
        if context["acm_tp10"] is None:
            failures.append("ACMTP10")
    except Exception as exc:
        logger.warning("ACMTP10 fetch failed: %s", exc)
        failures.append("ACMTP10")

    available = sum(1 for v in context.values() if v is not None)
    if failures:
        logger.warning("宏观指标采集失败: %s", ", ".join(failures))
    return {
        "status": "ok" if available > 0 else "all_failed",
        "available_count": available,
        "failed_indicators": failures,
        "indicators": context,
    }


def macro_signal_label(macro: dict) -> str:
    """从宏观数据生成情景标签字符串。

    格式: PMI X.X + CPI +X.X% + LPR X.X% →信号 | VIX X.X 等级 SOX X,XXX
    左侧为国内宏观，右侧（| 之后）为全球风险/AI需求指标。
    """
    indicators = macro.get("indicators", {})
    parts: list[str] = []

    pmi = indicators.get("pmi")
    if pmi:
        parts.append(f"PMI {pmi['value']}")

    cpi = indicators.get("cpi")
    if cpi and cpi.get("value") is not None:
        parts.append(f"CPI {cpi['value']:+.1f}%")

    lpr = indicators.get("lpr")
    if lpr:
        parts.append(f"LPR {lpr['value']}%")

    m2 = indicators.get("money_supply")
    if m2:
        cp = m2.get("credit_pulse")
        cp_str = f" 脉冲{cp:+.1f}%" if cp is not None else ""
        parts.append(f"M2 {m2['value']}%{cp_str}")

    loan = indicators.get("loan")
    if loan:
        loan_val = loan.get("value", 0)
        loan_fmt = f"{loan_val:.0f}亿" if loan_val >= 10000 else f"{loan_val/10000:.1f}万亿"
        parts.append(f"信贷 {loan_fmt}")

    # 政策方向：综合 LPR 与 CPI（LPR 优先，CPI 作为补充信号）
    policy_parts: list[str] = []
    if lpr:
        policy_parts.append(lpr.get("signal", ""))
    if cpi:
        cpi_val = cpi.get("value")
        if cpi_val is None:
            pass  # R12c: 异常值拦截后（signal=不可靠），不参与政策方向判定
        elif cpi_val < 0:
            policy_parts.append("CPI通缩压力")
        elif cpi_val > 3:
            policy_parts.append("CPI通胀压力")
    if policy_parts:
        if len(policy_parts) == 1:
            parts.append(f"→{policy_parts[0]}")
        else:
            parts.append(f"→{'/'.join(p for p in policy_parts if p)}")

    # ---- 全球指标（｜分隔）----
    global_parts: list[str] = []

    vix = indicators.get("vix")
    if vix:
        vix_val = vix.get("value")
        vix_signal = vix.get("signal", "")
        if vix_val is not None and vix_signal:
            global_parts.append(f"VIX {vix_val} {vix_signal}")
        elif vix_val is not None:
            global_parts.append(f"VIX {vix_val}")

    sox = indicators.get("sox")
    if sox:
        sox_val = sox.get("value")
        if sox_val is not None:
            sox_fmt = f"{sox_val:,.0f}" if sox_val >= 1000 else str(sox_val)
            global_parts.append(f"SOX {sox_fmt}")

    # ---- E2 v0.2.7: 跨资产锚（追加在既有全局指标之后，不破坏既有解析格式）----
    # 仅非基线信号入标签（基线 = 正常/中性/中位/空），避免标签冗长；
    # 月频英德法日 10Y 不进标签（滞后约 2.5 个月，属 E3 C6 复算块）。
    _GLOBAL_LABEL_SPECS = [
        ("dgs10", "美10Y", "pct2"),
        ("dgs30", "美30Y", "pct2"),
        ("dfii10", "实际利率", "pct2"),
        ("t10y2y", "期限利差", "pct2"),
        ("t5yie", "5Y盈亏", "pct2"),
        ("dtwexbgs", "美元指数", "num1"),
        ("dcoilbrenteu", "布油", "num1"),
        ("dexchus", "USDCNY", "num2"),
        ("acm_tp10", "ACM10Y", "num2"),
    ]
    _BASELINE_SIGNALS = {"", "正常", "中性", "中位"}
    for _key, _disp, _fmt in _GLOBAL_LABEL_SPECS:
        ind = indicators.get(_key)
        if not ind:
            continue
        val = ind.get("value")
        if val is None:  # D1: 显式 None 检查（0.0 是合法值）
            continue
        if _fmt == "pct2":
            token = f"{_disp} {val:.2f}%"
        elif _fmt == "num1":
            token = f"{_disp} {val:,.1f}"
        else:
            token = f"{_disp} {val:.2f}"
        sig = ind.get("signal", "")
        if sig not in _BASELINE_SIGNALS:
            token = f"{token} {sig}"
        global_parts.append(token)

    china_part = " + ".join(parts) if parts else ""
    global_part = " ".join(global_parts) if global_parts else ""

    if china_part and global_part:
        return f"{china_part} | {global_part}"
    elif global_part:
        return f"宏观数据不可得 | {global_part}"
    else:
        return china_part if china_part else "宏观数据不可得"


# ---------------------------------------------------------------------------
# E3 v0.2.7: 宏观快照序列消费（store.load_macro_history 的首个生产消费方）
#
# 背景：macro_snapshots 表、save_macro_snapshot、load_macro_history(days)
# 写/读路径均已存在但全仓零消费（store.py 注释「供调用方按需消费」）。
# 本函数消费 store 历史做 VIX/PMI 趋势，并对 DGS30/英德法日 10Y 直接取
# FRED 全序列（macro_snapshots 数值列仅 _MACRO_INDICATOR_KEYS 8 键，
# 无 DGS30/主权债列；raw_json 信封虽含新键，但快照只覆盖采集日、且随
# 本功能上线才开始积累 → 全序列以 FRED 直取为准）。
#
# P0 计算铁律：所有计数（连续 N 月、N 个交易日）走 Python 聚合（len/聚合
# 函数），禁止目视清单；极值断言（最高/最低）基于全量序列 max/min，
# 禁止子集；历史不足输出「样本不足 N 期」而非静默降级（D5 fail loud）。
# D3: N 期变化需要 N+1 个数据点。
# ---------------------------------------------------------------------------

_VIX_CHANGE_PERIOD = 20                 # VIX 20 日变化 → 需 21 个采集日（D3）
_VIX_CHANGE_MIN_ROWS = _VIX_CHANGE_PERIOD + 1
_PMI_DIRECTION_MIN_MONTHS = 2           # 「连续 N 月方向」至少 2 个月才有意义
_DGS30_MIN_OBS = 250                    # 分位/距高点所需最小样本（约 1 年交易日）
_SOVEREIGN_WINDOW_YEARS = 20            # C6 裁决口径：20 年新高
_SOVEREIGN_MIN_OBS = _SOVEREIGN_WINDOW_YEARS * 12 + 1  # 241 个月频观测（D3）


def _month_from_snapshot_date(date: str) -> str | None:
    """快照日期 → YYYY-MM（快照行 date 为上海口径 YYYYMMDD；ISO 亦兼容）。"""
    d = str(date or "")
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}"
    if len(d) >= 7 and d[4] == "-":
        return d[:7]
    return None


def _display_date(s: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（覆盖范围标注用）；其余格式原样。"""
    d = str(s or "")
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _vix_20d_change(rows: list[dict]) -> dict:
    """VIX 20 日变化：取末 21 个含 vix 的采集日，末值 − 20 期前值。

    计数走 Python 聚合；覆盖范围标注（D4）：20 个采集日可能跨更多自然日。
    """
    vals = [(str(r.get("date")), r.get("vix")) for r in rows]
    pts = []
    for d, v in vals:
        fv = _safe_num(v)
        if fv is not None:
            pts.append((d, fv))
    if len(pts) < _VIX_CHANGE_MIN_ROWS:
        return {
            "status": "insufficient",
            "note": f"样本不足 {_VIX_CHANGE_MIN_ROWS} 期（当前 {len(pts)} 期）",
        }
    window = pts[-_VIX_CHANGE_MIN_ROWS:]
    change = window[-1][1] - window[0][1]
    return {
        "status": "ok",
        "change": round(change, 2),
        "latest": window[-1][1],
        "earlier": window[0][1],
        "period": _VIX_CHANGE_PERIOD,
        "coverage": (
            f"{_VIX_CHANGE_PERIOD} 个采集日"
            f"（{_display_date(window[0][0])} → {_display_date(window[-1][0])}）"
        ),
    }


def _pmi_direction(rows: list[dict]) -> dict:
    """PMI 连续 N 月方向：快照按日去重为月度序列（同月取最新），
    从最新月向前数连续同向（>=50 扩张 / <50 收缩）月数。

    计数走 Python 聚合；月频数据带截至月份标注（E5 staleness）。
    """
    by_month: dict[str, float] = {}
    for r in rows:
        month = _month_from_snapshot_date(str(r.get("date") or ""))
        if month is None:
            continue
        v = _safe_num(r.get("pmi"))
        if v is None:
            continue
        by_month[month] = v  # 同月后写覆盖（快照按 date ASC）
    months = sorted(by_month)
    if not months:
        return {"status": "insufficient", "note": "样本不足（无 PMI 历史）"}
    if len(months) < _PMI_DIRECTION_MIN_MONTHS:
        return {
            "status": "insufficient",
            "note": f"样本不足 {_PMI_DIRECTION_MIN_MONTHS} 期（当前 {len(months)} 期）",
        }
    latest_month = months[-1]
    latest_val = by_month[latest_month]
    direction = "扩张" if latest_val >= 50 else "收缩"
    run = 1
    for m in reversed(months[:-1]):
        v = by_month[m]
        if (v >= 50) == (latest_val >= 50):
            run += 1
        else:
            break
    return {
        "status": "ok",
        "direction": direction,
        "consecutive_months": run,
        "as_of": latest_month,
        "latest_value": latest_val,
        "coverage": f"{months[-run]} → {latest_month}（{run} 个月）",
    }


def _dgs30_analysis(config: dict) -> dict:
    """DGS30 25 年序列分位 + 距全序列高点距离（收益率口径，C10 强制口径标注）。

    分位 = 当前值在序列中 ≤ 当前值的观测占比（当前值即最大值时为 100%）。
    极值基于全量序列 max（P0）；样本不足输出「样本不足 N 期」（D5）。
    """
    _, series = _fetch_fred_series_cached(
        "DGS30", config, _DGS30_FULL_LOOKBACK_DAYS, _DGS30_FULL_TTL_SECONDS)
    if not series:
        return {"status": "insufficient", "note": "样本不足（DGS30 序列不可得）"}
    n = len(series)
    if n < _DGS30_MIN_OBS:
        return {
            "status": "insufficient",
            "note": f"样本不足 {_DGS30_MIN_OBS} 期（当前 {n} 期）",
        }
    values = [v for _, v in series]
    last_date, current = series[-1]
    high = max(values)  # 全量序列 max（P0 极值铁律）
    high_date = next(d for d, v in reversed(series) if v == high)
    pct = sum(1 for v in values if v <= current) / n * 100
    distance = (current - high) / high * 100  # 收益率口径（C10）
    return {
        "status": "ok",
        "current": current,
        "as_of": last_date,
        "percentile": round(pct, 1),
        "high": high,
        "high_date": high_date,
        "distance_from_high_pct": round(distance, 2),  # 收益率口径（0.0 为合法值，勿用 or 兜底）
        "n_obs": n,
        "coverage": f"{series[0][0]} → {last_date}（{n} 个观测）",
    }


def _sovereign_analysis(config: dict, window_years: int = _SOVEREIGN_WINDOW_YEARS) -> dict:
    """C6 逐国主权债复算：IRLTLT01{GB,DE,FR,JP}M156N 全序列聚合。

    对每国：当前值 / 20 年窗口最高值+日期 / 全序列最高值+日期；
    「创 {window_years} 年新高」= 当前值 ≥ 20 年窗口 max（全量窗口，非子集）。
    输出「X 项中 Y 项创 N 年新高」+ 强制截至日期与 2.5 个月滞后标注。
    """
    countries = [
        ("gb", "英国", "IRLTLT01GBM156N"),
        ("de", "德国", "IRLTLT01DEM156N"),
        ("fr", "法国", "IRLTLT01FRM156N"),
        ("jp", "日本", "IRLTLT01JPM156N"),
    ]
    window_obs = window_years * 12 + 1  # D3: N 年窗口需 N*12+1 个月频观测
    per_country: dict[str, dict] = {}
    insufficient: list[str] = []
    for cc, name, series_id in countries:
        _, series = _fetch_fred_series_cached(
            series_id, config,
            _FRED_SOVEREIGN_SPECS[f"sovereign_{cc}10y"]["lookback_days"],
            _FRED_SOVEREIGN_SPECS[f"sovereign_{cc}10y"]["ttl_seconds"],
        )
        if not series:
            per_country[cc] = {"name": name, "status": "insufficient", "note": "序列不可得"}
            insufficient.append(cc.upper())
            continue
        n = len(series)
        if n < _SOVEREIGN_MIN_OBS:
            per_country[cc] = {
                "name": name, "status": "insufficient",
                "note": f"样本不足 {_SOVEREIGN_MIN_OBS} 期（当前 {n} 期）",
            }
            insufficient.append(cc.upper())
            continue
        values = [v for _, v in series]
        last_date, current = series[-1]
        as_of = last_date[:7]
        high = max(values)  # 全量序列 max（P0）
        high_date = next(d for d, v in reversed(series) if v == high)
        window = series[-window_obs:]
        window_max = max(v for _, v in window)
        new_high = current >= window_max
        per_country[cc] = {
            "name": name,
            "status": "ok",
            "current": current,
            "high": high,
            "high_date": high_date,
            "high_20y": window_max,
            "high_20y_date": next(d for d, v in reversed(window) if v == window_max),
            "new_20y_high": new_high,
            "as_of": as_of,
            "lag_note": _lag_note(as_of, "monthly"),
            "n_obs": n,
        }
    ok_countries = [c for c in per_country.values() if c.get("status") == "ok"]
    total = len(countries)
    new_count = sum(1 for c in ok_countries if c.get("new_20y_high"))
    status = "ok" if not insufficient else ("partial" if ok_countries else "all_insufficient")
    as_of_list = [c["as_of"] for c in ok_countries if c.get("as_of")]
    as_of = max(as_of_list) if as_of_list else ""
    lag_note = _lag_note(as_of, "monthly") if as_of else ""
    verdict = f"{total} 项中 {new_count} 项创 {window_years} 年新高"
    if insufficient:
        verdict += f"（{len(insufficient)} 项样本不足）"
    return {
        "status": status,
        "verdict": verdict,
        "new_high_count": new_count,
        "total": total,
        "window_years": window_years,
        "as_of": as_of,
        "lag_note": lag_note,
        "insufficient": insufficient,
        "countries": per_country,
    }


def macro_trend_analysis(
    history: list[dict] | None = None,
    *,
    history_days: int = 365,
    config: dict | None = None,
) -> dict[str, Any]:
    """E3 宏观快照序列消费：VIX 20 日变化 / PMI 连续 N 月方向 /
    DGS30 序列分位与距高点距离 / C6 逐国主权债复算。

    Args:
        history: load_macro_history(days) 行（可注入以便测试）；None 时
            lazy import store 读取（macro_snapshots 读路径，首个生产消费方）。
        history_days: 未注入 history 时读取近 N 日快照。
        config: env.get_config()（可注入以便测试）；None 时懒加载。

    Returns:
        字典（各子项带 status: ok / insufficient / partial / all_insufficient，
        不足时输出「样本不足 N 期」文本，不静默降级）。
    """
    if history is None:
        from . import store  # noqa: PLC0415 — 延迟导入避免模块环

        history = store.load_macro_history(history_days) or []
    if config is None:
        from . import env  # noqa: PLC0415

        config = env.get_config()
    return {
        "vix_20d": _vix_20d_change(history),
        "pmi_direction": _pmi_direction(history),
        "dgs30": _dgs30_analysis(config),
        "sovereign": _sovereign_analysis(config),
    }


def format_macro_trends(trends: dict) -> list[str]:
    """E3 趋势结果的报告行（含 staleness 标注；供 render 层插入报告）。

    每行带 [来源: Python calc: ...] 标注（P0 来源标注合法形式②）。
    """
    lines: list[str] = []
    vix = trends.get("vix_20d") or {}
    if vix.get("status") == "ok":
        lines.append(
            f"- VIX 20 日变化 {vix['change']:+.2f}"
            f"（{vix['coverage']}）"
            f"[来源: Python calc: 末 {_VIX_CHANGE_MIN_ROWS} 行 vix 值差]"
        )
    elif vix.get("status") == "insufficient":
        lines.append(f"- VIX 20 日变化：{vix.get('note', '样本不足')}")

    pmi = trends.get("pmi_direction") or {}
    if pmi.get("status") == "ok":
        lines.append(
            f"- PMI 连续 {pmi['consecutive_months']} 个月{pmi['direction']}"
            f"（截至 {pmi['as_of']}，{pmi['coverage']}）"
            f"[来源: Python calc: 月度去重后连续同向计数]"
        )
    elif pmi.get("status") == "insufficient":
        lines.append(f"- PMI 连续方向：{pmi.get('note', '样本不足')}")

    dgs30 = trends.get("dgs30") or {}
    if dgs30.get("status") == "ok":
        lines.append(
            f"- DGS30 {dgs30['current']:.2f}%，25 年序列 {dgs30['percentile']:.1f}% 分位"
            f"，距全序列高点 {dgs30['distance_from_high_pct']:+.2f}%"
            f"（收益率口径，高点 {dgs30['high']:.2f}% @ {dgs30['high_date']}；"
            f"{dgs30['coverage']}）"
            f"[来源: Python calc: DGS30 全序列 max/分位]"
        )
    elif dgs30.get("status") == "insufficient":
        lines.append(f"- DGS30 序列分析：{dgs30.get('note', '样本不足')}")

    sov = trends.get("sovereign") or {}
    if sov.get("status") in ("ok", "partial"):
        lines.append(
            f"- 主权债复算（C6）：{sov['verdict']}（{sov['lag_note']}）"
            f"[来源: Python calc: IRLTLT01{{GB,DE,FR,JP}}M156N 全序列 max]"
        )
        for cc, c in sov["countries"].items():
            if c.get("status") != "ok":
                lines.append(f"  - {cc.upper()}: {c.get('note', '样本不足')}")
                continue
            flag = "（创 20 年新高）" if c["new_20y_high"] else ""
            lines.append(
                f"  - {c['name']} 10Y 当前 {c['current']:.2f}%"
                f" / 20 年窗口最高 {c['high_20y']:.2f}% @ {c['high_20y_date']}"
                f" / 全序列最高 {c['high']:.2f}% @ {c['high_date']}"
                f"{flag}（{c['lag_note']}）"
            )
    elif sov.get("status") == "all_insufficient":
        lines.append(f"- 主权债复算（C6）：{sov.get('verdict', '样本不足')}（全部国家样本不足）")
    return lines
