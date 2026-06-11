"""报告渲染模块。从采集结果生成 compact/json/md 格式输出。

设计原则（参考 last30days-skill 的多源并行取证）：
  - 不是"兜底"(fallback)，而是"并行取证、汇总为证"
  - 每个维度展示各渠道的独立结果，标注以哪个为主
  - 所有渠道均失败时：明确标注"未获取到任何有效数据，无法判断"
  - 末尾附加"引用来源"章节（类似论文的 References）
"""

from __future__ import annotations

import html as _html_mod
import json
import re
from typing import Any

from .proxy import EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS

_EASTMONEY_BLOCKED_SHORT = "东方财富(East Money)主动拒绝连接"
_RAW_CONNECTION_REFUSED_SHORT = "服务器拒绝连接"


def sanitize_error(error: str, max_len: int = 60) -> str:
    """将原始 Python 异常转为可读的简短说明，截断到 max_len。

    优先检测东方财富封锁、DNS/代理等常见网络问题。
    """
    if not error:
        return "未知错误"
    if any(kw in error for kw in _EASTMONEY_BLOCKED_KEYWORDS):
        return _EASTMONEY_BLOCKED_SHORT
    # 通用 ConnectionError / Max retries exceeded（无论长度均替换为可读标签）
    if "ConnectionError" in error or "Max retries exceeded" in error:
        return _RAW_CONNECTION_REFUSED_SHORT
    # 其他：取最后一段有意义的内容
    cleaned = re.sub(r"\s+", " ", error).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


_sanitize_error = sanitize_error  # 模块内向后兼容


def _fmt(v: Any, unit: str = "") -> str:
    if v is None: return "-"
    if isinstance(v, float):
        if abs(v) >= 1e8: return f"{v/1e8:.2f}亿"
        if abs(v) >= 1e4: return f"{v/1e4:.2f}万"
        return f"{v:.2f}{unit}" if unit else f"{v:.2f}"
    return str(v)


def _render_dimension_data(dn: str, data: Any, lines: list[str]) -> None:
    """渲染维度主数据内容（不含来源标注）。"""
    if dn == "basic_info" and isinstance(data, dict):
        for k, v in data.items():
            lines.append(f"- {k}: {v}")
    elif dn == "financials" and isinstance(data, list):
        lines.append("| 期间 | ROE | EPS | 扣非净利润 |\n|------|-----|-----|-----------|")
        for r in data[:5]:
            lines.append(f"| {r.get('end_date','?')} | {_fmt(r.get('roe'),'%')} | {_fmt(r.get('eps'))} | {_fmt(r.get('profit_dedt'))} |")
    elif dn == "quote":
        if isinstance(data, dict):
            for k, v in data.items():
                lines.append(f"- {k}: {v}")
        elif isinstance(data, list) and data:
            # Tushare/akshare 日线数据：取最新一条展示
            r = data[-1]
            lines.append(f"- 日期: {r.get('trade_date', '?')}")
            lines.append(f"- 开盘: {_fmt(r.get('open'))}")
            lines.append(f"- 最高: {_fmt(r.get('high'))}")
            lines.append(f"- 最低: {_fmt(r.get('low'))}")
            lines.append(f"- 收盘: {_fmt(r.get('close'))}")
            lines.append(f"- 成交量: {_fmt(r.get('vol'))}")
    elif dn == "shareholders" and isinstance(data, list):
        lines.append("| 股东 | 持股比例 |\n|------|---------|")
        for r in data[:10]:
            lines.append(f"| {r.get('holder_name','?')} | {_fmt(r.get('hold_ratio'),'%')} |")
    elif dn == "northbound" and isinstance(data, list):
        lines.append("| 日期 | 净流向 |\n|------|-------|")
        for r in data[:7]:
            lines.append(f"| {r.get('trade_date','?')} | {_fmt(r.get('net_mf_vol'))} |")
    elif dn == "kline" and isinstance(data, list):
        lines.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |\n|------|------|------|------|------|--------|")
        for r in data[-10:]:
            lines.append(f"| {r.get('trade_date','?')} | {_fmt(r.get('open'))} | {_fmt(r.get('high'))} | {_fmt(r.get('low'))} | {_fmt(r.get('close'))} | {_fmt(r.get('vol'))} |")


