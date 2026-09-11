"""宏观事件日历数据层（invest-a-event-calendar v3）— 三源适配 + 过滤 + 归组。

三源（2026-09-10 实测；登记见 skills/lib/references/data-interface-map.md）：

===========  ================================  ==============  ==================
区域/类别     源                                前向窗口         备注
===========  ================================  ==============  ==================
中国          百度财经日历                       ≈30 天          实测窗口内覆盖 CPI/PPI/
             (akshare news_economic_baidu)                      社零/工业增加值/固投/
                                                                LPR/MLF/工业企业利润
美国（近场）  同上                              ≈30 天          实测窗口内**无**美国 CPI
美国（长窗）  FRED releases/dates               ≥3 个月         需 FRED_API_KEY；无时刻字段
议息会议      references/fomc_meetings.yaml    人工策展        无自动源（FRED 的
                                                                FOMC Press Release 是
                                                                每日新闻稿噪音）
===========  ================================  ==============  ==================

设计要点（对齐仓库既有约定）：
- **源不可得 ≠ 无事件**：每源返回 ``SourceResult``，``error`` 非空表示不可得；
  渲染层必须出「❌ 不可得」，**不得**打印「无日程 ✅」（仓库历史缺陷模式：
  把取数失败静默记成空窗）
- **降级显式标注**：对齐 ``unlock_source`` 的 ``(rows, error)`` 与 ``freshness``
  的 ``degraded``；无 FRED_API_KEY 是显式降级而非静默跳过
- **逐日三态**：单日 ok / empty / failed；失败重试后仍失败则显式记入 ``failed_days``
- **不依赖源 dtype**：``重要性`` 实测 str ``'1'`` 与 float ``1.0`` 混型，
  且只有 1/2 两档（噪音行同样有值）→ 一律经 ``safe_importance`` 归一，
  且**不作为筛选或强度依据**（强度由策展表映射）
- **不推测缺失字段**：FRED 无公布时刻 → 留空并注明，不用常识补 8:30 ET
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time as _time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "MacroEvent",
    "SourceResult",
    "safe_importance",
    "group_key",
    "display_title",
    "group_events",
    "filter_noise",
    "load_rules",
    "fetch_baidu_calendar",
    "fetch_us_calendar",
    "load_fomc_meetings",
    "build_view",
    "date_range",
    "iter_dates",
]

# 百度源的逐日失败率实测 ~12%（40 次里 5 次 HTTPError）；重试 3 次后单日
# 失败率 ≈ 0.12³ ≈ 0.17%。指数退避基准。
_RETRY_BACKOFF_S = 0.5


# ── 数据模型 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroEvent:
    """一条日历事件（已归组：``sub_items`` 保留被折叠的同发布多口径条目）。"""

    date: str          # YYYY-MM-DD
    time: str          # "HH:MM"；源不提供时为 ""（不推测）
    region: str        # 中国 / 美国 / 日本
    title: str
    period: str        # 统计周期（源提供时透传，否则 ""）
    importance: str    # 策展档位：高 / 中 / 低 / —（非源字段）
    source: str        # 来源标识
    note: str          # 备注（源重要性原值、不确定性声明等）
    sub_items: tuple[str, ...] = ()
    family: str = ""   # 发布级归组名（策展）；空则退回按标题归组


@dataclass
class SourceResult:
    """单源取数结果。``error`` 非空 = 该源**不可得**（≠ 无事件）。"""

    name: str
    events: list[MacroEvent] = field(default_factory=list)
    error: str | None = None
    coverage_end: str | None = None      # 观测到的事件覆盖边界（最后一条事件日）
    ok_days: int = 0
    empty_days: int = 0
    failed_days: list[str] = field(default_factory=list)
    filtered: int = 0                    # 被噪音过滤的条数
    filtered_families: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ── 基础工具 ─────────────────────────────────────────────────────────────


def iter_dates(start: str, end: str) -> Iterable[str]:
    """YYYYMMDD 闭区间逐日（含端点）。"""
    d = _dt.datetime.strptime(start, "%Y%m%d").date()
    e = _dt.datetime.strptime(end, "%Y%m%d").date()
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += _dt.timedelta(days=1)


def date_range(days_ahead: int, *, today: _dt.date | None = None) -> tuple[str, str]:
    """(今天, 今天+days_ahead) 的 YYYYMMDD——宏观日程窗口。"""
    t = today or _dt.date.today()
    return t.strftime("%Y%m%d"), (t + _dt.timedelta(days=days_ahead)).strftime("%Y%m%d")


def _norm_ymd(raw: Any) -> str | None:
    """把 '2026-9-16' / '20260916' / '2026-09-16' 归一为 'YYYY-MM-DD'；不可解析 → None。

    策展表是**手工誊录**资产（FOMC 表要求年度刷新），未补零属预期内手误：裸
    ``date.fromisoformat('2026-9-16')`` 会抛 ValueError，而字符串比较又会把
    ``'2026-9-16'`` 按字典序（'9' > '0'）误判为未来。
    """
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, dy = (int(g) for g in m.groups())
    else:
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) != 8:
            return None
        y, mo, dy = int(digits[:4]), int(digits[4:6]), int(digits[6:])
    try:
        return _dt.date(y, mo, dy).isoformat()
    except ValueError:
        return None


def _iso(d: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（已是 ISO 则原样返回）。"""
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def safe_importance(v: Any) -> int | None:
    """源 ``重要性`` 归一为 int；不可解析一律 None。

    实测该列 str ``'1'`` 与 float ``1.0`` 混型，且存在 NaN / None / 空串
    （pandas 3 会把 JSON null 变 NaN，而 NaN 为真值）。调用方**不得**依赖 dtype。
    """
    if v is None:
        return None
    if isinstance(v, float) and v != v:      # NaN
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "<na>"):
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return int(f)


