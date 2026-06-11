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
from pathlib import Path
from typing import Any

from .proxy import EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS

ENGINE_VERSION = "0.1.2"

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
    if "Clash/VPN" in error or "Clash TUN" in error:
        return _EASTMONEY_BLOCKED_SHORT
    # 通用 ConnectionError / Max retries exceeded（无论长度均替换为可读标签）
    if "ProxyError" in error or "Max retries exceeded" in error:
        return "本机代理/VPN 拦截（请检查 Clash 规则或关闭 TUN）"
    if "ConnectionError" in error or "Connection aborted" in error:
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


def _data_fields(dimension: str, data: Any) -> str:
    """提取维度获取到的有效数据字段摘要。

    返回逗号分隔的字段名列表，如 "公司名称、行业、上市日期"。
    """
    if data is None:
        return ""
    if isinstance(data, dict):
        # 字段名映射：中文字段更可读
        key_display = {
            "name": "公司名称", "area": "地区", "industry": "行业",
            "market": "上市市场", "list_date": "上市日期",
            "price": "最新价", "change_pct": "涨跌幅", "turnover_rate": "换手率",
            "pe_ratio": "PE", "total_mv": "总市值",
            "pe_ttm": "PE(TTM)", "pb": "PB", "ps_ttm": "PS(TTM)",
            "dv_ratio": "股息率", "history_available": "历史分位",
        }
        fields = []
        for k in data:
            display = key_display.get(k, k)
            if data[k] is not None:
                fields.append(display)
        return "、".join(fields) if fields else "有数据"
    if isinstance(data, list) and data:
        # 取第一条记录的键
        first = data[0]
        if isinstance(first, dict):
            fin_keys = {
                "end_date": "报告期", "roe": "ROE", "eps": "EPS",
                "profit_dedt": "扣非净利润", "revenue": "营收", "net_profit": "净利润",
                "trade_date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘", "vol": "成交量",
                "holder_name": "股东名称", "hold_ratio": "持股比例",
                "net_mf_vol": "净流向",
            }
            fields = [fin_keys.get(k, k) for k in first if first[k] is not None]
            return "、".join(fields) if fields else f"{len(data)}条记录"
        return f"{len(data)}条记录"
    return "有数据"


def _references_appendix(collection: dict[str, Any]) -> str:
    """引用来源附录。"""
    lines = ["---", "", "## 📚 引用来源（References）", ""]
    lines.append("| 维度 | 渠道 | 追溯路径 | 数据详情 |")
    lines.append("|------|------|----------|---------|")

    for dim in collection.get("dimensions", []):
        display = dim.get("display", dim.get("dimension", "?"))
        dim_data = dim.get("data")
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            meta = dim.get("_meta", {})
            icon = "✅" if dim_data is not None else "❌"
            qp = meta.get("query_params", "")
            src_name = meta.get("source", "?")
            detail = _data_fields(dim.get("dimension", ""), dim_data)
            status = detail if dim_data is not None else "不可用"
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
                detail = _data_fields(dim.get("dimension", ""), dim_data)
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ✅ {detail} |")
            elif error:
                lines.append(f"| {dim_label} | {src_name} | `{qp}` | ❌ {_sanitize_error(error, 55)} |")
            else:
                lines.append(f"| {dim_label} | {src_name} | — | ⏭️ 未尝试 |")

    return "\n".join(lines)


def _risk_footer() -> str:
    return f"""---

> ⚠️ **免责声明:** 本报告由 invest-A v{ENGINE_VERSION} 自动化引擎生成，仅供学习研究参考。
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

# ═══════════════════════════════════════════════════════════════════════════
# HTML 报告渲染（新版模板）
# ═══════════════════════════════════════════════════════════════════════════

_HTML_CSS = r"""
:root {
  --font-body: "Inter","PingFang SC","Noto Sans SC",system-ui,sans-serif;
  --font-mono: "IBM Plex Mono","SF Mono",monospace;
  --text-xs:clamp(.75rem,.7rem + .25vw,.875rem);
  --text-sm:clamp(.8125rem,.75rem + .3vw,.9375rem);
  --text-base:clamp(.9375rem,.88rem + .3vw,1.0625rem);
  --text-lg:clamp(1.0625rem,.95rem + .6vw,1.375rem);
  --text-xl:clamp(1.375rem,1.1rem + 1.4vw,2rem);
  --space-1:.25rem;--space-2:.5rem;--space-3:.75rem;--space-4:1rem;
  --space-5:1.25rem;--space-6:1.5rem;--space-8:2rem;--space-10:2.5rem;
  --r-sm:.25rem;--r-md:.5rem;--r-lg:.75rem;--r-xl:1.25rem;
  --trans:180ms cubic-bezier(.16,1,.3,1);
  --bg:#0d0f12;--sur:#111417;--sur2:#161a1f;--sur3:#1c2128;
  --bdr:rgba(255,255,255,.07);--bdr-hi:rgba(255,255,255,.12);
  --tx:#e2e8f0;--tx-m:#8892a4;--tx-f:#4a5568;
  --ac:#38bdf8;--ac-dim:rgba(56,189,248,.12);
  --up:#34d399;--up-d:rgba(52,211,153,.12);
  --dn:#f87171;--dn-d:rgba(248,113,113,.12);
  --wn:#fbbf24;--wn-d:rgba(251,191,36,.1);
  --c1:#38bdf8;--c2:#818cf8;--c3:#34d399;--c4:#f87171;--c5:#fb923c;
  --sh:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
}
[data-theme="light"]{
  --bg:#f4f6f9;--sur:#fff;--sur2:#f8fafc;--sur3:#f1f5f9;
  --bdr:rgba(0,0,0,.07);--bdr-hi:rgba(0,0,0,.12);
  --tx:#1a2030;--tx-m:#6b7a99;--tx-f:#a8b4cc;
  --ac:#0284c7;--ac-dim:rgba(2,132,199,.08);
  --up:#059669;--up-d:rgba(5,150,105,.08);
  --dn:#dc2626;--dn-d:rgba(220,38,38,.08);
  --wn:#d97706;--wn-d:rgba(217,119,6,.08);
  --sh:0 1px 2px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased;scroll-behavior:smooth;scroll-padding-top:52px}
body{font-family:var(--font-body);font-size:var(--text-base);color:var(--tx);background:var(--bg);min-height:100dvh;line-height:1.6}
button{cursor:pointer;background:none;border:none;font:inherit;color:inherit}
table{border-collapse:collapse;width:100%}
a{color:var(--ac);text-decoration:none}

/* layout */
.app{display:grid;grid-template-columns:200px 1fr;grid-template-rows:52px 1fr;min-height:100dvh}
.topbar{grid-column:1/-1;display:flex;align-items:center;gap:var(--space-3);padding:0 var(--space-6);height:52px;border-bottom:1px solid var(--bdr);background:var(--sur);position:sticky;top:0;z-index:100}
.sidebar{grid-row:2;background:var(--sur);border-right:1px solid var(--bdr);padding:var(--space-3) 0;position:sticky;top:52px;height:calc(100dvh - 52px);overflow-y:auto}
.main{grid-row:2;padding:var(--space-6) var(--space-8);display:flex;flex-direction:column;gap:var(--space-6)}

/* topbar */
.tl{display:flex;align-items:center;gap:var(--space-2);font-size:var(--text-xs);font-weight:700;letter-spacing:.08em;color:var(--tx-m);text-transform:uppercase}
.tl svg{color:var(--ac)}
.td{width:1px;height:18px;background:var(--bdr-hi)}
.tn{font-size:var(--text-base);font-weight:700}
.tc{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m);background:var(--sur3);padding:2px 8px;border-radius:var(--r-sm)}
.tp{font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600;margin-left:auto}
.tch{font-family:var(--font-mono);font-size:var(--text-xs);padding:2px 8px;border-radius:var(--r-sm)}
.badge{font-size:var(--text-xs);font-family:var(--font-mono);padding:2px 8px;border-radius:var(--r-sm);border:1px solid}
.b-ok{color:var(--up);border-color:var(--up-d);background:var(--up-d)}
.b-wn{color:var(--wn);border-color:var(--wn-d);background:var(--wn-d)}
.tbtn{width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:var(--r-md);color:var(--tx-m);transition:background var(--trans),color var(--trans)}
.tbtn:hover{background:var(--sur3);color:var(--tx)}

