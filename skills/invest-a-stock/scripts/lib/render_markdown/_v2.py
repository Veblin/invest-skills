"""V2/legacy rendering sections."""
from __future__ import annotations
import logging
# Import ALL names (including _-prefixed) from _base
from . import _base as __base_ref
for __base_n in dir(__base_ref):
    if not __base_n.startswith("__"):
        globals()[__base_n] = getattr(__base_ref, __base_n)
del __base_ref, __base_n

logger = logging.getLogger(__name__)

# --- render_json ---
def render_json(collection: dict[str, Any]) -> str:
    from ..json_util import dumps_json
    return dumps_json(collection)


# --- render ---
def render(collection: dict[str, Any], symbol: str, fmt: str = "compact",
           mode: str = "full", *, attach_extras: bool = False) -> str:
    """统一渲染入口。支持 compact / json / md / html 格式。

    compact  — 紧凑文本报告（v0.1.2 八段 v2 模板）
    json     — 结构化 JSON，适合程序消费
    md       — Markdown 九模块研究备忘录（v0.1.3 render_report_v3）
    html     — HTML 研究报告（v0.1.2 冻结模板）

    mode     — "full"（完整九模块）/"brief"（精简简报）/"concise"（对话场景精简）
    attach_extras — 默认 False：纯渲染流程不发起任何网络调用。仅显式置 True 时
                    联网补采 market_structure / phase2（如 synthesize 现场补齐）。
                    联网路径任何异常（超时/挂起/权限）都快速降级：记日志 + 渲染
                    缺失块标注（renderer 对缺失数据输出"未获取到任何有效数据"），
                    绝不阻塞渲染。
    """
    if attach_extras:
        from lib import collector
        if not collection.get("market_structure"):
            try:
                collector.attach_market_structure(collection, symbol)
            except Exception as exc:
                logger.warning("attach_market_structure failed (non-fatal): %s", exc)
                collection.setdefault("_meta", {})["market_structure_error"] = str(exc)
        try:
            collector.attach_phase2_extras(collection, symbol)
        except Exception as exc:
            logger.warning("attach_phase2_extras failed (non-fatal): %s", exc)

        # Events: backfill when never attached, or [] without events_summary (failed path).
        # Standard collect→report with events_summary already ran attach_events.
        events_attached = False
        try:
            from lib.events import attach_events, needs_events_backfill
            if needs_events_backfill(collection):
                deep_mode = collection.get("_meta", {}).get("deep", False)
                event_days = 90 if deep_mode else 30
                attach_events(collection, symbol, days=event_days)
                events_attached = True
        except Exception as e:
            logger.warning("attach_events failed (non-fatal): %s", e)

        # Build analysis cards when missing or events were just attached.
        try:
            meta = collection.setdefault("_meta", {})
            if events_attached:
                meta.pop("analysis_cards", None)
            if events_attached or "analysis_cards" not in meta:
                from lib.analysis_templates import build_analysis_cards
                build_analysis_cards(collection)
        except Exception as e:
            logger.warning("build_analysis_cards failed (non-fatal): %s", e)

    if fmt == "json":
        return render_json(collection)
    if fmt == "html":
        return render_html(collection, symbol)
    if fmt == "md":
        from ._concise import render_report_v3 as _v3  # deferred: avoid circular import
        return _v3(collection, symbol, mode=mode)
    return render_report_v2(collection, symbol)


# --- render_report_v2 ---
def render_report_v2(collection: dict[str, Any], symbol: str) -> str:
    """v0.1.2 八段研究模板。

    结构: 公司画像 → 经营质量 → 估值位置 → 资金与筹码 →
          技术结构 → 事件催化 → 核心矛盾 → 引用来源
    """
    dims = _index_dims(collection)
    import importlib as _il; _c = _il.import_module('lib.render_markdown._concise')  # deferred: avoid circular import

    parts: list[str] = [
        _header_v2(collection, symbol),
        _section_profile(dims),
        _section_quality(dims),
        render_valuation_section(dims, collection),
        _section_flow(dims, collection),
        render_technical_section(dims, collection),
        (_c._section_research_summary)(collection, symbol, dims),
        _section_events(collection),
        _section_thesis(dims, collection),
        _references_appendix(collection),
        _risk_footer(),
    ]
    return "\n\n".join(p for p in parts if p)


