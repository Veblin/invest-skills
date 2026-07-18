"""Risk / bull-bear / left-right probability sections."""
from __future__ import annotations

import logging
from typing import Any

from lib.nums import safe_float as _safe_num
from lib.technical import compute, sort_kline_asc
from lib.participant_scan import resolve_moneyflow
from lib.schema import ProbabilityStructure
from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

from .render_utils import (
    _bull_bear_valuation_divergence_text,
    _compute_metric_cagr,
    _cv,
    _evidence_conclusion_block,
    _get_dim_data,
    _historical_pe_median,
    _v3_cv7_block,
    _v3_cv8_block,
    _v3_trend_stage_hints,
    _v3_valuation_percentiles,
    _fmt_v2,
    _fmt_num,
    _get_safe,
    _coalesce_fin_field,
)

logger = logging.getLogger(__name__)

_INDUSTRY_CUSTOM_UNKNOWN_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("半导体", "芯片", "集成电路"),
        "国产化率/出口管制：公司核心设备/材料/工艺环节在国内的国产化率与替代进度如何？",
        "半导体产业链高度依赖进口设备与关键材料，出口管制升级或国产替代加速均可能显著改变公司中期收入/成本结构，且这类信息通常无法从财报直接获得，需产业链调研或公司公告补充。",
    ),
    (
        ("医药", "生物", "制药", "医疗"),
        "集采/医保政策传导：主力产品是否在近期或未来集采/医保谈判目录范围内，降价幅度预期如何？",
        "医药类标的的营收/毛利率对集采及医保谈判政策高度敏感，且政策落地时间与幅度具有不确定性，属于财报无法前瞻反映的关键变量。",
    ),
    (
        ("新能源", "锂电", "光伏", "储能"),
        "行业产能周期位置：当前细分赛道处于产能扩张/出清的哪个阶段，价格战是否已充分反映在近期报表中？",
        "新能源产业链历史上多次出现产能过剩导致价格快速下探，财报滞后于产能/价格变化，需结合行业排产数据交叉验证。",
    ),
)



# --- _v3_build_risk_report ---
def _v3_build_risk_report(
    collection: dict, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> dict[str, Any]:
    """汇总 risk_report 入参（模块 5/7 共用）。"""
    from lib.risk_scanner import risk_report

    fin = _get_dim_data(dims, "financials")
    fin_list = fin if isinstance(fin, list) else []
    pe_pct, _, _ = _v3_valuation_percentiles(dims, val_cache)
    val_payload: dict[str, Any] = {}
    if pe_pct is not None:
        val_payload["pe_percentile"] = pe_pct
        val_payload["pe"] = {"pct": pe_pct}
    industry_peers = collection.get("industry_peers") or {}
    peers = industry_peers.get("peers") or []
    debt_vals = [
        _safe_num(p.get("debt_to_assets"))
        for p in peers
        if _safe_num(p.get("debt_to_assets")) is not None
    ]
    industry_median_debt: float | None = None
    if debt_vals:
        from lib.valuation import median_of
        industry_median_debt = median_of([float(x) for x in debt_vals])
    kline = _get_dim_data(dims, "kline")
    return risk_report(
        fin_list,
        industry_peers=industry_peers,
        valuation=val_payload or None,
        northbound=market_structure.get("northbound"),
        kline=kline if isinstance(kline, list) else None,
        industry_median_debt=industry_median_debt,
    )


# --- _v3_bull_bear_implied_growth ---
def _v3_bull_bear_implied_growth(
    dims: dict[str, dict], market_structure: dict,
) -> tuple[dict[str, Any], float | None, float | None]:
    """复用 D-③：当前 PE + implied_growth + 实际 CAGR。"""
    val_data = _get_dim_data(dims, "valuation")
    current_pe: float | None = None
    if val_data and isinstance(val_data, list):
        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted if r.get("pe_ttm") is not None]
        if pe_seq:
            current_pe = float(pe_seq[-1])
    ig: dict[str, Any] = {}
    if current_pe is not None and current_pe > 0:
        erp_data = market_structure.get("erp") or {}
        risk_free_raw = erp_data.get("dgs10")
        risk_free = 0.025 if risk_free_raw is None else risk_free_raw / 100.0
        from lib.valuation import implied_growth
        ig = implied_growth(current_pe, risk_free, erp=0.06)
    fin = _get_dim_data(dims, "financials")
    cagr, np_cagr = None, None
    if fin and isinstance(fin, list):
        fin_list = sort_kline_asc(fin)
        cagr, _ = _compute_metric_cagr(fin_list, "revenue")
        np_cagr, _ = _compute_metric_cagr(fin_list, "net_profit")
    return ig, cagr, np_cagr