/* sidebar */
.sbl{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--tx-f);padding:var(--space-3) var(--space-3) var(--space-1)}
.sbi{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-2) var(--space-4);font-size:var(--text-sm);color:var(--tx-m);transition:background var(--trans),color var(--trans);cursor:pointer;border-left:2px solid transparent;text-decoration:none}
.sbi:hover{background:var(--sur3);color:var(--tx);text-decoration:none}
.sbi.active{color:var(--ac);background:var(--ac-dim);border-left-color:var(--ac)}
.sbi svg{flex-shrink:0;opacity:.7}

/* section */
.sh{display:flex;align-items:baseline;gap:var(--space-3);margin-bottom:var(--space-4)}
.st{font-size:var(--text-lg);font-weight:700}
.ss{font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono)}
.sd{flex:1;height:1px;background:var(--bdr)}

/* card */
.card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r-lg);padding:var(--space-5);box-shadow:var(--sh)}
.card-sm{padding:var(--space-4)}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--space-4)}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-4)}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-4)}
.g21{display:grid;grid-template-columns:2fr 1fr;gap:var(--space-4)}

/* kpi */
.kl{font-size:var(--text-xs);color:var(--tx-m);font-weight:500;text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-2)}
.kv{font-family:var(--font-mono);font-size:var(--text-xl);font-weight:600;line-height:1.1}
.ks{font-size:var(--text-xs);color:var(--tx-f);margin-top:var(--space-1);font-family:var(--font-mono)}

/* gauge */
.gr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.gr:last-child{border-bottom:none}
.gn{font-size:var(--text-xs);color:var(--tx-m);width:56px;flex-shrink:0}
.gtrack{flex:1;height:6px;background:var(--sur3);border-radius:3px;overflow:visible;position:relative}
.gfill{height:6px;border-radius:3px;position:relative;transition:width 1s cubic-bezier(.16,1,.3,1)}
.gmk{position:absolute;right:-3px;top:-3px;width:12px;height:12px;border-radius:50%;border:2px solid var(--sur)}
.gval{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx);width:64px;text-align:right;flex-shrink:0}
.gpct{font-family:var(--font-mono);font-size:var(--text-xs);width:44px;text-align:right;flex-shrink:0}

/* indicator pill */
.ipill{background:var(--sur2);border:1px solid var(--bdr);border-radius:var(--r-md);padding:var(--space-3)}
.iname{font-size:var(--text-xs);color:var(--tx-f);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-1)}
.ival{font-family:var(--font-mono);font-size:var(--text-base);font-weight:600}
.isig{font-size:var(--text-xs);margin-top:var(--space-1)}
.sig-bear{color:var(--dn)}.sig-bull{color:var(--up)}.sig-neutral{color:var(--wn)}

/* fin table */
.ft th{font-size:var(--text-xs);font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--tx-f);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi);text-align:right}
.ft th:first-child{text-align:left}
.ft td{font-family:var(--font-mono);font-size:var(--text-xs);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr);text-align:right;color:var(--tx-m)}
.ft td:first-child{text-align:left;color:var(--tx-f)}
.ft tr:last-child td{border-bottom:none;font-weight:600;color:var(--tx)}
.roe-hi{color:var(--up)!important}.roe-lo{color:var(--wn)!important}

/* flow */
.flr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.flr:last-child{border-bottom:none}
.fldate{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f);width:48px}
.flbar{height:6px;border-radius:3px;min-width:2px}
.fl-in{background:var(--up)}.fl-out{background:var(--dn)}
.flval{font-family:var(--font-mono);font-size:var(--text-xs);width:64px;text-align:right}
.fp{color:var(--up)}.fn{color:var(--dn)}

/* holder */
.hlr{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--bdr)}
.hlr:last-child{border-bottom:none}
.hlrk{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f);width:16px;text-align:right;flex-shrink:0}
.hln{flex:1;min-width:0}
.hlname{font-size:var(--text-xs);color:var(--tx-m);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hlbar{height:3px;border-radius:2px;background:var(--ac);margin-top:3px;transition:width .8s cubic-bezier(.16,1,.3,1)}
.hlpct{font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;flex-shrink:0}

/* ref */
.rtog{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-3) var(--space-4);background:var(--sur2);border-radius:var(--r-md);cursor:pointer;font-size:var(--text-xs);color:var(--tx-m);border:1px solid var(--bdr);user-select:none;transition:background var(--trans)}
.rtog:hover{background:var(--sur3)}
.rbody{display:none;margin-top:var(--space-3)}
.rbody.open{display:block}
.ref-ok{color:var(--up)}.ref-err{color:var(--dn)}
code{font-family:var(--font-mono);font-size:.85em;background:var(--sur3);padding:1px 5px;border-radius:var(--r-sm);color:var(--tx-m)}

/* verify */
.vnote{display:flex;align-items:flex-start;gap:var(--space-2);padding:var(--space-2) var(--space-3);background:var(--wn-d);border-radius:var(--r-sm);border-left:2px solid var(--wn);font-size:var(--text-xs);color:var(--tx-m);margin-top:var(--space-3)}

/* pending */
.pend{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:var(--space-3);padding:var(--space-10);background:var(--sur2);border-radius:var(--r-md);border:1px dashed var(--bdr-hi);text-align:center}
.pend svg{width:36px;height:36px;color:var(--tx-f)}
.pend-t{font-size:var(--text-sm);font-weight:600;color:var(--tx-m)}
.pend-d{font-size:var(--text-xs);color:var(--tx-f);max-width:32ch}

/* disclaimer */
.disc{font-size:var(--text-xs);color:var(--tx-f);padding:var(--space-4);background:var(--sur2);border-radius:var(--r-md);border:1px solid var(--bdr);line-height:1.8}
.disc strong{color:var(--wn)}

/* chart */
.cw{position:relative;height:220px}
.cw-sm{position:relative;height:160px}

/* scrollbar */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bdr-hi);border-radius:3px}

