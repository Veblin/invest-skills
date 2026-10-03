"""Financial row helpers shared across collector, store, risk_scanner, and scoring."""

from __future__ import annotations

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


def _ann_sort_key(row: dict) -> str:
    ann = normalize_end_date(str(row.get("ann_date") or ""))
    return ann if len(ann) == 8 and ann.isdigit() else ""


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
        key = normalize_end_date(str(r.get("end_date") or ""))
        if len(key) != 8 or not key.isdigit():
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


def resolve_rf(erp_data: dict | None) -> dict:
    """解析 ``market_structure.erp`` 的无风险利率（C2-a）。

    优先人民币口径：``cn10y``（akshare 中国 10Y）→ CNY；回退 ``dgs10``（币种随
    ``rf_currency``；旧封存快照缺该键时按来源字符串推断——FRED=USD、
    bond_zh/CN10Y=CNY）。A 股报告语境下 USD 回退标 ``is_wrong_currency=True``，
    消费者据此暂停方向解读（report-conventions §9.3）。全不可得 →
    ``is_default=True``。

    返回：rate_pct / source / currency / is_default / is_wrong_currency / label
    （label 用于正文标注，如「美债 10Y，FRED.DGS10」）。
    """
    erp = erp_data or {}
    cn = erp.get("cn10y")
    if cn is not None:
        src = str(erp.get("cn10y_source") or "akshare.bond_zh_us_rate")
        return {
            "rate_pct": float(cn),
            "source": src,
            "currency": "CNY",
            "is_default": False,
            "is_wrong_currency": False,
            "label": f"中国 10Y 国债，{src}",
        }
    raw = erp.get("dgs10")
    if raw is None:
        return {"rate_pct": None, "source": "", "currency": "",
                "is_default": True, "is_wrong_currency": False, "label": ""}
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
    return {
        "rate_pct": float(raw),
        "source": src,
        "currency": currency,
        "is_default": False,
        "is_wrong_currency": currency == "USD",
        "label": f"{name}，{src or '来源未知'}",
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
