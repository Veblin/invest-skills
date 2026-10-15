"""Financial row helpers shared across collector, store, risk_scanner, and scoring."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from lib.nums import coalesce_field, safe_float

# normalize_end_date 已提升至 skills/lib/dates.py（共用库提升），此处 re-export 保持 BC
from .shared_dates import normalize_end_date  # noqa: E402, F401

# --- C5 v0.2.7: 语义常量（全库统一，详见 host-docs python-code-review-checklist 任务 5）---

# 毛利率字段优先级：grossprofit_margin（tushare 真名）→ gross_margin →
# gross_profit_margin（拼错旧键，兜底兼容老快照）。全库唯一书面裁决见
# render_markdown/_concise.py 注释；数据生产者（collector/_orchestrate.py
# _peer_metrics_from_fina）恒同写前两 key，统一优先级不改变任何输出。
GROSS_MARGIN_FIELDS = ("grossprofit_margin", "gross_margin", "gross_profit_margin")

# OCF/NP 覆盖比判定阈值：EXCELLENT/GOOD/WEAK 为 _conclude_cash_flow_quality
# 分级边界；ALERT 为 concise 摘要的二元关注告警（非分级边界，不并入梯级）。
OCF_COVERAGE_EXCELLENT = 1.0
OCF_COVERAGE_GOOD = 0.8
OCF_COVERAGE_WEAK = 0.5
OCF_COVERAGE_ALERT = 0.6


def parse_end_date(raw: Any) -> date | None:
    """Parse a date string (YYYYMMDD / YYYY-MM-DD / YYYY.MM.DD) to a ``date`` object."""
    if raw is None:
        return None
    s = normalize_end_date(str(raw))
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def prior_year_end_date(end_date: str) -> str:
    """Report period → same calendar date one year earlier (YYYYMMDD)."""
    norm = normalize_end_date(end_date)
    if len(norm) < 8 or not norm[:8].isdigit():
        return ""
    return f"{int(norm[:4]) - 1}{norm[4:8]}"


def find_yoy_row(rows: list[dict], latest: dict) -> dict | None:
    """Locate the record with same calendar month-day, one year earlier.

    Compares normalized ``end_date`` values so ``2023-12-31`` matches ``20231231``.
    """
    yoy_end = prior_year_end_date(str(latest.get("end_date", "")))
    if not yoy_end:
        return None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if normalize_end_date(str(r.get("end_date", ""))) == yoy_end:
            return r
    return None


def yoy_from_fina_rows(
    rows: list[dict],
    field: str,
    *,
    rescan_invalid_prior: bool = False,
) -> float | None:
    """报告期行集 → 最新期对上年同期的同比 %（A05-2 共享计算基础，v0.3.2）。

    收敛 store._yoy_from_fina_rows 与 collector._revenue_yoy_from_fina_rows
    的重复计算（数值解析统一 ``safe_float``：NaN/±Inf/字符串 → None，不再把
    NaN/Inf 作为同比透出——离线反例见 A基础-财务纯函数反例.py yoy 组）。

    各调用方**保留自己的同期行回扫策略**（离线对照已复现差异）：
    - ``rescan_invalid_prior=False``（collector 旧行为）：首个上年同期行值
      不可解析即放弃；
    - ``True``（store 旧行为）：继续扫描同期重复记录中的下一条。

    口径：最新期 = ``end_date`` **归一后**最大行（:func:`sort_by_end_date` /
    :func:`latest_by_end_date`，A-F01；不可解析日期不得充当最新期）；
    当前/同期值均须 >0，否则 None；同期比较经 ``normalize_end_date`` 归一
    （ISO/compact 等价）；找不到同期 → None（不回扫到更早年份）；round(2)。
    """
    if not rows:
        return None
    sorted_rows = sort_by_end_date([r for r in rows if _end_date_sort_key(r)])
    if not sorted_rows:
        return None  # 无任何可解析报告期 → 不产出（不可解析不得当最新期）
    latest = sorted_rows[-1]
    cur_f = safe_float(latest.get(field))
    if cur_f is None or cur_f <= 0:
        return None
    norm_ed = normalize_end_date(str(latest.get("end_date", "")))
    if len(norm_ed) < 8:
        return None
    prev_ed = prior_year_end_date(norm_ed)
    if not prev_ed:
        return None
    prev_v: float | None = None
    for r in reversed(sorted_rows[:-1]):
        if normalize_end_date(str(r.get("end_date", ""))) != prev_ed:
            continue
        v = safe_float(r.get(field))
        if v is None and rescan_invalid_prior:
            continue  # store 旧行为：继续扫描同期下一条
        prev_v = v
        break
    if prev_v is None or prev_v <= 0:
        return None
    return round((cur_f - prev_v) / prev_v * 100, 2)


def _end_date_sort_key(row: dict) -> str:
    """报告期归一排序键：8 位 YYYYMMDD；不可解析 → ""（恒最小，不参与最新期）。"""
    ed = normalize_end_date(str(row.get("end_date") or ""))
    return ed if len(ed) == 8 and parse_end_date(ed) is not None else ""


def sort_by_end_date(rows: list[dict]) -> list[dict]:
    """报告期升序（稳定排序，A-F01 canonical）。

    **必须先归一日期再排序**：旧消费方按原始 end_date 字符串排序时，ISO 与
    compact 混排会错期——``'-' < '0'`` 使 ``"2026-03-31"`` 排在
    ``"2026-06-30"`` 之后（负责人定点探针：20250331=100、2025-06-30=200、
    20260331=110、2026-06-30=260，旧输出 10%，应为 30%）。
    不可解析日期置最前（键 ""）——``rows[-1]`` 恒为有效最新期（若存在）；
    同报告期并列保留输入顺序（稳定；修订行去重归 :func:`dedupe_by_end_date`）。
    """
    return sorted(rows, key=_end_date_sort_key)


def latest_by_end_date(rows: list[dict]) -> dict | None:
    """最新报告期行：归一后 end_date 最大者（A-F01 canonical）。

    - 不可解析日期行不参与（不得压过有效日期）；全部不可解析 → None；
    - 同报告期并列取输入序较后者（与 :func:`sort_by_end_date` 稳定排序的
      ``[-1]`` 同解；修订披露取舍归 :func:`dedupe_by_end_date`）。
    """
    best: dict | None = None
    best_key: tuple[str, int] = ("", -1)
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        k = _end_date_sort_key(r)
        if k and (k, i) > best_key:
            best_key, best = (k, i), r
    return best


def _ann_sort_key(row: dict) -> str:
    ann = normalize_end_date(str(row.get("ann_date") or ""))
    return ann if len(ann) == 8 and parse_end_date(ann) is not None else ""


def dedupe_by_end_date(rows: list[dict]) -> list[dict]:
    """同 end_date 只保留一行（C1-a：修订披露取 ann_date 最大者）。

    规则：两行都有 ann_date → 取较大（最新披露/修订，选择依据见 v0.3.1 收尾
    任务卡 C1）；一行有一行无 → 取有的；都无 → 保留输入顺序中先出现者（与
    _financial_panorama_table 原「F0-9 保留先出现」行为等价）。不排序（保持
    调用方排序职责），保留位置 = 首现位置。无法归一 end_date 的行原样保留、
    不参与合并（避免 "" 键把不可解析行误合并——同 _roe_trend_anchors 教训）。
    """
    out: list[dict] = []
    pos: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        key = _end_date_sort_key(r)
        if not key:
            out.append(r)
            continue
        at = pos.get(key)
        if at is None:
            pos[key] = len(out)
            out.append(r)
        elif _ann_sort_key(r) > _ann_sort_key(out[at]):
            out[at] = r
    return out


# --- C2-a：无风险利率解析（币种/来源）---

_RF_NAME_BY_CURRENCY = {"CNY": "中国 10Y 国债", "USD": "美债 10Y"}

_RF_INVALID_REASON = {
    "非有限数值": "非有限数值(NaN/Inf)",
    "不可解析": "不可解析",
    "布尔值": "布尔值",
}


def _finite_rate(raw: object) -> tuple[float | None, str]:
    """R11：利率输入 → 有限数值，否则 (None, 原因)。

    合法读数含 **0 与负值**（负利率是真实市场状态，不得按无效处理）；拒绝
    布尔、NaN/±Infinity 与无法解析为数值的字符串（反例：`"not-a-rate"`
    曾被 float() 直接抛 ValueError 崩掉报告链）。可解析的数值字符串
    （如 `"1.68"`）按数值接受。
    """
    if raw is None:
        return None, ""
    if isinstance(raw, bool):
        return None, "布尔值"
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            return None, "不可解析"
    if not math.isfinite(value):
        return None, "非有限数值"
    return value, ""


def resolve_rf(erp_data: dict | None) -> dict:
    """解析 ``market_structure.erp`` 的无风险利率（C2-a）。

    优先人民币口径：``cn10y``（akshare 中国 10Y）→ CNY；回退 ``dgs10``（币种随
    ``rf_currency``；旧封存快照缺该键时按来源字符串推断——FRED=USD、
    bond_zh/CN10Y=CNY）。A 股报告语境下 USD 回退标 ``is_wrong_currency=True``；
    来源与币种都无法确认时标 ``is_currency_unconfirmed=True``。**只有确认同
    币种（CNY）且值为有限数才准入方向解读**（``rf_usable``）。全不可得/全无效
    → ``is_default=True``。

    返回：rate_pct / source / currency / is_default / is_wrong_currency /
    is_currency_unconfirmed / rf_usable / label（label 用于正文标注，如
    「美债 10Y，FRED.DGS10」）/ invalid_inputs（存在但无效的字段名）/
    degraded_note（降级说明；无效输入被忽略时非空，供消费者如实披露）。

    R1（2026-10-04 独立复检）：原实现下 ``resolve_rf({'dgs10': 5.29})``（无
    来源/币种线索）返回 currency 空且 is_wrong_currency=False → 消费者按
    「可用」继续隐含增长方向解读。修正为「确认才准入」；零值利率（如
    cn10y=0.0）是合法读数，不按缺失处理。

    R11（2026-10-04 二轮复检）：非有限/不可解析输入曾被放行或直接抛错——
    `cn10y=NaN/Infinity` 走 CNY 可用分支，核心变量产出
    「g_implied nan%/inf%」；`cn10y="not-a-rate"` 抛 ValueError。现改为
    「有限数值才准入」：cn10y 无效 → 回退 dgs10（其有效性同样校验）；
    全部无效 → is_default=True；`invalid_inputs/degraded_note` 如实记录
    降级原因，标签与消费者消息随附披露。
    """
    erp = erp_data or {}
    invalid: list[str] = []
    cn = erp.get("cn10y")
    if cn is not None:
        value, reason = _finite_rate(cn)
        if value is not None:
            src = str(erp.get("cn10y_source") or "akshare.bond_zh_us_rate")
            return {
                "rate_pct": value,
                "source": src,
                "currency": "CNY",
                "is_default": False,
                "is_wrong_currency": False,
                "is_currency_unconfirmed": False,
                "rf_usable": True,
                "invalid_inputs": [],
                "degraded_note": "",
                "label": f"中国 10Y 国债，{src}",
            }
        invalid.append("cn10y")  # 存在但无效 → 回退 dgs10，原因随附
    raw = erp.get("dgs10")
    dgs_reason = ""
    if raw is not None:
        dgs_value, dgs_reason = _finite_rate(raw)
        if dgs_value is None:
            invalid.append("dgs10")
            raw = None
        else:
            raw = dgs_value
    if raw is None:
        note = ""
        if invalid:
            reasons = []
            if "cn10y" in invalid and cn is not None:
                _, r_cn = _finite_rate(cn)
                reasons.append(f"cn10y {_RF_INVALID_REASON.get(r_cn, r_cn)}")
            if "dgs10" in invalid:
                reasons.append(f"dgs10 {_RF_INVALID_REASON.get(dgs_reason, dgs_reason)}")
            note = "快照无风险利率值无效已忽略（" + "；".join(reasons) + "）"
        return {"rate_pct": None, "source": "", "currency": "",
                "is_default": True, "is_wrong_currency": False,
                "is_currency_unconfirmed": False, "rf_usable": False,
                "invalid_inputs": invalid, "degraded_note": note, "label": ""}
    src = str(erp.get("y10_source") or "")
    if not src:
        combined = str(erp.get("source") or "")
        src = combined.split("+", 1)[1] if "+" in combined else combined
    currency = str(erp.get("rf_currency") or "")
    if not currency:
        up = src.upper()
        currency = "USD" if "FRED" in up else (
            "CNY" if ("CN10Y" in up or "BOND_ZH" in up) else "")
    name = _RF_NAME_BY_CURRENCY.get(currency, "10Y 国债")
    note = ""
    if invalid:  # 走到这里 invalid 只可能是 cn10y（dgs10 无效时 raw 已置 None）
        _, r_cn = _finite_rate(cn)
        note = f"快照 cn10y 无效已忽略（{_RF_INVALID_REASON.get(r_cn, r_cn)}）"
    label = f"{name}，{src or '来源未知'}"
    if note:
        label += f"（{note}）"
    return {
        "rate_pct": raw,
        "source": src,
        "currency": currency,
        "is_default": False,
        "is_wrong_currency": currency == "USD",
        "is_currency_unconfirmed": currency not in ("CNY", "USD"),
        "rf_usable": currency == "CNY",
        "invalid_inputs": invalid,
        "degraded_note": note,
        "label": label,
    }


def gross_margin_annual_series(fin_rows: list[dict]) -> list[tuple[str, float]]:
    """Latest gross margin per calendar year, sorted ascending."""
    by_year: dict[str, float] = {}
    for r in fin_rows:
        y = normalize_end_date(str(r.get("end_date", "")))[:4]
        gm = coalesce_field(r, *GROSS_MARGIN_FIELDS)
        if y and gm is not None:
            by_year[y] = gm
    return sorted(by_year.items())


def gross_margin_trend_from_rows(
    fin_rows: list[dict], *, threshold: float = 0.5,
) -> str | None:
    """Year-over-year gross margin direction (up / down / flat)."""
    annual = gross_margin_annual_series(fin_rows)
    if len(annual) < 2:
        return None
    (_, m0), (_, m1) = annual[-2], annual[-1]
    if m1 < m0 - threshold:
        return "down"
    if m1 > m0 + threshold:
        return "up"
    return "flat"
