"""Deterministic, auditable research model for ``report --mode insight``.

The module deliberately separates collected facts from reader-facing findings.  It
does not forecast prices or infer causality: a finding is only a compact
description of relationships present in the collected data, with its limits and
source path kept alongside it.

Two reader-facing blocks are built here from data that already exists elsewhere in
the repository — the point is wiring, not new capability:

``discoveries``
    相对上一快照的实质变化。**唯一来源**是 ``lib.store`` 的关键字段快照对比，
    本模块不重算任何变化量。读取由调用方（CLI）注入，见 ``load_snapshot_diff``。
``analysis_chains``
    事实之间的同向/不同向关系、可能机制与替代解释（上限 2 条）。

⚠️ ``analysis_chains`` 是本项目的**内部工程约定**，**无同行评审先例**（2026-09-15
文献检索核实：text-as-data 方法族只做语气测度，不做叙事到链条的结构化）。因此
它的规则集与环节语义不得声称有文献背书；所有关系陈述只为「一致性证据」或
「机制未证实」，**不写因果**。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_CONTRACT_VERSION = "1.0"
GENERATOR_VERSION = "0.3.1"

MAX_DISCOVERIES = 3
MAX_ANALYSIS_CHAINS = 2

# 变化条目展示顺序：事件优先（时间性最强），其后按估值/财务/资金/技术/风险。
_DISCOVERY_ORDER: tuple[str, ...] = (
    "events", "valuation", "financials", "capital_flow", "technical", "risk",
)

_CATEGORY_LABELS = {
    "events": "事件", "valuation": "估值", "financials": "财务",
    "capital_flow": "资金", "technical": "技术", "risk": "风险",
}

# store.extract_key_snapshot 会产出的字段 → 中文标签。未命中的字段回退原始名。
_FIELD_LABELS = {
    "pe_pct": "PE 分位", "pb_pct": "PB 分位", "pe_ttm": "PE(TTM)", "pb": "PB",
    "roe": "ROE", "revenue_yoy": "营业收入同比", "net_profit_yoy": "归母净利润同比",
    "northbound_net": "北向净额(10日)", "margin_balance": "两融余额",
    "ma_alignment": "均线排列", "rsi": "RSI",
    "triggered_count": "风险信号数", "triggered_signals": "风险信号集合",
    "event_count": "事件数", "window_days": "事件窗口(日)",
}

# 价格反应不得作为链条环节证据（事件研究三前提 + 联合假设问题 + Granger 在
# 金融数据上的方向反转均不支持用「某日涨跌」证明环节成立）。
_CHAIN_FORBIDDEN_FACTS = frozenset({"quote.change_pct.latest"})

# 因果/绝对化措辞黑名单；lookbehind 排除本模块模板自带的「未证实/不证实」。
_CAUSAL_RE = re.compile(r"导致|引起|造成|致使|(?<!未)(?<!不)证实|证明了|必然")

_CHAIN_NOTE = "本条为工程约定，无同行评审先例；不得作为核心论证。"


class InsightSchemaError(ValueError):
    """Raised when a generated or loaded insight model is not auditable."""


def _number(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _index(collection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(dim.get("dimension")): dim for dim in collection.get("dimensions", [])
        if isinstance(dim, dict) and dim.get("dimension")
    }


def _source_id(dimension: str, dim: dict[str, Any] | None) -> str:
    meta = (dim or {}).get("_meta") or {}
    source = meta.get("source") or meta.get("primary_source") or "unknown"
    return f"{dimension}.{source}"


def _as_of(row: dict[str, Any], fallback: str) -> str:
    for key in ("end_date", "trade_date", "date", "ann_date"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def _fact(fid: str, value: Any, *, as_of: str, unit: str, basis: str,
          source_id: str, formula: str | None = None,
          availability: str = "available") -> dict[str, Any]:
    return {
        "id": fid, "value": value, "as_of": as_of, "unit": unit,
        "basis": basis, "source_ids": [source_id], "formula": formula,
        "availability": availability,
    }


def _percentile(values: list[float], latest: float) -> float | None:
    if len(values) < 20:
        return None
    return round(sum(value <= latest for value in values) / len(values) * 100, 1)


def _dedupe_financial_rows(fin_rows: list[Any]) -> list[dict[str, Any]]:
    """按报告期去重并升序排列。

    ``financials`` 维度原始列表既非升序又含重复期——store 快照 300750/#77 实测
    20 行仅 16 个唯一期（2022Q3–2023Q2 各两份，fcff/fcfe 取值冲突）。同一期取
    首次出现，避免下游按「相邻行」取数时拿到重复期。
    """
    by_period: dict[str, dict[str, Any]] = {}
    for row in fin_rows:
        if not isinstance(row, dict):
            continue
        by_period.setdefault(_as_of(row, ""), row)
    return [by_period[key] for key in sorted(by_period)]


def _same_period_prior_year(rows: list[dict[str, Any]], as_of: str) -> dict[str, Any] | None:
    """取上年同期行（``20260630`` → ``20250630``）；找不到返回 None。

    只认 8 位 YYYYMMDD 形态的报告期——其它形态（如非数字 date）不匹配，宁可
    不产出同比，也不给出一个错误的基准。
    """
    if not re.fullmatch(r"\d{8}", str(as_of)):
        return None
    target = str(int(as_of[:4]) - 1) + as_of[4:]
    for row in rows:
        if _as_of(row, "") == target:
            return row
    return None


def extract_facts(collection: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Extract a deliberately small, stable facts manifest from a collection."""
    dims = _index(collection)
    fetched_at = str(collection.get("fetched_at") or "unknown")
    facts: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []

    def add_source(dimension: str, dim: dict[str, Any] | None) -> str:
        sid = _source_id(dimension, dim)
        meta = (dim or {}).get("_meta") or {}
        sources.setdefault(sid, {
            "id": sid, "dimension": dimension,
            "source": meta.get("source") or meta.get("primary_source") or "unknown",
            "query_params": meta.get("query_params"),
        })
        return sid

    for dimension, dim in dims.items():
        if dim.get("status") not in {"available", "partial"} or not dim.get("data"):
            meta = dim.get("_meta") or {}
            gaps.append({
                "id": f"gap.{dimension}", "dimension": dimension,
                "reason": meta.get("error") or meta.get("reason") or "数据不可得",
                "attempted_sources": (meta.get("attempted_sources") or ([meta.get("source")] if meta.get("source") else [])),
            })

    basic = dims.get("basic_info")
    if isinstance((basic or {}).get("data"), dict):
        data = basic["data"]
        sid = add_source("basic_info", basic)
        if data.get("name") or data.get("股票简称"):
            facts.append(_fact("basic.name", data.get("name") or data.get("股票简称"), as_of=fetched_at,
                               unit="text", basis="证券简称", source_id=sid))
        if data.get("industry") or data.get("行业"):
            facts.append(_fact("basic.industry", data.get("industry") or data.get("行业"), as_of=fetched_at,
                               unit="text", basis="行业标签", source_id=sid))

    quote = dims.get("quote")
    if isinstance((quote or {}).get("data"), dict):
        data = quote["data"]
        sid = add_source("quote", quote)
        price = _number(data.get("price", data.get("close")))
        if price is not None:
            facts.append(_fact("quote.price.latest", price, as_of=fetched_at, unit="CNY", basis="最新价", source_id=sid))
        change = _number(data.get("change_pct"))
        if change is not None:
            facts.append(_fact("quote.change_pct.latest", change, as_of=fetched_at, unit="percent", basis="日涨跌幅", source_id=sid))

    valuation = dims.get("valuation")
    val_rows = (valuation or {}).get("data")
    if isinstance(val_rows, list) and val_rows:
        sid = add_source("valuation", valuation)
        rows = [row for row in val_rows if isinstance(row, dict)]
        latest = rows[-1]
        latest_pe = _number(latest.get("pe_ttm"))
        if latest_pe is not None:
            as_of = _as_of(latest, fetched_at)
            facts.append(_fact("valuation.pe_ttm.latest", latest_pe, as_of=as_of, unit="x", basis="PE(TTM)", source_id=sid))
            series = [value for row in rows if (value := _number(row.get("pe_ttm"))) is not None and value > 0]
            pct = _percentile(series, latest_pe)
            if pct is not None:
                facts.append(_fact("valuation.pe_ttm.percentile", pct, as_of=as_of, unit="percent",
                                   basis="正 PE 历史序列分位", source_id=sid,
                                   formula="count(PE<=latest positive PE)/count(positive PE)*100"))
        latest_pb = _number(latest.get("pb"))
        if latest_pb is not None:
            facts.append(_fact("valuation.pb.latest", latest_pb, as_of=_as_of(latest, fetched_at), unit="x", basis="PB", source_id=sid))

    financials = dims.get("financials")
    fin_rows = (financials or {}).get("data")
    if isinstance(fin_rows, list) and fin_rows:
        sid = add_source("financials", financials)
        rows = _dedupe_financial_rows(fin_rows)
        latest = rows[-1]
        as_of = _as_of(latest, fetched_at)
        for key, fid, unit, basis in (
            ("revenue", "financials.revenue.latest", "CNY", "营业收入"),
            ("net_profit", "financials.net_profit.latest", "CNY", "归母净利润"),
            ("n_cashflow_act", "financials.ocf.latest", "CNY", "经营现金流"),
            ("roe", "financials.roe.latest", "percent", "ROE"),
        ):
            value = _number(latest.get(key))
            if value is not None:
                facts.append(_fact(fid, value, as_of=as_of, unit=unit, basis=basis, source_id=sid))
        # B4（2026-09-15 修正）：原先取 rows[-2] 作「相邻已披露期」，在报告期混合的
        # 序列上会拿半年报 ÷ 一季报（H1 本身含 Q1），300750 实测得出 +114.4% 这种
        # 无意义的数（正确同口径同比 +54.80%）。改为**按报告期对齐取上年同期**，
        # 且先按 end_date 去重——`financials` 维度原始列表既非升序又含重复期
        # （store 快照 #77 实测 20 行仅 16 个唯一期）。找不到同期则**不产出该 fact**。
        revenue = _number(latest.get("revenue"))
        same_period_prior = _same_period_prior_year(rows, as_of)
        prior = _number(same_period_prior.get("revenue")) if same_period_prior else None
        if revenue is not None and prior not in (None, 0):
            facts.append(_fact("financials.revenue.change", round((revenue / prior - 1) * 100, 1), as_of=as_of,
                               unit="percent", basis="营业收入同比（同报告期口径）", source_id=sid,
                               formula="(latest revenue/same period prior year revenue-1)*100"))
        ocf = _number(latest.get("n_cashflow_act", latest.get("ocf")))
        net_profit = _number(latest.get("net_profit"))
        if ocf is not None and net_profit not in (None, 0):
            facts.append(_fact("financials.ocf_to_np.latest", round(ocf / net_profit, 3), as_of=as_of,
                               unit="ratio", basis="经营现金流/归母净利润", source_id=sid,
                               formula="n_cashflow_act/net_profit"))

    kline = dims.get("kline")
    rows = (kline or {}).get("data")
    if isinstance(rows, list) and rows:
        sid = add_source("kline", kline)
        ordered = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: _as_of(row, ""))
        closes = [_number(row.get("close")) for row in ordered]
        closes = [value for value in closes if value is not None]
        if len(closes) >= 20:
            latest = closes[-1]; ma20 = sum(closes[-20:]) / 20
            facts.append(_fact("technical.price_vs_ma20.latest", round((latest / ma20 - 1) * 100, 2),
                               as_of=_as_of(ordered[-1], fetched_at), unit="percent", basis="收盘价相对MA20",
                               source_id=sid, formula="(latest close/mean(last 20 closes)-1)*100"))
    return facts, sources, gaps