# 同发布的多口径后缀（实测：加拿大 8 月 CPI 被拆成 10+ 条；中国社零拆 3 条）
_CALIBER_RE = re.compile(
    r"(?:年率|月率|季率|读数|初值|终值|修正值|前值|季调后|未季调|年初至今|单月|当周)"
    r"[-—]?"
)


_IMPORTANCE_ORDER = {"高": 3, "中": 2, "低": 1, "—": 0}


def _importance_rank(level: str) -> int:
    """档位排序键（未知档位按最低处理，不抛）。"""
    return _IMPORTANCE_ORDER.get(str(level), 0)


def group_key(title: str) -> str:
    """归组键：剥掉 月率/年率/年初至今/读数 等口径尾巴，再去掉全部标点空白。

    只用于**分桶**（故做激进归一），**不用于展示**——展示走 :func:`display_title`，
    否则括号与空格会被啃掉（如「MLF操作规模(亿元)」→「MLF操作规模亿元」）。
    """
    return re.sub(r"[\(\)（）%\-—\s]", "", display_title(title))


def display_title(title: str) -> str:
    """展示名：只剥口径尾巴、折叠空白，**保留**括号等单位信息。"""
    return re.sub(r"\s+", " ", _CALIBER_RE.sub("", str(title))).strip()


def group_events(events: Sequence[MacroEvent]) -> list[MacroEvent]:
    """按 (日期, 地区, 归组键) 折叠；被折叠的原始条目保留在 ``sub_items``。

    归组键 = 策展 ``family``（发布级，如「美国CPI」）——源会把**同一次发布**拆成
    十几个分项（实测：CPI 9 条、EIA 周报 14 条、GDP 6 条），不折叠则一天一行可达
    31 项。无 family 时退回 ``group_key``（按标题剥口径），保证直接构造的事件
    （测试、未来新源）仍能折叠同发布的多口径条目。

    折叠后展示 family；条目多于 1 时附「（N 项）」，明细留在 ``sub_items``。
    """
    bucket: dict[tuple[str, str, str], MacroEvent] = {}
    for e in events:
        key = (e.date, e.region, e.family or group_key(e.title))
        cur = bucket.get(key)
        if cur is None:
            bucket[key] = replace(e, title=display_title(e.title), sub_items=(e.title,))
        else:
            # 档位取**族内最高**：发布的重要性等于其最重要分项，而非碰巧第一条。
            # （曾因取首条，使 FRED 标为「高」的 PCE 并入百度分项后降成「中」，
            #   整个发布从头部重点段消失。）
            bucket[key] = replace(
                cur,
                sub_items=cur.sub_items + (e.title,),
                importance=max((cur.importance, e.importance), key=_importance_rank),
            )

    out: list[MacroEvent] = []
    for meta in bucket.values():
        if meta.family and len(meta.sub_items) > 1:
            meta = replace(meta, title=f"{meta.family}（{len(meta.sub_items)} 项）")
        out.append(meta)
    return sorted(out, key=lambda e: (e.date, e.time, e.region, e.title))