# --- _header_v2 ---
def _header_v2(collection: dict, symbol: str) -> str:
    name = ""
    basic = _get_dim_data(_index_dims(collection), "basic_info")
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
    title = f"# {symbol} {name} 研究快照"
    lines = [
        title.strip(),
        f"采集时间: {fmt_fetched_at(collection.get('fetched_at', ''))}",
        f"维度: {collection['summary']['available']}/{collection['summary']['total']} 有数据"
        + (f"（{collection['summary']['degraded']} 降级）" if collection['summary'].get('degraded') else ""),
        "",
        "> ⚠️ **风险提示:** 本报告由自动化引擎生成，仅供研究备忘录参考，不构成任何投资建议、买卖指令或目标价预测。",
        "",
    ]
    return "\n".join(lines)


# --- _section_profile ---
def _section_profile(dims: dict[str, dict]) -> str:
    """公司画像（basic_info 事实罗列）。"""
    data = _get_dim_data(dims, "basic_info")
    if not data or not isinstance(data, dict):
        return _missing_section("公司画像", "basic_info 维度无数据")

    # LAW 17: 标题含关键数据
    name = data.get("name", "") or data.get("股票简称", "")
    industry = data.get("industry", "")
    market = data.get("market", "")
    title_parts = [f"## 一、{name}"] if name else ["## 一、公司画像"]
    if industry:
        title_parts.append(f"· {industry}")
    if market:
        title_parts.append(f"· {market}")
    lines = [" ".join(title_parts), ""]
    lines.append(f"**结论：** {name or '该标的'}为{industry or '未知行业'}上市公司，以下为基础信息快照。")
    lines.append("")
    # 关键字段映射
    key_fields = [
        ("name", "公司名称"),
        ("股票简称", "简称"),
        ("industry", "行业"),
        ("area", "地区"),
        ("market", "上市市场"),
        ("list_date", "上市日期"),
        # total_mv / pe_ratio 字段 basic_info 采集未请求，暂不渲染
    ]
    for key, label in key_fields:
        v = data.get(key)
        if v is not None:
            lines.append(f"- **{label}:** {v}")

    # 上市时长判断
    list_date = data.get("list_date", "")
    if list_date:
        try:
            from datetime import datetime
            ld = datetime.strptime(str(list_date)[:8], "%Y%m%d")
            years = (datetime.now() - ld).days / 365.25
            if years < 5:
                lines.append(f"- ⚠️ 上市约 {years:.1f} 年，属次新股，历史数据窗口较短")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append("🔍 **待独立验证:** 行业分类可能因数据源口径不同存在差异。上市日期以交易所公告为准。")
    return "\n".join(lines)