def _find(facts: list[dict[str, Any]], fid: str) -> dict[str, Any] | None:
    return next((fact for fact in facts if fact["id"] == fid), None)


def _finding(fid: str, claim: str, *, fact_ids: list[str], counter_fact_ids: list[str] | None = None,
             unknown_ids: list[str] | None = None, strength: str = "medium", status: str = "supported",
             relevance: str = "secondary", verification: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": fid, "claim": claim, "fact_ids": fact_ids, "counter_fact_ids": counter_fact_ids or [],
            "unknown_ids": unknown_ids or [], "association_status": "descriptive", "evidence_strength": strength,
            "status": status, "profile_relevance": relevance, "verification": verification or {}}


def build_findings(facts: list[dict[str, Any]], gaps: list[dict[str, Any]], profile: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build only descriptive conclusions for which the manifest has evidence."""
    findings: list[dict[str, Any]] = []
    pe = _find(facts, "valuation.pe_ttm.latest"); pct = _find(facts, "valuation.pe_ttm.percentile")
    if pe and pct:
        zone = "偏高" if pct["value"] >= 80 else ("偏低" if pct["value"] <= 20 else "中间")
        findings.append(_finding("valuation-position", f"PE(TTM) 为 {pe['value']:.2f}x，位于可用正 PE 序列的 {pct['value']:.1f}% 分位，属于历史样本的{zone}位置；这只描述估值位置，不能单独解释未来表现。",
                                 fact_ids=[pe["id"], pct["id"]], relevance="primary",
                                 verification={"event": "下一报告期", "test": "核对盈利变化与估值口径是否同步更新"}))
    revenue_change = _find(facts, "financials.revenue.change")
    ocf_ratio = _find(facts, "financials.ocf_to_np.latest")
    if revenue_change:
        direction = "增长" if revenue_change["value"] > 0 else "下降"
        counter = [ocf_ratio["id"]] if ocf_ratio else []
        findings.append(_finding("financial-revenue-change", f"营业收入同比为 {revenue_change['value']:+.1f}%（同报告期口径），呈现{direction}；口径已按报告期对齐，仍应以财报原文核对。",
                                 fact_ids=[revenue_change["id"]], counter_fact_ids=counter,
                                 status="mixed" if ocf_ratio else "supported", relevance="primary",
                                 verification={"event": "下一次定期报告", "test": "比较同口径收入、净利润和现金流变化"}))
    if ocf_ratio:
        descriptor = "低于" if ocf_ratio["value"] < 0.6 else "不低于"
        findings.append(_finding("cash-conversion", f"最新披露期经营现金流/归母净利润为 {ocf_ratio['value']:.3f}，{descriptor} 0.6；该比值只能提示现金转化需要复核，不能单独判断经营质量。",
                                 fact_ids=[ocf_ratio["id"]], unknown_ids=["gap.balance_sheet"], relevance="primary",
                                 verification={"event": "下一次定期报告", "test": "核对经营现金流、应收和存货的同口径变化"}))
    ma20 = _find(facts, "technical.price_vs_ma20.latest")
    if ma20:
        relation = "上方" if ma20["value"] >= 0 else "下方"
        findings.append(_finding("technical-state", f"最新收盘价相对 MA20 为 {ma20['value']:+.2f}%，位于 MA20 {relation}；这是市场状态描述，不构成操作信号。",
                                 fact_ids=[ma20["id"]], strength="weak", relevance="secondary",
                                 verification={"event": "后续交易日", "test": "观察价格、成交量与基本面证据是否一致"}))
    if profile and "valuation" in (profile.get("focuses") or []):
        findings.sort(key=lambda item: (item["id"] != "valuation-position", item["profile_relevance"] != "primary"))
    elif profile and "capital_flow" in (profile.get("focuses") or []):
        findings.sort(key=lambda item: (item["id"] != "technical-state", item["profile_relevance"] != "primary"))
    else:
        findings.sort(key=lambda item: (item["profile_relevance"] != "primary", item["id"]))
    findings = findings[:5]
    tension: dict[str, Any]
    if revenue_change and revenue_change["value"] > 0 and ocf_ratio and ocf_ratio["value"] < 0.6:
        tension = {"claim": "收入变化与现金转化没有形成同向的充分证据，现金流及营运资本是最需要补证的矛盾。",
                   "fact_ids": [revenue_change["id"], ocf_ratio["id"]], "status": "mixed"}
    else:
        tension = {"claim": "当前可用 Facts 尚不足以形成可验证的单一核心矛盾；应先补齐财务和事件证据。",
                   "fact_ids": [], "status": "insufficient"}
    return findings, tension


def _beijing(timestamp: Any) -> str | None:
    """UTC/ISO 时间戳 → 北京时间标签。

    失败时截断回退，**不得回落成 ISO 直出**——本仓已发生过「同报告混时区」缺陷
    （``render_markdown/_v3.py`` 的 P2-2 修复）。"""
    if not timestamp:
        return None
    text = str(timestamp)
    try:
        from lib.shared_dates import fmt_fetched_at

        return fmt_fetched_at(text)
    except Exception:  # noqa: BLE001 - 时间格式化失败不该阻断报告
        return text[:16]


def load_snapshot_diff(symbol: str, collection: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """读取「当前采集 vs store 上次快照」的关键字段对比。

    返回 ``(key_diff | None, reason)``，``reason ∈ {"ok","no_history","store_unavailable"}``。

    **本模块唯一的 I/O 点与唯一的 try/except 点。** 读取刻意放在调用方（CLI）而非
    ``build_report_model`` 内部：``store.connect_db`` 会 ``mkdir`` 并建库，若模型层
    自动读 store，未隔离 store 的测试会读写用户的真实库、断言随库内容漂移。
    """
    try:
        from lib.store import load_key_diff_vs_stored
    except Exception:  # noqa: BLE001 - 分发包缺 store 时降级
        return None, "store_unavailable"
    try:
        diff = load_key_diff_vs_stored(symbol, collection)
    except Exception:  # noqa: BLE001 - 库损坏/锁竞争一律降级为「不可得」
        return None, "store_unavailable"
    if diff is None:
        return None, "no_history"
    return diff, "ok"


def build_discoveries(key_diff: dict[str, Any] | None, *, reason: str = "no_history") -> dict[str, Any]:
    """把 store 的关键字段对比归一化为渲染用的发现区块。

    本函数**不重算任何变化量**——`old`/`new`/`pct` 全部原样取自 store，
    保证 insight 与 full 模式的「相对上次调研变化」同源同值。

    ``status`` 是渲染分支的唯一开关；``reason`` 只进侧车供审计，渲染器禁读。
    """
    block: dict[str, Any] = {
        "status": "none", "reason": reason,
        "old_at": None, "new_at": None, "old_at_label": None, "new_at_label": None,
        "items": [], "events": None, "unchanged_count": 0,
    }
    if not key_diff:
        return block

    old_at = key_diff.get("old_at") or None
    new_at = key_diff.get("new_at") or None
    block["old_at"], block["new_at"] = old_at, new_at
    block["old_at_label"], block["new_at_label"] = _beijing(old_at), _beijing(new_at)
    block["unchanged_count"] = len(key_diff.get("unchanged") or [])

    items: list[dict[str, Any]] = []
    for category, entries in (key_diff.get("categories") or {}).items():
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            field = str(entry.get("field") or "")
            if not field:
                continue
            items.append({
                "category": str(category),
                "category_label": _CATEGORY_LABELS.get(str(category), str(category)),
                "field": field,
                "label": _FIELD_LABELS.get(field, field),
                "old": entry.get("old"), "new": entry.get("new"), "pct": entry.get("pct"),
            })

    def _order(item: dict[str, Any]) -> int:
        try:
            return _DISCOVERY_ORDER.index(item["category"])
        except ValueError:
            return len(_DISCOVERY_ORDER)

    items.sort(key=_order)
    block["items"] = items[:MAX_DISCOVERIES]
    events = key_diff.get("events")
    block["events"] = events if isinstance(events, dict) and events else None

    if block["items"] or block["events"]:
        block["status"] = "changed"
        block["reason"] = "ok"
    else:
        block["reason"] = "no_material_change"
    return block


# ── A5：分析链（工程约定，无同行评审先例；只写关系与替代解释，不写因果） ──────


def _chain_valuation_vs_earnings(fx: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """CH-1：估值分位 × 收入同比方向。中间分位不建链（不硬凑）。"""
    pct, chg = fx.get("valuation.pe_ttm.percentile"), fx.get("financials.revenue.change")
    if not pct or not chg:
        return None
    value, change = pct["value"], chg["value"]
    if not (value <= 20 or value >= 80):
        return None
    zone = "偏低" if value <= 20 else "偏高"
    move = "增长" if change > 0 else "下降"
    aligned = (value <= 20 and change < 0) or (value >= 80 and change > 0)
    return {
        "id": "chain.valuation-vs-earnings",
        "fact_ids": [pct["id"], chg["id"]],
        "relation": (
            f"PE(TTM) 位于可用正 PE 序列的 {value:.1f}% 分位（{zone}），"
            f"营业收入同比为 {change:+.1f}%（{move}）；两者方向{'一致' if aligned else '不一致'}。"
        ),
        "mechanism": (
            "可能机制（未证实）：市场对未来盈利可持续性或风险溢价重新定价，"
            "使估值分位与已披露增长不同向。"
            if not aligned else
            "可能机制（未证实）：已披露的数据与定价方向相互印证，"
            "但两者是否共享同一驱动因素，现有证据无法区分。"
        ),
        "alternatives": [
            "报告期口径不可比（跨度、追溯调整或合并范围变化），收入变化不代表趋势。",
            "行业整体估值与风险偏好等系统性因素同时推动分位（本项目未采集同业横截面）。",
            "分位基于单一窗口的正 PE 序列，窗口长度与样本结构会影响该数值本身。",
        ],
        "association_status": "consistent" if aligned else "mechanism_unconfirmed",
        "verification": {
            "event": "下一次定期报告",
            "test": "核对同口径收入、净利率与 PE 分位是否同时更新；并记录同业估值中位数（当前未采集）",
        },
        "note": _CHAIN_NOTE,
    }


def _chain_cash_conversion(fx: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """CH-2：现金转化 × 收入同比。阈值 0.6 沿用 build_findings 既有判定。"""
    ratio, chg = fx.get("financials.ocf_to_np.latest"), fx.get("financials.revenue.change")
    if not ratio or not chg:
        return None
    value, change = ratio["value"], chg["value"]
    weak = value < 0.6
    return {
        "id": "chain.cash-conversion",
        "fact_ids": [ratio["id"], chg["id"]],
        "relation": (
            f"最新披露期经营现金流/归母净利润为 {value:.3f}，"
            f"营业收入同比为 {change:+.1f}%；该比值{'低于' if weak else '不低于'} 0.6。"
        ),
        "mechanism": (
            "可能机制（未证实）：增长伴随应收或存货占用增加，或收益确认与收款节奏错位，"
            "使现金转化低于利润。"
            if weak else
            "现金转化与利润方向不冲突：该比值不低于 0.6，但单期比值不足以判断趋势。"
        ),
        "alternatives": [
            "期末集中收款或票据结算使单期比值跨期波动，单点读数不代表常态。",
            "非经常性损益与少数股东权益使分母口径与现金流不完全匹配。",
            "本项目未采集应收账款、存货与合同负债，无法区分营运资本占用与其他原因。",
        ],
        "association_status": "mechanism_unconfirmed" if weak else "consistent",
        "verification": {
            "event": "下一次定期报告",
            "test": "核对经营现金流、应收账款与存货的同口径变化，确认比值是否回到 0.6 以上",
        },
        "note": _CHAIN_NOTE,
    }


def _chain_price_state_vs_valuation(fx: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """CH-3：价格状态 × 估值分位。恒为「机制未证实」——两种解释不可分。"""
    ma, pct = fx.get("technical.price_vs_ma20.latest"), fx.get("valuation.pe_ttm.percentile")
    if not ma or not pct:
        return None
    value, percentile = ma["value"], pct["value"]
    zone = "偏低" if percentile <= 20 else ("偏高" if percentile >= 80 else "中间")
    return {
        "id": "chain.price-state-vs-valuation",
        "fact_ids": [ma["id"], pct["id"]],
        "relation": (
            f"最新收盘价相对 MA20 为 {value:+.2f}%（{'上方' if value >= 0 else '下方'}），"
            f"PE 分位为 {percentile:.1f}%（{zone}）。价格状态只用于提出需要区分的解释。"
        ),
        "mechanism": (
            "可能机制（未证实）：若市场已计入负面预期，价格走弱与低分位会同向；"
            "若恶化尚未被披露数据反映，两者也会同向——现有证据无法区分这两种解释。"
        ),
        "alternatives": [
            "价格状态与估值分位可能分别由风险偏好、流动性等不同因素驱动，同向只是共同暴露于市场因子。",
            "MA20 是短窗口状态量、分位基于长窗口序列，两者口径不可直接比较。",
        ],
        "association_status": "mechanism_unconfirmed",
        "verification": {
            "event": "后续交易日与下一次定期报告",
            "test": "观察价格状态是否与披露数据同向变化；若出现背离，记录为需要重估的解释分歧",
        },
        "note": _CHAIN_NOTE,
    }


_CHAIN_RULES = (
    _chain_valuation_vs_earnings,
    _chain_cash_conversion,
    _chain_price_state_vs_valuation,
)


def build_analysis_chains(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按固定优先级构造分析链（上限 ``MAX_ANALYSIS_CHAINS``）。

    纯函数：零 I/O、零 LLM、不随 profile 变化（排序个性化留待后续版本）。
    前置事实缺失或方向不构成链时**整条省略**，不硬凑；零链由渲染层出占位声明。
    """
    fx = {fact["id"]: fact for fact in facts if isinstance(fact, dict) and fact.get("id")}
    chains: list[dict[str, Any]] = []
    for rule in _CHAIN_RULES:
        if len(chains) >= MAX_ANALYSIS_CHAINS:
            break
        try:
            chain = rule(fx)
        except Exception:  # noqa: BLE001 - 单条规则异常不该拖垮整份报告
            continue
        if chain:
            chains.append(chain)
    return chains


def validate_insight(model: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    facts = model.get("facts")
    findings = model.get("findings")
    if not isinstance(facts, list) or not isinstance(findings, list):
        return ["facts 和 findings 必须为列表"]
    ids = {fact.get("id") for fact in facts if isinstance(fact, dict)}
    if len(ids) != len(facts) or None in ids:
        errors.append("Facts ID 必须唯一且非空")
    for fact in facts:
        if not isinstance(fact, dict) or not fact.get("source_ids") or not fact.get("as_of"):
            errors.append("每个 Fact 必须有 source_ids 和 as_of")
            break
        if fact.get("formula") and not str(fact["formula"]).strip():
            errors.append("派生 Fact 的 formula 不得为空")
            break
    for finding in findings:
        if not isinstance(finding, dict) or not finding.get("claim"):
            errors.append("Finding 必须有 claim")
            continue
        refs = list(finding.get("fact_ids") or []) + list(finding.get("counter_fact_ids") or [])
        if not finding.get("fact_ids") or any(ref not in ids for ref in refs):
            errors.append(f"Finding {finding.get('id')} 引用了不存在的 Fact")
        if finding.get("association_status") not in {"descriptive", "consistent", "mechanism_unconfirmed", "indeterminate"}:
            errors.append(f"Finding {finding.get('id')} 的关联边界非法")
    errors.extend(_validate_discoveries(model.get("discoveries")))
    errors.extend(_validate_chains(model.get("analysis_chains"), ids))
    return errors


def _validate_discoveries(block: Any) -> list[str]:
    """校验发现区块（缺键视为合法，兼容旧/手写 model）。"""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["discoveries 必须是对象"]
    errors: list[str] = []
    status = block.get("status")
    if status not in {"changed", "none"}:
        return [f"discoveries.status 非法：{status!r}"]
    items = block.get("items")
    if not isinstance(items, list):
        return ["discoveries.items 必须是列表"]
    if status == "none":
        if items:
            errors.append("discoveries.status 为 none 时 items 必须为空")
        return errors
    if not items:
        errors.append("discoveries.status 为 changed 时 items 不得为空")
    if len(items) > MAX_DISCOVERIES:
        errors.append(f"discoveries 条目不得超过 {MAX_DISCOVERIES} 条")
    for item in items:
        if not isinstance(item, dict) or not item.get("category") or not item.get("label"):
            errors.append("discoveries 每条必须含 category 与 label")
            break
        # 只查键是否存在，不要求非 None：store 的 diff 会合法产出「值 → None」
        # （某指标本期转为不可得，如 margin_balance）。渲染层以 "-" 呈现。
        if "old" not in item or "new" not in item:
            errors.append("discoveries 每条必须含 old 与 new")
            break
    if not block.get("old_at") or not block.get("new_at"):
        errors.append("discoveries.status 为 changed 时必须带 old_at 与 new_at")
    if not block.get("old_at_label") or not block.get("new_at_label"):
        errors.append("discoveries 的时间标签缺失（须为北京时间，不得回落 ISO 直出）")
    events = block.get("events")
    if events is not None and not isinstance(events, dict):
        errors.append("discoveries.events 必须是对象或 null")
    return errors


def _validate_chains(chains: Any, fact_ids: set[Any]) -> list[str]:
    """校验分析链（缺键视为 ``[]``）。链比 finding 更严：不允许 descriptive。"""
    if chains is None:
        return []
    if not isinstance(chains, list):
        return ["analysis_chains 必须是列表"]
    errors: list[str] = []
    if len(chains) > MAX_ANALYSIS_CHAINS:
        errors.append(f"analysis_chains 不得超过 {MAX_ANALYSIS_CHAINS} 条")
    seen: set[str] = set()
    for chain in chains:
        if not isinstance(chain, dict):
            errors.append("analysis_chains 每条必须为对象")
            break
        cid = chain.get("id")
        if not cid or cid in seen:
            errors.append("analysis_chains 的 id 必须非空且唯一")
        else:
            seen.add(str(cid))
        refs = chain.get("fact_ids") or []
        if len(refs) < 2:
            errors.append(f"分析链 {cid} 至少须关联两个事实")
        elif any(ref not in fact_ids for ref in refs):
            errors.append(f"分析链 {cid} 引用了不存在的 Fact")
        if not chain.get("relation") or not chain.get("mechanism"):
            errors.append(f"分析链 {cid} 必须含 relation 与 mechanism")
        alternatives = chain.get("alternatives")
        if not isinstance(alternatives, list) or len(alternatives) < 2 or any(not str(a).strip() for a in alternatives):
            errors.append(f"分析链 {cid} 至少须给出两条替代解释")
        if chain.get("association_status") not in {"consistent", "mechanism_unconfirmed"}:
            errors.append(f"分析链 {cid} 的关联边界非法")
        verification = chain.get("verification")
        if not isinstance(verification, dict) or not verification.get("event") or not verification.get("test"):
            errors.append(f"分析链 {cid} 必须含可观察的验证动作")
        if _CHAIN_FORBIDDEN_FACTS & set(refs):
            errors.append(f"分析链 {cid} 不得引用价格反应类事实")
        text = " ".join([str(chain.get("relation") or ""), str(chain.get("mechanism") or "")]
                        + [str(a) for a in (alternatives or [])])
        if _CAUSAL_RE.search(text):
            errors.append(f"分析链 {cid} 含因果断言措辞")
    return errors


def build_report_model(collection: dict[str, Any], symbol: str, profile: dict[str, Any] | None = None,
                       *, key_diff: dict[str, Any] | None = None,
                       diff_reason: str = "no_history") -> dict[str, Any]:
    """组装 insight 报告模型。

    ``key_diff`` / ``diff_reason`` 由调用方（CLI）注入——本函数**不做任何 I/O**，
    见 ``load_snapshot_diff`` 的说明。
    """
    facts, sources, gaps = extract_facts(collection)
    findings, tension = build_findings(facts, gaps, profile)
    completion = "complete" if len(findings) >= 2 else "insufficient"
    model = {
        "report_contract_version": REPORT_CONTRACT_VERSION, "generator_version": GENERATOR_VERSION,
        "mode": "insight", "completion": completion, "symbol": symbol,
        "fetched_at": collection.get("fetched_at"), "profile": profile or {}, "facts": facts,
        "sources": list(sources.values()), "gaps": gaps, "findings": findings, "core_tension": tension,
        "discoveries": build_discoveries(key_diff, reason=diff_reason),
        "analysis_chains": build_analysis_chains(facts),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    errors = validate_insight(model)
    if errors:
        raise InsightSchemaError("; ".join(errors))
    return model


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_sidecars(report_path: Path, model: dict[str, Any], *, html_path: Path | None = None) -> dict[str, Path]:
    """Write Facts and Findings first; publish the manifest last."""
    facts_path = report_path.with_suffix(".facts.json")
    insight_path = report_path.with_suffix(".insight.json")
    manifest_path = report_path.with_suffix(".report.json")
    _atomic_json(facts_path, {"report_contract_version": REPORT_CONTRACT_VERSION, "facts": model["facts"], "sources": model["sources"]})
    _atomic_json(insight_path, {key: model[key] for key in ("report_contract_version", "mode", "completion", "symbol", "fetched_at", "profile", "findings", "gaps", "core_tension", "discoveries", "analysis_chains")})
    _atomic_json(manifest_path, {
        "report_contract_version": REPORT_CONTRACT_VERSION, "generator_version": model["generator_version"],
        "mode": "insight", "completion": model["completion"], "facts_manifest": facts_path.name,
        "insight": insight_path.name, "report": report_path.name,
        "html": html_path.name if html_path else None,
    })
    return {"facts": facts_path, "insight": insight_path, "manifest": manifest_path}