def filter_noise(
    rows: Sequence[dict], *, patterns: Sequence[str], min_days: int = 5
) -> tuple[list[dict], int, list[str]]:
    """滤掉每日类噪音；返回 (保留行, 过滤条数, 命中族标签)。

    两条互补规则（缺一不可）：
    1. **频率检测**（结构性）：同一事件名在 ``≥min_days`` 个不同日期、且同一时刻
       出现 → 判每日类。自然放过周频/月频（30 天窗内最多 1-2 次）。
    2. **策展 pattern**（须极窄）：只匹配已实测的噪音族。
       ⚠️ 切勿写成宽泛的「库存」——会误杀「美国 EIA 原油库存」这类**真实**事件
       （它的公布会移动价格）。宁可少过滤。
    """
    pats = [re.compile(p) for p in patterns]

    seen: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        k = (str(r.get("事件", "")), str(r.get("时间", "")))
        seen.setdefault(k, set()).add(str(r.get("日期", "")))
    daily = {k for k, days in seen.items() if len(days) >= min_days}

    kept: list[dict] = []
    dropped = 0
    families: list[str] = []

    def _drop(name: str) -> None:
        nonlocal dropped
        dropped += 1
        if name not in families:
            families.append(name)

    for r in rows:
        name = str(r.get("事件", ""))
        if (name, str(r.get("时间", ""))) in daily or any(p.search(name) for p in pats):
            _drop(name)
            continue
        kept.append(r)
    return kept, dropped, families


# ── 策展规则表 ───────────────────────────────────────────────────────────


def load_rules(path: Path | str | None) -> dict:
    """读取策展规则 YAML；缺失/损坏 → {}（调用方据此走默认口径并标注）。"""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        import yaml  # 惰性：无 yaml 环境下降级为默认口径而非崩
    except ImportError:  # pragma: no cover
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 规则表损坏不阻断取数
        return {}
    return data if isinstance(data, dict) else {}


# ── 源 1：百度财经日历（中国 + 美国近场）──────────────────────────────────


def _fetch_baidu_day(date: str, cookie: str | None = None):
    """单日拉取（模块级独立函数：可 mock，且便于集中处理重试）。

    ``cookie`` **必须转交 akshare**：其签名为 ``news_economic_baidu(date, cookie=None)``，
    cookie 为空时**每次调用**都会先发两个握手请求（BAIDUID + HMACCOUNT），30 天窗口
    就是 90 个请求——这正是实测 0.3~0.5s/次与 ≈12% 失败率的来源，也是 `--baidu-cookie`
    存在的理由。（此前收下 cookie 却未转交 → 旗标端到端空操作。）

    注意**不**包 ``akshare_direct_session``：那是东财源的 requests 直连+节流，
    而百度源走 ``curl_cffi``，包了无效还会给 30 天循环白加节流。
    """
    import akshare as ak

    return ak.news_economic_baidu(date=date, cookie=cookie)