# --- _section_quality ---
def _section_quality(dims: dict[str, dict]) -> str:
    """经营质量（financials 表格 + 趋势句）。"""
    data = _get_dim_data(dims, "financials")
    if not data or not isinstance(data, list) or len(data) == 0:
        return _missing_section("经营质量", "financials 维度无数据")

    data = sort_kline_asc(data)  # end_date 与 trade_date 同格式可复用

    # LAW 17: 标题含最新 ROE + 趋势（_safe_num 守卫：None/"nan" 字符串走 "?" 兜底）
    latest_roe = _safe_num(data[-1].get("roe")) if data else None
    roe_title = f"ROE {latest_roe}%" if latest_roe is not None else "?"
    trend_str = ""
    if len(data) >= 2 and latest_roe is not None:
        prev_roe = _safe_num(data[-2].get("roe"))
        if prev_roe is not None:
            trend_str = "↑" if latest_roe > prev_roe else ("↓" if latest_roe < prev_roe else "→")
    lines = [f"## 二、{roe_title} {trend_str} · 近 8 期财务趋势", ""]
    roe_judgment = f"最新 ROE {latest_roe}%{trend_str}" if latest_roe is not None else "财务数据有限"
    lines.append(f"**结论：** {roe_judgment}，以下为近 8 期核心财务指标。")
    lines.append("")

    # 表格（最近 8 期，升序后取末尾）
    lines.append("| 报告期 | ROE(%) | EPS | 扣非净利润 | 营收 | 净利润 |")
    lines.append("|--------|--------|-----|-----------|------|--------|")
    for r in data[-8:]:
        roe = r.get("roe", "-")
        eps = r.get("eps", "-")
        profit_dedt = _fmt_v2(r.get("profit_dedt"))
        revenue = _fmt_v2(r.get("revenue"))
        net_profit = _fmt_v2(r.get("net_profit"))
        lines.append(f"| {r.get('end_date', '?')} | {roe} | {eps} | {profit_dedt} | {revenue} | {net_profit} |")

    # 趋势句（Python 仅陈述事实）
    if len(data) >= 2:
        latest = data[-1]
        prev = data[-2]
        roe_now = latest.get("roe")
        roe_prev = prev.get("roe")
        if roe_now is not None and roe_prev is not None and isinstance(roe_now, (int, float)) and isinstance(roe_prev, (int, float)):
            direction = "上升" if roe_now > roe_prev else ("下降" if roe_now < roe_prev else "持平")
            lines.append(f"\n最近两期 ROE 趋势: {roe_prev}% → {roe_now}%（{direction}）")

    lines.append("")
    lines.append("🔍 **待独立验证:** 财务数据来自第三方数据源，应与公司年报/季报交叉核对。扣非净利润口径可能因源而异。")
    return "\n".join(lines)