def _source_status_block(all_sources: list[dict] | None) -> str:
    """生成各渠道的独立取证状态块。"""
    if not all_sources:
        return ""
    rows = []
    for s in all_sources:
        source = s.get("source", "?")
        avail = s.get("data_available", False)
        error = s.get("error") or ""
        qp = s.get("query_params", "")
        icon = "✅" if avail else ("❌" if error else "⏭️")
        status = "成功" if avail else (f"失败: {_sanitize_error(error, 80)}" if error else "未尝试")
        qp_str = f" `{qp}`" if qp else ""
        rows.append(f"  - **{source}** {icon} — {status}{qp_str}")
    return "\n".join(rows)


def render_compact(collection: dict[str, Any], symbol: str) -> str:
    """紧凑文本报告（已弃用，v0.1.2 起 render() 路由到 render_report_v2）。

    保留供向后兼容；新代码应直接使用 render() 或 render_report_v2()。
    """
    lines = [
        f"# {symbol} 采集报告",
        f"采集时间: {collection.get('fetched_at','')[:19]}",
        f"状态: {collection['summary']['available']}/{collection['summary']['total']} 维度有数据",
        "",
    ]

    for dim in collection.get("dimensions", []):
        dn, display = dim["dimension"], dim["display"]
        data, meta = dim.get("data"), dim.get("_meta", {})
        source = meta.get("source", "none")
        all_src = meta.get("all_sources")
        has_data = data is not None

        # ---- 主数据区块 ----
        if has_data:
            xv = _source_status_block(all_src)
            lines.append(f"## ✅ {display}")
            lines.append(f"**主数据来源：** {source}")
            if xv:
                lines.append("")
                lines.append("**各渠道取证状态：**")
                lines.append(xv)
                lines.append("")
            _render_dimension_data(dn, data, lines)
        else:
            lines.append(f"## ⚠️ {display}")
            error = dim.get("error", "无可用数据源")
            lines.append("")
            lines.append(f"> **未获取到任何有效数据，无法判断。**")
            lines.append(f"> 原因：{_sanitize_error(error, 80)}")
            xv = _source_status_block(all_src)
            if xv:
                lines.append("")
                lines.append("**已尝试的渠道：**")
                lines.append(xv)
        lines.append("")

    # ---- 引用来源附录（类似论文 References） ----
    lines.append("---")
    lines.append("## 📚 引用来源（References）")
    lines.append("")
    lines.append("| 维度 | 渠道 | 追溯路径 | 数据状态 |")
    lines.append("|------|------|----------|---------|")
    for dim in collection.get("dimensions", []):
        display = dim["display"]
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            src_entry = dim.get("_meta", {})
            icon = "✅" if dim.get("data") is not None else "❌"
            qp = src_entry.get("query_params", "")
            src_name = src_entry.get("source", "?")
            status = "可用" if dim.get("data") is not None else "不可用"
            lines.append(f"| {display} | {src_name} | `{qp}` | {icon} {status} |")
            continue

        first = True
        for s in all_src:
            src_name = s.get("source", "?")
            avail = s.get("data_available", False)
            error = s.get("error", "")
            qp = s.get("query_params", "")
            dim_label = display if first else ""
            first = False
            if avail:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ✅ 有数据 |")
            elif error:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ❌ {_sanitize_error(error, 55)} |")
            else:
                lines.append(f"| {dim_label} | {src_name} | — | ⏭️ 未尝试 |")

    return "\n".join(lines)


def render_json(collection: dict[str, Any]) -> str:
    return json.dumps(collection, ensure_ascii=False, indent=2, default=str)


def render(collection: dict[str, Any], symbol: str, fmt: str = "compact") -> str:
    """统一渲染入口。支持 compact / json / md / html 格式。

    compact  — 紧凑文本报告，适合终端直接阅读（v0.1.2 起使用 v2 模板）
    json     — 结构化 JSON，适合程序消费
    md       — Markdown 分析报告（v0.1.2 起使用 v2 模板）
    html     — HTML 研究报告（v0.1.2+ 新增）
    """
    if fmt == "json":
        return render_json(collection)
    if fmt == "html":
        return render_html(collection, symbol)
    return render_report_v2(collection, symbol)