def _baidu_rows_to_dicts(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def fetch_baidu_calendar(
    start: str,
    end: str,
    *,
    retries: int = 3,
    cookie: str | None = None,
    regions: Sequence[str] = ("中国", "美国", "日本"),
    rules: dict | None = None,
) -> SourceResult:
    """逐日拉取百度财经日历 → 归一事件（多区域）。

    **时刻与日期均为北京口径**（实测：美联储 H.4.1 每周四美东 16:30 发布，
    源中记为**次日** 04:30）→ 可直接与其它源并入同一「北京日期」时间轴。

    逐日三态：ok（有行）/ empty（合法空窗）/ failed（重试耗尽）——
    后者显式记入 ``failed_days``，**不得**当作空窗。

    区域白名单按 ``rules["region_keywords"][区域]`` 分别配置；某区未配置表 →
    该区全收（档位记「—」），配置了的区只收命中项。
    """
    rules = rules or {}
    pats = list(rules.get("noise_patterns") or [])
    min_days = int(rules.get("noise_min_days", 5))
    region_kw: dict = rules.get("region_keywords") or {}

    ok_days = 0
    empty_days = 0
    failed: list[str] = []
    raw: list[dict] = []

    for ds in iter_dates(start, end):
        rows = None
        for attempt in range(max(1, retries)):
            try:
                rows = _baidu_rows_to_dicts(_fetch_baidu_day(ds, cookie))
                break
            except Exception:  # noqa: BLE001 —— 失败计数在下方，不在此吞掉
                if attempt + 1 < max(1, retries):
                    _time.sleep(_RETRY_BACKOFF_S * (2 ** attempt))
        if rows is None:
            failed.append(ds)
            continue
        if not rows:
            empty_days += 1
            continue
        ok_days += 1
        raw.extend(rows)

    if not raw and failed:
        return SourceResult(
            name="百度财经日历", error=f"全部 {len(failed)} 天取数失败（疑源故障/网络）",
            ok_days=ok_days, empty_days=empty_days, failed_days=failed,
        )

    # 地区过滤（地区缺失时回退 国家 列——实测额外列）
    picked: list[dict] = []
    for r in raw:
        reg = str(r.get("地区") or r.get("国家") or "").strip()
        if reg not in regions:
            continue
        picked.append({"日期": _iso(r.get("日期", "")), "时间": str(r.get("时间") or ""),
                       "地区": reg, "事件": str(r.get("事件") or ""),
                       "前值": r.get("前值"), "重要性": r.get("重要性"),
                       "统计周期": r.get("统计周期") or ""})

    if region_kw:
        annotated = []
        for p in picked:
            table = region_kw.get(p["地区"])
            if table is None:
                p["_imp"] = "—"
                annotated.append(p)
                continue
            # 长关键词优先匹配（"采购经理" 先于 "PMI"，避免宽泛词抢走具体档位）
            for kw in sorted(table, key=len, reverse=True):
                if kw in p["事件"]:
                    spec = table.get(kw) or {}
                    p["_imp"] = str(spec.get("importance") or "—")
                    p["_fam"] = str(spec.get("family") or "")
                    annotated.append(p)
                    break
        picked = annotated

    kept, dropped, families = filter_noise(picked, patterns=pats, min_days=min_days)

    events = []
    for row in kept:
        lvl = safe_importance(row.get("重要性"))
        events.append(MacroEvent(
            date=row["日期"], time=row["时间"], region=row["地区"], title=row["事件"],
            period=str(row.get("统计周期") or ""),
            importance=row.get("_imp", "—"), source="baidu",
            note=f"源重要性:{lvl}" if lvl is not None else "",
            family=row.get("_fam", ""),
        ))
    events = group_events(events)
    coverage = max((e.date for e in events), default=None)

    return SourceResult(
        name="百度财经日历", events=events, coverage_end=coverage,
        ok_days=ok_days, empty_days=empty_days, failed_days=failed,
        filtered=dropped, filtered_families=families,
    )


# ── 源 2：FRED releases/dates（美国长窗）─────────────────────────────────

_FRED_URL = "https://api.stlouisfed.org/fred/releases/dates"


def _get_fred_release_dates(start: str, end: str, api_key: str) -> dict:
    """FRED releases/dates 直取（fredapi 无该端点 → urllib，写法师承
    invest-a-stock ``macro.py::_fetch_fred_series``）。"""
    params = {
        "api_key": api_key, "file_type": "json",
        "realtime_start": start, "realtime_end": end,
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc", "limit": 1000,
    }
    url = f"{_FRED_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 —— 固定官方主机
        return json.loads(resp.read().decode("utf-8"))


def fetch_us_calendar(
    start: str, end: str, *, fred_key: str | None, rules: dict,
) -> SourceResult:
    """FRED 未来发布日 → 归一事件（白名单按 (release_id, release_name) 对判定）。

    - 无 key → **显式降级**（error 非空），不得静默返回空表
    - 白名单外（如每日一条的 ``FOMC Press Release``）天然被排除
    - id 命中但 name 不符 → 报**配置漂移**并排除该行（fail-loud，不静默纳入错数据）
    """
    if not fred_key:
        return SourceResult(
            name="FRED", error="未配置 FRED_API_KEY —— 美国长窗口日程不可得（≠ 无事件）",
        )

    entries = list((rules or {}).get("us_releases") or [])
    # id 非数字（手工编辑手误）不得让整个 --macro 崩：`int()` 此前在 try 之外 →
    # `ValueError: invalid literal for int()`，而同模块其它失败都会转成显式不可得行。
    by_pair: dict[tuple[int, str], dict] = {}
    by_id: dict[int, dict] = {}
    bad_ids: list[str] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        try:
            rid = int(e["id"])
        except (KeyError, TypeError, ValueError):
            if e.get("id") is not None:
                bad_ids.append(str(e.get("id")))
            continue
        by_pair[(rid, str(e["name"]))] = e
        by_id.setdefault(rid, e)

    try:
        payload = _get_fred_release_dates(_iso(start), _iso(end), fred_key)
    except Exception as exc:  # noqa: BLE001 —— 网络/权限失败须显式返回
        return SourceResult(name="FRED", error=f"取数失败：{type(exc).__name__}: {str(exc)[:120]}")

    events: list[MacroEvent] = []
    notes: list[str] = []
    if bad_ids:
        notes.append("策展表 us_releases 含非数字 id（已跳过该条，请修正 "
                     "references/macro_sources.yaml）：" + "、".join(bad_ids))
    for rd in (payload or {}).get("release_dates") or []:
        try:
            rid = int(rd.get("release_id"))
        except (TypeError, ValueError):
            continue
        name = str(rd.get("release_name") or "")
        date = _iso(rd.get("date", ""))
        hit = by_pair.get((rid, name))
        if hit is None:
            if rid in by_id:
                expected = by_id[rid]["name"]
                msg = (f"配置漂移：release_id={rid} 现名为「{name}」，策展表记为"
                       f"「{expected}」——该行已排除，请更新 references/macro_sources.yaml")
                if msg not in notes:
                    notes.append(msg)
            continue
        events.append(MacroEvent(
            date=date, time="", region="美国", title=str(hit.get("label") or name),
            period="", importance=str(hit.get("importance") or "—"), source="fred",
            # FRED 只给美东日期、无时刻 → 不换算也不推测（该类发布多在美东上午，
            # 对应北京时间当日；若为美东下午则可能落在北京次日，故标注口径）
            note="美东日期口径（源无时刻，不推测）",
            # family 显式配置（指向与百度侧同名的发布），否则退回 label——两个源
            # 对同一次发布会用不同措辞（FRED「Personal Income and Outlays」vs
            # 百度「PCE」），不显式对齐就会同日并列出两行看起来重复的条目
            family=str(hit.get("family") or hit.get("label") or name),
        ))

    seen_names = {e.title for e in events}
    missed = [str(e.get("label") or e.get("name")) for e in entries
              if (e.get("label") or e.get("name")) not in seen_names]
    if missed:
        notes.append("策展表条目未在返回集中出现（疑改名/暂无排期）：" + "、".join(missed))

    events = group_events(events)
    return SourceResult(
        name="FRED", events=events,
        coverage_end=max((e.date for e in events), default=None), notes=notes,
    )


# ── 源 3：FOMC 策展表（无自动源）────────────────────────────────────────


def load_fomc_meetings(
    path: Path | str, *, today: str
) -> tuple[list[MacroEvent], list[str]]:
    """读 FOMC 会议策展表 → (未过去的会议, 告警列表)。

    - 文件缺失/解析失败 → 显式告警（渲染层据此出「不可得」，不得出「无议息会议」）
    - 表已过期（全部会议日 < today）→ 告警写明覆盖至何时，返回空
    - 事件日取**决议公布日** = 会议结束日
    """
    p = Path(path)
    if not p.is_file():
        return [], [f"FOMC 策展表不可得（文件缺失）：{p}——本次**不含**议息会议，"
                    "≠ 无议息会议"]
    try:
        import yaml
    except ImportError:  # pragma: no cover
        return [], ["FOMC 策展表不可得（环境缺 pyyaml）——本次**不含**议息会议"]
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [], [f"FOMC 策展表不可得（解析失败：{type(exc).__name__}）——"
                    "本次**不含**议息会议"]
    if not isinstance(data, dict):
        return [], ["FOMC 策展表不可得（结构非映射）——本次**不含**议息会议"]

    meetings = data.get("meetings") or []
    last_verified = str(data.get("last_verified") or "未标注")
    rows: list[tuple[str, bool]] = []
    warns: list[str] = []
    for m in meetings:
        if not isinstance(m, dict):
            continue
        raw_end = str(m.get("end") or m.get("start") or "")
        end = _norm_ymd(raw_end)
        if end is None:
            if raw_end.strip():
                warns.append(f"FOMC 策展表有无法解析的日期（已跳过该条）：{raw_end!r}")
            continue
        if end != raw_end.strip():
            # 手工誊录未补零（2026-9-16）会被 `date.fromisoformat` 抛、且字典序比较
            # 还会误判为未来——归一化 + 告警，不因一处手误崩掉整个 --macro
            warns.append(f"FOMC 策展表日期已归一化：{raw_end!r} → {end}（建议手工补零）")
        rows.append((end, bool(m.get("sep"))))

    future = [(d, sep) for d, sep in rows if d >= _norm_ymd(today)]
    if not future:
        covered_to = max((d for d, _ in rows), default="—")
        return [], warns + [
            f"FOMC 策展表已过期（覆盖至 {covered_to}，最后核对 {last_verified}）"
            "——本次**不含**议息会议，请更新 references/fomc_meetings.yaml"]

    events = [
        MacroEvent(
            # 统一轴为北京日期：决议为美东 14:00（官方新闻稿页头「For release at
            # 2:00 p.m. EDT」），+12/13h → **北京次日** 02:00/03:00。不换算会让用户
            # 按北京日期查看时整整错过一天。
            date=(_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat(),
            time="", region="美国", title="FOMC 议息会议",
            period="", importance="高", source="fomc",
            note="；".join(p for p in (
                f"美东 {d} 14:00 决议 → 北京次日凌晨（日期已换算）",
                "含经济预测摘要（SEP）" if sep else "",
                "官方注：会议日期在紧邻的上次会议确认前均为**暂定**",
            ) if p),
            family="FOMC 议息会议",
        )
        for d, sep in sorted(future)
    ]
    return events, warns


# ── 汇总视图 ─────────────────────────────────────────────────────────────


def build_view(results: Sequence[SourceResult]) -> dict:
    """三源结果 → 统一视图 {events, coverage, errors, notes, filtered}。

    ``coverage[region]`` 取该区各源的**最远**覆盖边界（美国：FRED 比策展表远）；
    ``errors`` 收集各源不可得原因——渲染层必须逐条呈现，不得据空 events 出「无事件」。
    """
    events: list[MacroEvent] = []
    coverage: dict[str, str] = {}
    errors: list[str] = []
    notes: list[str] = []
    filtered = 0
    families: list[str] = []

    for r in results:
        if r.error:
            errors.append(f"{r.name}：{r.error}")
        notes.extend(r.notes)
        filtered += r.filtered
        for fam in r.filtered_families:
            if fam not in families:
                families.append(fam)
        events.extend(r.events)
        if r.coverage_end:
            for reg in {e.region for e in r.events}:
                if r.coverage_end > coverage.get(reg, ""):
                    coverage[reg] = r.coverage_end

    # 跨源去重：同 (日期, 区域) 内，FRED 的**短标签**若已被百度源更细的条目覆盖
    # （去掉「美国」前缀后是某条条目名的子串），则不再重复展示——实测 10-02 同时
    # 出现「美国9月非农就业人口变动(万)」（百度，带时刻）与「美国非农就业」
    # （FRED，仅日期），读起来像两条不同事件。仅 FRED→被百度吞并，反向不成立
    # （百度长窗覆盖不到，FRED 是那段唯一来源）。跨日期/区域不生效。
    baidu_titles: dict[tuple[str, str], list[str]] = {}
    for e in events:
        if e.source == "baidu":
            baidu_titles.setdefault((e.date, e.region), []).append(e.title)
    deduped: list[MacroEvent] = []
    for e in events:
        if e.source == "fred":
            core = e.title[2:] if e.title.startswith("美国") else e.title
            if core and any(core in t for t in baidu_titles.get((e.date, e.region), [])):
                continue
        deduped.append(e)

    return {
        "events": group_events(deduped),
        "coverage": coverage,
        "errors": errors,
        "notes": notes,
        "filtered": filtered,
        "filtered_families": families,
    }