# --- render_valuation_section ---
def render_valuation_section(dims: dict[str, dict], collection: dict = None) -> str:
    """估值位置（valuation 维度 + valuation.py 分位计算）。"""
    val_dim = dims.get("valuation", {})
    val_data = _get_dim_data(dims, "valuation")

    lines = []
    if collection:
        try:
            from ..render_extras import render_rigor_warnings
            strict = bool((collection.get("_meta") or {}).get("strict_rigor"))
            rigor = render_rigor_warnings(collection, strict=strict)
            if rigor:
                lines.append(rigor)
        except ImportError:
            pass

    if val_data is None:
        # 无数据 → 标注
        meta = _get_dim_meta(dims, "valuation")
        error = dims.get("valuation", {}).get("error", "估值维度无数据")
        lines.append(f"> **估值数据不可得。** 原因: {_sanitize_error(error, 80)}")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认 Tushare Token 配置后重试，或手动查询 PE/PB 当前值。")
        return "\n".join(lines)

    # 判断数据来源
    meta = _get_dim_meta(dims, "valuation")
    source = meta.get("source", "未知")

    # LAW 17: 提前计算标题后缀
    title_suffix = "估值位置"
    judgment = "估值数据有限，以下为当前可得估值指标的并列呈现。"

    # 处理 Tushare daily_basic 序列
    if isinstance(val_data, list) and len(val_data) > 0:
        # C6 v0.2.7：数据层收敛到 canonical _v3_load_valuation_summary
        # （与原手工代码同调 valuation_summary，仅 dv_ratio 经 _safe_num 归一；
        # 输出逐字节不变。本函数仅 --emit html 使用，随 HTML 模板跨版本移除）
        summary = _v3_load_valuation_summary(dims)
        window_label = summary.get("window_label", "历史") if summary else "历史"

        # LAW 17: 构建含数据的标题 + 段首主旨句
        pe = summary["pe"]
        if pe["current"] is not None:
            pct_s = f"{pe['pct']:.1f}%" if pe.get("pct") is not None else "?"
            median_s = f"{pe.get('median', 0):.1f}x" if pe.get("median") is not None else "?x"
            title_suffix = f"PE {pe['current']:.1f}x · {pct_s} 分位 · 中位 {median_s}"
            judgment = f"当前 PE(TTM) {pe['current']:.1f}x，处于{window_label} {pct_s} 分位（中位数 {median_s}），估值处于历史**{pe['zone']}**区间。"

        lines[:0] = [f"## 三、{title_suffix}", "", f"**结论：** {judgment}", ""]

        lines.append(f"**来源:** {source}（{window_label}历史序列 + 分位计算）")
        lines.append(f"**数据:** {summary['n_samples']} 个有效交易日")
        lines.append("")

        # PE（pct = 历史中严格低于当前值的比例，与 zone 标签含义一致）
        pe = summary["pe"]
        if pe["current"] is not None:
            pct_str = f"，{window_label} {pe['pct']:.1f}% 分位" if pe["pct"] is not None else ""
            median_str = f"（中位数 {pe['median']:.2f}x）" if pe.get("median") is not None else ""
            lines.append(f"**PE(TTM):** {pe['current']:.2f}x{pct_str}{median_str}，处于历史**{pe['zone']}**区间。")
        else:
            lines.append(f"**PE(TTM):** {pe.get('reason', '不可得')}")

        # PB
        pb = summary["pb"]
        if pb["current"] is not None:
            pct_str = f"，{window_label} {pb['pct']:.1f}% 分位" if pb["pct"] is not None else ""
            median_str = f"（中位数 {pb['median']:.2f}x）" if pb.get("median") is not None else ""
            lines.append(f"**PB:** {pb['current']:.2f}x{pct_str}{median_str}，处于历史**{pb['zone']}**区间。")
        else:
            lines.append(f"**PB:** {pb.get('reason', '不可得')}")

        # 股息率（Tushare daily_basic.dv_ratio 为百分比值如 0.42 表示 0.42%）
        if summary["dv_ratio"] is not None:
            lines.append(f"**股息率:** {summary['dv_ratio']:.2f}%（最近交易日 dv_ratio）")
        else:
            lines.append("**股息率:** 不可得")

        # PS
        ps = summary.get("ps", {})
        if ps.get("current") is not None:
            pct_str = f"，分位 {ps['pct']:.1f}%" if ps.get("pct") is not None else ""
            lines.append(f"**PS(TTM):** {ps['current']:.2f}x{pct_str}")

        # 警告
        for w in summary.get("warnings", []):
            lines.append(f"⚠️ {w}")

    elif isinstance(val_data, dict):
        # 腾讯快照（无历史序列）
        pe = val_data.get("pe_ttm")
        if pe is not None:
            title_suffix = f"PE {pe:.1f}x · 快照估值"
            judgment = f"当前 PE(TTM) {pe:.1f}x（快照数据，无历史分位）。"
        lines[:0] = [f"## 三、{title_suffix}", "", f"**结论：** {judgment}", ""]
        has_history = val_data.get("history_available", False)
        lines.append(f"**来源:** {source}（快照数据）")
        lines.append("")
        if pe is not None:
            lines.append(f"**PE(TTM):** {pe:.2f}x（当前快照）")
        if not has_history:
            lines.append("")
            lines.append("> ⚠️ **历史分位不可得，仅展示当前 PE/PB。** 需配置 Tushare Token 获取历史估值序列。")

    lines.append("")
    lines.append("🔍 **待独立验证:** PE 亏损期为 null 已剔除；行业相对估值 v0.1.2 未覆盖。估值分位不构成买卖判断。")
    return "\n".join(lines)