# ---- v2 报告模板 ----

def render_report_v2(collection: dict[str, Any], symbol: str) -> str:
    """v0.1.2 八段研究模板。

    结构: 公司画像 → 经营质量 → 估值位置 → 资金与筹码 →
          技术结构 → 事件催化 → 核心矛盾 → 引用来源
    """
    dims = _index_dims(collection)

    parts: list[str] = [
        _header_v2(collection, symbol),
        _section_profile(dims),
        _section_quality(dims),
        render_valuation_section(dims, collection),
        _section_flow(dims, collection),
        render_technical_section(dims, collection),
        _section_events_placeholder(),
        _section_thesis_placeholder(dims),
        _references_appendix(collection),
        _risk_footer(),
    ]
    return "\n\n".join(p for p in parts if p)


def _index_dims(collection: dict) -> dict[str, dict]:
    """将 dimensions 列表转为 dict。"""
    dims = collection.get("dimensions", [])
    return {d.get("dimension", ""): d for d in dims}


def _get_dim_data(dims: dict[str, dict], key: str) -> Any:
    """获取维度主数据。"""
    d = dims.get(key, {})
    return d.get("data")


def _get_dim_meta(dims: dict[str, dict], key: str) -> dict:
    """获取维度 meta。"""
    d = dims.get(key, {})
    return d.get("_meta", {})


def _header_v2(collection: dict, symbol: str) -> str:
    name = ""
    basic = _get_dim_data(_index_dims(collection), "basic_info")
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
    title = f"# {symbol} {name} 研究快照"
    lines = [
        title.strip(),
        f"采集时间: {collection.get('fetched_at', '')[:19]}",
        f"维度: {collection['summary']['available']}/{collection['summary']['total']} 有数据"
        + (f"（{collection['summary']['degraded']} 降级）" if collection['summary'].get('degraded') else ""),
        "",
        "> ⚠️ **风险提示:** 本报告由自动化引擎生成，仅供学习研究参考，不构成任何投资建议、买卖指令或目标价预测。",
        "",
    ]
    return "\n".join(lines)


def _section_profile(dims: dict[str, dict]) -> str:
    """公司画像（basic_info 事实罗列）。"""
    data = _get_dim_data(dims, "basic_info")
    if not data or not isinstance(data, dict):
        return _missing_section("公司画像", "basic_info 维度无数据")

    lines = ["## 一、公司画像", ""]
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


def _section_quality(dims: dict[str, dict]) -> str:
    """经营质量（financials 表格 + 趋势句）。"""
    data = _get_dim_data(dims, "financials")
    if not data or not isinstance(data, list) or len(data) == 0:
        return _missing_section("经营质量", "financials 维度无数据")

    from lib.technical import sort_kline_asc
    data = sort_kline_asc(data)  # end_date 与 trade_date 同格式可复用

    lines = ["## 二、经营质量", ""]

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


def render_valuation_section(dims: dict[str, dict], collection: dict = None) -> str:
    """估值位置（valuation 维度 + valuation.py 分位计算）。"""
    val_dim = dims.get("valuation", {})
    val_data = _get_dim_data(dims, "valuation")

    lines = ["## 三、估值位置", ""]

    if val_data is None:
        # 无数据 → 标注
        meta = _get_dim_meta(dims, "valuation")
        error = dims.get("valuation", {}).get("error", "估值维度无数据")
        lines.append(f"> **估值数据不可得。** 原因: {_sanitize_error(error, 80)}")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认 Tushare Token 配置后重试，或手动查询 PE/PB 当前值。")
        return "\n".join(lines)

    # 判断数据来源
    from lib.valuation import valuation_summary
    meta = _get_dim_meta(dims, "valuation")
    source = meta.get("source", "未知")

    # 处理 Tushare daily_basic 序列
    if isinstance(val_data, list) and len(val_data) > 0:
        from lib.technical import sort_kline_asc
        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted]
        pb_seq = [r.get("pb") for r in val_sorted]
        ps_seq = [r.get("ps_ttm") or r.get("ps") for r in val_sorted]
        dv = None
        for r in reversed(val_sorted):
            if r.get("dv_ratio") is not None:
                dv = r.get("dv_ratio")
                break

        # 判断窗口（A 股 ~242 交易日/年，1250 ≈ 5 年）
        if len(val_sorted) >= 1250:
            window_label = "近5年"
        elif len(val_sorted) >= 250:
            window_label = f"近{len(val_sorted) // 250}年"
        else:
            window_label = "上市以来（数据有限）"

        summary = valuation_summary(pe_seq, pb_seq, ps_seq=ps_seq,
                                   dv_ratio=dv, window_label=window_label)

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


