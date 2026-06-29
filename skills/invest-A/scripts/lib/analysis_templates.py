"""Analysis template cards for structured report input to Claude.

Design:
  - Pure Python library (no LLM API calls).
  - Builds structured analysis input cards from collected data.
  - Cards are embedded in report output and interpreted by Claude in conversation,
    NOT during the CLI collect/report process.
  - Idempotent: build_analysis_cards skips if cards already exist.

Card types:
  Template A: MDANarrativeCard — 财报MD&A叙事卡片
  Template B: EventClassificationCard — 公告事件分类卡片
  Template C: SentimentCard — 业绩会/研报情绪卡片
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---- Module-level cache ----

_TAXONOMY_CACHE: dict | None = None

# Path to the taxonomy YAML file (relative to this module: scripts/references/)
_TAXONOMY_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "event_type_taxonomy.yaml"
)

# ---- High-confidence direction rules ----
# Keyed by event_type, value is (direction_hint, direction_confidence)

_DIRECTION_RULES: dict[str, tuple[str, str]] = {
    "buyback": ("正向", "medium"),
    "dividend": ("正向", "medium"),
    "holder_increase": ("正向", "medium"),
    "holder_decrease": ("负向", "medium"),
    "st_risk": ("负向", "medium"),
    "litigation": ("负向", "medium"),
}

_DIRECTION_DISCLAIMER = "[参考: 事件类型分类规则，不构成投资建议]"


# ---- Template A ----

@dataclass
class MDANarrativeCard:
    """Template A: 财报MD&A叙事卡片.

    Aggregates key financial metrics from the latest reporting period
    with year-over-year deltas where possible.
    """
    revenue_growth_yoy: Optional[float]
    profit_growth_yoy: Optional[float]
    gross_margin: Optional[float]
    gross_margin_change: Optional[float]
    net_margin: Optional[float]
    net_margin_change: Optional[float]
    operating_cashflow: Optional[float]
    net_profit: Optional[float]
    cashflow_quality_hint: str
    roe: Optional[float]
    roe_change: Optional[float]
    debt_ratio: Optional[float]
    debt_ratio_change: Optional[float]
    asset_turnover: Optional[float]
    asset_turnover_change: Optional[float]
    narrative_slot: str
    generated_at: str


# ---- Template B ----

@dataclass
class EventClassificationCard:
    """Template B: 公告事件分类卡片.

    Groups events from collection["events"] by type and enriches
    each group with taxonomy metadata and direction hints.
    """
    event_type: str
    event_label: str
    events: list[dict]
    impact_dimension: str
    default_duration_hint: str
    direction_hint: str
    direction_confidence: str
    direction_note: str
    sentiment_impact_slot: str
    generated_at: str


# ---- Template C ----

@dataclass
class SentimentCard:
    """Template C: 业绩会/研报情绪卡片.

    Summarises analyst sentiment from research reports:
    rating distribution, EPS consensus, key takeaways.
    """
    research_count: int
    rating_distribution: dict
    eps_forecast_mean: Optional[float]
    eps_forecast_high: Optional[float]
    eps_forecast_low: Optional[float]
    eps_forecast_count: int
    latest_summary: str
    sentiment_slot: str
    data_source_note: str
    generated_at: str


# ---- Main entry point ----


def build_analysis_cards(collection: dict) -> dict:
    """Build all applicable analysis cards and attach to collection._meta.

    Idempotent: if ``_meta.analysis_cards`` already exists, this is a no-op.

    Args:
        collection: The collected data dict (may be modified in-place).

    Returns:
        The same collection dict with ``_meta.analysis_cards`` populated.
    """
    meta = collection.setdefault("_meta", {})
    if "analysis_cards" in meta:
        logger.info("analysis_cards already exist, skipping")
        return collection

    cards: dict[str, Any] = {}

    # Template A — financial narrative
    mda_card = _build_mda_card(collection)
    cards["mda_narrative"] = _card_to_dict(mda_card)

    # Template B — event classifications
    event_cards = _build_event_classification_cards(collection)
    cards["event_classifications"] = [_card_to_dict(c) for c in event_cards]

    # Template C — sentiment
    sentiment_card = _build_sentiment_card(collection)
    cards["sentiment"] = _card_to_dict(sentiment_card)

    meta["analysis_cards"] = cards
    return collection


# ---- Template A builder ----


def _build_mda_card(collection: dict) -> Optional[MDANarrativeCard]:
    """Build Template A from the **financials** dimension.

    Reads the latest and prior-year financial records, computes YoY
    changes and a cashflow-vs-profit quality hint.

    Returns:
        MDANarrativeCard, or *None* when financials are unavailable.
    """
    dims = collection.get("dimensions", [])
    fin_dim: dict | None = None
    for d in dims:
        if isinstance(d, dict) and d.get("dimension") == "financials":
            fin_dim = d
            break

    if fin_dim is None:
        return None

    raw_data = fin_dim.get("data")
    if not raw_data or not isinstance(raw_data, list) or len(raw_data) == 0:
        return None

    # Keep only records with a parseable end_date
    records: list[dict] = []
    for r in raw_data:
        ed = r.get("end_date")
        if ed and isinstance(ed, (str, int)) and len(str(ed).strip()) >= 4:
            records.append(r)

    if not records:
        return None

    # Latest first
    records.sort(key=lambda r: str(r.get("end_date", "")), reverse=True)
    latest = records[0]
    latest_end = str(latest.get("end_date", "")).strip()

    # ---- Locate the YoY companion (same calendar month-day, year-1) ----
    yoy_record: dict | None = None
    if len(latest_end) >= 8:
        try:
            yoy_end = str(int(latest_end[:4]) - 1) + latest_end[4:]
            for r in records:
                if str(r.get("end_date", "")).strip() == yoy_end:
                    yoy_record = r
                    break
        except (ValueError, TypeError):
            pass

    # ---- Numeric extraction helper ----
    def _get(d: dict, *keys: str) -> Optional[float]:
        for k in keys:
            v = d.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    now_iso = datetime.now(timezone.utc).isoformat()

    # ---- Revenue ----
    revenue = _get(latest, "revenue")
    rev_growth: Optional[float] = None
    if revenue is not None and yoy_record is not None:
        rev_prior = _get(yoy_record, "revenue")
        if rev_prior is not None and abs(rev_prior) > 1e-9:
            rev_growth = round((revenue - rev_prior) / abs(rev_prior) * 100, 2)

    # ---- Net profit ----
    net_profit_val = _get(latest, "net_profit")
    profit_growth: Optional[float] = None
    if net_profit_val is not None and yoy_record is not None:
        np_prior = _get(yoy_record, "net_profit")
        if np_prior is not None and abs(np_prior) > 1e-9:
            profit_growth = round((net_profit_val - np_prior) / abs(np_prior) * 100, 2)

    # ---- Margins ----
    gm = _get(latest, "grossprofit_margin", "gross_margin")
    nm = _get(latest, "netprofit_margin", "net_margin")

    gm_change: Optional[float] = None
    if gm is not None and yoy_record is not None:
        gm_prior = _get(yoy_record, "grossprofit_margin", "gross_margin")
        if gm_prior is not None:
            gm_change = round(gm - gm_prior, 2)

    nm_change: Optional[float] = None
    if nm is not None and yoy_record is not None:
        nm_prior = _get(yoy_record, "netprofit_margin", "net_margin")
        if nm_prior is not None:
            nm_change = round(nm - nm_prior, 2)

    # ---- Operating cashflow & quality hint ----
    ocf = _get(latest, "n_cashflow_act", "ocf")
    cq_hint = ""
    if ocf is not None and net_profit_val is not None and abs(net_profit_val) > 1e-9:
        if ocf > net_profit_val * 1.1:
            cq_hint = "良好"
        elif ocf >= net_profit_val * 0.9:
            cq_hint = "一般"
        else:
            cq_hint = "需关注"

    # ---- ROE ----
    roe = _get(latest, "roe")
    roe_change: Optional[float] = None
    if roe is not None and yoy_record is not None:
        roe_prior = _get(yoy_record, "roe")
        if roe_prior is not None:
            roe_change = round(roe - roe_prior, 2)

    # ---- Debt ratio ----
    debt_ratio = _get(latest, "debt_to_assets")
    debt_ratio_change: Optional[float] = None
    if debt_ratio is not None and yoy_record is not None:
        dr_prior = _get(yoy_record, "debt_to_assets")
        if dr_prior is not None:
            debt_ratio_change = round(debt_ratio - dr_prior, 2)

    # ---- Asset turnover ----
    at = _get(latest, "assets_turn")
    at_change: Optional[float] = None
    if at is not None and yoy_record is not None:
        at_prior = _get(yoy_record, "assets_turn")
        if at_prior is not None:
            at_change = round(at - at_prior, 2)

    return MDANarrativeCard(
        revenue_growth_yoy=rev_growth,
        profit_growth_yoy=profit_growth,
        gross_margin=gm,
        gross_margin_change=gm_change,
        net_margin=nm,
        net_margin_change=nm_change,
        operating_cashflow=ocf,
        net_profit=net_profit_val,
        cashflow_quality_hint=cq_hint,
        roe=roe,
        roe_change=roe_change,
        debt_ratio=debt_ratio,
        debt_ratio_change=debt_ratio_change,
        asset_turnover=at,
        asset_turnover_change=at_change,
        narrative_slot="[待 Claude 填充管理层论述解读]",
        generated_at=now_iso,
    )


# ---- Template B builder ----


def _build_event_classification_cards(
    collection: dict,
) -> list[EventClassificationCard]:
    """Build Template B cards from ``collection["events"]``.

    Events are grouped by ``type``, enriched with taxonomy metadata,
    and annotated with high-confidence direction rules when applicable.

    Returns:
        List of EventClassificationCard (may be empty).
    """
    events = collection.get("events")
    if not events or not isinstance(events, list):
        return []

    taxonomy = _load_taxonomy()
    event_types_meta = taxonomy.get("event_types", {})

    # Group by event type
    grouped: dict[str, list[dict]] = {}
    for ev in events:
        etype = str(ev.get("type", "other"))
        grouped.setdefault(etype, []).append(ev)

    now_iso = datetime.now(timezone.utc).isoformat()
    cards: list[EventClassificationCard] = []

    for etype, ev_list in grouped.items():
        meta = event_types_meta.get(etype, {})
        label = meta.get("label", etype)
        impact_dim = meta.get("impact_dimension", "治理")
        duration = meta.get("default_duration_hint", "短期扰动")

        # Direction hint from high-confidence rules
        rule = _DIRECTION_RULES.get(etype)
        if rule:
            direction_hint, direction_confidence = rule
            direction_note = _DIRECTION_DISCLAIMER
        else:
            direction_hint = ""
            direction_confidence = "low"
            direction_note = ""

        cards.append(
            EventClassificationCard(
                event_type=etype,
                event_label=label,
                events=ev_list,
                impact_dimension=impact_dim,
                default_duration_hint=duration,
                direction_hint=direction_hint,
                direction_confidence=direction_confidence,
                direction_note=direction_note,
                sentiment_impact_slot="[待 Claude 填充语气信号]",
                generated_at=now_iso,
            )
        )

    # Sort: cards with a direction_hint first, then by descending event count
    cards.sort(key=lambda c: (0 if c.direction_hint else 1, -len(c.events)))
    return cards


# ---- Template C builder ----


def _build_sentiment_card(collection: dict) -> Optional[SentimentCard]:
    """Build Template C from the **research** dimension.

    Requires ``research_summary`` data inside the research dimension entry.
    Falls back gracefully when data is absent.

    Returns:
        SentimentCard, or *None* when research data is unavailable.
    """
    dims = collection.get("dimensions", [])
    res_dim: dict | None = None
    for d in dims:
        if isinstance(d, dict) and d.get("dimension") == "research":
            res_dim = d
            break

    if res_dim is None:
        return None

    summary = res_dim.get("research_summary")
    if not summary or not isinstance(summary, dict):
        return None

    now_iso = datetime.now(timezone.utc).isoformat()

    # ---- Rating distribution ----
    ratings = summary.get("latest_ratings", [])
    rating_dist: dict[str, int] = {"买入": 0, "增持": 0, "中性": 0, "减持": 0}
    for r in ratings:
        rating_str = str(r.get("rating", ""))
        if "买入" in rating_str and "卖" not in rating_str:
            rating_dist["买入"] = rating_dist.get("买入", 0) + 1
        elif "增持" in rating_str:
            rating_dist["增持"] = rating_dist.get("增持", 0) + 1
        elif "中性" in rating_str:
            rating_dist["中性"] = rating_dist.get("中性", 0) + 1
        elif "减持" in rating_str:
            rating_dist["减持"] = rating_dist.get("减持", 0) + 1

    # ---- EPS forecasts ----
    eps_forecasts = summary.get("eps_forecasts", [])
    eps_mean: Optional[float] = None
    eps_high: Optional[float] = None
    eps_low: Optional[float] = None
    eps_count = 0
    if eps_forecasts:
        first = eps_forecasts[0]
        eps_mean = first.get("avg_eps")
        eps_count = first.get("n_analysts", 0)
        eps_high = first.get("max_eps") or first.get("high")
        eps_low = first.get("min_eps") or first.get("low")
    # Do not fallback target_price_range (CNY) into EPS fields — different units.

    # ---- Latest summary text ----
    summary_text = summary.get("summary_text", "")
    if not summary_text:
        raw = res_dim.get("data")
        if raw and isinstance(raw, list) and len(raw) > 0:
            title = raw[0].get("report_title", "") or str(raw[0].get("title", ""))
            summary_text = title[:200]

    # ---- Data source note ----
    source = summary.get("source", "")
    if source == "tushare.report_rc":
        source_note = "数据源: Tushare report_rc（机构研报盈利预测+评级）"
    elif source == "tushare.forecast":
        source_note = "数据源: Tushare forecast（公司业绩预告）"
    elif source == "akshare.research":
        source_note = "数据源: akshare 东方财富个股研报"
    else:
        source_note = "数据源: 未获取到结构化研报数据"

    return SentimentCard(
        research_count=len(ratings),
        rating_distribution=rating_dist,
        eps_forecast_mean=eps_mean,
        eps_forecast_high=eps_high,
        eps_forecast_low=eps_low,
        eps_forecast_count=eps_count,
        latest_summary=(summary_text[:200] if summary_text else ""),
        sentiment_slot="[待 Claude 标注语气信号]",
        data_source_note=source_note,
        generated_at=now_iso,
    )


# ---- Taxonomy loader ----


def _load_taxonomy() -> dict:
    """Load ``event_type_taxonomy.yaml`` with module-level caching.

    Returns:
        Parsed taxonomy dict (``{"schema_version": ..., "event_types": ...}``).
    """
    global _TAXONOMY_CACHE
    if _TAXONOMY_CACHE is not None:
        return _TAXONOMY_CACHE

    if not _TAXONOMY_PATH.exists():
        logger.warning("Taxonomy file not found: %s", _TAXONOMY_PATH)
        _TAXONOMY_CACHE = {"schema_version": "0.1", "event_types": {}}
        return _TAXONOMY_CACHE

    import yaml

    try:
        with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            data = {"schema_version": "0.1", "event_types": {}}
        _TAXONOMY_CACHE = data
    except Exception as exc:
        logger.warning("Failed to load taxonomy: %s", exc)
        _TAXONOMY_CACHE = {"schema_version": "0.1", "event_types": {}}

    return _TAXONOMY_CACHE


# ---- Serialisation ----


def _card_to_dict(card: Any) -> dict | None:
    """Convert a dataclass instance to a plain dict for JSON serialisation.

    Args:
        card: A dataclass instance, a dict, or *None*.

    Returns:
        Plain dict representation, or *None* when input is *None*.
    """
    if card is None:
        return None
    if isinstance(card, dict):
        return card
    if hasattr(card, "__dataclass_fields__"):
        return asdict(card)
    logger.warning("_card_to_dict: unexpected type %s", type(card).__name__)
    return None