# --- _section_flow ---
def _section_flow(dims: dict[str, dict], collection: dict = None) -> str:
    """资金与筹码（shareholders + northbound + quote）。"""
    # 行情（提前获取用于标题）
    quote_data = _get_dim_data(dims, "quote")

    # LAW 17: 标题含价格数据 + 段首主旨句
    price = None
    chg_pct = None
    if isinstance(quote_data, dict):
        price = coalesce_field(quote_data, "close", "price")
        chg_pct = coalesce_field(quote_data, "change_pct", "pct_chg")
    elif isinstance(quote_data, list) and quote_data:
        price = quote_data[-1].get("close")
        chg_pct = None
    title_suffix = "资金与筹码"
    judgment = "资金与筹码数据如下。"
    if price is not None:
        title_suffix = f"收盘 {price} · 资金流向"
        judgment = f"最新收盘价 {price}，资金流向与筹码分布见下方。"
        if chg_pct is not None:
            title_suffix = f"收盘 {price}（{chg_pct:+.2f}%）· 资金流向"
            judgment = f"最新收盘价 {price}（{chg_pct:+.2f}%），资金流向与筹码分布见下方。"
    lines = [f"## 四、{title_suffix}", ""]
    lines.append(f"**结论：** {judgment}")
    lines.append("")
    if quote_data:
        if isinstance(quote_data, dict):
            price = coalesce_field(quote_data, "price", "close")
            change = quote_data.get("change_pct")
            turnover = quote_data.get("turnover_rate")
            if price is not None:
                change_str = f"（{change:+.2f}%）" if change is not None else ""
                lines.append(f"**最新价:** {price}{change_str}")
            if turnover is not None:
                lines.append(f"**换手率:** {turnover}%")
        elif isinstance(quote_data, list) and quote_data:
            r = quote_data[-1]
            lines.append(f"**最新收盘:** {r.get('close', '-')}（{r.get('trade_date', '-')}）")

    # 北向资金（升序后取最近 7 日）
    nb_data = _get_dim_data(dims, "northbound")
    if nb_data and isinstance(nb_data, list) and nb_data:
        nb_sorted = sort_kline_asc(nb_data)
        lines.append("")
        lines.append("**北向资金近7日:**")
        lines.append("| 日期 | 净流向 |")
        lines.append("|------|--------|")
        for r in nb_sorted[-7:]:
            lines.append(f"| {r.get('trade_date', '?')} | {_fmt_v2(r.get('net_mf_vol'))} |")

    # 十大股东
    sh_data = _get_dim_data(dims, "shareholders")
    if sh_data and isinstance(sh_data, list) and sh_data:
        lines.append("")
        lines.append("**前十大股东（最新报告期）:**")
        lines.append("| 股东 | 持股比例 |")
        lines.append("|------|---------|")
        for r in sh_data[:10]:
            lines.append(f"| {r.get('holder_name', '?')} | {_fmt_v2(r.get('hold_ratio'), '%')} |")

    if not quote_data and not nb_data and not sh_data:
        lines.append("> 资金与筹码数据暂无。")

    lines.append("")
    lines.append("🔍 **待独立验证:** 北向资金为估算值，股东数据可能有报告期滞后。")
    return "\n".join(lines)