def _section_flow(dims: dict[str, dict], collection: dict = None) -> str:
    """资金与筹码（shareholders + northbound + quote）。"""
    lines = ["## 四、资金与筹码", ""]

    # 行情
    quote_data = _get_dim_data(dims, "quote")
    if quote_data:
        if isinstance(quote_data, dict):
            price = quote_data.get("price") or quote_data.get("close")
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
        from lib.technical import sort_kline_asc
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


def render_technical_section(dims: dict[str, dict], collection: dict = None) -> str:
    """技术结构（kline → technical.py 计算 + 渲染）。"""
    kline_data = _get_dim_data(dims, "kline")
    lines = ["## 五、技术结构", ""]

    if not kline_data or not isinstance(kline_data, list) or len(kline_data) == 0:
        lines.append("> K 线数据不可得，跳过技术分析。")
        lines.append("")
        lines.append("🔍 **待独立验证:** 确认日K线维度采集成功。")
        return "\n".join(lines)

    meta = _get_dim_meta(dims, "kline")
    source = meta.get("source", "未知")
    lines.append(f"[复权: 不复权 / 来源: {source}]")

    from lib.technical import compute, sort_kline_asc
    kline_data = sort_kline_asc(kline_data)
    tech = compute(kline_data)

    if "error" in tech:
        lines.append(f"> 技术指标计算失败: {sanitize_error(tech.get('message', '未知错误'), 80)}")
        return "\n".join(lines)

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


def _section_events_placeholder() -> str:
    """事件催化占位（v0.1.2 不实现自动分析）。"""
    return """## 六、事件催化

> 本节由分析阶段（Claude）根据公告、新闻、行业动态撰写，非引擎自动生成。
> v0.1.2 引擎仅提供数据卡片，Claude 应通过 WebSearch 补充近期事件。

（待分析阶段填写）

🔍 **待独立验证:** 事件分析依赖 WebSearch 结果，应标注每条信息的 URL 来源。"""


def _section_thesis_placeholder(dims: dict[str, dict]) -> str:
    """核心矛盾占位（v0.1.2 引擎只填数据卡片）。"""
    from lib.technical import sort_kline_asc

    # 尝试提取关键数据（统一升序后取末位）
    fin_data = _get_dim_data(dims, "financials")
    roe_str = "?"
    if fin_data and isinstance(fin_data, list) and fin_data:
        fin_sorted = sort_kline_asc(fin_data)
        latest = fin_sorted[-1]
        roe = latest.get("roe")
        if roe is not None:
            roe_str = f"{roe}%"

    pe_str = "?"
    val_data = _get_dim_data(dims, "valuation")
    if val_data and isinstance(val_data, list) and val_data:
        val_sorted = sort_kline_asc(val_data)
        pe = val_sorted[-1].get("pe_ttm")
        if pe is not None:
            pe_str = f"{pe}x"

    trend_str = "?"
    kline_data = _get_dim_data(dims, "kline")
    if kline_data and isinstance(kline_data, list) and len(kline_data) >= 20:
        from lib.technical import compute
        tech = compute(kline_data)
        if "error" not in tech:
            trend_str = tech["trend"]["alignment"].get("trend_label", "?")

    return f"""## ⚡ 核心矛盾（当前最值得跟踪的问题）

> 本节由分析阶段（Claude）根据上下文数据卡片撰写，非引擎自动生成。
> 数据输入: 经营质量 ROE={roe_str} | 估值 PE={pe_str} | 技术趋势={trend_str}

（待分析阶段填写）"""