@media(max-width:900px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .main{padding:var(--space-4)}
  .g4{grid-template-columns:repeat(2,1fr)}
  .g3,.g2,.g21{grid-template-columns:1fr}
}
"""


# ── Chart.js 本地加载 ─────────────────────────────────────────────────────

_CHART_JS_CACHE: str | None = None


def _load_chart_js() -> str:
    """读取本地 chart.umd.min.js。离线可用，避免 CDN 依赖。

    优先从本地资产目录读取；回退为空字符串（图表不渲染，其余内容正常）。
    """
    global _CHART_JS_CACHE
    if _CHART_JS_CACHE is not None:
        return _CHART_JS_CACHE

    p = Path(__file__).resolve().parent / "assets" / "chart.umd.min.js"
    try:
        _CHART_JS_CACHE = p.read_text(encoding="utf-8")
        return _CHART_JS_CACHE
    except Exception:
        _CHART_JS_CACHE = ""
        return ""


# ── Section builders ──────────────────────────────────────────────────────


def _html_topbar(
    symbol: str, name: str, price_str: str, change_str: str,
    price_color: str, chg_color: str, summary: dict,
) -> str:
    av = summary.get("available", 0)
    total = summary.get("total", 0)
    deg = summary.get("degraded", 0)
    badge_cls = "b-ok" if av >= total * 0.5 else "b-wn"
    badge_text = f"{av}/{total} 维度" + (f"（{deg} 降级）" if deg else "")
    ver_badge = f"v{ENGINE_VERSION}"
    return f'''<header class="topbar">
  <div class="tl">
    <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
      <rect x="1.5" y="1.5" width="19" height="19" rx="4" stroke="currentColor" stroke-width="1.5"/>
      <path d="M7 15.5L11 7L15 15.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M8.8 12.5H13.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
    invest-A
  </div>
  <div class="td"></div>
  <span class="tn">{_html_mod.escape(name or symbol)}</span>
  <span class="tc">{_html_mod.escape(symbol)}</span>
  <span class="tp" style="color:{price_color}">{price_str}</span>
  <span class="tch" style="color:{chg_color};background:{chg_color.replace("var(--up)","var(--up-d)").replace("var(--dn)","var(--dn-d)")}">{change_str}</span>
  <span class="badge {badge_cls}">{badge_text}</span>
  <span class="badge b-ok">{ver_badge}</span>
  <button class="tbtn" data-theme-toggle aria-label="切换主题">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
</header>'''


def _html_sidebar() -> str:
    return '''<nav class="sidebar">
  <div class="sbl">概览</div>
  <a class="sbi active" href="#overview"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>行情快照</a>
  <a class="sbi" href="#valuation"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>估值分析</a>
  <div class="sbl">财务</div>
  <a class="sbi" href="#financials"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>财务指标</a>
  <div class="sbl">市场</div>
  <a class="sbi" href="#technicals"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 17 9 11 13 15 21 7"/></svg>技术指标</a>
  <a class="sbi" href="#northbound"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M2 12l10-10 10 10"/></svg>北向资金</a>
  <a class="sbi" href="#holders"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>股东结构</a>
  <div class="sbl">分析</div>
  <a class="sbi" href="#events"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>事件 &amp; 综合</a>
  <a class="sbi" href="#refs"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>数据来源</a>
</nav>'''


def _html_overview(
    price_str: str, change_str: str, price_color: str, chg_color: str,
    volume_str: str, turover_str: str, atr_str: str, vol5d_str: str,
    dv_str: str, ma250_str: str, ma250_pos: str, kline_days: int,
) -> str:
    # 默认值
    price_str = price_str or "--"
    change_str = change_str or "--"
    volume_str = volume_str or "--"
    turover_str = turover_str or "--"
    atr_str = atr_str or "--"
    vol5d_str = vol5d_str or "--"
    dv_str = dv_str or "--"
    ma250_str = ma250_str or "--"
    ma250_color = "var(--up)" if "上方" in ma250_pos else ("var(--dn)" if "下方" in ma250_pos else "var(--tx)")
    return f'''<section id="overview">
  <div class="sh"><span class="st">行情快照</span><div class="sd"></div><span class="ss">交易日 {kline_days}d</span></div>
  <div class="g4">
    <div class="card card-sm"><div class="kl">最新价</div><div class="kv" style="color:{price_color}">{price_str}</div><div class="ks">较昨收 {change_str}</div></div>
    <div class="card card-sm"><div class="kl">换手率</div><div class="kv">{turover_str}</div><div class="ks">ATR(14) = {atr_str}</div></div>
    <div class="card card-sm"><div class="kl">近5日均量</div><div class="kv" style="font-size:var(--text-lg)">{volume_str}</div><div class="ks">MA250 = {ma250_str} <span style="color:{ma250_color}">{ma250_pos}</span></div></div>
    <div class="card card-sm"><div class="kl">股息率</div><div class="kv">{dv_str.split("%")[0] if "%" in dv_str else dv_str}%</div><div class="ks">dv_ratio 最近交易日</div></div>
  </div>
</section>'''


def _html_valuation(
    pe_pct: str, pe_val: str, pe_color: str,
    pb_pct: str, pb_val: str, pb_color: str,
    ps_pct: str, ps_val: str, ps_color: str,
    pe_median: str, pb_median: str, zone_signal: str, zone_color: str,
    n_samples: int, window_label: str,
    pe_above_median: bool, pb_above_median: bool,
) -> str:
    if not pe_val or pe_val == "--":
        return f'''<section id="valuation">
  <div class="sh"><span class="st">估值分析</span><div class="sd"></div><span class="ss">数据不可得</span></div>
  <div class="card" style="padding:var(--space-10);text-align:center">
    <div style="font-size:var(--text-sm);color:var(--tx-f)">估值维度无数据，请配置 Tushare Token 获取历史估值序列。</div>
  </div>
</section>'''
    pe_pct_s = pe_pct or "0"
    pb_pct_s = pb_pct or "0"
    ps_pct_s = ps_pct or "0"
    pe_v = pe_val or "0"
    pb_v = pb_val or "0"
    ps_v = ps_val or "0"

    pe_med_str = f"{pe_median}x" if pe_median and pe_median != "--" else "--"
    pb_med_str = f"{pb_median}x" if pb_median and pb_median != "--" else "--"

    pe_below = "当前低于中位数" if not pe_above_median else "当前高于中位数"
    pb_below = "当前低于中位数" if not pb_above_median else "当前高于中位数"

    return f'''<section id="valuation">
  <div class="sh"><span class="st">估值分析</span><div class="sd"></div><span class="ss">{window_label}分位 · {n_samples}交易日</span></div>
  <div class="g21">
    <div class="card">
      <div style="font-size:var(--text-xs);color:var(--tx-f);margin-bottom:var(--space-4)">分位越低代表估值越便宜（相对{window_label}）</div>
      <div class="gr">
        <div class="gn">PE(TTM)</div>
        <div class="gtrack"><div class="gfill" style="width:{pe_pct_s}%;background:var(--c1)"><div class="gmk" style="background:var(--c1)"></div></div></div>
        <div class="gval">{pe_v}</div><div class="gpct" style="color:var(--c1)">{pe_pct_s}%</div>
      </div>
      <div class="gr">
        <div class="gn">PB</div>
        <div class="gtrack"><div class="gfill" style="width:{pb_pct_s}%;background:var(--c2)"><div class="gmk" style="background:var(--c2)"></div></div></div>
        <div class="gval">{pb_v}</div><div class="gpct" style="color:var(--c2)">{pb_pct_s}%</div>
      </div>
      <div class="gr">
        <div class="gn">PS(TTM)</div>
        <div class="gtrack"><div class="gfill" style="width:{ps_pct_s}%;background:var(--wn)"><div class="gmk" style="background:var(--wn)"></div></div></div>
        <div class="gval">{ps_v}</div><div class="gpct" style="color:var(--wn)">{ps_pct_s}%</div>
      </div>
      <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>PE 亏损期已剔除；行业相对估值 v0.1.2 未覆盖，分位不构成买卖判断。</div>
    </div>
    <div class="card">
      <div style="font-size:var(--text-xs);color:var(--tx-f);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-4)">历史中位数</div>
      <div style="display:flex;flex-direction:column;gap:var(--space-5)">
        <div><div class="kl">PE 中位数</div><div style="font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600">{pe_med_str}</div><div class="ks">{pe_below}</div></div>
        <div><div class="kl">PB 中位数</div><div style="font-family:var(--font-mono);font-size:var(--text-lg);font-weight:600">{pb_med_str}</div><div class="ks">{pb_below}</div></div>
        <div><div class="kl">综合信号</div><div style="font-size:var(--text-base);font-weight:600;color:{zone_color}">{zone_signal}</div></div>
      </div>
    </div>
  </div>
</section>'''


def _html_financials(fin_table_html: str, fin_note: str) -> str:
    return f'''<section id="financials">
  <div class="sh"><span class="st">财务指标</span><div class="sd"></div><span class="ss">近8期季报</span></div>
  <div class="g2">
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">ROE / EPS 趋势</div>
      <div class="cw"><canvas id="roeChart"></canvas></div>
    </div>
    <div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">扣非净利润（亿元）</div>
      <div class="cw"><canvas id="profitChart"></canvas></div>
    </div>
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    {fin_table_html}
    <div class="vnote"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>{_html_mod.escape(fin_note)}</div>
  </div>
</section>'''


def _html_technicals(
    macd_html: str, rsi_kdj_html: str, boll_html: str, ma_grid_html: str,
    tech_note: str, tech_source: str,
) -> str:
    return f'''<section id="technicals">
  <div class="sh"><span class="st">技术指标</span><div class="sd"></div><span class="ss">{_html_mod.escape(tech_source)}</span></div>
  <div class="g3">
    {macd_html}
    {rsi_kdj_html}
    {boll_html}
  </div>
  <div class="card" style="margin-top:var(--space-4)">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">均线排列 <span style="font-size:var(--text-xs);font-weight:400;margin-left:var(--space-2)" id="maTrendLabel"></span></div>
    {ma_grid_html}
  </div>
</section>'''


def _html_northbound(nb_html: str) -> str:
    return f'''<section id="northbound">
  <div class="sh"><span class="st">北向资金</span><div class="sd"></div><span class="ss">近7日净流向 · moneyflow（估算值）</span></div>
  <div class="card">
    {nb_html}
  </div>
</section>'''


def _html_holders(holders_html: str) -> str:
    return f'''<section id="holders">
  <div class="sh"><span class="st">股东结构</span><div class="sd"></div><span class="ss">前十大流通股东 · 最新报告期</span></div>
  <div class="card">
    <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">持股比例</div>
    {holders_html}
    <div class="vnote" style="margin-top:var(--space-3)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>报告期数据约有1季度滞后，以公司公告为准。</div>
  </div>
</section>'''


def _html_events() -> str:
    return '''<section id="events">
  <div class="sh"><span class="st">事件分析 &amp; 综合判断</span><div class="sd"></div><span class="ss">待 Claude 分析阶段填写</span></div>
  <div class="g2">
    <div class="pend"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg><div class="pend-t">事件分层分析</div><div class="pend-d">由 Claude 通过 WebSearch 补充近期公告、行业动态、重大事件</div></div>
    <div class="pend"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><div class="pend-t">综合研判</div><div class="pend-d">等待 Claude 分析阶段填写</div></div>
  </div>
</section>'''


def _html_refs(ref_rows_html: str) -> str:
    return f'''<section id="refs">
  <div class="sh"><span class="st">数据来源</span><div class="sd"></div><span class="ss">可追溯调用路径</span></div>
  <div class="rtog" onclick="this.nextElementSibling.classList.toggle('open');this.querySelector('.ra').textContent=this.nextElementSibling.classList.contains('open')?'▴':'▾'">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
    展开数据追溯表<span class="ra" style="margin-left:auto">▾</span>
  </div>
  <div class="rbody">
    <div class="card" style="margin-top:var(--space-3)">
      <table>
        <thead><tr>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">维度</td>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">接口</td>
          <td style="font-size:var(--text-xs);font-weight:600;color:var(--tx-f);text-transform:uppercase;padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--bdr-hi)">数据详情</td>
        </tr></thead>
        <tbody>
          {ref_rows_html}
        </tbody>
      </table>
    </div>
  </div>
</section>'''


def _html_risk_banner() -> str:
    return (
        f'<div class="disc" style="margin-bottom:var(--space-4);border-left:3px solid var(--wn)">'
        f'<strong>⚠ 风险提示</strong> — 本报告由 invest-A v{ENGINE_VERSION} 自动化引擎生成，'
        f'仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        f'</div>'
    )


def _html_disclaimer() -> str:
    return (
        f'<div class="disc"><strong>⚠ 免责声明</strong> — 本报告由 invest-A v{ENGINE_VERSION} 自动化引擎生成，'
        f'仅供学习研究参考，<strong>不构成任何投资建议、买卖指令或目标价预测</strong>。'
        f'所有技术指标均为市场状态描述，非交易信号。'
        f'数据来源见上文 References 表，可能与实际公告存在差异，请以公司公告和交易所数据为准。'
        f'</div>'
    )


# ── Data helpers ──────────────────────────────────────────────────────────


def _extract_financials_data(dims: dict) -> tuple[list, list, list, list, str, str]:
    """从 dimensions 提取财务数据，返回 (labels, roe, eps, profit, table_html, note)。"""
    fin = _get_dim_data(dims, "financials")
    if not fin or not isinstance(fin, list) or not fin:
        return [], [], [], [], "<div style='padding:2rem;text-align:center;color:var(--tx-f)'>财务数据不可得</div>", "财务数据不可得"

    from lib.technical import sort_kline_asc
    fin = sort_kline_asc(fin)
    recent = fin[-8:] if len(fin) >= 8 else fin

    labels = []
    roe_data = []
    eps_data = []
    profit_data = []
    for r in recent:
        ed = str(r.get("end_date", ""))
        if len(ed) >= 7:
            labels.append(ed[2:4] + "Q" + str((int(ed[4:6]) - 1) // 3 + 1))
        else:
            labels.append(ed)
        roe_v = r.get("roe")
        roe_data.append(round(roe_v, 2) if roe_v is not None else None)
        eps_v = r.get("eps")
        eps_data.append(round(eps_v, 2) if eps_v is not None else None)
        pd_v = r.get("profit_dedt")
        profit_data.append(round(pd_v / 1e8, 2) if pd_v is not None else None)

    # 财务表格 HTML
    rows_html = ""
    for r in recent:
        ed = str(r.get("end_date", ""))
        if len(ed) >= 7:
            qlabel = ed[:4] + "-" + ed[4:6] + "-" + ed[6:8] if len(ed) == 8 else ed
        else:
            qlabel = ed
        roe_v = r.get("roe")
        roe_str = f"{roe_v:.2f}" if roe_v is not None else "-"
        eps_str = f"{eps_v:.2f}" if (eps_v := r.get("eps")) is not None else "-"
        pd_v = r.get("profit_dedt")
        pd_str = _fmt_v2(pd_v) if pd_v is not None else "-"
        rev_v = r.get("revenue")
        rev_str = _fmt_v2(rev_v) if rev_v is not None else "-"
        np_v = r.get("net_profit")
        np_str = _fmt_v2(np_v) if np_v is not None else "-"
        # ROE 高/低标记
        roe_cls = ""
        if len(recent) >= 3:
            all_roe = [x.get("roe") for x in recent if x.get("roe") is not None]
            if all_roe and roe_v is not None:
                avg = sum(all_roe) / len(all_roe)
                roe_cls = ' class="roe-hi"' if roe_v > avg * 1.1 else (' class="roe-lo"' if roe_v < avg * 0.9 else "")
        rows_html += f"<tr><td>{qlabel}</td><td{roe_cls}>{roe_str}</td><td>{eps_str}</td><td>{pd_str}</td><td>{rev_str}</td><td>{np_str}</td></tr>\n"

    table_html = f'''<table class="ft">
      <thead><tr><th>报告期</th><th>ROE(%)</th><th>EPS(元)</th><th>扣非净利润</th><th>营收</th><th>净利润</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>'''

    note = "营收/净利润字段为空（akshare接口降级）。" if not any(r.get("revenue") for r in recent) else "财务数据来自第三方数据源，应与公司年报/季报交叉核对。"
    return labels, roe_data, eps_data, profit_data, table_html, note


def _extract_valuation_data(dims: dict) -> dict:
    """提取估值数据用于 gauge 和 JS。"""
    val_data = _get_dim_data(dims, "valuation")
    result: dict = {
        "pe_pct": None, "pe_val": None, "pe_color": "var(--c1)",
        "pb_pct": None, "pb_val": None, "pb_color": "var(--c2)",
        "ps_pct": None, "ps_val": None, "ps_color": "var(--wn)",
        "pe_median": None, "pb_median": None,
        "zone_signal": "--", "zone_color": "var(--tx-m)",
        "n_samples": 0, "window_label": "近5年",
        "pe_above_median": False, "pb_above_median": False,
    }
    if not val_data or not isinstance(val_data, list) or not val_data:
        return result

    from lib.technical import sort_kline_asc
    from lib.valuation import valuation_summary

    vs = sort_kline_asc(val_data)
    pe_seq = [r.get("pe_ttm") for r in vs]
    pb_seq = [r.get("pb") for r in vs]
    ps_seq = [r.get("ps_ttm") or r.get("ps") for r in vs]
    dv = next((r.get("dv_ratio") for r in reversed(vs) if r.get("dv_ratio") is not None), None)

    if len(vs) >= 1250:
        wl = "近5年"
    elif len(vs) >= 250:
        wl = f"近{len(vs) // 250}年"
    else:
        wl = "上市以来（数据有限）"

    summary = valuation_summary(pe_seq, pb_seq, ps_seq=ps_seq, dv_ratio=dv, window_label=wl)
    result["window_label"] = wl
    result["n_samples"] = summary.get("n_samples", 0)

    pe = summary.get("pe", {})
    if pe.get("current") is not None:
        result["pe_val"] = f"{pe['current']:.2f}x"
        result["pe_pct"] = f"{pe['pct']:.1f}" if pe.get("pct") is not None else None
        result["pe_median"] = f"{pe['median']:.2f}" if pe.get("median") is not None else None
        result["pe_above_median"] = (pe.get("current") or 0) > (pe.get("median") or float("inf"))

    pb = summary.get("pb", {})
    if pb.get("current") is not None:
        result["pb_val"] = f"{pb['current']:.2f}x"
        result["pb_pct"] = f"{pb['pct']:.1f}" if pb.get("pct") is not None else None
        result["pb_median"] = f"{pb['median']:.2f}" if pb.get("median") is not None else None
        result["pb_above_median"] = (pb.get("current") or 0) > (pb.get("median") or float("inf"))

    ps = summary.get("ps", {})
    if ps.get("current") is not None:
        result["ps_val"] = f"{ps['current']:.2f}x"
        result["ps_pct"] = f"{ps['pct']:.1f}" if ps.get("pct") is not None else None

    # 综合信号
    zones = []
    if pe.get("zone"):
        zones.append(pe["zone"])
    if pb.get("zone"):
        zones.append(pb["zone"])
    if any("偏" in z for z in zones):
        result["zone_signal"] = "偏低" if zones.count("偏低") > zones.count("偏高") else ("偏高" if zones.count("偏高") > zones.count("偏低") else "适中区间")
        if "偏低" in result["zone_signal"]:
            result["zone_color"] = "var(--up)"
        elif "偏高" in result["zone_signal"]:
            result["zone_color"] = "var(--dn)"
        else:
            result["zone_color"] = "var(--wn)"
    else:
        result["zone_signal"] = "适中区间"
        result["zone_color"] = "var(--wn)"
    return result


def _extract_technical_html(dims: dict) -> dict:
    """提取技术指标数据，返回结构化 dict 和 HTML 片段。"""
    kd = _get_dim_data(dims, "kline")
    result: dict = {
        "macd_html": "", "rsi_kdj_html": "", "boll_html": "",
        "ma_grid_html": "", "trend_label": "", "atr_14": None,
        "vol5d": None, "ma250_val": None, "ma250_pos": "",
        "kline_days": 0, "tech_source": "",
        "ma_20_slope": None, "ma_60_slope": None,
    }
    if not kd or not isinstance(kd, list) or not kd:
        empty = '<div style="padding:2rem;text-align:center;color:var(--tx-f);grid-column:1/-1">K 线数据不可得</div>'
        result.update(macd_html=empty, rsi_kdj_html="", boll_html="", ma_grid_html=empty)
        return result

    from lib.technical import compute, sort_kline_asc
    kd = sort_kline_asc(kd)
    result["kline_days"] = len(kd)
    meta = _get_dim_meta(dims, "kline")
    result["tech_source"] = f"不复权 · {meta.get('source', '未知')}"

    tech = compute(kd)
    if "error" in tech:
        err = tech.get("message", "未知错误")
        err_html = f'<div style="padding:2rem;text-align:center;color:var(--dn);grid-column:1/-1">技术指标计算失败: {sanitize_error(err, 80)}</div>'
        result.update(macd_html=err_html, rsi_kdj_html="", boll_html="", ma_grid_html=err_html)
        return result

    closes = [r.get("close", 0) or 0 for r in kd]
    latest_close = closes[-1] if closes else 0

    # MACD
    macd = tech.get("momentum", {}).get("macd", {})
    if macd.get("available"):
        dif_v = macd["dif"]
        dea_v = macd["dea"]
        hist_v = macd["histogram"]
        cross = macd.get("cross", {})
        cross_desc = cross.get("desc", "")
        has_bear = "下方" in cross_desc or "下穿" in cross_desc
        has_bull = "上方" in cross_desc or "上穿" in cross_desc
        macd_col = "var(--dn)" if has_bear else ("var(--up)" if has_bull else "var(--tx)")
        hist_trend = macd.get("histogram_trend", "")
        result["macd_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">MACD <span style="font-size:var(--text-xs);color:var(--tx-f);font-weight:400">(12,26,9)</span></div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2)">
        <div class="ipill"><div class="iname">DIF</div><div class="ival" style="color:{macd_col}">{dif_v:.2f}</div></div>
        <div class="ipill"><div class="iname">DEA</div><div class="ival" style="color:{macd_col}">{dea_v:.2f}</div></div>
        <div class="ipill"><div class="iname">柱</div><div class="ival" style="color:{macd_col}">{hist_v:.2f}</div></div>
      </div>
      <div style="margin-top:var(--space-3);font-size:var(--text-xs);color:{macd_col}">{'▼' if has_bear else '▲'} {cross_desc}{(' · ' + hist_trend) if hist_trend else ''}</div>
    </div>'''
    else:
        reason = macd.get("reason", "MACD 不可得")
        result["macd_html"] = f'<div class="card"><div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">MACD</div><div style="font-size:var(--text-xs);color:var(--tx-f);padding:1rem 0;text-align:center">{reason}</div></div>'

    # RSI / KDJ
    rsi = tech.get("overbought_oversold", {}).get("rsi", {})
    kdj = tech.get("overbought_oversold", {}).get("kdj", {})
    rsi_pills = ""
    for p in ("6", "12", "24"):
        r = rsi.get(p, {})
        if r.get("available"):
            v = r["value"]
            zone = r.get("zone", "中性")
            sig_cls = "sig-bear" if zone == "偏低" else ("sig-bull" if zone == "偏高" else "sig-neutral")
            v_color = "var(--dn)" if zone == "偏低" else ("var(--up)" if zone == "偏高" else "var(--tx)")
            rsi_pills += f'<div class="ipill"><div class="iname">RSI({p})</div><div class="ival" style="color:{v_color}">{v:.1f}</div><div class="isig {sig_cls}">{zone}</div></div>'
        else:
            rsi_pills += f'<div class="ipill"><div class="iname">RSI({p})</div><div class="ival" style="font-size:var(--text-xs);color:var(--tx-f)">--</div><div class="isig sig-neutral">N/A</div></div>'

    kdj_pills = ""
    kdj_color = "var(--tx)"
    if kdj.get("available"):
        k_val = kdj["k"]
        d_val = kdj["d"]
        j_val = kdj["j"]
        kdj_color = "var(--dn)" if j_val < 20 else ("var(--up)" if j_val > 80 else "var(--tx)")
        kdj_pills = f'''<div class="ipill"><div class="iname">K</div><div class="ival" style="color:{kdj_color}">{k_val:.1f}</div></div>
        <div class="ipill"><div class="iname">D</div><div class="ival" style="color:{kdj_color}">{d_val:.1f}</div></div>
        <div class="ipill"><div class="iname">J</div><div class="ival" style="color:{kdj_color}">{j_val:.1f}</div></div>'''
    else:
        kdj_pills = '<div class="ipill" style="grid-column:1/-1;text-align:center"><div class="iname">KDJ</div><div style="font-size:var(--text-xs);color:var(--tx-f)">不可得</div></div>'

    result["rsi_kdj_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">RSI / KDJ</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2);margin-bottom:var(--space-2)">
        {rsi_pills}
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space-2)">
        {kdj_pills}
      </div>
    </div>'''

    # BOLL
    boll = tech.get("volatility", {}).get("boll", {})
    if boll.get("available"):
        upper = boll["upper"]
        mid = boll["mid"]
        lower = boll["lower"]
        pos = boll.get("position", "")
        pos_pct = 50
        if pos == "上轨上方":
            pos_pct = 5
        elif pos == "中轨上方":
            pos_pct = 35
        elif pos == "中轨附近":
            pos_pct = 50
        elif pos == "中轨下方":
            pos_pct = 65
        elif pos == "下轨下方":
            pos_pct = 90
        boll_range = upper - lower
        if boll_range > 0:
            pos_pct = max(5, min(95, (latest_close - lower) / boll_range * 100))

        if latest_close <= mid:
            boll_cls = "var(--dn)" if latest_close <= lower * 1.02 else "var(--tx)"
        else:
            boll_cls = "var(--up)" if latest_close >= upper * 0.98 else "var(--tx)"

        result["boll_html"] = f'''<div class="card">
      <div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">布林带 <span style="font-size:var(--text-xs);color:var(--tx-f);font-weight:400">(20,2)</span></div>
      <div style="display:flex;flex-direction:column;gap:var(--space-2)">
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">上轨</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{upper:.2f}</span></div>
        <div style="position:relative;height:48px;background:linear-gradient(180deg,rgba(56,189,248,.04) 0%,rgba(56,189,248,.14) 50%,rgba(56,189,248,.04) 100%);border-radius:var(--r-sm);border:1px solid var(--bdr)">
          <div style="position:absolute;left:0;right:0;top:50%;height:1px;background:rgba(56,189,248,.25)"></div>
          <div style="position:absolute;left:{pos_pct:.0f}%;top:83%;transform:translate(-50%,-50%);width:8px;height:8px;border-radius:50%;background:{boll_cls};box-shadow:0 0 8px {boll_cls}"></div>
        </div>
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">中轨 MA20</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{mid:.2f}</span></div>
        <div style="display:flex;justify-content:space-between"><span style="font-size:var(--text-xs);color:var(--tx-f)">下轨</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-m)">{lower:.2f}</span></div>
        <div style="display:flex;justify-content:space-between;border-top:1px solid var(--bdr);padding-top:var(--space-2);margin-top:2px">
          <span style="font-size:var(--text-xs);color:{boll_cls}">收盘（{pos}）</span>
          <span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;color:{boll_cls}">{latest_close:.2f}</span>
        </div>
      </div>
    </div>'''
    else:
        reason = boll.get("reason", "BOLL 不可得")
        result["boll_html"] = f'<div class="card"><div style="font-size:var(--text-sm);font-weight:600;margin-bottom:var(--space-3)">布林带</div><div style="font-size:var(--text-xs);color:var(--tx-f);padding:1rem 0;text-align:center">{reason}</div></div>'

    # MA grid
    trend = tech.get("trend", {})
    ma = trend.get("ma", {})
    alignment = trend.get("alignment", {})
    slopes = trend.get("slope", {})
    result["trend_label"] = alignment.get("trend_label", "")

    ma_pills = ""
    for p in (5, 10, 20, 60, 120, 250):
        vals = ma.get(str(p), [])
        if vals and vals[-1] is not None:
            ma_v = vals[-1]
            slope = slopes.get(str(p))
            slope_str = f"斜率{'+' if slope and slope >= 0 else ''}{slope:.1f}%" if slope is not None else "--"
            pos_str = "上方" if latest_close > ma_v else ("下方" if latest_close < ma_v else "附近")
            pos_color = "var(--up)" if pos_str == "上方" else ("var(--dn)" if pos_str == "下方" else "var(--tx)")
            slp_color = "var(--up)" if slope and slope >= 0 else ("var(--dn)" if slope and slope < 0 else "var(--tx)")
            border_extra = ';border-color:rgba(56,189,248,.25)' if p == 250 else ''
            name_color = ' style="color:var(--ac)"' if p == 250 else ''
            ma_pills += f'<div class="ipill" style="text-align:center{border_extra}"><div class="iname"{name_color}>MA{p}</div><div style="font-family:var(--font-mono);font-size:var(--text-sm);color:{pos_color}">{ma_v:.2f}</div><div style="font-size:var(--text-xs);color:{slp_color}">{pos_str} · {slope_str}</div></div>'
        else:
            avail = trend.get("ma_availability", {}).get(str(p), "")
            err_txt = avail or "数据不足"
            ma_pills += f'<div class="ipill" style="text-align:center;opacity:.5"><div class="iname">MA{p}</div><div style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--tx-f)">{err_txt}</div></div>'

    result["ma_grid_html"] = f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:var(--space-3)">{ma_pills}</div>'

    # ATR
    atr = tech.get("volatility", {}).get("atr", {})
    if atr.get("available"):
        result["atr_14"] = f"{atr['value']:.2f}"

    # Volume
    vol_info = tech.get("volume", {})
    result["vol5d"] = vol_info.get("avg_vol_5d")

    # MA250
    ma250_vals = ma.get("250", [])
    if ma250_vals and ma250_vals[-1] is not None:
        result["ma250_val"] = f"{ma250_vals[-1]:.2f}"
        result["ma250_pos"] = "上方" if latest_close > ma250_vals[-1] else ("下方" if latest_close < ma250_vals[-1] else "附近")

    return result


def _extract_northbound_data(dims: dict) -> dict:
    """提取北向资金数据。"""
    nb = _get_dim_data(dims, "northbound")
    result: dict = {
        "flow_data": [], "total_flow": 0, "pos_days": 0, "total_days": 0,
        "has_data": False,
    }
    if not nb or not isinstance(nb, list) or not nb:
        return result

    from lib.technical import sort_kline_asc
    nb = sort_kline_asc(nb)
    recent = nb[-7:] if len(nb) >= 7 else nb
    result["total_days"] = len(recent)
    flow_total = 0
    pos = 0
    for r in recent:
        td = str(r.get("trade_date", ""))
        if len(td) >= 10:
            md = td[5:10]
        elif len(td) >= 8:
            md = td[4:6] + "-" + td[6:8]
        else:
            md = td
        nv = r.get("net_mf_vol", 0) or 0
        flow_total += nv
        if nv > 0:
            pos += 1
        result["flow_data"].append([md, round(nv, 2), td, None])
    result["total_flow"] = round(flow_total, 2)
    result["pos_days"] = pos
    result["has_data"] = True
    return result


def _extract_holders_data(dims: dict) -> dict:
    """提取股东数据（最新报告期前十大）。"""
    sh = _get_dim_data(dims, "shareholders")
    result: dict = {"holders": [], "has_data": False}
    if not sh or not isinstance(sh, list) or not sh:
        return result
    result["holders"] = [
        (str(r.get("holder_name", "?")), r.get("hold_ratio", 0) or 0)
        for r in sh[:10]
    ]
    result["has_data"] = bool(result["holders"])
    return result


def _extract_refs_data(collection: dict) -> list[tuple[str, str, bool, str]]:
    """提取数据追溯信息，返回 [(维度, 接口, 是否可用, 详情), ...]。"""
    refs = []
    for dim in collection.get("dimensions", []):
        display = dim.get("display", dim.get("dimension", "?"))
        dn = dim.get("dimension", "")
        dim_data = dim.get("data")
        all_src = dim.get("_meta", {}).get("all_sources")
        if not all_src:
            meta = dim.get("_meta", {})
            qp = meta.get("query_params", "")
            src_name = meta.get("source", "?")
            avail = dim_data is not None
            detail = _data_fields(dn, dim_data) if avail else ""
            refs.append((display, f"{src_name}: {qp}" if qp else src_name, avail, detail))
        else:
            for s in all_src:
                sn = s.get("source", "?")
                qp = s.get("query_params", "")
                avail = s.get("data_available", False)
                # all_sources 中每个源有独立 data 吗？没有——只有 data_available 布尔。
                # 同一维度下所有源共享 dim_data，但为保持列准确，失败源标为空。
                detail = _data_fields(dn, dim_data) if avail else ""
                refs.append((display, f"{sn}: {qp}" if qp else sn, avail, detail))
    return refs


# ── HTML 内联脚本（普通字符串，避免 f-string 花括号转义错误） ─────────────

_HTML_APP_SCRIPT_LOGIC = r"""
// theme
(function(){
  const btn=document.querySelector('[data-theme-toggle]'),html=document.documentElement;
  let t='dark';html.setAttribute('data-theme',t);
  btn&&btn.addEventListener('click',()=>{
    t=t==='dark'?'light':'dark';html.setAttribute('data-theme',t);
    btn.innerHTML=t==='dark'?'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>':'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
    renderCharts();
  });
})();