# --- render_technical_section ---
def render_technical_section(dims: dict[str, dict], collection: dict = None) -> str:
    """技术结构（kline → technical.py 计算 + 渲染）。"""
    kline_data = _get_dim_data(dims, "kline")
    # LAW 17: 标题将在技术指标计算后动态构建
    lines = []

    if not kline_data or not isinstance(kline_data, list) or len(kline_data) == 0:
        lines.append("> K 线数据不可得，跳过技术分析。")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认日K线维度采集成功。")
        return "\n".join(lines)

    meta = _get_dim_meta(dims, "kline")
    source = meta.get("source", "未知")
    lines.append(f"[复权: 不复权 / 来源: {source}]")

    kline_data = sort_kline_asc(kline_data)
    tech = compute(kline_data)

    if "error" in tech:
        lines[:0] = [
            f"## 五、技术结构（计算失败）", "",
            f"**结论：** 技术指标计算失败：{sanitize_error(tech.get('message', '未知错误'), 80)}", ""]
        lines.append(f"[复权: 不复权 / 来源: {source}]")
        return "\n".join(lines)

    # LAW 17: 构建含趋势数据的标题 + 段首主旨句
    trend_label = tech.get("trend", {}).get("alignment", {}).get("trend_label", "")
    close_val = kline_data[-1].get("close") if kline_data else None
    close_str = f"{close_val:.2f}" if close_val is not None else "?"
    title_suffix = f"MA 排列：{trend_label} · 收盘 {close_str}" if trend_label else f"技术结构 · 收盘 {close_str}"
    judgment = f"当前均线排列：{trend_label}，收盘价 {close_str}，详见下方指标。" if trend_label else f"收盘价 {close_str}，技术指标见下方。"

    lines[:0] = [f"## 五、{title_suffix}", "", f"**结论：** {judgment}", ""]

    # --- 趋势 ---
    trend = tech["trend"]
    lines.append("")
    lines.append("### 趋势")
    alignment = trend.get("alignment", {})
    lines.append(f"**均线排列:** {alignment.get('trend_label', '?')}")

    # MA 关键值
    ma = trend.get("ma", {})
    ma_strs = []
    for p in ("5", "10", "20", "60", "120"):
        vals = ma.get(p, [])
        if vals and vals[-1] is not None:
            ma_strs.append(f"MA{p}={vals[-1]:.2f}")
    if ma_strs:
        lines.append(f"**均线位置:** {', '.join(ma_strs)}")

    # MA250
    vals_250 = ma.get("250", [])
    if vals_250 and vals_250[-1] is not None:
        lines.append(f"**MA250:** {vals_250[-1]:.2f}")
    else:
        avail = trend.get("ma_availability", {}).get("250", "")
        if avail:
            lines.append(f"**MA250:** {avail}")

    # 均线斜率
    slopes = trend.get("slope", {})
    slope_strs = []
    for p in ("20", "60"):
        s = slopes.get(p)
        if s is not None:
            slope_strs.append(f"MA{p}斜率 {'+' if s >= 0 else ''}{s:.1f}%")
    if slope_strs:
        lines.append(f"**均线斜率:** {', '.join(slope_strs)}")

    # 趋势摘要
    sentences = trend.get("summary_sentences", [])
    for s in sentences:
        lines.append(f"- {s}")

    # --- 动量 ---
    macd = tech["momentum"]["macd"]
    lines.append("")
    lines.append("### 动量")
    if macd.get("available"):
        lines.append(f"**MACD:** DIF={macd['dif']}, DEA={macd['dea']}, 柱={macd['histogram']}")
        cross = macd.get("cross", {})
        if cross:
            lines.append(f"**DIF/DEA:** {cross.get('desc', '?')}")
        if macd.get("histogram_trend"):
            lines.append(f"**柱体:** {macd['histogram_trend']}")
    else:
        lines.append(f"MACD: {macd.get('reason', '不可得')}")

    # --- 超买超卖 ---
    rsi = tech["overbought_oversold"].get("rsi", {})
    kdj = tech["overbought_oversold"].get("kdj", {})
    lines.append("")
    lines.append("### 超买超卖")
    rsi_strs = []
    for p in ("6", "12", "24"):
        r = rsi.get(p, {})
        if r.get("available"):
            rsi_strs.append(f"RSI({p})={r['value']:.1f}（{r['zone']}）")
        elif r.get("reason"):
            rsi_strs.append(f"RSI({p}): {r['reason']}")
    lines.append("; ".join(rsi_strs) if rsi_strs else "RSI: 不可得")

    if kdj.get("available"):
        lines.append(f"**KDJ:** K={kdj['k']:.1f}, D={kdj['d']:.1f}, J={kdj['j']:.1f}")
    else:
        reason = kdj.get("reason", "不可得")
        lines.append(f"**KDJ:** {reason}")

    # --- 波动 ---
    vol = tech["volatility"]
    boll = vol.get("boll", {})
    atr = vol.get("atr", {})
    lines.append("")
    lines.append("### 波动")
    if boll.get("available"):
        pos = boll.get("position", "")
        pos_str = f"，收盘价{pos}" if pos else ""
        lines.append(f"**BOLL:** 上轨 {boll['upper']}, 中轨 {boll['mid']}, 下轨 {boll['lower']}{pos_str}")
    else:
        lines.append(f"**BOLL:** {boll.get('reason', '不可得')}")

    if atr.get("available"):
        lines.append(f"**ATR(14):** {atr['value']}")
    else:
        lines.append(f"**ATR(14):** {atr.get('reason', '不可得')}")

    # --- 成交量 ---
    vol_info = tech["volume"]
    lines.append("")
    lines.append("### 成交量")
    if vol_info.get("status"):
        lines.append(f"**量比:** {vol_info['status']}")
    lines.append(f"**近5日均量:** {vol_info.get('avg_vol_5d', '-')}")
    if vol_info.get("recent_spike_days", 0) > 0:
        lines.append(f"近20日有 {vol_info['recent_spike_days']} 日量比>1.5")

    # --- 结构 ---
    structure = tech["structure"]
    extremes = structure.get("extremes", {})
    dd = structure.get("drawdown_60d", {})
    lines.append("")
    lines.append("### 结构")

    for n in (20, 60, 120):
        ex = extremes.get(n, {})
        if ex.get("available"):
            lines.append(f"- 近{n}日最高 {ex['max']}（{ex.get('max_date', '')}），最低 {ex['min']}（{ex.get('min_date', '')}）")
            if ex.get("is_n_day_high"):
                lines.append(f"  → 当前处近{n}日新高")

    if dd.get("available"):
        lines.append(f"- 近60日最大回撤: {dd['drawdown_pct']:.1f}%（峰值 {dd['peak']} 于 {dd.get('peak_date', '')}）")

    lines.append("")
    lines.append("🔍 **待独立验证:** 技术指标基于不复权收盘价计算。均线/RSI/MACD 为描述性统计，不构成交易信号。")
    return "\n".join(lines)