# --- _section_bull_bear ---
def _section_bull_bear(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    market_structure: dict,
    risk_data: dict[str, Any],
    *,
    val_cache: dict | None = None,
) -> str:
    """模块 5：多空逻辑链、关键分歧点、预期差（LAW 15）。

    升级后的格式 v0.1.4:
    - 多头/空头链改为「假设→传导→数字」结构
    - 每链包含: 核心假设, 传导链, 对应数字(利润预测表+隐含市值)
    - 末尾增加「关键分歧点」独立章节
    """
    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)

    # LAW 17: 构建含数据的标题 + 段首主旨句
    pe_s = f"PE {pe_pct:.1f}% 分位" if pe_pct is not None else ""
    title_suffix = f"Bull/Bear 多空逻辑链 · {pe_s}" if pe_s else "Bull/Bear 多空逻辑链与情景估值"
    judgment = f"当前 {pe_s}，以下为 Bull/Bear 对称辩论与多情景估值参考。" if pe_s else "以下为 Bull/Bear 多空逻辑链与情景估值分析。"

    lines = [f"## 5. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    sw = market_structure.get("sw_index") or {}
    nb = market_structure.get("northbound") or {}
    industry_peers = collection.get("industry_peers") or {}
    rankings = industry_peers.get("rankings") or {}
    target = industry_peers.get("target") or {}
    fin = _get_dim_data(dims, "financials")
    latest_fin: dict = {}
    if fin and isinstance(fin, list):
        latest_fin = sort_kline_asc(fin)[-1]

    # ── gather raw data for chains ──────────────────────────────────
    nb_v = _safe_num(nb.get("net_sum_10d"))
    mf_net, mf_key = resolve_moneyflow(market_structure.get("moneyflow"))
    roe = _safe_num(latest_fin.get("roe") or target.get("roe"))
    roe_rank_pct = rankings.get("roe_pct")
    rev_yoy_pct = rankings.get("revenue_yoy_pct")
    svi = sw.get("stock_vs_industry_pct")
    erp_data = market_structure.get("erp") or {}
    erp_pct = erp_data.get("percentile_5y")
    ocf = _safe_num(latest_fin.get("ocf") or latest_fin.get("n_cashflow_act"))
    np_v = _safe_num(latest_fin.get("net_profit"))
    ig, cagr, np_cagr = _v3_bull_bear_implied_growth(dims, market_structure)
    ref_cagr = cagr if cagr is not None else np_cagr
    ref_label = "营收 CAGR" if cagr is not None else ("净利润 CAGR" if np_cagr is not None else None)
    rev_yoy = target.get("revenue_yoy")
    latest_pe = ig.get("pe")

    # collected triggered risk signals
    risk_bear_signals: list[dict] = []
    risk_bull_signal: dict | None = None
    for sig in risk_data.get("signals") or []:
        if not sig.get("triggered"):
            continue
        sev = sig.get("severity") or ""
        if sev in ("高", "中"):
            risk_bear_signals.append(sig)
        elif sev == "参考" and sig.get("id") == "valuation_extreme_low":
            risk_bull_signal = sig

    # ── build bull chains (假设→传导→数字) ─────────────────────────
    bull_chains: list[dict] = []

    # Bull chain 1: 估值偏低链
    if pe_pct is not None and pe_pct < ZONE_LOW_THRESHOLD:
        chain: dict = {
            "title": "估值偏低 — 均值回归潜力",
            "assumption": (
                f"当前 PE 处于历史 {pe_zone or '偏低区'}（{pe_pct:.1f}% 分位），"
                f"低于历史上大多数时期的估值中枢。"
            ),
            "transmission": (
                "低估值分位 → 市场对该标的情绪悲观，定价已计入较多负面预期 → "
                "若基本面不发生实质性恶化，PE 存在向历史中位数回归的动力 → "
                "估值修复将推动股价上升。"
            ),
            "numbers": [],
            "strength": "⚠️ 中",
        }
        if latest_pe is not None:
            chain["numbers"].append(f"- 当前 PE: {latest_pe:.1f}x")
        chain["strength"] = "✅ 强" if (pe_pct is not None and pe_pct < 10) else "⚠️ 中"
        # implied market cap — PE 中位数来自 valuation 历史序列
        if np_v is not None and np_v > 0 and latest_pe is not None:
            current_mc = np_v * latest_pe
            median_pe = _historical_pe_median(val_cache, dims)
            chain["numbers"].append(
                f"- 以净利润 {_fmt_v2(np_v)} × 当前 PE {latest_pe:.1f}x = 隐含市值 "
                f"{_fmt_v2(current_mc)}"
            )
            if median_pe is not None and median_pe > 0:
                implied_mc = np_v * median_pe
                chain["numbers"].append(
                    f"- 若 PE 修复至历史中位数 {median_pe:.1f}x（来源: valuation 维度），"
                    f"对应市值约 {_fmt_v2(implied_mc)}"
                )
            else:
                chain["numbers"].append(
                    "- PE 历史中位数不可得，未生成修复场景估算 [来源: valuation 维度]"
                )
        bull_chains.append(chain)

    # Bull chain 2: Extremely low valuation signal (reverse risk)
    if risk_bull_signal is not None:
        chain = {
            "title": "极端低估参考信号",
            "assumption": f"{risk_bull_signal.get('detail', '估值处于极端低位')}",
            "transmission": (
                "极端低估信号触发 → 历史上类似阶段曾出现估值修复窗口 "
                "[推测，待验证：样本案例与胜率待补] → 可关注估值修复机会。"
            ),
            "numbers": [f"- 信号来源: risk_scanner / {risk_bull_signal.get('category', 'market')}"],
            "strength": "⚠️ 中",
        }
        bull_chains.append(chain)

    # Bull chain 3: 资金流入链
    if nb_v is not None and nb_v > 0:
        chain = {
            "title": "北向资金持续流入",
            "assumption": (
                f"北向资金近 10 个交易日净流入 {_fmt_v2(nb_v)}，"
                f"外资对该标的存在配置意愿。"
            ),
            "transmission": (
                "北向资金净流入 → 外资看多信号 → 增量资金入场推升需求 → "
                "短期量价配合，有利于股价表现。"
            ),
            "numbers": [f"- 近 10 日北向净流入: {_fmt_v2(nb_v)}"],
            "strength": "⚠️ 中",
        }
        if latest_pe is not None and np_v is not None and np_v > 0:
            chain["numbers"].append(
                f"- 当前 PE {latest_pe:.1f}x，净利润 {_fmt_v2(np_v)}，"
                f"资金流入行为可能加速估值回归"
            )
        bull_chains.append(chain)

    # Bull chain 4: 盈利质量链
    fund_quality = roe is not None and roe >= 18
    peer_roe = roe_rank_pct is not None and roe_rank_pct >= 60
    peer_rev = rev_yoy_pct is not None and rev_yoy_pct >= 60
    cf_quality = ocf is not None and np_v is not None and np_v > 0 and (ocf / np_v) >= 0.6
    if fund_quality or peer_roe or peer_rev or cf_quality:
        quality_items = []
        if roe is not None and roe >= 18:
            quality_items.append(f"ROE {roe:.1f}%")
        if roe_rank_pct is not None and roe_rank_pct >= 60:
            quality_items.append(f"ROE 同行分位 {roe_rank_pct:.1f}%")
        if rev_yoy_pct is not None and rev_yoy_pct >= 60:
            quality_items.append(f"营收增速同行分位 {rev_yoy_pct:.1f}%")
        if cf_quality:
            quality_items.append(f"经营现金流/净利润 = {ocf / np_v:.2f}")
        chain = {
            "title": "基本面质量偏优",
            "assumption": f"财务数据显示盈利能力较强：{'；'.join(quality_items)}。",
            "transmission": (
                "高 ROE / 同行领先 → 企业具有竞争优势或良好管理层治理 → "
                "盈利持续性强 → 市场应对其给予估值溢价 → "
                "支撑当前股价甚至推动上行。"
            ),
            "numbers": [],
            "strength": "✅ 强" if (roe is not None and roe >= 22) else "⚠️ 中",
        }
        if np_v is not None and np_v > 0:
            chain["numbers"].append(f"- 当期净利润: {_fmt_v2(np_v)}")
        if roe is not None:
            chain["numbers"].append(f"- ROE: {roe:.1f}%（≥18% 视为高质量门槛）")
        bull_chains.append(chain)

    # Bull chain 5: 技术动量链
    if svi is not None and svi > 0:
        chain = {
            "title": "个股相对强势",
            "assumption": f"个股近 20 个交易日跑赢其行业指数 {svi:+.2f}%，体现短期相对强势。",
            "transmission": (
                "跑赢行业 → 资金主动配置该标的而非行业 β → "
                "相对动量可能延续 → 短期趋势有利于多头。"
            ),
            "numbers": [f"- 近 20 日相对行业超额收益: {svi:+.2f}%"],
            "strength": "⚠️ 中",
        }
        bull_chains.append(chain)

    # Bull chain 6: 宏观支持链
    if erp_pct is not None and erp_pct >= 70:
        chain = {
            "title": "ERP 处于高位，权益风险溢价补偿丰厚",
            "assumption": f"ERP 5 年分位 {erp_pct:.1f}%，股权风险溢价处于历史偏高水平。",
            "transmission": (
                "ERP 高位 → 股票相对债券的性价比突出 → "
                "长期资金可能增加权益配置 → 宏观环境利好权益资产。"
            ),
            "numbers": [f"- ERP 5 年分位: {erp_pct:.1f}%"],
            "strength": "⚠️ 中",
        }
        bull_chains.append(chain)

    # ── build bear chains ───────────────────────────────────────────
    bear_chains: list[dict] = []

    # Bear chain 1: 估值偏高链
    if pe_pct is not None and pe_pct > ZONE_HIGH_THRESHOLD:
        chain = {
            "title": "估值偏高 — 均值回归风险",
            "assumption": (
                f"当前 PE 处于历史 {pe_zone or '偏高区'}（{pe_pct:.1f}% 分位），"
                f"高于大多数历史时期的估值水平。"
            ),
            "transmission": (
                "高估值分位 → 市场对该标的预期已较为充分 → "
                "一旦基本面不及预期，估值和盈利面临「双杀」 → "
                "PE 向历史中枢回归将导致股价下行。"
            ),
            "numbers": [],
            "strength": "✅ 强" if (pe_pct is not None and pe_pct > 90) else "⚠️ 中",
        }
        if latest_pe is not None and np_v is not None and np_v > 0:
            current_mc = np_v * latest_pe
            median_pe = _historical_pe_median(val_cache, dims)
            chain["numbers"].append(
                f"- 当前 PE: {latest_pe:.1f}x；以净利润 {_fmt_v2(np_v)} 计，隐含市值 "
                f"{_fmt_v2(current_mc)}"
            )
            if median_pe is not None and median_pe > 0 and median_pe < latest_pe:
                implied_mc = np_v * median_pe
                chain["numbers"].append(
                    f"- 若 PE 回落至历史中位数 {median_pe:.1f}x（来源: valuation 维度），"
                    f"市值约 {_fmt_v2(implied_mc)}"
                )
            elif median_pe is None:
                chain["numbers"].append(
                    "- PE 历史中位数不可得，未生成回落场景估算 [来源: valuation 维度]"
                )
        bear_chains.append(chain)

    # Bear chain 2: 资金流出链
    if nb_v is not None and nb_v < -500_000_000:
        chain = {
            "title": "北向资金大幅流出（超 5 亿阈值）",
            "assumption": (
                f"北向资金近 10 个交易日净流出 {_fmt_v2(nb_v)}，"
                f"超过 5 亿元预警阈值。"
            ),
            "transmission": (
                "北向大幅流出 → 外资主动减仓 → 抛压增加 → "
                "短期资金面恶化，压制股价表现。"
            ),
            "numbers": [f"- 近 10 日北向净流出: {_fmt_v2(nb_v)}（阈值 5 亿）"],
            "strength": "⚠️ 中",
        }
        bear_chains.append(chain)

    # Bear chain 3: 盈利弱链
    if roe is not None and roe < 10:
        chain = {
            "title": "ROE 偏低",
            "assumption": f"最近 ROE 为 {roe:.1f}%，低于 10% 的盈利效率门槛。",
            "transmission": (
                "低 ROE → 资本回报效率不足 → 企业内生增长动力有限 → "
                "市场对其给予估值折价 → 压制股价。"
            ),
            "numbers": [f"- ROE: {roe:.1f}%（<10% 视为偏低）"],
            "strength": "⚠️ 中",
        }
        if np_v is not None and np_v > 0:
            chain["numbers"].append(f"- 当期净利润: {_fmt_v2(np_v)}")
        bear_chains.append(chain)

    # Bear chain 4: 现金流质量弱
    if ocf is not None and np_v is not None and np_v > 0 and (ocf / np_v) < 0.6:
        chain = {
            "title": "经营现金流未能覆盖净利润",
            "assumption": (
                f"经营现金流/净利润 = {ocf / np_v:.2f}，低于 0.6 的及格线，"
                f"利润含金量偏低。"
            ),
            "transmission": (
                "利润与现金流不匹配 → 盈利可能依赖应收账款或非现金项目 → "
                "现金流紧张增加运营风险 → 市场调整盈利质量预期 → 估值受压。"
            ),
            "numbers": [
                f"- OCF/NP 比率: {ocf / np_v:.2f}",
                f"- 经营现金流: {_fmt_v2(ocf)} vs 净利润: {_fmt_v2(np_v)}",
            ],
            "strength": "⚠️ 中",
        }
        bear_chains.append(chain)

    # Bear chain 5: 技术弱链
    if svi is not None and svi < 0:
        chain = {
            "title": "个股相对弱势",
            "assumption": f"个股近 20 个交易日跑输其行业指数 {svi:+.2f}%。",
            "transmission": (
                "跑输行业 → 资金对该标的出现避险行为 → "
                "相对弱势可能延续 → 短期趋势对多头不利。"
            ),
            "numbers": [f"- 近 20 日相对行业超额收益: {svi:+.2f}%"],
            "strength": "⚠️ 中",
        }
        bear_chains.append(chain)

    # Bear chain 6: risk signals
    for sig in risk_bear_signals:
        chain = {
            "title": f"风险信号: {sig['name']}",
            "assumption": sig.get("detail", "触发风险监测信号。"),
            "transmission": (
                f"「{sig.get('category', '')}」类别风险触发 → "
                f"影响企业的 {sig.get('name', '相关')} 方面 → "
                f"若持续或加剧，市场可能下调盈利预期和估值倍数 → 股价承压。"
            ),
            "numbers": [f"- 严重程度: {sig.get('severity', '')} 级"],
            "strength": "✅ 强" if sig.get("severity") == "高" else "⚠️ 中",
        }
        bear_chains.append(chain)

    # ── bear chain padding (F-2): 若空头论据显著少于多头，用可复用数据构建 ──
    # 通用空方模板补齐差额；模板本身也可能因数据不足被跳过，绝不为凑数量编造
    # 无依据的论点（AGENTS.md 约束 3）。
    _bear_titles = {c["title"] for c in bear_chains}

    def _try_add_valuation_neutral_bear() -> bool:
        if "估值偏高 — 均值回归风险" in _bear_titles:
            return False  # 已存在对称的估值偏高链，无需重复
        if pe_pct is None or pe_pct < ZONE_LOW_THRESHOLD:
            return False  # PE 分位本身处于低位，构造"估值不低"论点将自相矛盾
        chain = {
            "title": "估值未处于低位 — 修复安全边际有限",
            "assumption": (
                f"当前 PE 处于历史 {pe_zone or '中性偏高区'}（{pe_pct:.1f}% 分位），"
                f"并非历史低位，估值端不具备低估安全边际。"
            ),
            "transmission": (
                "估值分位不低 → 市场已给予该标的中性以上定价 → "
                "若基本面出现边际走弱或不及预期 → 估值缺乏低位缓冲，"
                "股价对负面消息的敏感度更高。"
            ),
            "numbers": [f"- 当前 PE 分位: {pe_pct:.1f}%（{pe_zone or '中性偏高区'}）[来源: valuation 维度]"],
            "strength": "❓ 弱",
        }
        bear_chains.append(chain)
        _bear_titles.add(chain["title"])
        return True

    def _try_add_industry_competition_bear() -> bool:
        title = "行业竞争格局变化"
        if title in _bear_titles:
            return False
        if not industry_peers.get("sufficient"):
            return False
        items = []
        if roe_rank_pct is not None and roe_rank_pct < 50:
            items.append(f"ROE 同行分位 {roe_rank_pct:.1f}%（低于同行中位）")
        if rev_yoy_pct is not None and rev_yoy_pct < 50:
            items.append(f"营收增速同行分位 {rev_yoy_pct:.1f}%（低于同行中位）")
        if not items:
            return False  # 同行数据显示公司相对占优，不构造矛盾论点
        chain = {
            "title": title,
            "assumption": (
                f"同行对比数据显示：{'；'.join(items)}，公司在行业内的相对位置并不领先"
                f" [来源: industry_peers 维度]。"
            ),
            "transmission": (
                "同行排名靠后 → 议价能力/抗风险能力相对偏弱 → "
                "若行业竞争加剧或格局生变，公司份额或毛利率可能率先承压 → "
                "盈利能见度下降，市场可能下调估值倍数。"
            ),
            "numbers": [f"- {it}" for it in items],
            "strength": "⚠️ 中",
        }
        bear_chains.append(chain)
        _bear_titles.add(title)
        return True

    for _pad_tpl in (_try_add_valuation_neutral_bear, _try_add_industry_competition_bear):
        if len(bear_chains) >= len(bull_chains) - 1:
            break
        _pad_tpl()

    _bear_shortfall_note = ""
    if len(bear_chains) < len(bull_chains) - 1:
        _bear_shortfall_note = (
            "⚠️ 当前数据支持的空头论据数量少于多头，这是数据可得性限制导致的结构性不对称，"
            "并非模型对该标的方向性看多的结论；补充空头论据所需的同行/估值数据暂不可得。"
        )

    # ── 5a. Bull chain: 假设→传导→数字 ────────────────────────────
    lines.append("### 5a. 多头逻辑链")
    if bull_chains:
        for idx, bc in enumerate(bull_chains, 1):
            lines.append(f"#### 多头逻辑 {idx}: {bc['title']}")
            lines.append(f"- **核心假设**: {bc['assumption']}")
            lines.append(f"- **传导链**: {bc['transmission']}")
            lines.append("**对应数字**:")
            if bc["numbers"]:
                lines.extend(bc["numbers"])
            else:
                lines.append("  - 数据不足，未生成量化估算")
            lines.append(f"- 证据强度: {bc['strength']}")
            lines.append("")
    else:
        lines.append("- 当前数据未形成明确多头逻辑链 [来源: 模块 2/4/6 汇总]")
        lines.append("")

    # ── 5b. Bear chain: 假设→传导→数字 ────────────────────────────
    lines.append("### 5b. 空头逻辑链")
    if bear_chains:
        for idx, bc in enumerate(bear_chains, 1):
            lines.append(f"#### 空头逻辑 {idx}: {bc['title']}")
            lines.append(f"- **核心假设**: {bc['assumption']}")
            lines.append(f"- **传导链**: {bc['transmission']}")
            lines.append("**对应数字**:")
            if bc["numbers"]:
                lines.extend(bc["numbers"])
            else:
                lines.append("  - 数据不足，未生成量化估算")
            lines.append(f"- 证据强度: {bc['strength']}")
            lines.append("")
    else:
        lines.append("- 当前数据未形成明确空头逻辑链 [来源: 模块 2/4/6 + risk_scanner]")
        lines.append("")

    # ── 5c. 关键分歧点 ──────────────────────────────────────────────
    lines.append("### 5c. 关键分歧点")
    lines.append("双方争议最大的两个变量：")
    divergence_count = 0
    # divergence: PE historical position vs revenue growth
    if pe_pct is not None and rev_yoy is not None:
        divergence_count += 1
        lines.append(
            f"{divergence_count}. **[估值 vs 盈利]**："
            f"{_bull_bear_valuation_divergence_text(pe_pct, pe_zone, float(rev_yoy))}"
        )
    # divergence: implied growth vs actual CAGR
    if ig.get("g_implied") is not None and ref_cagr is not None and ref_label:
        divergence_count += 1
        g_pct = ig["g_implied"] * 100
        direction_bull = "低估" if g_pct > ref_cagr else "合理"
        direction_bear = "透支" if g_pct > ref_cagr else "悲观"
        lines.append(
            f"{divergence_count}. **[隐含增长 vs 实际增长]**：Bull 认为 g_implied "
            f"({g_pct:.2f}%) {direction_bull}，未来增长可期；Bear 认为 "
            f"实际{ref_label} {ref_cagr:+.2f}% 无法匹配，定价{direction_bear}。"
        )
    # divergence: northbound vs moneyflow (if we haven't hit 2)
    m_v = mf_net
    if divergence_count < 2 and nb_v is not None and m_v is not None and nb_v * m_v < 0:
        divergence_count += 1
        bull_direction = "净流入" if nb_v > 0 else "净流出"
        bear_direction = "净流入" if m_v > 0 else "净流出"
        lines.append(
            f"{divergence_count}. **[资金流向背离]**：Bull 关注北向 {_fmt_v2(nb_v)} "
            f"（{bull_direction}），认为外资流入是正面信号；Bear 关注主力 "
            f"{_fmt_v2(m_v)}（{bear_direction}），认为内资撤离是预警。"
        )
    if divergence_count == 0:
        lines.append("1. 关键变量数据不足，暂无法提炼定量分歧点 [来源: 多维度缺口]")
    lines.append("")

    # ── 5d. 预期差（LAW 15） — unchanged ──────────────────────────
    lines.append("### 5d. 预期差（LAW 15）")
    if ig.get("g_implied") is not None:
        g_pct = ig["g_implied"] * 100
        lines.append(
            f"- 市场隐含增长率 g_implied ≈ **{g_pct:.2f}%**（PE {ig.get('pe')}x，"
            f"r={ig.get('r', 0) * 100:.2f}%）[来源: lib.valuation.implied_growth / 模块 4 D-③]"
        )
        if ref_cagr is not None and ref_label:
            gap = g_pct - ref_cagr
            if abs(gap) > 5:
                direction = "偏乐观" if gap > 0 else "偏悲观"
                lines.append(
                    f"- 与实际{ref_label}（{ref_cagr:+.2f}%）差距 {gap:+.2f}pp，定价{direction}"
                    f" [来源: financials CAGR vs D-③]"
                )
            else:
                lines.append(
                    f"- 与实际{ref_label}（{ref_cagr:+.2f}%）接近，定价大致反映历史增长"
                    f" [来源: financials CAGR vs D-③]"
                )
        else:
            lines.append("- 实际 CAGR 不可得，仅呈现 g_implied 供与模块 4 D-③ 对照 [来源: financials 缺口]")
        if ig.get("warning"):
            lines.append(f"- ⚠️ {ig['warning']}")
    else:
        lines.append("- PE 或国债收益率不可得，预期差计算跳过（详见模块 4 D-③） [来源: valuation/erp 缺口]")
    if pb_pct is not None and pe_pct is not None:
        if (pe_pct >= 70 and pb_pct < 50) or (pe_pct <= 30 and pb_pct >= 50):
            lines.append("")
            lines.append(
                _cv(
                    "divergence", "CV-6", "PE 分位 vs PB 分位（分歧视角）",
                    f"PE 分位 {pe_pct:.1f}% 与 PB 分位 {pb_pct:.1f}% 方向不一致",
                    "中",
                )
            )
    lines.append("")
    if _bear_shortfall_note:
        lines.append(_bear_shortfall_note)
        lines.append("")
    lines.append("🔍 **待独立验证:** 逻辑链为数据驱动的叙事框架，非方向判断；预期差须与财报 PDF 交叉核对。")
    return "\n".join(lines)


# --- _generate_custom_unknowns ---
def _generate_custom_unknowns(collection: dict, dims: dict[str, dict]) -> list[tuple[str, str]]:
    """v0.1.8 A-3: 根据标的行业/估值历史位置/内部人行为特征生成定制化待验证问题。

    返回 `(问题, 为什么重要)` 元组列表；规则命中的数据字段不可得时跳过该规则，
    不编造问题所需的数据支撑（AGENTS.md 约束 3）。
    """
    result: list[tuple[str, str]] = []
    if not isinstance(dims, dict):
        return result

    # 规则 1: 行业关键词匹配
    basic = _get_dim_data(dims, "basic_info")
    industry_name = ""
    if isinstance(basic, dict):
        industry_name = str(basic.get("industry", "") or basic.get("行业", "") or "")
    if industry_name:
        for keywords, question, why in _INDUSTRY_CUSTOM_UNKNOWN_RULES:
            if any(kw in industry_name for kw in keywords):
                result.append((question, why))
                break  # 仅取首个匹配的行业规则，避免同类问题堆叠

    # 规则 2: PE 历史位置极端值
    try:
        pe_pct, _pb_pct, _zone = _v3_valuation_percentiles(dims, None)
    except Exception:
        pe_pct = None
    if pe_pct is not None:
        if pe_pct > 90:
            result.append((
                f"当前估值隐含增速能否兑现：PE 历史位置已达 {pe_pct:.1f}%（>90%），"
                "市场定价隐含的增长预期是否有具体订单/产能落地依据支撑？",
                "历史高位定价对增长不及预期的敏感度更高，需要独立验证市场隐含增速的合理性，"
                "而非仅依赖历史位置数字本身下结论。",
            ))
        elif pe_pct < 10:
            result.append((
                f"低估值历史位置是否反映真实基本面恶化：PE 历史位置仅 {pe_pct:.1f}%（<10%），"
                "是短期情绪压制还是盈利模式已发生结构性变化？",
                "极低历史位置可能对应周期底部或基本面持续恶化两种截然不同的情形，"
                "仅靠估值位置无法区分，需结合订单/现金流等一手信息独立核实。",
            ))

    # 规则 3: 内部人一致性信号极端值
    holder_changes = dims.get("holder_changes") or {}
    try:
        from lib.scoring import insider_signal

        signal = insider_signal(holder_changes)
    except Exception:
        signal = "数据不足"
    if signal == "强负向":
        result.append((
            "内部人集中减持后的资金用途与后续动向：近 12 月已披露的多主体减持是否有后续增持/回购计划？",
            "多主体同向减持是行为事实但动机不明确（可能是个人资金需求，也可能反映对公司前景的判断），"
            "需要结合后续公告与管理层表态独立验证，避免单凭减持行为得出方向性结论。",
        ))

    return result


# --- _section_risk_uncertainty ---
def _section_risk_uncertainty(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    market_structure: dict,
    risk_data: dict[str, Any],
) -> str:
    """模块 7：三层结构风险信号表 + Known Unknowns。

    三层结构：
      1. 报表风险（Financial Statement）— category = "financial"
      2. 商业风险（Business / Operational）— category = "business"
      3. 市场风险（Market / Technical）— category = "market"

    每条风险带触发条件（detail）、严重度、时间窗口。
    输出中禁止出现"崩溃"和"崩盘"。
    """
    cat_titles = {
        "financial": "### 报表风险（Financial Statement）",
        "business": "### 商业风险（Business / Operational）",
        "market": "### 市场风险（Market / Technical）",
    }
    status_labels = {
        "triggered": "已触发",
        "clear": "未触发",
        "insufficient_data": "数据不足",
        "pending_agent": "待 Agent",
    }
    # 根据严重度推导时间窗口参考
    _TIME_WINDOW_MAP = {"高": "1-3 个月", "中": "3-6 个月", "低": "6-12 个月", "参考": "视条件触发"}

    # LAW 17: 构建含风险统计数据的标题
    risk_signals_n = risk_data.get("coverage", {}).get("auto", 0) if isinstance(risk_data, dict) else 0
    triggered_n = risk_data.get("triggered_count", 0) if isinstance(risk_data, dict) else 0
    title_suffix = f"触发 {triggered_n}/{risk_signals_n} 项风险信号" if risk_signals_n else "风险与不确定性"
    judgment = f"自动判定覆盖 {risk_signals_n}/17 信号，当前触发 {triggered_n} 项，详见下方三层风险结构。" if risk_signals_n else "以下为三层风险信号与已知未知分析。"

    lines = [f"## 7. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    coverage = risk_data.get("coverage") or {}
    auto_n = coverage.get("auto", 0)
    lines.append(
        f"自动判定覆盖：**{auto_n}/17** 信号；"
        f"当前触发 **{risk_data.get('triggered_count', 0)}** 项。"
    )
    lines.append("")

    # 将信号按 category 分组为三层
    categories_order = ["financial", "business", "market"]
    grouped: dict[str, list[dict]] = {c: [] for c in categories_order}
    for sig in risk_data.get("signals") or []:
        cat = sig.get("category", "")
        if cat in grouped:
            grouped[cat].append(sig)
        else:
            # fallback — unknown category in a catch-all bucket
            grouped.setdefault("other", []).append(sig)

    for cat in categories_order:
        sigs = grouped.get(cat, [])
        if not sigs:
            continue
        lines.append(cat_titles[cat])
        lines.append("")
        lines.append("| 信号 | 状态 | 严重度 | 时间窗口 | 说明 |")
        lines.append("|------|------|--------|---------|------|")
        for sig in sigs:
            name = str(sig.get("name", "?"))
            raw_status = sig.get("status", "")
            status = status_labels.get(raw_status, raw_status)
            sev_raw = sig.get("severity")
            sev = str(sev_raw) if sev_raw else "—"
            triggered = sig.get("triggered", False)
            raw_detail = str(sig.get("detail", "")).replace("|", "/")

            # 状态图标：已触发用 ⚠️ 标注（非 pass/fail 语义）
            if raw_status == "triggered":
                status_display = f"⚠️ {status}"
            else:
                status_display = status

            # 时间窗口
            tw = _TIME_WINDOW_MAP.get(sev, "—") if triggered else "—"

            # 说明列：触发时展示 detail，否则 "—"
            detail_display = raw_detail if triggered or raw_status in ("insufficient_data", "pending_agent") else "—"

            lines.append(f"| {name} | {status_display} | {sev} | {tw} | {detail_display} |")
        lines.append("")

    # 处理 "other" category（如有）
    other_sigs = grouped.get("other", [])
    if other_sigs:
        lines.append("### 其他风险信号")
        lines.append("")
        lines.append("| 信号 | 状态 | 严重度 | 时间窗口 | 说明 |")
        lines.append("|------|------|--------|---------|------|")
        for sig in other_sigs:
            name = str(sig.get("name", "?"))
            raw_status = sig.get("status", "")
            status = status_labels.get(raw_status, raw_status)
            sev = str(sig.get("severity", "")) or "—"
            triggered = sig.get("triggered", False)
            raw_detail = str(sig.get("detail", "")).replace("|", "/")
            status_display = f"⚠️ {status}" if raw_status == "triggered" else status
            tw = _TIME_WINDOW_MAP.get(sev, "—") if triggered else "—"
            detail_display = raw_detail if triggered or raw_status in ("insufficient_data", "pending_agent") else "—"
            lines.append(f"| {name} | {status_display} | {sev} | {tw} | {detail_display} |")
        lines.append("")

    lines.append("### Known Unknowns（已知未知项）")
    lines.append("")
    lines.append("已知未知项（当前全市场在此处处于「盲飞」状态）：")
    _KNOWN_UNKNOWN_SLOTS = [
        ("订单可见度", "当前无公开订单披露，依赖产业链调研"),
        ("技术路线时间表", "关键技术验证节点时间窗口待独立核实"),
        ("政策/贸易变量", "相关政策的不确定时间窗口"),
    ]
    slot_idx = 0
    for slot_idx, (slot_name, default_hint) in enumerate(_KNOWN_UNKNOWN_SLOTS, 1):
        lines.append(f"{slot_idx}. **{slot_name}：** {default_hint}")

    # ---- v0.1.8 A-3: 标的定制化待验证问题 ----
    custom_unknowns = _generate_custom_unknowns(collection, dims)
    for question, why_it_matters in custom_unknowns:
        slot_idx += 1
        lines.append(f"{slot_idx}. **{question}** — {why_it_matters}")

    scanner_unknowns = risk_data.get("known_unknowns") or []
    if scanner_unknowns:
        slot_idx += 1
        lines.append(f"{slot_idx}. **扫描器补充：** " + "；".join(scanner_unknowns[:3]))

    # Governance events cross-reference
    events_all = collection.get("events") or []
    if events_all and isinstance(events_all, list):
        gov_types = {"litigation", "st_risk"}
        gov_events = [
            e for e in events_all
            if str(e.get("type", "")).lower() in gov_types
        ]
        if gov_events:
            gov_lines = []
            for ge in gov_events[:5]:
                gdate = str(ge.get("date", ""))
                gtitle = str(ge.get("title", ""))
                if len(gtitle) > 60:
                    gtitle = gtitle[:57] + "..."
                gov_lines.append(f"{gdate} {gtitle}")
            if gov_lines:
                slot_idx += 1
                lines.append(f"{slot_idx}. **近期治理事件:** {'；'.join(gov_lines)}")

    lines.append("")
    lines.append(
        _evidence_conclusion_block(
            f"{symbol} 风险扫描呈现报表/商业/市场三层定量信号",
            [
                ("✅" if auto_n >= 15 else "⚠️", f"自动判定 {auto_n}/17 项"),
                (
                    "⚠️" if risk_data.get("triggered_count", 0) > 0 else "✅",
                    f"触发 {risk_data.get('triggered_count', 0)} 项定量风险信号",
                ),
            ],
        )
    )
    lines.append("")
    lines.append("🔍 **待独立验证:** 商业类与客户集中度等信号需结合年报附注与 WebSearch 定性补充。")

    # 禁令检查：确保输出中不含"崩溃"或"崩盘"
    output = "\n".join(lines)
    for banned_word in ("崩溃", "崩盘"):
        if banned_word in output:
            output = output.replace(banned_word, "**违规词**")
    return output


# --- _section_left_right_probability ---
def _section_left_right_probability(
    collection: dict, symbol: str, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> str:
    # LAW 17: 构建含数据的标题
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    pe_s = f"PE {pe_pct:.1f}% 分位" if pe_pct is not None else ""
    title_suffix = f"左/右概率判断 · {pe_s}" if pe_s else "左侧/右侧概率判断"
    judgment = f"基于 {pe_s} 的综合位置评估，左/右概率见下方分析。" if pe_s else "左侧/右侧概率的综合评估，详见下方。"

    lines = [f"## 6. {title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    lines.append("### 当前趋势位置（描述性参考，非单一结论）")
    kline = _get_dim_data(dims, "kline")
    trend_label = ""
    if kline and isinstance(kline, list):
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            trend_label = tech["trend"]["alignment"].get("trend_label", "")
            lines.append(f"- **技术结构:** {trend_label}")
    lines.append("- **阶段对照（均未选定，仅供概率权重参考）:**")
    lines.append(f"  {_v3_trend_stage_hints(trend_label)}")
    lines.append("")
    lines.append("### 左侧概率的主要支撑依据")
    left_items: list[str] = []
    if pe_pct is not None and pe_pct < ZONE_LOW_THRESHOLD:
        left_items.append(f"① PE 历史位置偏低（{pe_pct:.1f}%），证据强度：⚠️")
    erp = market_structure.get("erp")
    if erp and erp.get("percentile_5y") is not None and erp["percentile_5y"] >= 70:
        left_items.append(f"② ERP 5年区间位置偏高（{erp['percentile_5y']}%），证据强度：⚠️")
    if not left_items:
        left_items.append("① 左侧参考指标数据不足或未达到阈值，证据强度：❓")
    lines.append("")
    lines.append("### 右侧概率的主要支撑依据")
    right_items: list[str] = []
    if kline and isinstance(kline, list):
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            label = tech["trend"]["alignment"].get("trend_label", "")
            if "多头" in label:
                right_items.append(f"① MA 多头排列（{label}），证据强度：⚠️")
            macd = tech["momentum"]["macd"]
            if macd.get("available"):
                right_items.append(f"② MACD DIF={macd.get('dif')} DEA={macd.get('dea')}，证据强度：❓")
    sw = market_structure.get("sw_index")
    if sw and sw.get("stock_vs_industry_pct") is not None and sw["stock_vs_industry_pct"] > 0:
        right_items.append(f"③ 个股跑赢行业（{sw['stock_vs_industry_pct']:+.2f}%），证据强度：⚠️")
    # P1d：右侧趋势延续信号组合（满足 ≥2/3 视为强化）
    continuation_hits: list[str] = []
    fin_lr = _get_dim_data(dims, "financials")
    if fin_lr and isinstance(fin_lr, list):
        fin_sorted = sort_kline_asc(fin_lr)
        if len(fin_sorted) >= 2:
            rev_now = _safe_num(fin_sorted[-1].get("revenue"))
            rev_prev = _safe_num(fin_sorted[-2].get("revenue"))
            if rev_now is not None and rev_prev is not None and rev_prev > 0:
                rev_yoy_lr = (rev_now - rev_prev) / rev_prev * 100
                if rev_yoy_lr > 100:
                    continuation_hits.append(f"季度营收同比 {rev_yoy_lr:+.1f}%（>100%）")
    if kline and isinstance(kline, list):
        tech_lr = compute(sort_kline_asc(kline))
        if "error" not in tech_lr:
            ma60 = tech_lr.get("trend", {}).get("ma60") or {}
            if ma60.get("slope_pct") is not None and ma60["slope_pct"] > 0:
                continuation_hits.append(f"MA60 斜率为正（{ma60['slope_pct']:+.2f}%/期）")
    mf_lr = market_structure.get("moneyflow") or {}
    nb_lr = market_structure.get("northbound") or {}
    mf10 = _safe_num(mf_lr.get("net_sum_10d") or nb_lr.get("net_sum_10d"))
    if mf10 is not None and mf10 > 0:
        continuation_hits.append(f"主力资金/北向近10日净流入 {_fmt_v2(mf10)}")
    if len(continuation_hits) >= 2:
        right_items.append(
            f"④ 趋势延续信号组合 {len(continuation_hits)}/3 项："
            + "；".join(continuation_hits) + "，证据强度：⚠️"
        )
    if not right_items:
        right_items.append("① 右侧参考指标数据不足，证据强度：❓")

    prob = ProbabilityStructure(
        left_items=left_items,
        right_items=right_items,
        trigger_conditions=[
            "| 下季财报核心指标方向变化 | 催化剂 | 1-3 个月 | 基本面叙事可能重构 |",
            "| 行业政策/竞争格局事件 | 风险事件 | 不确定 | 行业相对强弱或改变 |",
            "| 均线/MACD 结构破坏 | 技术 | 短期 | 趋势描述需更新 |",
        ],
        watch_nodes=[
            "| 下季度财报期 | 业绩公布 | 净利润同比、经营现金流 |",
        ],
    )
    lines.extend(prob.left_items)
    lines.append("")
    lines.extend(prob.right_items)
    lines.append("")
    lines.append("### 走势转变的触发条件")
    lines.append("| 触发条件 | 类型 | 时间窗口 | 影响 |")
    lines.append("|---------|------|---------|------|")
    lines.extend(prob.trigger_conditions)
    lines.append("")
    lines.append("### 下一个重要观察节点")
    lines.append("| 时间 | 事件 | 关注指标 |")
    lines.append("|------|------|---------|")
    lines.extend(prob.watch_nodes)
    mf_net, mf_key = resolve_moneyflow(market_structure.get("moneyflow"))
    pe_pct_lr, _, _ = _v3_valuation_percentiles(dims, val_cache)
    cv7_lr = _v3_cv7_block(pe_pct_lr, mf_net)
    if cv7_lr:
        lines.append("")
        lines.append("### 估值-资金交叉验证（左/右权重参考）")
        lines.append(cv7_lr)
    cv8_lr = _v3_cv8_block(
        market_structure.get("erp"),
        market_structure.get("put_call_ratio"),
        market_structure.get("short_margin"),
    )
    if cv8_lr:
        lines.append("")
        lines.append(cv8_lr)
    lines.append("")
    lines.append("🔍 **待独立验证:** 本节呈现概率结构与支持依据，不构成位置判断。")
    return "\n".join(lines)