def _references_appendix(collection: dict[str, Any]) -> str:
    """引用来源附录。"""
    lines = ["---", "", "## 📚 引用来源（References）", ""]
    lines.append("| 维度 | 渠道 | 追溯路径 | 数据状态 |")
    lines.append("|------|------|----------|---------|")

    for dim in collection.get("dimensions", []):
        display = dim.get("display", dim.get("dimension", "?"))
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            meta = dim.get("_meta", {})
            icon = "✅" if dim.get("data") is not None else "❌"
            qp = meta.get("query_params", "")
            src_name = meta.get("source", "?")
            status = "可用" if dim.get("data") is not None else "不可用"
            lines.append(f"| {display} | {src_name} | `{qp}` | {icon} {status} |")
            continue

        first = True
        for s in all_src:
            src_name = s.get("source", "?")
            avail = s.get("data_available", False)
            error = s.get("error", "")
            qp = s.get("query_params", "")
            dim_label = display if first else ""
            first = False
            if avail:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ✅ 有数据 |")
            elif error:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ❌ {_sanitize_error(error, 55)} |")
            else:
                lines.append(f"| {dim_label} | {src_name} | — | ⏭️ 未尝试 |")

    return "\n".join(lines)


def _risk_footer() -> str:
    return """---

> ⚠️ **免责声明:** 本报告由 invest-A v0.1.2 自动化引擎生成，仅供学习研究参考。
> 不构成任何投资建议、买卖指令或目标价预测。所有技术指标均为市场状态描述，非交易信号。
> 数据来源见上文 References 表，可能与实际公告存在差异，请以公司公告和交易所数据为准。"""


def _missing_section(title: str, reason: str) -> str:
    return f"""## {title}

> **未获取到任何有效数据，无法判断。**
> 原因: {reason}

🔍 **待独立验证:** 确认数据源配置后重试，或通过 WebSearch 手动补充。"""


def _fmt_v2(v: Any, unit: str = "") -> str:
    """辅助格式化。"""
    if v is None:
        return "-"
    if isinstance(v, float):
        if abs(v) >= 1e8:
            return f"{v / 1e8:.2f}亿"
        if abs(v) >= 1e4:
            return f"{v / 1e4:.2f}万"
        return f"{v:.2f}{unit}" if unit else f"{v:.2f}"
    return str(v)


# ---- HTML 报告渲染 ----