# --- _section_events ---
def _section_events(collection: dict) -> str:
    """事件催化：复用 V3 事件时间线真实渲染（替代 v0.1.2 死占位）。

    无 events 时降级输出标题 + 未采集说明（八段结构前缀恒在）。
    """
    import importlib as _il
    _c = _il.import_module('lib.render_markdown._concise')  # deferred: avoid circular import
    text = _c._section_events_timeline(collection)
    if text:
        # 与 V3 标题文本耦合（_v3.py _section_events_timeline）——V3 改标题需同步
        return text.replace("## 3a. 事件时间线", "## 六、事件催化（事件时间线）", 1)
    return ("## 六、事件催化\n\n"
            "近期公告/事件数据未采集（引擎未附加 events）\n\n"
            "🔍 **待独立验证:** 事件分析依赖 WebSearch 结果，应标注每条信息的 URL 来源。")


# --- _section_thesis ---
def _section_thesis(dims: dict[str, dict], collection: dict) -> str:
    """核心矛盾：复用 V3 引擎推理（替代 v0.1.2 死占位）。

    V3 _executive_core_contradictions 恒返回 2 条（数据驱动，不足时填充
    "独立维度交叉验证不足，需补充外部信源 [推测，待验证]"）。
    """
    import importlib as _il
    _c = _il.import_module('lib.render_markdown._concise')  # deferred: avoid circular import
    items = _c._executive_core_contradictions(collection, dims) or []
    lines = ["## ⚡ 核心矛盾（当前最值得跟踪的问题）", ""]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)