// sidebar active
document.querySelectorAll('.sbi').forEach(el=>el.addEventListener('click',()=>{
  document.querySelectorAll('.sbi').forEach(e=>e.classList.remove('active'));
  el.classList.add('active');
}));

// trend label
const tl=document.getElementById('maTrendLabel');
if(tl&&trendLabel) tl.textContent=trendLabel;

// charts
let charts={};
function renderCharts(){
  Object.values(charts).forEach(c=>c.destroy());charts={};
  const isDark=document.documentElement.getAttribute('data-theme')!=='light';
  const tc=isDark?'#8892a4':'#6b7a99',gc=isDark?'rgba(255,255,255,.06)':'rgba(0,0,0,.06)';
  const tt={backgroundColor:isDark?'#1c2128':'#fff',titleColor:isDark?'#e2e8f0':'#1a2030',bodyColor:tc,borderColor:isDark?'rgba(255,255,255,.1)':'rgba(0,0,0,.1)',borderWidth:1};
  const xs={ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},grid:{color:'transparent'}};
  const ys={ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},grid:{color:gc}};

  if(finLabels.length>0){
    charts.roe=new Chart(document.getElementById('roeChart'),{type:'line',data:{labels:finLabels,datasets:[{label:'ROE(%)',data:roeData,borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.15)',fill:true,tension:.35,pointRadius:4,pointBackgroundColor:'#38bdf8'},{label:'EPS(元)',data:epsData,borderColor:'#818cf8',borderDash:[4,4],tension:.35,pointRadius:3,pointBackgroundColor:'#818cf8',yAxisID:'y2'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'top',labels:{color:tc,font:{size:11},boxWidth:10,padding:10}},tooltip:{...tt,mode:'index',intersect:false}},scales:{x:xs,y:ys,y2:{position:'right',ticks:{color:'#818cf8',font:{family:'IBM Plex Mono',size:10}},grid:{color:'transparent'}}}}});
    const pVals=profitData.filter(v=>v!=null);
    const pAvg=pVals.length?pVals.reduce((a,b)=>a+b,0)/pVals.length:0;
    charts.profit=new Chart(document.getElementById('profitChart'),{type:'bar',data:{labels:finLabels,datasets:[{data:profitData,backgroundColor:profitData.map(v=>v==null?'rgba(128,128,128,.3)':(v<pAvg?'rgba(248,113,113,.5)':'rgba(52,211,153,.5)')),borderColor:profitData.map(v=>v==null?'#666':(v<pAvg?'#f87171':'#34d399')),borderWidth:1,borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{...tt}},scales:{x:xs,y:ys}}});
  }

  if(flowData.length>0){
    const fLabels=flowData.map(d=>d[0]);
    const fVals=flowData.map(d=>Math.round(d[1]/10000*100)/100);
    const fClose=closePriceSeries.slice(-flowData.length);
    charts.flow=new Chart(document.getElementById('flowChart'),{
      type:'bar',
      data:{labels:fLabels,datasets:[
        {type:'bar',label:'日净流向(万)',data:fVals,backgroundColor:fVals.map(v=>v>0?'rgba(52,211,153,0.75)':'rgba(248,113,113,0.75)'),borderColor:fVals.map(v=>v>0?'#34d399':'#f87171'),borderWidth:1,borderRadius:3,yAxisID:'yFlow',order:2},
        {type:'line',label:'收盘价',data:fClose,borderColor:isDark?'rgba(226,232,240,0.9)':'rgba(30,40,60,0.9)',borderWidth:1.5,pointRadius:3,pointBackgroundColor:isDark?'#e2e8f0':'#1e2840',tension:.3,yAxisID:'yPrice',order:1}
      ]},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{...tt,callbacks:{label:ctx=>{if(ctx.datasetIndex===0)return ' 净流向: '+(ctx.raw>0?'+':'')+ctx.raw.toFixed(2)+'万';return ' 收盘价: '+ctx.raw+'元';}}}},scales:{x:{...xs,grid:{color:'transparent'},ticks:{maxRotation:0}},yFlow:{...ys,position:'left',title:{display:true,text:'净流向(万)',color:tc,font:{size:10,family:'IBM Plex Mono'}}},yPrice:{position:'right',grid:{color:'transparent'},ticks:{color:tc,font:{family:'IBM Plex Mono',size:10}},title:{display:true,text:'收盘价(元)',color:tc,font:{size:10,family:'IBM Plex Mono'}}}}}
    });
  }
}

window.addEventListener('load',renderCharts);
"""


def _build_html_app_script(
    fin_labels_json: str,
    fin_roe_json: str,
    fin_eps_json: str,
    fin_profit_json: str,
    flow_data_json: str,
    closep_series: str,
    trend_label_json: str,
) -> str:
    """组装 HTML 内联脚本：数据行用 f-string 注入，逻辑块为普通字符串。"""
    data_lines = f"""// data
const finLabels={fin_labels_json};
const roeData={fin_roe_json};
const epsData={fin_eps_json};
const profitData={fin_profit_json};
const flowData={flow_data_json};
const closePriceSeries={closep_series};
const trendLabel={trend_label_json};
"""
    return data_lines + _HTML_APP_SCRIPT_LOGIC


# ── Main entry point ────────────────────────────────────────────────────


def render_html(collection: dict[str, Any], symbol: str, md_text: str | None = None) -> str:
    """HTML 研究报告（新版模板）。

    直接构建结构化 HTML，匹配 host-docs/stock-report.html 模板样式和交互。
    支持 Chart.js 图表、暗/亮主题切换、侧边栏导航。

    Args:
        collection: collector.collect_all() 的结果
        symbol: 股票代码（如 "600519"）
        md_text: 已弃用，保留仅为 CLI 向后兼容；HTML 仅读取 collection
    """
    del md_text  # stdout Markdown 由 invest.py 单独渲染
    dims = _index_dims(collection)
    basic = _get_dim_data(dims, "basic_info") or {}
    summary = collection.get("summary", {})
    fetched_at = collection.get("fetched_at", "")[:19]

    name = basic.get("name", "") or basic.get("股票简称", "")
    industry = basic.get("industry", "")

    # ── 行情数据 ──
    quote = _get_dim_data(dims, "quote")
    price = None
    change_pct = None
    turnover = None
    if isinstance(quote, dict):
        price = quote.get("price") or quote.get("close")
        change_pct = quote.get("change_pct")
        turnover = quote.get("turnover_rate")
    elif isinstance(quote, list) and quote:
        qsorted = sorted(quote, key=lambda x: x.get("trade_date", ""))
        last = qsorted[-1]
        price = last.get("close") or last.get("price")

    price_str = f"{price:.2f}" if price is not None else "--"
    is_down = change_pct is not None and change_pct < 0
    is_up = change_pct is not None and change_pct > 0
    price_color = "var(--dn)" if is_down else ("var(--up)" if is_up else "var(--tx)")
    change_str = f"{change_pct:+.2f}%" if change_pct is not None else "--"
    chg_color = "var(--dn)" if is_down else ("var(--up)" if is_up else "var(--tx-m)")
    turnover_str = f"{turnover:.2f}%" if turnover is not None else "--"

    # ── 财务数据 ──
    fin_labels, fin_roe, fin_eps, fin_profit, fin_table_html, fin_note = _extract_financials_data(dims)

    # ── 估值数据 ──
    val = _extract_valuation_data(dims)

    # ── 技术数据 ──
    tech = _extract_technical_html(dims)
    atr_str = tech.get("atr_14") or "--"
    vol5d_raw = tech.get("vol5d")
    vol5d_str = _fmt_v2(vol5d_raw) if vol5d_raw is not None else "--"
    ma250_val = tech.get("ma250_val")
    ma250_str = ma250_val or "--"
    ma250_pos = tech.get("ma250_pos", "")
    kline_days = tech.get("kline_days", 0)

    # ── 股息率 ──
    dv_str = "--"
    val_data = _get_dim_data(dims, "valuation")
    if isinstance(val_data, list) and val_data:
        from lib.technical import sort_kline_asc
        vs = sort_kline_asc(val_data)
        dv = next((r.get("dv_ratio") for r in reversed(vs) if r.get("dv_ratio") is not None), None)
        if dv is not None:
            dv_str = f"{dv:.2f}%"

    # ── 北向资金 ──
    nb = _extract_northbound_data(dims)
    flow_data_json = json.dumps(nb["flow_data"]) if nb["has_data"] else "[]"
    flow_total = nb.get("total_flow", 0)
    flow_pos = nb.get("pos_days", 0)
    flow_days = nb.get("total_days", 0)
    flow_color = "var(--dn)" if flow_total < 0 else ("var(--up)" if flow_total > 0 else "var(--tx)")
    flow_total_str = _fmt_v2(flow_total, "") if flow_total else "0"
    nb_html = ""
    if nb["has_data"]:
        nb_html = f'''
    <div style="display:flex;align-items:center;gap:var(--space-4);margin-bottom:var(--space-3);flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:6px;font-size:var(--text-xs);color:var(--tx-m)"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--up)"></span>净流入</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:var(--text-xs);color:var(--tx-m)"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--dn)"></span>净流出</div>
      <div style="margin-left:auto;display:flex;gap:var(--space-3)">
        <div class="ipill" style="padding:4px 10px"><span style="font-size:var(--text-xs);color:var(--tx-f)">7日净流入&nbsp;</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600;color:{flow_color}">{flow_total_str}</span></div>
        <div class="ipill" style="padding:4px 10px"><span style="font-size:var(--text-xs);color:var(--tx-f)">净入天数&nbsp;</span><span style="font-family:var(--font-mono);font-size:var(--text-xs);font-weight:600">{flow_pos}/{flow_days}</span></div>
      </div>
    </div>
    <div style="position:relative;height:240px"><canvas id="flowChart"></canvas></div>
    <div class="vnote" style="margin-top:var(--space-3)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>左轴：日净流向（万元）；右轴：收盘价（元）。北向资金为估算值，仅供参考。</div>'''
    else:
        nb_html = '<div style="padding:2rem;text-align:center;color:var(--tx-f)">北向资金数据不可得</div>'

    # ── 股东数据 ──
    holders_data = _extract_holders_data(dims)
    if holders_data["has_data"]:
        max_hold = max(h[1] for h in holders_data["holders"]) if holders_data["holders"] else 1
        holder_rows = "".join(
            f'<div class="hlr"><div class="hlrk">{i+1}</div><div class="hln"><div class="hlname">{_html_mod.escape(h[0])}</div><div class="hlbar" style="width:{(h[1]/max_hold*100):.0f}%"></div></div><div class="hlpct">{_fmt_v2(h[1], "%")}</div></div>'
            for i, h in enumerate(holders_data["holders"])
        )
        holders_html = f'<div id="holderList">{holder_rows}</div>'
    else:
        holders_html = '<div style="padding:2rem;text-align:center;color:var(--tx-f)">股东数据不可得</div>'

    # ── 引用来源 ──
    refs_data = _extract_refs_data(collection)
    ref_rows = "".join(
        f'<tr><td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr);color:var(--tx-m)">{_html_mod.escape(d)}</td>'
        f'<td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr)"><code>{_html_mod.escape(a)}</code></td>'
        f'<td style="font-family:var(--font-mono);font-size:var(--text-xs);padding:8px 12px;border-bottom:1px solid var(--bdr)"><span class="{"ref-ok" if ok else "ref-err"}">{detail if ok else ("✗ " + "不可用")}</span></td></tr>'
        for d, a, ok, detail in refs_data
    )

    # ── Chart.js 数据序列化 ──
    fin_labels_json = json.dumps(fin_labels, ensure_ascii=False)
    fin_roe_json = json.dumps(fin_roe, ensure_ascii=False)
    fin_eps_json = json.dumps(fin_eps, ensure_ascii=False)
    fin_profit_json = json.dumps(fin_profit, ensure_ascii=False)

    # ── 构建各模块 ──
    topbar = _html_topbar(symbol, name, price_str, change_str, price_color, chg_color, summary)
    sidebar = _html_sidebar()
    overview = _html_overview(price_str, change_str, price_color, chg_color,
                              vol5d_str, turnover_str, atr_str, vol5d_str,
                              dv_str, ma250_str, ma250_pos, kline_days)
    valuation = _html_valuation(
        val.get("pe_pct") or "0", val.get("pe_val") or "--", val.get("pe_color", "var(--c1)"),
        val.get("pb_pct") or "0", val.get("pb_val") or "--", val.get("pb_color", "var(--c2)"),
        val.get("ps_pct") or "0", val.get("ps_val") or "--", val.get("ps_color", "var(--wn)"),
        val.get("pe_median") or "--", val.get("pb_median") or "--",
        val.get("zone_signal", "--"), val.get("zone_color", "var(--tx-m)"),
        val.get("n_samples", 0), val.get("window_label", "近5年"),
        val.get("pe_above_median", False), val.get("pb_above_median", False),
    )
    financials = _html_financials(fin_table_html, fin_note)
    technicals = _html_technicals(
        tech.get("macd_html", ""), tech.get("rsi_kdj_html", ""), tech.get("boll_html", ""),
        tech.get("ma_grid_html", ""), "", tech.get("tech_source", ""),
    )
    northbound = _html_northbound(nb_html)
    holders_sec = _html_holders(holders_html)

    events_sec = _html_events()
    refs_sec = _html_refs(ref_rows)
    risk_banner = _html_risk_banner()
    disclaimer = _html_disclaimer()

    # ── Trend label (filled by JS) ──
    trend_label_json = json.dumps(tech.get("trend_label", ""), ensure_ascii=False)

    # ── Quote price series for flow chart ──
    kd = _get_dim_data(dims, "kline")
    closep_series = "[]"
    if isinstance(kd, list) and kd:
        from lib.technical import sort_kline_asc
        kd = sort_kline_asc(kd)
        recent_closes = [r.get("close") for r in kd[-14:]]
        closep_series = json.dumps(recent_closes, ensure_ascii=False)

    # ── 构建完整 HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_mod.escape(f"{symbol} {name}")} — invest-A 研报</title>
<style>
{_HTML_CSS}
</style>
</head>
<body>
<div class="app">
{topbar}
{sidebar}
<main class="main">
<div style="display:flex;align-items:center;gap:var(--space-4);padding-bottom:var(--space-4);border-bottom:1px solid var(--bdr)">
  <div>
    <div style="font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono);margin-bottom:2px">采集时间 {_html_mod.escape(fetched_at)}</div>
    <div style="font-size:var(--text-xs);color:var(--tx-f)">维度 <span style="color:var(--wn);font-weight:600">{summary.get("available", 0)}/{summary.get("total", 0)} 有数据</span>{f'（{summary.get("degraded", 0)} 个接口降级）' if summary.get("degraded") else ''} · 不复权</div>
  </div>
  <span style="margin-left:auto;font-size:var(--text-xs);color:var(--tx-f);font-family:var(--font-mono)">tushare · akshare · baostock</span>
</div>

{risk_banner}
{overview}
{valuation}
{financials}
{technicals}
{northbound}
{holders_sec}
{events_sec}
{refs_sec}
{disclaimer}
</main>
</div>

<script>
{_load_chart_js()}
</script>
<script>
{_build_html_app_script(
    fin_labels_json, fin_roe_json, fin_eps_json, fin_profit_json,
    flow_data_json, closep_series, trend_label_json,
)}
</script>
</body>
</html>"""

    return html