_HTML_CSS = """\
:root {
  --color-bg: #ffffff;
  --color-text: #1a1a2e;
  --color-muted: #6b7280;
  --color-accent: #2563eb;
  --color-border: #e5e7eb;
  --color-header-bg: #f8fafc;
  --color-table-header: #f1f5f9;
  --color-table-stripe: #f8fafc;
  --color-warning-bg: #fef3c7;
  --color-warning-border: #f59e0b;
  --color-danger: #ef4444;
  --color-success: #22c55e;
  --color-code-bg: #f1f5f9;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif;
  --font-mono: "SF Mono", "Fira Code", "Noto Sans Mono", monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: var(--font-sans);
  color: var(--color-text);
  background: var(--color-bg);
  line-height: 1.8;
  font-size: 15px;
  padding: 0;
}

.container {
  max-width: 960px;
  margin: 0 auto;
  padding: 40px 32px;
}

/* ---- Header ---- */
.report-header {
  margin-bottom: 40px;
  padding-bottom: 24px;
  border-bottom: 2px solid var(--color-border);
}
.report-header h1 {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin-bottom: 8px;
}
.report-header .meta {
  color: var(--color-muted);
  font-size: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.report-header .meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* ---- Risk Banner ---- */
.risk-banner {
  background: var(--color-warning-bg);
  border-left: 4px solid var(--color-warning-border);
  padding: 14px 18px;
  border-radius: 6px;
  font-size: 13px;
  color: #92400e;
  margin-bottom: 32px;
}
.risk-banner strong { font-weight: 600; }

/* ---- Section ---- */
.section {
  margin-bottom: 40px;
}
.section h2 {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-accent);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 20px 0 10px;
  color: var(--color-text);
}
.section p {
  margin-bottom: 10px;
}

/* ---- Table ---- */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0 16px;
  font-size: 14px;
}
thead th {
  background: var(--color-table-header);
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  border-bottom: 2px solid var(--color-border);
  white-space: nowrap;
}
tbody td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--color-border);
}
tbody tr:nth-child(even) {
  background: var(--color-table-stripe);
}
tbody tr:hover {
  background: #eff6ff;
}

/* ---- Blockquote / Warning ---- */
blockquote {
  background: var(--color-code-bg);
  border-left: 4px solid var(--color-accent);
  padding: 12px 16px;
  margin: 12px 0;
  border-radius: 0 6px 6px 0;
  font-size: 14px;
  color: #374151;
}
blockquote p { margin-bottom: 0; }

/* ---- Code ---- */
code {
  font-family: var(--font-mono);
  background: var(--color-code-bg);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  color: #be123c;
}
pre {
  background: #1e293b;
  color: #e2e8f0;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  margin: 12px 0;
}
pre code {
  background: none;
  color: inherit;
  padding: 0;
}

/* ---- List ---- */
ul, ol {
  padding-left: 20px;
  margin-bottom: 12px;
}
li { margin-bottom: 4px; }

/* ---- Verify Notice ---- */
.verify-notice {
  background: #f0fdf4;
  border-left: 4px solid var(--color-success);
  padding: 12px 16px;
  border-radius: 0 6px 6px 0;
  margin: 12px 0;
  font-size: 13px;
  color: #166534;
}

/* ---- References Table ---- */
.refs-table {
  font-size: 13px;
}
.refs-table code {
  font-size: 12px;
  word-break: break-all;
}
.status-ok { color: var(--color-success); font-weight: 600; }
.status-err { color: var(--color-danger); }
.status-ok::before { content: "✅ "; }
.status-err::before { content: "❌ "; }

/* ---- Footer ---- */
.report-footer {
  margin-top: 48px;
  padding-top: 24px;
  border-top: 2px solid var(--color-border);
  font-size: 13px;
  color: var(--color-muted);
}
.report-footer .disclaimer {
  background: var(--color-warning-bg);
  border-left: 4px solid var(--color-warning-border);
  padding: 12px 16px;
  border-radius: 6px;
  margin-bottom: 12px;
  color: #92400e;
}

/* ---- Print ---- */
@media print {
  body { font-size: 13px; }
  .container { padding: 20px; }
  .section { page-break-inside: avoid; }
}

/* ---- Responsive ---- */
@media (max-width: 640px) {
  .container { padding: 16px; }
  .report-header h1 { font-size: 22px; }
  table { font-size: 13px; }
  thead th, tbody td { padding: 6px 8px; }
}
"""


def _md_to_html(md_text: str) -> str:
    """将 Markdown 转为 HTML 片段。"""
    import markdown as _md

    # 预处理：将 Markdown 图片链接换行处理
    # 直接使用 markdown 库的标准扩展
    extensions = [
        "markdown.extensions.extra",       # tables, fenced_code, etc.
        "markdown.extensions.codehilite",  # 代码高亮
        "markdown.extensions.toc",         # 目录（预留）
        "markdown.extensions.nl2br",       # 换行保留
    ]
    html = _md.markdown(md_text, extensions=extensions)
    return html


def _build_html_document(title: str, body_html: str) -> str:
    """包装完整 HTML 文档。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{_HTML_CSS}
</style>
</head>
<body>
<div class="container">
{body_html}
</div>
</body>
</html>"""


def render_html(collection: dict[str, Any], symbol: str, md_text: str | None = None) -> str:
    """HTML 研究报告。

    复用 render_report_v2() 的 Markdown 输出，转换为结构化 HTML。
    可传入预计算的 md_text 以避免重复渲染（cmd_report HTML 模式下复用）。
    """
    if md_text is None:
        md_text = render_report_v2(collection, symbol)

    # 提取报告标题
    name = ""
    dims = _index_dims(collection)
    basic = _get_dim_data(dims, "basic_info")
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
    title = _html_mod.escape(f"{symbol} {name}".strip())

    # 分割 Markdown 的各部分
    body_html = _md_to_html(md_text)

    # 对 HTML 做后处理：给 "🔍 待独立验证" 段落包装 verify-notice 样式
    # 匹配 <p>🔍 <strong>待独立验证:</strong> ...</p> 并替换为 styled div
    body_html = re.sub(
        r'<p>🔍 <strong>待独立验证:?</strong>([^<]*)</p>',
        r'<div class="verify-notice">🔍 <strong>待独立验证:</strong>\1</div>',
        body_html,
    )

    return _build_html_document(title, body_html)

