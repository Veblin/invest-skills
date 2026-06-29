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

from lib.nums import safe_float as _safe_num

from .proxy import (
    EASTMONEY_BLOCKED_KEYWORDS as _EASTMONEY_BLOCKED_KEYWORDS,
    EASTMONEY_FAILURE_PROXY_MARKER,
    EASTMONEY_FAILURE_TUN_MARKER,
)
from .schema import CrossValidation, DriverFactor, ProbabilityStructure

ENGINE_VERSION = "0.1.5"


def _cross_validation_marker(cv: CrossValidation | None) -> str:
    """生成交叉验证状态标记。"""
    if cv is None:
        return ""
    if cv.status == "convergence":
        return f"🟢 **印证** — {cv.detail}"
    return f"🟡 **分歧** — {cv.detail}"


def _meta_cv_line(meta: dict) -> str:
    """从 legacy _meta 生成交叉验证行。"""
    cv_status = meta.get("cross_validation")
    if not cv_status:
        return ""
    detail = meta.get("cross_validation_detail") or ""
    if cv_status == "convergence":
        return f"🟢 **印证** — {detail or '多源数据一致'}"
    return f"🟡 **分歧** — {detail or '多源数据存在差异'}"


def _render_engine_extras(collection: dict[str, Any]) -> list[str]:
    """渲染引擎层产出：宏观、融合、可信度、产业链。"""
    lines: list[str] = []

    macro = collection.get("macro_context") or {}
    if macro.get("status") == "ok":
        from .macro import macro_signal_label
        lines.append(f"**[宏观情景]** {macro_signal_label(macro)}")

    chain = collection.get("chain_context") or {}
    if chain.get("status") == "ok" and chain.get("industry"):
        pos = chain.get("chain_position") or "—"
        lines.append(f"**[产业链]** {chain['industry']} · {pos}")

    fusion = collection.get("fusion") or {}
    if fusion:
        lines.append("**[多源融合]**")
        for dim, fp in sorted(fusion.items()):
            if isinstance(fp, dict):
                fv = fp.get("fused_value")
                consensus = fp.get("consensus", "?")
                diff = fp.get("max_diff_pct", 0)
                lines.append(f"  - {dim}: 融合值={fv} · {consensus} · 最大差异={diff}%")

    cred = collection.get("credibility") or {}
    if cred:
        top = sorted(cred.items(), key=lambda x: -x[1])[:5]
        cred_s = ", ".join(f"{k}={v:.0f}" for k, v in top)
        lines.append(f"**[证据可信度]** {cred_s}")

    return lines

_EASTMONEY_BLOCKED_SHORT = "东方财富(East Money)主动拒绝连接"
_RAW_CONNECTION_REFUSED_SHORT = "服务器拒绝连接"


def sanitize_error(error: str, max_len: int = 60) -> str:
    """将原始 Python 异常转为可读的简短说明，截断到 max_len。

    优先检测东方财富封锁、DNS/代理等常见网络问题。
    """
    if not error:
        return "未知错误"
    # Coerce non-string types (e.g., Exception objects, int error codes)
    if not isinstance(error, str):
        error = str(error)
    if EASTMONEY_FAILURE_TUN_MARKER in error:
        return "push2 不可达（TUN/CDN），已用 Tushare/Baostock 替代"
    if EASTMONEY_FAILURE_PROXY_MARKER in error:
        return "HTTP 代理未绕过，请配置 Clash DIRECT 或关闭代理"
    if any(kw in error for kw in _EASTMONEY_BLOCKED_KEYWORDS):
        return _EASTMONEY_BLOCKED_SHORT
    if "Clash" in error or "VPN" in error:
        return "可能与 Clash / VPN 有关，关闭代理后重试"
    if "ProxyError" in error or "Max retries exceeded" in error:
        return "可能与 Clash / VPN 有关，关闭代理后重试"
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
    extras = _render_engine_extras(collection)
    if extras:
        lines.extend(extras)
        lines.append("")

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
            cv_line = _meta_cv_line(meta)
            if cv_line:
                lines.append(cv_line)
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
    from .json_util import dumps_json
    return dumps_json(collection)


def render(collection: dict[str, Any], symbol: str, fmt: str = "compact",
           mode: str = "full", *, attach_extras: bool = True) -> str:
    """统一渲染入口。支持 compact / json / md / html 格式。

    compact  — 紧凑文本报告（v0.1.2 八段 v2 模板）
    json     — 结构化 JSON，适合程序消费
    md       — Markdown 九模块研究备忘录（v0.1.3 render_report_v3）
    html     — HTML 研究报告（v0.1.2 冻结模板）

    mode     — "full"（完整九模块）或 "brief"（精简简报）
    attach_extras — False 时跳过 market_structure / phase2 补采（离线 synthesize）
    """
    if attach_extras:
        from lib import collector
        if not collection.get("market_structure"):
            collector.attach_market_structure(collection, symbol)
        collector.attach_phase2_extras(collection, symbol)

    if fmt == "json":
        return render_json(collection)
    if fmt == "html":
        return render_html(collection, symbol)
    if fmt == "md":
        return render_report_v3(collection, symbol, mode=mode)
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
        _section_research_summary(collection, symbol, dims),
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


def _get_analysis_cards(collection: dict) -> dict:
    """Safe accessor for analysis_cards from collection._meta."""
    return (collection.get("_meta") or {}).get("analysis_cards") or {}


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
        "> ⚠️ **风险提示:** 本报告由自动化引擎生成，仅供研究备忘录参考，不构成任何投资建议、买卖指令或目标价预测。",
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
        from lib.valuation import valuation_window_label
        window_label = valuation_window_label(len(val_sorted))

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

> ⚠️ **免责声明:** 本报告由 invest-A v{ENGINE_VERSION} 自动化引擎生成，仅供研究备忘录与多因子分析参考。
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


# ---- v3 报告模板（v0.1.3 Phase 1） ----

_CV_ICONS = {"convergence": "🟢", "divergence": "🟡", "gap": "🔴"}


def _cross_validation_block(cv: CrossValidation) -> str:
    return cv.to_markdown()


def _cv(
    status: str, code: str, data_pair: str, detail: str, reliability: str,
) -> str:
    return CrossValidation(status, code, data_pair, detail, reliability).to_markdown()


def _v3_northbound_signal_label(nb: dict) -> str:
    """北向净额标签：hsgt_top10 为上榜日累计，akshare 为连续交易日。"""
    days = int(nb.get("days") or 0)
    amount = _fmt_v2(nb.get("net_sum_10d"))
    src = str(nb.get("source") or "")
    if "hsgt_top10" in src:
        return f"上榜日累计净额 {amount}（{days} 个上榜日）"
    if days:
        return f"近 {days} 日净额 {amount}"
    return f"净额 {amount}"


def _v3_law11_trigger_d(dims: dict[str, dict]) -> bool:
    """LAW 11 触发源 D：52 周高低区间极端，或价格贴近 MA60 盘整。"""
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list):
        return False
    from lib.technical import sort_kline_asc, compute

    rows = sort_kline_asc(kline)
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    if len(closes) < 60:
        return False

    n52 = min(len(closes), 250)
    window = closes[-n52:]
    hi, lo = max(window), min(window)
    cur = closes[-1]
    if hi > lo:
        pos = (cur - lo) / (hi - lo)
        if pos >= 0.85 or pos <= 0.15:
            return True

    tech = compute(rows)
    if "error" in tech:
        return False
    ma60_vals = tech["trend"]["ma"].get("60") or []
    ma60 = ma60_vals[-1] if ma60_vals else None
    if ma60 is not None and float(ma60) > 0:
        if abs(cur - float(ma60)) / float(ma60) <= 0.03:
            return True
    return False


def _strip_section_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("##"):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _evidence_conclusion_block(conclusion: str, evidences: list[tuple[str, str]]) -> str:
    """LAW 12 证据-结论映射块。evidences: [(强度符号, 描述), ...]"""
    lines = [f"**结论：{conclusion}**", "", "支持证据："]
    for sym, desc in evidences:
        lines.append(f"  {sym} {desc}")
    strong = sum(1 for s, _ in evidences if s == "✅")
    weak = sum(1 for s, _ in evidences if s == "❓")
    if strong >= 2:
        strength = "强"
        note = "多条直接数据支撑，主要竞争性解释已排除。"
    elif weak >= len(evidences) // 2 + 1:
        strength = "弱"
        note = "证据以相关性或单一来源为主，结论可信度受限。"
    else:
        strength = "中"
        note = "数据方向支持结论，但存在其他合理解释或来源单一。"
    lines.extend(["", f"综合证据强度：{strength}", f"  {note}"])
    return "\n".join(lines)


def _v3_multi_source_consistency(dims: dict[str, dict]) -> tuple[str, str]:
    """模块 1 多源一致性：🟢 多源并行 / 🟡 部分降级 / 🔴 单源或不可得。"""
    checks: list[str] = []
    for key in ("quote", "valuation", "kline", "financials"):
        meta = _get_dim_meta(dims, key)
        all_src = meta.get("all_sources") or []
        if not all_src:
            if _get_dim_data(dims, key) is not None:
                checks.append("single")
            else:
                checks.append("gap")
            continue
        avail = [s for s in all_src if s.get("data_available")]
        tried = [s for s in all_src if s.get("data_available") or s.get("error")]
        if len(avail) >= 2:
            checks.append("multi")
        elif len(avail) == 1 and len(tried) >= 2:
            checks.append("degraded")
        elif avail:
            checks.append("single")
        else:
            checks.append("gap")
    if not checks:
        return "🔴", "核心维度无可比对的并行取证记录"
    multi_n = sum(1 for c in checks if c == "multi")
    degraded_n = sum(1 for c in checks if c == "degraded")
    gap_n = sum(1 for c in checks if c in ("gap", "single"))
    if multi_n >= 2 and gap_n == 0:
        return "🟢", f"{multi_n} 个核心维度具备多源并行取证且均有数据"
    if multi_n >= 1 or degraded_n >= 1:
        return "🟡", (
            f"多源 {multi_n} / 降级 {degraded_n} / 单源或缺口 {gap_n}；"
            "极端值需对照 primary 源与附录追溯表"
        )
    return "🔴", "核心维度以单源或不可得为主，交叉验证能力受限"


def _v3_cv7_assessment(
    pe_pct: float | None, mf_out: float | int | None,
) -> tuple[str, str] | None:
    """CV-7：PE 分位 vs 主力资金方向。

    分位边界与 valuation.ZONE_LOW/HIGH_THRESHOLD 一致（严格 <30 / >70）。
    """
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

    if pe_pct is None or mf_out is None:
        return None
    mf_f = float(mf_out)
    if pe_pct < ZONE_LOW_THRESHOLD and mf_f < 0:
        return "convergence", f"PE 低位（{pe_pct:.1f}%）且主力资金净流出"
    if pe_pct > ZONE_HIGH_THRESHOLD and mf_f > 0:
        return "divergence", f"PE 高位（{pe_pct:.1f}%）但主力资金净流入"
    return "gap", "估值与资金流向未呈现典型背离/共振"


def _v3_cv7_block(pe_pct: float | None, mf_out: float | int | None) -> str | None:
    assessed = _v3_cv7_assessment(pe_pct, mf_out)
    if assessed is None:
        return None
    status, detail = assessed
    return _cv(status, "CV-7", "PE 分位 vs 资金流出", detail, "中")


def _v3_cv8_assessment(
    erp: dict | None,
    pcr: dict | None,
    short_margin: dict | None,
) -> tuple[str, str] | None:
    """CV-8：ERP 分位 vs 认沽认购比 vs 融券增速。"""
    erp_p = (erp or {}).get("percentile_5y")
    pcr_p = (pcr or {}).get("percentile_5y") or (pcr or {}).get("percentile_60d")
    sm_g = (short_margin or {}).get("growth_pct")
    sm_p = (short_margin or {}).get("percentile_5y")

    flags: list[tuple[str, bool | None]] = []
    if erp_p is not None:
        flags.append(("ERP", erp_p >= 70))
    if pcr_p is not None:
        flags.append(("PCR", pcr_p >= 70))
    if sm_g is not None:
        flags.append(("融券增速", sm_g > 0))
    elif sm_p is not None:
        flags.append(("融券分位", sm_p >= 70))

    scored = [(n, v) for n, v in flags if v is not None]
    if len(scored) < 2:
        return None

    left_n = sum(1 for _, v in scored if v)
    erp_s = f"{erp_p:.1f}%" if erp_p is not None else "-"
    pcr_s = f"{pcr_p:.1f}%" if pcr_p is not None else "-"
    sm_s = f"{sm_g:+.2f}%" if sm_g is not None else (
        f"{sm_p:.1f}%分位" if sm_p is not None else "-"
    )
    detail_base = f"ERP 5年分位 {erp_s}；认沽认购比分位 {pcr_s}；融券 {sm_s}"

    if left_n >= 2:
        return "convergence", f"左侧情绪指标共振 — {detail_base}"
    if erp_p is not None and pcr_p is not None and erp_p >= 70 and pcr_p < 30:
        return "divergence", f"ERP 偏高但认沽认购比未处高位 — {detail_base}"
    if erp_p is not None and pcr_p is not None and erp_p < 30 and pcr_p >= 70:
        return "divergence", f"认沽认购比偏高但 ERP 未处高位 — {detail_base}"
    return "gap", f"情绪三角未呈现典型共振或背离 — {detail_base}"


def _v3_cv8_block(
    erp: dict | None,
    pcr: dict | None,
    short_margin: dict | None,
) -> str | None:
    assessed = _v3_cv8_assessment(erp, pcr, short_margin)
    if assessed is None:
        return None
    status, detail = assessed
    return _cv(status, "CV-8", "ERP 分位 vs 认沽认购比 vs 融券增速", detail, "中")


def _v3_ms_availability_note(availability: dict, key: str) -> str:
    status = (availability or {}).get(key, "")
    if status == "available":
        return ""
    if status.startswith("partial"):
        return f"（{status}）"
    if status.startswith("unavailable"):
        reason = status.split(":", 1)[-1].strip()
        return f"（不可得：{reason}）"
    return "（不可得）" if status else ""


def _v3_build_candidate_explanations(
    *,
    chg: float | None,
    window_label: str,
    chg_s: str,
    dims: dict[str, dict],
    market_structure: dict,
    val_cache: dict | None = None,
) -> list[tuple[str, str, str, str]]:
    """LAW 13 候选解释，最多 5 条。返回 (标签, 文本, 证据, 强度)。"""
    explanations: list[tuple[str, str, str, str]] = []
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    sw = market_structure.get("sw_index") or {}
    mf = market_structure.get("moneyflow") or {}
    nb = market_structure.get("northbound") or {}

    if chg is not None:
        explanations.append((
            "A",
            f"价格{window_label}变动 {chg_s} 可能与估值/资金因子共振",
            "见下方多因子矩阵",
            "⚠️",
        ))
    else:
        explanations.append((
            "A",
            "K 线不足，价格变化幅度不可得",
            "kline 维度",
            "❓",
        ))

    if pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20):
        zone = "偏高" if pe_pct >= 80 else "偏低"
        explanations.append((
            "B",
            f"估值历史分位{zone}（PE {pe_pct:.1f}%）驱动定价预期重估",
            "valuation 历史分位",
            "⚠️",
        ))
    elif pb_pct is not None and (pb_pct >= 80 or pb_pct <= 20):
        zone = "偏高" if pb_pct >= 80 else "偏低"
        explanations.append((
            "B",
            f"PB 历史分位{zone}（{pb_pct:.1f}%）或反映资产定价差异",
            "valuation 历史分位",
            "⚠️",
        ))

    rel = sw.get("relative_vs_benchmark_pct")
    svi = sw.get("stock_vs_industry_pct")
    if rel is not None and abs(rel) >= 5:
        explanations.append((
            "C",
            f"行业板块相对沪深300 {rel:+.2f}%，行业景气或拖累/支撑个股",
            sw.get("source", "sw_daily"),
            "⚠️",
        ))
    elif svi is not None and abs(svi) >= 5:
        explanations.append((
            "C",
            f"个股相对行业 {svi:+.2f}%，个股特异性因素可能主导",
            sw.get("source", "sw_daily"),
            "⚠️",
        ))

    kline = _get_dim_data(dims, "kline")
    if kline and isinstance(kline, list):
        from lib.technical import sort_kline_asc, compute
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            label = tech["trend"]["alignment"].get("trend_label", "")
            if label:
                explanations.append((
                    "D",
                    f"技术趋势结构（{label}）与价格动量方向一致或背离",
                    "technical.py MA 排列",
                    "⚠️",
                ))

    mf_v = mf.get("net_sum_5d")
    nb_v = nb.get("net_sum_10d")
    if chg is not None and mf_v is not None:
        price_up = chg > 0
        flow_in = float(mf_v) > 0
        if price_up != flow_in:
            explanations.append((
                "E",
                f"价格{window_label}{chg_s} 与主力近 5 日净额方向不一致，或存在博弈/滞后",
                mf.get("source", "moneyflow"),
                "❓",
            ))
    elif nb_v is not None and mf_v is not None:
        if float(nb_v) * float(mf_v) < 0:
            explanations.append((
                "E",
                "北向与主力资金方向相反，资金归因存在分歧",
                f"{nb.get('source', '')} vs {mf.get('source', '')}",
                "❓",
            ))

    return explanations[:5]


def _v3_pick_dominant_factor(rows: list[str]) -> str:
    """从矩阵行中选取方向明确且强度最高的因子。"""
    scored: list[tuple[int, str, str, str]] = []
    for row in rows:
        if "跳过" in row or "---" in row:
            continue
        parts = [p.strip() for p in row.split("|")]
        if len(parts) < 5:
            continue
        cat, signal, direction, strength = parts[1], parts[2], parts[3], parts[4]
        if direction in ("—", "→中性"):
            continue
        weight = 2 if "⚠️" in strength else (1 if "❓" in strength else 0)
        if weight:
            scored.append((weight, cat, direction, signal))
    if not scored:
        return "数据不足，暂无法声明主导因子；可持续性：待观察"
    scored.sort(key=lambda x: (-x[0], x[1]))
    _, cat, direction, signal = scored[0]
    return f"{cat}（{signal}，{direction}）；可持续性：待观察"


def _v3_trend_stage_hints(label: str) -> str:
    """LAW 16：并列阶段对照，不勾选单一结论。"""
    base = "□ 上升趋势  □ 筑底区间  □ 高位震荡  □ 下降趋势  □ 不明确"
    if not label:
        return base
    if "多头" in label:
        hint = "数据更接近「上升趋势」描述，但不排除震荡或反转"
    elif "空头" in label:
        hint = "数据更接近「下降趋势」描述，但不排除筑底或反弹"
    else:
        hint = "数据更接近「高位震荡/整理」描述，方向待确认"
    return f"{base}\n  - 对照说明（非结论）: {hint}"


def _v3_price_change(dims: dict[str, dict]) -> tuple[float | None, int | None]:
    """返回 (涨跌幅%, 实际跨度交易日数)。不足 20 日时仍计算但 window < 20。"""
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list) or len(kline) < 2:
        return None, None
    from lib.technical import sort_kline_asc
    rows = sort_kline_asc(kline)
    if len(rows) >= 21:
        recent = rows[-21:]
    else:
        recent = rows
    c0 = recent[0].get("close")
    c1 = recent[-1].get("close")
    if c0 is None or c1 is None:
        return None, None
    if float(c0) == 0:
        return None, None  # zero close, cannot compute percentage
    window = len(recent) - 1
    pct = (float(c1) - float(c0)) / float(c0) * 100
    return pct, window


def _v3_price_window_label(window: int | None) -> str:
    if window is None:
        return "涨跌幅"
    if window >= 20:
        return "近 20 个交易日"
    return f"近 {window} 个交易日（K 线不足 20 日）"


def _v3_price_change_pct(dims: dict[str, dict]) -> float | None:
    pct, _ = _v3_price_change(dims)
    return pct


def _v3_load_valuation_summary(
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> dict | None:
    """加载 valuation_summary 并写入 val_cache（含 pe 中位数供场景化估算）。"""
    if val_cache is not None and "val_summary" in val_cache:
        return val_cache["val_summary"]
    val_data = _get_dim_data(dims, "valuation")
    summary: dict | None = None
    if val_data and isinstance(val_data, list):
        from lib.technical import sort_kline_asc
        from lib.valuation import valuation_summary, valuation_window_label
        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted]
        pb_seq = [r.get("pb") for r in val_sorted]
        window_label = valuation_window_label(len(val_sorted))
        summary = valuation_summary(pe_seq, pb_seq, window_label=window_label)
    if val_cache is not None:
        val_cache["val_summary"] = summary
        if summary:
            pe = summary.get("pe") or {}
            pb = summary.get("pb") or {}
            val_cache["result"] = (pe.get("pct"), pb.get("pct"), pe.get("zone"))
        else:
            val_cache["result"] = (None, None, None)
    return summary


def _v3_valuation_percentiles(
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> tuple[float | None, float | None, str | None]:
    if val_cache is not None and "result" in val_cache:
        return val_cache["result"]
    summary = _v3_load_valuation_summary(dims, val_cache)
    if not summary:
        return (None, None, None)
    pe = summary.get("pe") or {}
    pb = summary.get("pb") or {}
    return (pe.get("pct"), pb.get("pct"), pe.get("zone"))


def _executive_core_contradictions(
    collection: dict,
    dims: dict[str, dict],
    val_cache: dict | None = None,
) -> list[str]:
    """从已有数据卡片提炼两条核心矛盾（数据驱动，非占位）。"""
    items: list[str] = []
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)

    roe = eps = None
    fin = dims.get("financials", {}).get("data")
    if isinstance(fin, list) and fin:
        latest = fin[-1]
        roe = latest.get("roe")
        eps = latest.get("eps")

    if pe_pct is not None and roe is not None:
        try:
            roe_f = float(roe)
            if pe_pct >= 70 and roe_f < 10:
                items.append(
                    f"估值历史位置偏高（PE {pe_pct:.0f}%）vs 盈利质量偏弱"
                    f"（ROE {roe_f:.1f}%）[来源: valuation+financials]"
                )
            elif pe_pct <= 30 and roe_f >= 12:
                items.append(
                    f"估值历史位置偏低（PE {pe_pct:.0f}%）vs 盈利质量尚可"
                    f"（ROE {roe_f:.1f}%）[来源: valuation+financials]"
                )
        except (TypeError, ValueError):
            pass

    ms = collection.get("market_structure") or {}
    nb = ms.get("northbound") or dims.get("northbound", {}).get("data") or {}
    net10 = nb.get("net_sum_10d") if isinstance(nb, dict) else None
    quote = dims.get("quote", {}).get("data") or {}
    chg = quote.get("change_pct") if isinstance(quote, dict) else None
    if net10 is not None and chg is not None:
        try:
            net_f, chg_f = float(net10), float(chg)
            if net_f > 0 and chg_f < -2:
                items.append(
                    f"北向近10日净流入 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
                    f"[来源: northbound+quote]"
                )
            elif net_f < 0 and chg_f > 2:
                items.append(
                    f"北向近10日净流出 {net_f:+.0f} 与股价 {chg_f:+.1f}% 背离"
                    f"[来源: northbound+quote]"
                )
        except (TypeError, ValueError):
            pass

    cred = collection.get("credibility") or {}
    if cred:
        low = [k for k, v in cred.items() if v < 50]
        if low:
            items.append(
                f"可信度偏低维度: {', '.join(low[:3])}"
                f"{'…' if len(low) > 3 else ''} [来源: rerank]"
            )

    while len(items) < 2:
        items.append("独立维度交叉验证不足，需补充外部信源 [推测，待验证]")
    return items[:2]


def _section_executive_summary(collection, symbol, dims, val_cache=None):
    """生成一屏内可读的执行摘要：一行话定位 + 两条矛盾 + 三个观察点。"""
    lines = ["## 执行摘要", ""]

    basic = dims.get("basic_info", {}).get("data", {})
    name = ""
    industry = ""
    if isinstance(basic, dict):
        name = basic.get("name", "") or basic.get("股票简称", "")
        industry = basic.get("industry", "")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)

    name_str = f"{symbol} {name}".strip()
    industry_str = f"（{industry}）" if industry else ""
    summary = _v3_load_valuation_summary(dims, val_cache)
    pe_median = (summary.get("pe") or {}).get("median") if summary else None
    if pe_pct is not None:
        median_part = f"（中位数 {pe_median:.2f}x）" if pe_median is not None else ""
        pe_str = f"PE 历史位置 {pe_pct:.1f}%{median_part}"
    else:
        pe_str = "PE 不可得"
    lines.append(f"**{name_str}**{industry_str} — {pe_str}")
    lines.append("")

    lines.append("**核心矛盾：**")
    for i, item in enumerate(_executive_core_contradictions(collection, dims, val_cache), 1):
        lines.append(f"{i}. {item}")
    lines.append("")

    lines.append("**关键观察点：**")
    fin = dims.get("financials", {}).get("data")
    if fin and isinstance(fin, list) and fin:
        latest = fin[-1]
        rot = latest.get('roe', '?')
        eps = latest.get('eps', '?')
        lines.append(f"- 财务: 最近报告期 ROE={rot}%, EPS={eps}")
    else:
        lines.append("- 财务: 数据不可得")

    quote = dims.get("quote", {}).get("data", {})
    if isinstance(quote, dict):
        price = quote.get("close") or quote.get("price")
        if price:
            lines.append(f"- 行情: 最新价 {price}")

    ms_icon, ms_detail = _v3_multi_source_consistency(dims)
    lines.append(f"- 数据质量: {ms_icon} {ms_detail}")

    return "\n".join(lines)


def _section_research_question(
    collection: dict, symbol: str, *, val_cache: dict | None = None,
) -> str:
    dims = _index_dims(collection)
    lines = ["## 0. 研究问题卡", ""]
    triggers: list[str] = []

    chg, window = _v3_price_change(dims)
    if chg is not None and window is not None and window >= 20 and abs(chg) >= 10:
        triggers.append("A")

    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    if (pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20)) or (
        pb_pct is not None and (pb_pct >= 80 or pb_pct <= 20)
    ):
        triggers.append("B")

    ms = collection.get("market_structure") or {}
    sw = ms.get("sw_index") or {}
    rel = sw.get("relative_vs_benchmark_pct")
    if rel is not None and abs(rel) >= 5:
        triggers.append("C")

    if _v3_law11_trigger_d(dims):
        triggers.append("D")

    trigger_labels = {
        "A": "变化驱动（价格/财报/公告异动）",
        "B": "估值位置驱动（历史分位极端）",
        "C": "行业结构驱动（板块相对强弱）",
        "D": "趋势结构驱动（价格区间/均线结构）",
    }
    if triggers:
        lines.append("**激活的触发源:** " + "、".join(f"{t} {trigger_labels[t]}" for t in triggers))
    else:
        lines.append("**激活的触发源:** 暂无明确触发（以事实快照为主构建问题）")

    lines.extend([
        "", "```",
        f"核心问题：{symbol} 当前价格与基本面/市场结构之间，哪些驱动力尚不确定？",
        "└── 子问题 ① 近 20 日价格变化能否被财务与估值数据解释？",
        "└── 子问题 ② 资金与行业情绪信号是否指向相反方向？",
        "└── 子问题 ③ 若主导解释成立，对估值定价的传导路径是什么？",
        "",
        "为什么这是好问题：将可验证数据与未决不确定性分离，避免把相关性误读为因果。",
        "```",
    ])
    lines.extend([
        "", "### 九模块研究框架", "",
        _research_framework_mermaid(), "",
    ])
    lines.append("🔍 **待独立验证:** 触发源依赖采集数据完整性；公告/政策类触发需 WebSearch 补充。")
    return "\n".join(lines)


def _load_report_key_diff(symbol: str, collection: dict) -> dict | None:
    """若 store 有历史快照，返回当前采集相对上次的关键字段 diff。"""
    try:
        from lib.store import load_key_diff_vs_stored
        return load_key_diff_vs_stored(symbol, collection)
    except Exception:
        return None


def _snapshot_diff_block(key_diff: dict) -> str:
    from lib.store import format_key_diff_markdown_lines

    old_at = key_diff.get("old_at", "")[:19]
    new_at = key_diff.get("new_at", "")[:19]
    lines = ["", "### 相对上次调研变化", ""]
    if old_at and new_at:
        lines.append(f"对比区间：{old_at} → {new_at}（本次采集）")
        lines.append("")
    lines.extend(format_key_diff_markdown_lines(key_diff))
    lines.append("")
    lines.append(
        "🔍 **待独立验证:** 跨时点变化基于 store 快照字段提取，"
        "应与 `invest.py diff` 输出交叉核对。"
    )
    return "\n".join(lines)


def _section_snapshot(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    *,
    val_cache: dict | None = None,
    key_diff: dict | None = None,
) -> str:
    lines = ["## 1. 当前状态快照", ""]
    quote = _get_dim_data(dims, "quote")
    if isinstance(quote, dict):
        price = quote.get("close") or quote.get("price")
        chg = quote.get("change_pct")
        if price is not None:
            chg_s = f"（{chg:+.2f}%）" if chg is not None else ""
            lines.append(f"- **最新价:** {price}{chg_s}")

    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    if pe_pct is not None:
        lines.append(f"- **PE(TTM) 历史分位:** {pe_pct:.1f}%（{pe_zone or '—'}）")
    if pb_pct is not None:
        lines.append(f"- **PB 历史分位:** {pb_pct:.1f}%")

    fin = _get_dim_data(dims, "financials")
    if fin and isinstance(fin, list):
        from lib.technical import sort_kline_asc
        fin = sort_kline_asc(fin)
        latest = fin[-1]
        lines.append(
            f"- **最近财报:** {latest.get('end_date', '?')} "
            f"ROE={latest.get('roe', '-')}%, 净利润={_fmt_v2(latest.get('net_profit'))}"
        )
        np_v = latest.get("net_profit")
        ocf = latest.get("ocf") or latest.get("n_cashflow_act")
        if np_v is not None and ocf is not None:
            np_f, ocf_f = float(np_v), float(ocf)
            if np_f > 0 and ocf_f > 0:
                ratio = ocf_f / np_f
                cv_status = "convergence" if ratio >= 0.5 else "divergence"
            elif np_f < 0 and ocf_f < 0:
                cv_status = "divergence"
            elif np_f == 0 or ocf_f == 0:
                cv_status = "gap"
            else:
                cv_status = "divergence"
            cv_detail = f"净利润 {_fmt_v2(np_v)} vs 经营现金流 {_fmt_v2(ocf)}"
            lines.append("")
            lines.append(_cv(cv_status, "CV-1", "净利润 vs 经营现金流", cv_detail, "中（单期财报）"))
        else:
            lines.append("")
            lines.append(_cv(
                "gap", "CV-1", "净利润 vs 经营现金流",
                "经营现金流字段不可得，无法交叉验证利润质量", "低",
            ))

    if pe_pct is not None and pb_pct is not None:
        if (pe_pct >= 70 and pb_pct >= 70) or (pe_pct <= 30 and pb_pct <= 30):
            cv3 = "convergence"
            cv3d = f"PE 分位 {pe_pct:.1f}% 与 PB 分位 {pb_pct:.1f}% 同向"
        else:
            cv3 = "divergence"
            cv3d = f"PE 分位 {pe_pct:.1f}% 与 PB 分位 {pb_pct:.1f}% 方向不一致"
        lines.append("")
        lines.append(_cv(cv3, "CV-3", "PE 分位 vs PB 分位", cv3d, "中"))

    ms_icon, ms_detail = _v3_multi_source_consistency(dims)
    ms_strength = {"🟢": "✅", "🟡": "⚠️", "🔴": "❓"}.get(ms_icon, "⚠️")
    lines.extend([
        "",
        "### 多源一致性",
        f"{ms_icon} **并行取证状态** — {ms_detail}",
    ])

    if key_diff is None:
        key_diff = _load_report_key_diff(symbol, collection)
    if key_diff and key_diff.get("categories"):
        lines.append(_snapshot_diff_block(key_diff))

    lines.append("")
    lines.append(_evidence_conclusion_block(
        "当前快照呈现价格、估值与最近财报的并列事实",
        [
            ("✅", "行情与估值数据来自采集维度 primary 源"),
            (ms_strength, f"多源一致性：{ms_detail}"),
        ],
    ))
    lines.append("")
    lines.append("🔍 **待独立验证:** 快照数字应与财报 PDF / 交易所行情交叉核对。")
    return "\n".join(lines)


def _v3_matrix_row(factor: DriverFactor) -> str:
    return factor.to_matrix_row()


def _v3_driver_unavailable(category: str) -> DriverFactor:
    return DriverFactor(category, "[数据源不可用，该因子跳过]", "—", "—", "—")


def _section_dynamic_drivers(
    collection: dict, symbol: str, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> str:
    lines = ["## 2. 动态驱动分析", ""]
    chg, window = _v3_price_change(dims)
    window_label = _v3_price_window_label(window)
    chg_s = f"{chg:+.2f}%" if chg is not None else "不可得"
    lines.append(f"{window_label}涨跌幅：**{chg_s}**（采集: {collection.get('fetched_at', '')[:10]}）")
    lines.append("")
    lines.append("### 候选解释（LAW 13，上限 5 条）")
    lines.append("")
    candidates = _v3_build_candidate_explanations(
        chg=chg,
        window_label=window_label,
        chg_s=chg_s,
        dims=dims,
        market_structure=market_structure,
        val_cache=val_cache,
    )
    for label, text, evidence, strength in candidates:
        lines.append(f"→ 解释 {label}：{text}")
        lines.append(f"   证据：{evidence}")
        lines.append(f"   强度：{strength}")
        lines.append("")
    if len(candidates) < 5:
        lines.append("⚠️ **尚无候选解释的部分：** 公告/政策类事件需 WebSearch 或 anns 数据补充。")
        lines.append("")
    lines.append("### 多因子驱动矩阵")
    lines.append("")
    lines.append("| 因子类别 | 具体信号 | 方向 | 强度 | 数据来源 |")
    lines.append("|---------|---------|------|------|---------|")

    factors: list[DriverFactor] = []
    fin = _get_dim_data(dims, "financials")
    np_now, np_prev = None, None
    if fin and isinstance(fin, list) and len(fin) >= 2:
        from lib.technical import sort_kline_asc
        fin = sort_kline_asc(fin)
        np_now = fin[-1].get("net_profit")
        np_prev = fin[-2].get("net_profit")
        if np_now is not None and np_prev is not None:
            d = "↑正向" if float(np_now) > float(np_prev) else ("↓负向" if float(np_now) < float(np_prev) else "→中性")
            factors.append(DriverFactor("基本面", "净利润环比", d, "⚠️", "financials"))
        else:
            factors.append(DriverFactor("基本面", "净利润", "→中性", "❓", "financials"))
    else:
        factors.append(_v3_driver_unavailable("基本面"))

    sw = market_structure.get("sw_index")
    if sw and sw.get("return_20d_pct") is not None:
        r = sw["return_20d_pct"]
        d = "↑正向" if r > 0 else ("↓负向" if r < 0 else "→中性")
        factors.append(DriverFactor(
            "行业景气", f"申万板块 20 日 {r:+.2f}%", d, "⚠️", sw.get("source", "sw_daily"),
        ))
    else:
        factors.append(_v3_driver_unavailable("行业景气"))

    nb = market_structure.get("northbound")
    if nb and nb.get("net_sum_10d") is not None:
        v = nb["net_sum_10d"]
        d = "↑正向" if v > 0 else ("↓负向" if v < 0 else "→中性")
        factors.append(DriverFactor(
            "资金（北向）", _v3_northbound_signal_label(nb), d, "⚠️", nb.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("资金（北向）"))

    mf = market_structure.get("moneyflow")
    if mf and mf.get("net_sum_5d") is not None:
        v = mf["net_sum_5d"]
        d = "↑正向" if v > 0 else ("↓负向" if v < 0 else "→中性")
        factors.append(DriverFactor(
            "资金（主力）", f"近 5 日主力净额 {_fmt_v2(v)}", d, "⚠️", mf.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("资金（主力）"))

    mg = market_structure.get("margin")
    if mg and mg.get("change_pct") is not None:
        v = mg["change_pct"]
        d = "↑正向" if v > 0 else ("↓负向" if v < 0 else "→中性")
        factors.append(DriverFactor(
            "情绪（融资）", f"融资余额变化 {v:+.2f}%", d, "⚠️", mg.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("情绪（融资）"))

    to = market_structure.get("turnover")
    if to and to.get("ratio_5_60") is not None:
        r = to["ratio_5_60"]
        d = "↑正向" if r > 1.1 else ("↓负向" if r < 0.9 else "→中性")
        factors.append(DriverFactor(
            "情绪（换手）", f"5日/60日换手比 {r:.2f}", d, "⚠️", to.get("source", ""),
        ))
    else:
        factors.append(_v3_driver_unavailable("情绪（换手）"))

    kline = _get_dim_data(dims, "kline")
    ma_dir = "→中性"
    ma_strength = "❓"
    fin_dir = "→中性"
    if kline and isinstance(kline, list):
        from lib.technical import sort_kline_asc, compute
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            label = tech["trend"]["alignment"].get("trend_label", "")
            if "多头" in label:
                ma_dir, ma_strength = "↑正向", "⚠️"
            elif "空头" in label:
                ma_dir, ma_strength = "↓负向", "⚠️"
            factors.append(DriverFactor(
                "技术趋势", label or "MA 排列", ma_dir, ma_strength, "technical.py",
            ))
        else:
            factors.append(_v3_driver_unavailable("技术趋势"))
    else:
        factors.append(_v3_driver_unavailable("技术趋势"))

    if np_now is not None and np_prev is not None:
        fin_dir = "↑正向" if float(np_now) > float(np_prev) else (
            "↓负向" if float(np_now) < float(np_prev) else "→中性")

    # 事件催化因子 — from collection["events"]
    events_list = collection.get("events") or []
    if events_list and isinstance(events_list, list) and len(events_list) > 0:
        event_count = len(events_list)
        summary = (collection.get("_meta") or {}).get("events_summary") or {}
        top_types = summary.get("top_types", [])
        top_types_str = "、".join(
            f"{t['type']}({t['count']})" for t in top_types[:3]
        ) if top_types else f"{event_count}条"
        event_label = f"近{summary.get('window_days', 30)}日 {event_count}条事件（{top_types_str}）"
        factors.append(DriverFactor(
            "事件催化", event_label, "→中性", "⚠️",
            "akshare stock_individual_notice_report",
        ))
    else:
        factors.append(DriverFactor(
            "事件催化", "事件数据暂不可用（akshare 公告接口未返回数据）", "—", "—",
            "akshare stock_individual_notice_report",
        ))
    rows = [f.to_matrix_row() for f in factors]
    lines.extend(rows)

    pos = sum(1 for r in rows if "↑正向" in r)
    neg = sum(1 for r in rows if "↓负向" in r)
    neu = len(rows) - pos - neg
    lines.extend([
        "",
        f"因子方向一致性：{pos} 正向 / {neg} 负向 / {neu} 中性或跳过",
        "",
        "### 因子交叉验证结论",
    ])
    if ma_dir == fin_dir and ma_dir != "→中性" and fin_dir != "→中性":
        lines.append(_cv(
            "convergence", "CV-6", "MA 趋势 vs 近期业绩方向",
            f"技术趋势 {ma_dir} 与净利润环比方向 {fin_dir} 一致", "中",
        ))
    elif ma_dir != "→中性" and fin_dir != "→中性" and ma_dir != fin_dir:
        lines.append(_cv(
            "divergence", "CV-6", "MA 趋势 vs 近期业绩方向",
            f"技术趋势 {ma_dir} 与净利润环比方向 {fin_dir} 不一致", "中",
        ))
    else:
        lines.append(_cv(
            "gap", "CV-6", "MA 趋势 vs 近期业绩方向",
            "技术或业绩方向数据不足", "低",
        ))

    lines.append("")
    dominant = _v3_pick_dominant_factor(rows)
    lines.append(f"→ **主导因子（声明）:** {dominant}")
    lines.append("")
    lines.append("🔍 **待独立验证:** 候选解释仅为假说列表，非因果归因。")
    return "\n".join(lines)


def _section_market_structure(
    collection: dict, symbol: str, market_structure: dict, *, val_cache: dict | None = None,
) -> str:
    lines = ["## 3. 市场结构分析", ""]
    sw = market_structure.get("sw_index")
    if sw:
        ret = sw.get("return_20d_pct")
        ret_s = f"{ret}%" if ret is not None else "-"
        lines.append(f"- **申万行业指数:** {sw.get('index_code', '?')} 20日涨跌 {ret_s}")
        svi = sw.get("stock_vs_industry_pct")
        if svi is not None:
            lines.append(f"- **个股 vs 行业:** {svi:+.2f}%")
        stock_ret = sw.get("stock_return_20d_pct")
        ind_ret = sw.get("return_20d_pct")
        if stock_ret is not None and ind_ret is not None:
            svi_s = f"{svi:+.2f}%" if svi is not None else "-"
            if stock_ret * ind_ret > 0 or (stock_ret == 0 and ind_ret == 0):
                cv5 = "convergence"
                cv5d = (
                    f"个股 20 日 {stock_ret:+.2f}% 与行业 {ind_ret:+.2f}% 同向"
                    f"（个股相对板块 {svi_s}）"
                )
            elif stock_ret != 0 and ind_ret != 0:
                cv5 = "divergence"
                cv5d = (
                    f"个股 20 日 {stock_ret:+.2f}% 与行业 {ind_ret:+.2f}% 反向"
                    f"（个股相对板块 {svi_s}）"
                )
            else:
                cv5 = "gap"
                cv5d = f"个股或行业 20 日涨跌有一方为零（个股相对板块 {svi_s}）"
            lines.append("")
            lines.append(_cv(cv5, "CV-5", "申万板块 vs 个股相对强弱", cv5d, "中"))
        rel = sw.get("relative_vs_benchmark_pct")
        if rel is not None:
            lines.append(f"- **板块相对沪深300:** {rel:+.2f}%")
    else:
        lines.append("> 申万行业指数不可得。")

    nb = market_structure.get("northbound")
    mf = market_structure.get("moneyflow")
    if nb or mf:
        lines.append("")
        lines.append("### 资金态度")
        if nb:
            nb_src = nb.get("source", "northbound")
            lines.append(
                f"- 北向个股资金流（{nb_src}）{_v3_northbound_signal_label(nb)}"
            )
        if mf:
            lines.append(f"- 主力（moneyflow）近 5 日净额: {_fmt_v2(mf.get('net_sum_5d'))}")
        if nb and mf:
            n_v = float(nb.get("net_sum_10d") or 0)
            m_v = float(mf.get("net_sum_5d") or 0)
            if n_v * m_v > 0:
                cv4 = "convergence"
                cv4d = "北向与主力净流入方向一致"
            elif n_v == 0 or m_v == 0:
                cv4 = "gap"
                cv4d = "资金数据不完整"
            else:
                cv4 = "divergence"
                cv4d = "北向与主力净流入方向相反"
            lines.append("")
            lines.append(_cv(cv4, "CV-4", "北向 vs 主力大单", cv4d, "中"))

    to = market_structure.get("turnover")
    erp = market_structure.get("erp")
    if to or erp:
        lines.append("")
        lines.append("### ERP / 换手")
        if to:
            lines.append(
                f"- 换手率: 5日均 {to.get('avg_5d', '-')}%，60日均 {to.get('avg_60d', '-')}%，"
                f"分位 {to.get('percentile_60d', '-')}%"
            )
        if erp:
            partial_note = "（样本日不足，分位仅供参考）" if erp.get("partial") else ""
            y10_src = erp.get("source", "")
            if "+" in y10_src:
                _, bond_src = y10_src.split("+", 1)
                y10_note = f"；10Y 国债来源: {bond_src}"
            else:
                y10_note = ""
            lines.append(
                f"- ERP（沪深300）: {erp.get('raw', '-')}%，5年分位 {erp.get('percentile_5y', '-')}%"
                f"{partial_note}{y10_note} [对齐样本 {erp.get('erp_days', '-')} 日]"
            )

    pe_pct, _, _ = _v3_valuation_percentiles(_index_dims(collection), val_cache)
    mf_out = (mf or {}).get("net_sum_5d")
    cv7 = _v3_cv7_block(pe_pct, mf_out)
    if cv7:
        lines.append("")
        lines.append(cv7)

    avail = market_structure.get("availability") or {}
    pcr = market_structure.get("put_call_ratio")
    sm = market_structure.get("short_margin")
    nhr = market_structure.get("new_high_ratio")
    etf = market_structure.get("etf_flow")

    lines.append("")
    lines.append("### 3b. ETF 资金")
    if etf:
        parts = [f"**{etf.get('ts_code', '510300.SH')}**"]
        if etf.get("net_flow_5d") is not None:
            parts.append(f"近5日估算净流入 {_fmt_v2(etf['net_flow_5d'])}")
        if etf.get("net_flow_10d") is not None:
            parts.append(f"近10日估算净流入 {_fmt_v2(etf['net_flow_10d'])}")
        if etf.get("price_incomplete"):
            parts.append("（收盘价缺失，未估算缺失区间）")
        parts.append(f"[{etf.get('source', '')}]")
        lines.append("- " + "；".join(parts))
    else:
        lines.append(
            f"- ETF 资金流向不可得"
            f"{_v3_ms_availability_note(avail, 'etf_flow')}"
        )

    if pcr or sm or nhr or any(
        avail.get(k) for k in ("put_call_ratio", "short_margin", "new_high_ratio")
    ):
        lines.append("")
        lines.append("### 3c. 情绪指标")
        if pcr:
            partial = "（历史样本不足，分位仅供参考）" if pcr.get("partial") else ""
            pct_5y = pcr.get("percentile_5y")
            pct_60d = pcr.get("percentile_60d")
            if pct_5y is not None and pct_60d is not None and pct_5y != pct_60d:
                pct_s = f"5年分位 {pct_5y}%，60日分位 {pct_60d}%"
            else:
                pct_s = f"分位 {pct_5y if pct_5y is not None else pct_60d if pct_60d is not None else '-'}"
            lines.append(
                f"- **50ETF 认沽认购比:** {pcr.get('ratio', '-')}，"
                f"{pct_s}{partial} "
                f"[{pcr.get('source', '')}]"
            )
        else:
            lines.append(
                f"- **50ETF 认沽认购比:** 不可得"
                f"{_v3_ms_availability_note(avail, 'put_call_ratio')}"
            )
        if sm:
            sm_pct = sm.get("percentile_5y")
            scope = "交易所" if sm.get("scope") == "exchange" else "个股"
            pct_note = f"，5年分位 {sm_pct}%" if sm_pct is not None else ""
            lines.append(
                f"- **融券余额增速（{scope}）:** {sm.get('growth_pct', '-')}%"
                f"{pct_note} [{sm.get('source', '')}]"
            )
        else:
            lines.append(
                f"- **融券余额增速:** 不可得"
                f"{_v3_ms_availability_note(avail, 'short_margin')}"
            )
        if nhr:
            partial = "（采样近似）" if nhr.get("partial") else ""
            lines.append(
                f"- **创新高个股占比:** {nhr.get('ratio_pct', '-')}%"
                f"，60日分位 {nhr.get('percentile_60d', '-')}%"
                f"{partial} [样本 {nhr.get('sample_size', '-')}]"
            )
        else:
            lines.append(
                f"- **创新高个股占比:** 不可得"
                f"{_v3_ms_availability_note(avail, 'new_high_ratio')}"
            )

    ms_evidences: list[tuple[str, str]] = []
    if sw:
        ms_evidences.append((
            "⚠️",
            f"申万行业 20 日涨跌 {sw.get('return_20d_pct', '-')}%"
            f"（{sw.get('index_code', '?')}）",
        ))
    else:
        ms_evidences.append(("❓", "申万行业指数不可得"))
    if nb or mf:
        parts = []
        if nb:
            parts.append(f"北向 {_v3_northbound_signal_label(nb)}")
        if mf:
            parts.append(f"主力近5日 {_fmt_v2(mf.get('net_sum_5d'))}")
        ms_evidences.append(("⚠️", "；".join(parts)))
    else:
        ms_evidences.append(("❓", "北向/主力资金数据不完整"))
    if erp:
        erp_desc = f"ERP {erp.get('raw', '-')}%（5年分位 {erp.get('percentile_5y', '-')}%）"
        if erp.get("partial"):
            erp_desc += "，样本日不足"
        ms_evidences.append(("⚠️", erp_desc))
    elif to:
        ms_evidences.append(("❓", "ERP 不可得，仅换手数据可参考"))
    if pcr:
        pct = pcr.get("percentile_5y") or pcr.get("percentile_60d")
        ms_evidences.append((
            "⚠️",
            f"50ETF 认沽认购比 {pcr.get('ratio', '-')}（分位 {pct if pct is not None else '-'}%）",
        ))
    if sm:
        ms_evidences.append((
            "⚠️",
            f"融券余额增速 {sm.get('growth_pct', '-')}%",
        ))
    lines.append("")
    lines.append(_evidence_conclusion_block(
        "市场结构呈现行业相对强弱、资金态度与 ERP/换手并列事实",
        ms_evidences,
    ))

    lines.append("")
    lines.append("🔍 **待独立验证:** Tushare 积分不足时见 availability 标注（sw_daily 需 5000 分，2000 分档走 akshare 回退）。")
    return "\n".join(lines)


def _section_events_timeline(collection: dict) -> str:
    """事件时间线（模块 3-3b 过渡段）。

    渲染 events 列表为时间降序表格，并附加 Template B 事件分类摘要。
    """
    events_all = collection.get("events") or []
    if not events_all or not isinstance(events_all, list) or len(events_all) == 0:
        return ""

    lines = ["## 3a. 事件时间线", ""]

    # 按 date 降序排列
    sorted_events = sorted(
        events_all,
        key=lambda e: str(e.get("date", "")),
        reverse=True,
    )
    shown = sorted_events[:15]

    lines.append("| 日期 | 类型 | 公告标题 | 影响维度 | 持续性质 |")
    lines.append("|------|------|---------|---------|---------|")
    for ev in shown:
        date = str(ev.get("date", ""))
        etype = str(ev.get("type", "other"))
        title = str(ev.get("title", ""))
        impact = str(ev.get("impact_dimension", ""))
        duration = str(ev.get("duration", ""))
        # Trim long titles for table display
        if len(title) > 50:
            title = title[:47] + "..."
        # Escape pipe chars
        title = title.replace("|", "/")
        lines.append(f"| {date} | {etype} | {title} | {impact} | {duration} |")

    hide_count = max(0, len(sorted_events) - 15)
    if hide_count > 0:
        lines.append(f"| ... | ... | （另有 {hide_count} 条事件未展示） | ... | ... |")

    lines.append("")
    lines.append(f"[来源: akshare stock_individual_notice_report / {len(sorted_events)} 条事件]")
    lines.append("")

    # ---- Template B classification cards ----
    cards = _get_analysis_cards(collection)
    event_classifications = cards.get("event_classifications") or []
    if event_classifications and isinstance(event_classifications, list):
        lines.append("**事件分类摘要**（规则推断，待 Claude 验证）:")
        lines.append("")
        for ec in event_classifications:
            ev_type = ec.get("event_label", ec.get("event_type", "其他"))
            ev_count = len(ec.get("events", []))
            direction_hint = ec.get("direction_hint", "")
            duration = ec.get("default_duration_hint", "")
            direction_note = ec.get("direction_note", "")
            summary_parts = [f"**{ev_type}** ({ev_count}条)"]
            if direction_hint:
                summary_parts.append(f"方向: {direction_hint}")
                if direction_note:
                    summary_parts.append(f"{direction_note}")
            else:
                summary_parts.append("[参考: 事件类型分类规则，不构成投资建议]")
            lines.append("  - " + " ".join(summary_parts))
        lines.append("")

    # Industry / market event placeholders
    ind_note = collection.get("_meta", {}).get("industry_events_note")
    mkt_note = collection.get("_meta", {}).get("market_events_note")
    if ind_note:
        lines.append(f"⏭️ **行业事件**: {ind_note}")
    if mkt_note:
        lines.append(f"⏭️ **市场事件**: {mkt_note}")
    if ind_note or mkt_note:
        lines.append("")

    return "\n".join(lines)


def _fmt_end_date(val: Any) -> str:
    """报告期展示（避免把 YYYYMMDD 当数字格式化）。"""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    digits = s.replace("-", "").replace("/", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def _fin_field_num(row: dict, *keys: str) -> float | None:
    """按字段名取数值；0.0 为合法值（不用 truthy 判断）。"""
    for key in keys:
        if key in row and row[key] is not None:
            return _safe_num(row[key])
    return None


def _fmt_peer_metric(v: Any, *, signed: bool = False) -> str:
    if v is None:
        return "-"
    return f"{float(v):+.2f}" if signed else f"{float(v):.2f}"


def _competitive_position_label(pct: float | None) -> str | None:
    """营收增速同行分位 → 龙头/挑战者/追赶者（分位越高增速越快）。"""
    if pct is None:
        return None
    if pct >= 75:
        return "龙头"
    if pct >= 40:
        return "挑战者"
    return "追赶者"


def _v3_trigger_c_active(market_structure: dict) -> bool:
    sw = market_structure.get("sw_index") or {}
    rel = sw.get("relative_vs_benchmark_pct")
    return rel is not None and abs(rel) >= 5


def _get_safe(rows: list[dict], field: str, default: Any = None) -> Any:
    """从已排序财务记录列表中提取最新非 None 值。"""
    for r in reversed(rows):
        v = r.get(field)
        if v is not None:
            n = _safe_num(v)
            return n if n is not None else v
    return default


def _coalesce_fin_field(rows: list[dict], *fields: str) -> float | None:
    """按字段顺序合并财务数值；0.0 视为有效值，不用 `or` 链。"""
    for field in fields:
        v = _get_safe(rows, field)
        if v is not None:
            n = _safe_num(v)
            if n is not None:
                return n
    return None


def _fmt_num(v: Any, *, decimals: int = 2, suffix: str = "") -> str:
    """安全格式化数值（兼容 numpy / Decimal 等经 _safe_num 归一化后的类型）。"""
    n = _safe_num(v)
    if n is None:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)
    return f"{n:.{decimals}f}{suffix}"


def _conclude_profit_structure(
    roe: float | None, gm: float | None, debt_ratio: float | None = None,
) -> str:
    """盈利结构结论：一句话判断。"""
    parts = []
    if roe is not None:
        if roe >= 15:
            parts.append("盈利能力较强，ROE 处于较优区间")
        elif roe >= 10:
            parts.append("盈利能力中等，ROE 处于中等水平")
        elif roe >= 6:
            parts.append("盈利能力一般，ROE 偏低")
        else:
            parts.append("盈利能力薄弱，ROE 显著偏低")
    if gm is not None:
        if gm >= 40:
            parts.append("毛利率较高，产品或服务具有较强定价权")
        elif gm >= 20:
            parts.append("毛利率处于中等水平，定价权一般")
        else:
            parts.append("毛利率偏低，产品或服务差异化不足")
    if roe is None and gm is None:
        return "盈利结构数据不足，无法形成有效判断"
    if debt_ratio is not None and debt_ratio > 70 and roe is not None and roe > 15:
        parts.append("需注意高杠杆对 ROE 的虚增效应")
    return "；".join(parts)


def _conclude_cash_flow_quality(
    cf_ratio: float | None, ar_growth: float | None,
    rev_growth: float | None, ocf: float | None,
    *,
    np_v: float | None = None,
) -> str:
    """现金流质量结论：一句话判断。"""
    if np_v is not None and np_v <= 0:
        if ocf is not None and ocf > 0:
            return (
                "利润为负但经营现金流为正，覆盖比不适用；"
                "需结合亏损原因判断现金流质量"
            )
        if ocf is not None:
            return "利润为负，经营现金流/净利润覆盖比不适用"
        return "利润为负，现金流质量数据不足"
    if cf_ratio is not None:
        if cf_ratio < 0:
            return "经营现金流/净利润覆盖比为负，比值不适用（利润与现金流方向不一致）"
        base = ""
        if cf_ratio >= 1.0:
            base = "现金流质量优秀，经营现金流充分覆盖净利润"
        elif cf_ratio >= 0.8:
            base = "现金流质量良好，经营现金流基本覆盖净利润"
        elif cf_ratio >= 0.5:
            base = "现金流质量偏弱，经营现金流与净利润存在一定背离"
        else:
            base = "现金流质量较差，经营现金流与净利润严重背离"
        if ar_growth is not None and rev_growth is not None:
            if ar_growth > rev_growth * 1.5:
                return base + "；应收增速远超营收，回款质量存疑"
            if ar_growth > rev_growth:
                return base + "；应收增速略高于营收，需关注回款节奏"
        return base
    if ocf is not None:
        return "经营现金流数据可用，但缺少净利润项，无法计算覆盖比"
    return "现金流质量数据不足，无法形成有效判断"


def _conclude_asset_liability(
    debt_ratio: float | None, em: float | None,
    ar_cur: float | None, inv_cur: float | None,
    rev_cur: float | None,
) -> str:
    """资产负债与扩产路径结论：一句话判断。"""
    parts = []
    if debt_ratio is not None:
        if debt_ratio >= 70:
            parts.append("资产负债率较高（>70%），财务杠杆偏大")
        elif debt_ratio >= 50:
            parts.append("资产负债率适中（50%-70%），杠杆水平合理")
        else:
            parts.append("资产负债率较低（<50%），财务结构稳健")
    if em is not None:
        if em > 3:
            parts.append("权益乘数偏高，扩产依赖外部融资")
        elif em < 1.5:
            parts.append("权益乘数偏低，扩产空间充足")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        working_ratio = (ar_cur + inv_cur) / rev_cur
        if working_ratio > 0.5:
            parts.append("运营资金占用较大，应收+存货占营收比例偏高")
        else:
            parts.append("运营资金管理效率良好")
    if not parts:
        return "资产负债数据不足，无法形成有效判断"
    return "；".join(parts)


def _evidence_strength_label(data_available: list[bool]) -> str:
    """根据可用数据项占比判断证据强度。"""
    if not data_available:
        return "数据不足"
    if not any(data_available):
        return "❓ 弱"
    ratio = sum(data_available) / len(data_available)
    if ratio >= 0.8:
        return "✅ 强"
    if ratio >= 0.5:
        return "⚠️ 中"
    return "❓ 弱"


def _historical_pe_median(val_cache: dict | None, dims: dict[str, dict]) -> float | None:
    """从 valuation 维度取 PE 历史中位数（LAW 3 可追溯）。"""
    summary = _v3_load_valuation_summary(dims, val_cache)
    if not summary:
        return None
    pe = summary.get("pe") or {}
    median = pe.get("median")
    return float(median) if median is not None else None


def _bull_bear_valuation_divergence_text(
    pe_pct: float,
    pe_zone: str | None,
    rev_yoy: float,
) -> str:
    """模块 5c：按 PE 历史区间位置分支 Bull/Bear 估值叙事。"""
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

    zone_label = pe_zone or "中间带"
    if pe_pct < ZONE_LOW_THRESHOLD:
        return (
            f"Bull 认为 PE 历史区间位置 {pe_pct:.1f}%（{zone_label}），"
            f"定价偏悲观、存在修复空间；Bear 认为营收同比 {rev_yoy:+.1f}%"
            f"不足以支撑估值向上修复。"
        )
    if pe_pct > ZONE_HIGH_THRESHOLD:
        return (
            f"Bull 认为营收同比 {rev_yoy:+.1f}% 可支撑当前定价；"
            f"Bear 认为 PE 历史区间位置 {pe_pct:.1f}%（{zone_label}），"
            f"估值透支、均值回归风险上升。"
        )
    return (
        f"Bull 认为营收同比 {rev_yoy:+.1f}% 与 PE 历史区间位置 {pe_pct:.1f}%"
        f"尚未完全定价；Bear 认为二者匹配度存疑，需观察增速能否维持。"
    )


def _financial_panorama_table(fin_list: list[dict]) -> list[str]:
    """模块4 业绩全景表（P1b：含 EPS 列）。"""
    if not fin_list:
        return []
    from lib.technical import sort_kline_asc
    rows = sort_kline_asc(fin_list)[-8:]
    lines = [
        "### 业绩全景（近8期）",
        "",
        "| 报告期 | ROE(%) | EPS | 营收 | 净利润 | 毛利率 | 净利率 |",
        "|--------|--------|-----|------|--------|--------|--------|",
    ]
    for r in rows:
        roe = r.get("roe", "-")
        eps = r.get("eps") if r.get("eps") is not None else r.get("basic_eps", "-")
        rev = _fmt_v2(r.get("revenue"))
        np_ = _fmt_v2(r.get("net_profit"))
        gm = r.get("grossprofit_margin") if r.get("grossprofit_margin") is not None else r.get("gross_margin")
        gm_s = f"{gm:.2f}" if isinstance(gm, (int, float)) else "-"
        npm = r.get("netprofit_margin") or r.get("np_margin")
        if npm is None:
            rev_n = _safe_num(r.get("revenue"))
            np_n = _safe_num(r.get("net_profit"))
            npm = (np_n / rev_n * 100) if rev_n and np_n and rev_n > 0 else None
        npm_s = f"{npm:.2f}" if isinstance(npm, (int, float)) else "-"
        lines.append(
            f"| {r.get('end_date', '?')} | {roe} | {eps} | {rev} | {np_} | {gm_s} | {npm_s} |"
        )
    lines.append("")
    lines.append("> EPS 来源: financials 维度 / fina_indicator.basic_eps 或 eps 字段")
    lines.append("")
    return lines


def _section_fundamentals_layered(
    dims: dict[str, dict], collection: dict, symbol: str, *, val_cache: dict | None = None,
) -> str:
    """v0.1.3 Phase 2：分层激活基本面 12 题 + LAW 10/14/15 完整框架。

    P0-3 升级：新增「核心判断摘要」与「12题回答状态表」。
    """
    lines = ["## 4. 静态基本面分析", ""]

    # ---- Template A: MD&A 快速扫描 ----
    cards = _get_analysis_cards(collection)
    mda_card = cards.get("mda_narrative")
    if mda_card and isinstance(mda_card, dict):
        gen_at = mda_card.get("generated_at", "")[:10] if mda_card.get("generated_at") else ""
        lines.append("> **MD&A 快速扫描** (自动计算) | 生成时间: " + gen_at)
        rg = mda_card.get("revenue_growth_yoy")
        pg = mda_card.get("profit_growth_yoy")
        gm = mda_card.get("gross_margin")
        gmc = mda_card.get("gross_margin_change")
        nm = mda_card.get("net_margin")
        nmc = mda_card.get("net_margin_change")
        ocf = mda_card.get("operating_cashflow")
        np = mda_card.get("net_profit")
        cq = mda_card.get("cashflow_quality_hint", "")
        ratio_str = ""
        if ocf is not None and np is not None and np != 0:
            ratio_str = f"{ocf/np:.2f}"
        roe = mda_card.get("roe")
        dr = mda_card.get("debt_ratio")
        _fmt_pct = lambda v: f"{v:.2f}%" if v is not None else "—"
        _fmt_pp = lambda v: f"{v:+.1f}pp" if v is not None else "—"
        rg_s = f"{rg:.2f}%" if rg is not None else "—"
        pg_s = f"{pg:.2f}%" if pg is not None else "—"
        lines.append(f"> - 营收增速: {rg_s} | 净利润增速: {pg_s}")
        lines.append(f"> - 毛利率: {_fmt_pct(gm)} ({_fmt_pp(gmc)}) | 净利率: {_fmt_pct(nm)} ({_fmt_pp(nmc)})")
        if ratio_str:
            lines.append(f"> - 经营现金流/净利润: {ratio_str} → 利润含金量: {cq}")
        if roe is not None:
            dr_label = f"{dr:.2f}%" if dr is not None else "—"
            lines.append(f"> - ROE: {roe:.2f}% | 负债率: {dr_label}")
        ns = mda_card.get("narrative_slot", "")
        if ns:
            lines.append(f"> - 叙事解读: {ns}")
        lines.append("")

    fin = _get_dim_data(dims, "financials")
    fin_list: list[dict] = []
    if fin and isinstance(fin, list):
        from lib.technical import sort_kline_asc
        fin_list = sort_kline_asc(fin)

    latest_fin = fin_list[-1] if fin_list else {}
    prev_fin = fin_list[-2] if len(fin_list) >= 2 else {}
    first_fin = fin_list[0] if fin_list else {}

    # =================================================================
    # 核心判断摘要（P0-3 升级）
    # =================================================================
    roe_val = _get_safe(fin_list, "roe")
    gm_val = _coalesce_fin_field(fin_list, "grossprofit_margin", "gross_margin")
    np_v = _safe_num(latest_fin.get("net_profit"))
    profit_dedt = _safe_num(latest_fin.get("profit_dedt"))
    debt_ratio = _coalesce_fin_field(fin_list, "debt_ratio", "debt_to_assets")
    em_val = _coalesce_fin_field(fin_list, "equity_multiplier", "em")
    ocf_val = _coalesce_fin_field(fin_list, "ocf", "n_cashflow_act")
    cf_ratio_val: float | None = None
    if ocf_val is not None and np_v is not None and np_v > 0:
        cf_ratio_val = ocf_val / np_v
    rev_cur = _safe_num(latest_fin.get("revenue"))
    rev_prev = _safe_num(prev_fin.get("revenue"))
    rev_yoy: float | None = None
    if rev_cur is not None and rev_prev is not None and rev_prev > 0:
        rev_yoy = (rev_cur - rev_prev) / rev_prev * 100
    ar_cur = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    ar_prev = _safe_num(prev_fin.get("accounts_receiv") or prev_fin.get("ar"))
    ar_growth: float | None = None
    if ar_cur is not None and ar_prev is not None and ar_prev > 0:
        ar_growth = (ar_cur - ar_prev) / ar_prev * 100
    inv_cur = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    cagr, cagr_years_span = _compute_metric_cagr(fin_list, "revenue")
    np_cagr, np_cagr_years_span = _compute_metric_cagr(fin_list, "net_profit")
    fin_rev_list = [r for r in fin_list if _safe_num(r.get("revenue")) is not None]

    lines.append("\n### 核心判断摘要\n")

    # 判断1: 盈利结构
    lines.append("#### 盈利结构")
    lines.append(f"[结论] {_conclude_profit_structure(roe_val, gm_val, debt_ratio)}")
    lines.append("")
    lines.append("[事实]")
    if roe_val is not None:
        lines.append(f"- ROE(TTM) = {_fmt_num(roe_val)}%")
    if gm_val is not None:
        lines.append(f"- 毛利率 = {_fmt_num(gm_val)}%")
    if debt_ratio is not None:
        lines.append(f"- 资产负债率 = {_fmt_num(debt_ratio)}%")
    if profit_dedt is not None and np_v is not None and np_v > 0:
        c4_ratio = profit_dedt / np_v
        lines.append(f"- 扣非/净利润 = {c4_ratio:.2f}")
    lines.append("")
    lines.append("[分析]")
    analysis_parts = []
    if roe_val is not None and gm_val is not None:
        if roe_val >= 15 and gm_val >= 40:
            analysis_parts.append("高 ROE × 高毛利率组合，盈利模式具备结构优势")
        elif roe_val >= 15 and gm_val < 20:
            analysis_parts.append("ROE 虽然较高但毛利率偏低，盈利依赖高周转或高杠杆驱动，需警惕可持续性")
        elif roe_val < 10 and gm_val >= 40:
            analysis_parts.append("高毛利率但低 ROE，可能费用率偏高或资产周转效率不足")
        else:
            analysis_parts.append(f"ROE={roe_val:.1f}%、毛利率={gm_val:.1f}%，盈利模式处于行业常见区间，需持续跟踪变化趋势")
    if debt_ratio is not None and debt_ratio > 70:
        analysis_parts.append("资产负债率偏高，需关注偿债风险与财务费用对利润的侵蚀")
    if profit_dedt is not None and np_v is not None and np_v > 0:
        c4_check = profit_dedt / np_v
        if c4_check < 0.7:
            analysis_parts.append("非经常性损益占比过大，净利润质量存疑")
    if not analysis_parts:
        analysis_parts.append("数据有限，无法进行充分的分析推理")
    lines.append("；".join(analysis_parts))
    lines.append("")
    e1_items = [roe_val is not None, gm_val is not None, debt_ratio is not None,
                profit_dedt is not None and np_v is not None and np_v > 0]
    lines.append(f"[证据强度: {_evidence_strength_label(e1_items)}]")
    lines.append("")

    # 判断2: 现金流质量
    lines.append("#### 现金流质量")
    lines.append(f"[结论] {_conclude_cash_flow_quality(cf_ratio_val, ar_growth, rev_yoy, ocf_val, np_v=np_v)}")
    lines.append("")
    lines.append("[事实]")
    if ocf_val is not None:
        lines.append(f"- 经营现金流 = {_fmt_v2(ocf_val)}")
    if np_v is not None:
        lines.append(f"- 净利润 = {_fmt_v2(np_v)}")
    if cf_ratio_val is not None:
        lines.append(f"- 经营现金流/净利润 = {cf_ratio_val:.2f}")
    if ar_growth is not None and rev_yoy is not None:
        lines.append(f"- 应收增速 vs 营收增速：{ar_growth:+.2f}% vs {rev_yoy:+.2f}%")
    lines.append("")
    lines.append("[分析]")
    cf_analysis = []
    if cf_ratio_val is not None:
        if cf_ratio_val >= 1.0:
            cf_analysis.append("经营现金流充分覆盖净利润，利润含金量高")
        elif cf_ratio_val >= 0.8:
            cf_analysis.append("经营现金流基本覆盖净利润，利润质量良好")
        else:
            cf_analysis.append("经营现金流覆盖不足，利润质量存疑，需关注应收与存货变化")
    if ar_growth is not None and rev_yoy is not None:
        if ar_growth > rev_yoy * 1.5:
            cf_analysis.append(f"应收增速远超营收增速，存在赊销膨胀或回款恶化的风险")
        elif ar_growth > rev_yoy:
            cf_analysis.append("应收增速略高于营收增速，需关注回款节奏变化")
        else:
            cf_analysis.append("应收增速低于营收增速，收入增长质量较高")
    if not cf_analysis:
        cf_analysis.append("数据有限，无法进行充分的现金流分析")
    lines.append("；".join(cf_analysis))
    lines.append("")
    e2_items = [ocf_val is not None, np_v is not None,
                ar_growth is not None and rev_yoy is not None]
    lines.append(f"[证据强度: {_evidence_strength_label(e2_items)}]")
    lines.append("")

    # 判断3: 资产负债与扩产路径
    lines.append("#### 资产负债与扩产路径")
    conclusion3 = _conclude_asset_liability(debt_ratio, em_val, ar_cur, inv_cur, rev_cur)
    lines.append(f"[结论] {conclusion3}")
    lines.append("")
    lines.append("[事实]")
    if debt_ratio is not None:
        lines.append(f"- 资产负债率 = {debt_ratio:.2f}%")
    if em_val is not None:
        lines.append(f"- 权益乘数 = {em_val:.2f}")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        wc_ratio = (ar_cur + inv_cur) / rev_cur * 100
        lines.append(f"- (应收+存货)/营收 = {wc_ratio:.1f}%")
    lines.append("")
    lines.append("[分析]")
    al_analysis = []
    if debt_ratio is not None:
        if debt_ratio >= 70:
            al_analysis.append("资产负债率偏高，扩产主要依赖负债融资，财务风险较大")
        elif debt_ratio >= 50:
            al_analysis.append("资产负债率适中，扩产可在负债与权益间灵活选择")
        else:
            al_analysis.append("资产负债率较低，扩产空间充足，可使用合理杠杆加速发展")
    if em_val is not None:
        if em_val > 3:
            al_analysis.append("权益乘数较高，扩产路径可能受限于融资能力")
        elif em_val < 1.5:
            al_analysis.append("权益乘数偏低，扩产路径可适度加杠杆")
    if ar_cur is not None and inv_cur is not None and rev_cur is not None and rev_cur > 0:
        wc_ratio_val = (ar_cur + inv_cur) / rev_cur
        if wc_ratio_val > 0.5:
            al_analysis.append("运营资金占用偏高，扩产时需关注现金流压力")
        else:
            al_analysis.append("运营资金占用较低，扩产的现金流压力较小")
    if not al_analysis:
        al_analysis.append("数据有限，无法进行充分的资产负债分析")
    lines.append("；".join(al_analysis))
    lines.append("")
    e3_items = [debt_ratio is not None, em_val is not None,
                ar_cur is not None and rev_cur is not None]
    lines.append(f"[证据强度: {_evidence_strength_label(e3_items)}]")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_financial_panorama_table(fin_list))

    val_data = _get_dim_data(dims, "valuation")
    pe_seq: list[float] = []
    pb_seq: list[float] = []
    ps_seq: list[float] = []
    val_sorted: list[dict] = []
    dv_ratio: float | None = None
    val_window_label = "历史"
    current_pe: float | None = None
    current_pb: float | None = None
    if val_data and isinstance(val_data, list):
        from lib.technical import sort_kline_asc
        from lib.valuation import valuation_window_label

        val_sorted = sort_kline_asc(val_data)
        pe_seq = [r.get("pe_ttm") for r in val_sorted if r.get("pe_ttm") is not None]
        pb_seq = [r.get("pb") for r in val_sorted if r.get("pb") is not None]
        ps_seq = [
            r.get("ps_ttm") or r.get("ps")
            for r in val_sorted
            if (r.get("ps_ttm") is not None or r.get("ps") is not None)
        ]
        for r in reversed(val_sorted):
            if r.get("dv_ratio") is not None:
                dv_ratio = _safe_num(r.get("dv_ratio"))
                break
        val_window_label = valuation_window_label(len(val_sorted))
        if pe_seq:
            current_pe = pe_seq[-1]
        if pb_seq:
            current_pb = pb_seq[-1]

    # 行业同行数据
    industry_peers = collection.get("industry_peers") or {}
    ms = collection.get("market_structure") or {}
    sw = ms.get("sw_index") or {}
    pmi_data = ms.get("pmi") or {}
    pe_pct, pb_pct_ext, _ = _v3_valuation_percentiles(dims, val_cache)
    trigger_c = _v3_trigger_c_active(ms)

    rev_cur = _safe_num(latest_fin.get("revenue"))
    rev_prev = _safe_num(prev_fin.get("revenue"))
    rev_yoy: float | None = None
    if rev_cur is not None and rev_prev is not None and rev_prev > 0:
        rev_yoy = (rev_cur - rev_prev) / rev_prev * 100

    np_cur = _safe_num(latest_fin.get("net_profit"))
    np_prev = _safe_num(prev_fin.get("net_profit"))
    ocf = _safe_num(latest_fin.get("ocf") or latest_fin.get("n_cashflow_act"))
    np_v = _safe_num(latest_fin.get("net_profit"))
    cf_ratio: float | None = None
    if ocf is not None and np_v is not None and np_v > 0:
        cf_ratio = ocf / np_v

    cagr, cagr_years_span = _compute_metric_cagr(fin_list, "revenue")
    np_cagr, np_cagr_years_span = _compute_metric_cagr(fin_list, "net_profit")
    fin_rev_list = [r for r in fin_list if _safe_num(r.get("revenue")) is not None]

    # =================================================================
    # 4a. 行业位置（3 题）
    # =================================================================
    lines.append("### 4a. 行业位置")
    lines.append("")

    # A-① 行业景气度
    lines.append("#### A-① 行业景气度")
    if sw and sw.get("return_20d_pct") is not None:
        lines.append(
            f"申万行业指数 {sw.get('index_code', '?')}（{sw.get('industry', '?')}）"
            f"近 20 日涨跌：**{sw['return_20d_pct']:+.2f}%**。"
        )
        rel = sw.get("relative_vs_benchmark_pct")
        if rel is not None:
            direction = "跑赢" if rel > 0 else "跑输"
            lines.append(f"相对沪深 300：{rel:+.2f}%（{direction}大盘）。")
        svi = sw.get("stock_vs_industry_pct")
        if svi is not None:
            direction = "跑赢" if svi > 0 else "跑输"
            lines.append(f"个股相对行业：{svi:+.2f}%（{direction}行业板块）。")
    else:
        lines.append("数据不足：[申万行业指数不可得，无法判断行业景气度]")
    if pmi_data.get("manufacturing_pmi") is not None:
        lines.append(
            f"制造业 PMI：**{pmi_data['manufacturing_pmi']:.1f}**（{pmi_data.get('month', '?')}，"
            f"{pmi_data.get('signal', '?')}）[来源: {pmi_data.get('source', 'akshare')}]"
        )
    else:
        lines.append("PMI/产量：数据不足：[宏观 PMI 数据源不可得；产量分项需行业数据库或 WebSearch 补充]")
    lines.append("")
    sw_ret = sw.get("return_20d_pct")
    a1_pitfall = (
        f"本次申万板块近 20 日涨跌为 {sw_ret:+.2f}%，若直接等同于公司经营改善，"
        f"可能忽略板块内部分化——需核对本公司营收增速是否与板块同向。"
        if sw_ret is not None else
        "申万行业指数本次不可得，若仅凭个股涨跌判断行业景气，可能把公司特异性波动误判为行业趋势。"
    )
    lines.append(_law10_hint(
        "行业景气度决定个股定价的贝塔部分，板块同向运动时个股 Alpha 的置信度更高。",
        a1_pitfall,
        [
            "对比板块内市值相近公司涨跌幅离散度",
            "查近期行业政策/供需公告（WebSearch）验证板块方向持续性",
            "观察板块成交量是否放大（放量趋势 vs 缩量反弹）",
        ],
    ))
    if trigger_c:
        lines.append("")
        lines.append("**[扩展激活 · 触发源 C]** 行业结构驱动：建议补充竞争格局变化分析——"
                     "板块相对大盘偏离显著时，需区分行业景气 vs 估值重估 vs 政策预期。")
        lines.append("**[扩展激活 · 行业政策]** 近 30 日行业政策/监管事件需 WebSearch 补充，"
                     "并追溯政策传导至公司收入/成本的具体路径。")
    lines.append("")

    # A-② 竞争位置
    lines.append("#### A-② 竞争位置")
    basic = _get_dim_data(dims, "basic_info")
    industry_name = ""
    if isinstance(basic, dict):
        industry_name = basic.get("industry", "") or basic.get("行业", "") or ""
    rev = _safe_num(latest_fin.get("revenue"))
    ry_pct = industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    ry_rank = industry_peers.get("rankings", {}).get("revenue_yoy_rank")
    ry_total = industry_peers.get("rankings", {}).get("revenue_yoy_total")
    target_ry = (industry_peers.get("target") or {}).get("revenue_yoy")
    peer_source = industry_peers.get("peer_source")
    if peer_source == "stock_basic_fallback":
        warn = industry_peers.get("warning") or "非申万 L3 成分股"
        lines.append(f"⚠️ {warn}")
    if rev is not None and industry_peers.get("sufficient"):
        lines.append(f"所属行业：{industry_name or industry_peers.get('industry_name') or '未知'}（申万分类）。")
        if ry_pct is not None and ry_rank and ry_total:
            pos = _competitive_position_label(ry_pct)
            ry_s = f"{target_ry:+.2f}%" if target_ry is not None else "—"
            lines.append(
                f"竞争位置参考：**{pos}**（营收增速 {ry_s}，同行排名 {ry_rank}/{ry_total}，分位 {ry_pct:.1f}%）。"
            )
        else:
            lines.append("数据不足：[同行营收增速排名字段不完整]")
    else:
        if industry_name:
            lines.append(f"所属行业：{industry_name}。")
        if peer_source == "stock_basic_fallback":
            lines.append("数据不足：[同行池非申万 L3 成分，分位排名已降级]")
        else:
            err = industry_peers.get("error", "缺少同行营收对比数据")
            lines.append(f"数据不足：[{err}]")
    lines.append("")
    a2_pitfall = (
        f"本次营收增速同行分位为 {ry_pct:.1f}%（排名 {ry_rank}/{ry_total}），"
        f"若据此直接认定竞争壁垒，可能忽略毛利率与 ROE 的转化效率。"
        if ry_pct is not None and ry_rank and ry_total else
        f"本次仅有行业名「{industry_name or '?'}」、缺少同行增速对比，"
        "不宜仅凭营收规模推断龙头地位。"
    )
    lines.append(_law10_hint(
        "竞争位置决定定价溢价/折价的合理性——龙头享有流动性溢价，追赶者需证明成长性。",
        a2_pitfall,
        [
            "对比毛利率与行业均值差异（见 A-③）",
            "查公司市占率数据（年报/行业报告）",
            "关注近 3 年竞争位置是上升还是下降趋势",
        ],
    ))
    lines.append("")

    # A-③ 毛利率 vs 行业中位数
    lines.append("#### A-③ 毛利率 vs 行业中位数")
    gross_margin = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
    if gross_margin is None:
        for r in reversed(fin_list):
            gross_margin = _fin_field_num(r, "gross_margin", "grossprofit_margin")
            if gross_margin is not None:
                break
    if gross_margin is not None:
        lines.append(f"最新报告期毛利率：**{gross_margin:.2f}%**。")
        peer_gms = [
            _fin_field_num(p, "gross_margin", "grossprofit_margin")
            for p in industry_peers.get("peers", [])
        ]
        peer_gms = [g for g in peer_gms if g is not None]
        if len(peer_gms) >= 3:
            from lib.valuation import median_of
            ind_med = median_of(peer_gms)
            diff = gross_margin - ind_med
            lines.append(f"同行毛利率中位数：**{ind_med:.2f}%**（样本 {len(peer_gms)} 家），差异 **{diff:+.2f}pp**。")
        else:
            lines.append("数据不足：[同行毛利率样本不足 3 家，需 income 表批量采集]")
    else:
        lines.append("数据不足：[毛利率字段不可得；需 fina_indicator.grossprofit_margin 或 income 表]")
    lines.append("")
    a3_pitfall = (
        f"本次毛利率 {gross_margin:.2f}%，若仅因高于行业均值就认定定价权，"
        "可能忽略销售费用率是否同步偏高（高毛利低净利模式）。"
        if gross_margin is not None else
        "本次毛利率不可得，不宜用 ROE 或营收增速间接替代毛利率做定价权判断。"
    )
    lines.append(_law10_hint(
        "毛利率是定价权的第一道防线——高毛利率意味着客户对价格不敏感或产品有差异化壁垒。",
        a3_pitfall,
        [
            "对比同行业公司毛利率离散度（若可得）",
            "观察毛利率近 3 年趋势：下降可能暗示竞争加剧或成本上升",
            "结合应收/现金流验证收入质量（见 C-③）",
        ],
    ))
    lines.append("")

    # =================================================================
    # 4b. 商业质量（3 题）
    # =================================================================
    lines.append("### 4b. 商业质量")
    lines.append("")

    # B-① 护城河来源
    lines.append("#### B-① 护城河来源")
    roe_now = _safe_num(latest_fin.get("roe"))
    roe_first = _safe_num(first_fin.get("roe"))
    if roe_now is not None:
        lines.append(f"当前 ROE：**{roe_now:.2f}%**。")
        if roe_first is not None and len(fin_list) >= 4:
            trend = "强化" if roe_now > roe_first + 2 else (
                "侵蚀" if roe_now < roe_first - 2 else "稳定")
            lines.append(f"近 {len(fin_list)} 期 ROE 趋势：{roe_first:.2f}% → {roe_now:.2f}%（{trend}）。")
        lines.append("")
        lines.append("护城河定性判断需结合以下维度（数据引擎提供定量基础，AI 做定性综合）：")
        lines.append(f"- **利润转化效率：** ROE={roe_now:.2f}%、扣非/净利润比例见 C-④")
        lines.append("- **现金流健康度：** 经营现金流/净利润覆盖比见 C-③")
        lines.append("- **收入可持续性：** 近 3 年营收 CAGR 见 C-①")
        lines.append("- **资产回报效率：** 杜邦拆解见 C-②")
    else:
        lines.append("数据不足：[缺少 ROE 数据，无法评估护城河]")
    lines.append("")
    lines.append(_law10_hint(
        "护城河是长期估值的锚——没有护城河的高增长公司，估值收缩速度往往快于预期。",
        (
            f"本次 ROE {roe_now:.2f}%"
            + (f"（{roe_first:.2f}% → {roe_now:.2f}%）" if roe_first is not None else "")
            + "，若直接等同于强护城河，可能忽略高杠杆或周期高点的一次性贡献（见 C-② 杜邦）。"
            if roe_now is not None else
            "本次 ROE 不可得，不宜用营收增速或 PE 分位间接替代护城河判断。"
        ),
        [
            "杜邦拆解 ROE 来源（见 C-②）",
            "对比同行 ROE 中位数（见可比公司表）",
            "查公司年报中「核心竞争力」部分与实际财务数据是否一致",
        ],
    ))
    lines.append("")

    # B-② 增长驱动力
    lines.append("#### B-② 增长驱动力")
    if rev_cur is not None and rev_prev is not None and rev_prev > 0:
        lines.append(f"最近一期营收同比：**{rev_yoy:+.2f}%**。")
    if np_cur is not None and np_prev is not None and np_prev > 0:
        np_yoy = (np_cur - np_prev) / np_prev * 100
        lines.append(f"最近一期净利润同比：**{np_yoy:+.2f}%**。")
    if not (rev_cur and rev_prev) and not (np_cur and np_prev):
        lines.append("数据不足：[缺少两期以上可比营收/净利润数据]")
    elif cagr is not None and cagr_years_span is not None:
        lines.append(
            f"近 {cagr_years_span:.1f} 年营收 CAGR：**{cagr:+.2f}%**（多年增长趋势锚点）。"
        )
        if rev_yoy is not None:
            if rev_yoy > cagr + 3:
                sustain = "加速，驱动力仍在强化"
            elif rev_yoy < cagr - 3:
                sustain = "减速，需关注驱动力是否切换"
            else:
                sustain = "与多年趋势基本一致，驱动力仍在持续"
            lines.append(
                f"驱动力持续性：**{sustain}**（最近同比 {rev_yoy:+.2f}% vs CAGR {cagr:+.2f}%）。"
            )
        gm_first = _fin_field_num(first_fin, "gross_margin", "grossprofit_margin")
        gm_latest = gross_margin
        if gm_latest is None:
            gm_latest = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
        if gm_first is not None and gm_latest is not None:
            gm_chg = gm_latest - gm_first
            if gm_chg > 1 and (cagr or 0) > 0:
                lines.append(
                    f"价驱动信号：毛利率 {gm_first:.2f}% → {gm_latest:.2f}%（{gm_chg:+.2f}pp），"
                    "收入增长可能含定价/结构升级贡献。"
                )
            elif abs(gm_chg) <= 1 and (cagr or 0) > 0:
                lines.append(
                    f"量驱动信号：毛利率基本稳定（{gm_first:.2f}% → {gm_latest:.2f}%），"
                    "增长更多来自规模扩张或份额提升。"
                )
        roe_first_v = _safe_num(first_fin.get("roe"))
        roe_latest_v = _safe_num(latest_fin.get("roe"))
        if roe_first_v is not None and roe_latest_v is not None:
            if roe_latest_v > roe_first_v + 3 and (cagr or 0) > 0:
                lines.append(
                    f"杠杆驱动警示：ROE {roe_first_v:.2f}% → {roe_latest_v:.2f}% 升幅较大，"
                    "需结合 C-② 杜邦验证是否来自权益乘数。"
                )
    lines.append("")
    lines.append("增长驱动力来源需结合以下判断：")
    lines.append("- **量驱动：** 收入增速 > 行业均值 → 份额扩张（收入/应收见 C-③）")
    lines.append("- **价驱动：** 毛利率扩张 + 收入增长 → 定价权提升（毛利率见 A-③）")
    lines.append("- **杠杆驱动：** ROE 提升来自权益乘数 → 不可持续（杜邦见 C-②）")
    lines.append("")
    b2_pitfall = (
        f"本次营收同比 {rev_yoy:+.2f}%，若等同于价值创造，可能忽略资本开支/ROIC——"
        "低回报扩张反而摧毁股东价值。"
        if rev_yoy is not None else
        "本次缺少两期可比营收，不宜用单季利润波动推断增长驱动力类型。"
    )
    lines.append(_law10_hint(
        "增长驱动力类型决定估值倍数——量价齐升（质量最高）vs 纯杠杆扩张（质量最低）。",
        b2_pitfall,
        [
            "对比营收增速与行业均值（见可比公司表）",
            "观察毛利率与营收增速方向是否一致（量价关系）",
            "关注业绩预告/管理层指引中的增长驱动力表述",
        ],
    ))
    lines.append("")
    lines.append("**[扩展激活 · 业绩预告]** 数据不足：[业绩预告数据源未接入，需 Tushare forecast / WebSearch 补充]；"
                 "若后续获取预告，应对比 B-② 驱动力是否发生转换。")
    lines.append("")

    # B-③ 现金流模式
    lines.append("#### B-③ 现金流模式")
    if ocf is not None and np_v is not None and np_v > 0:
        cf_ratio = ocf / np_v
        quality = "健康" if cf_ratio >= 0.8 else (
            "偏弱" if cf_ratio >= 0.5 else "严重背离")
        lines.append(f"经营现金流/净利润覆盖比：**{cf_ratio:.2f}**（{quality}）。")
        if cf_ratio < 0.8:
            lines.append("⚠️ 现金流覆盖比 < 0.8，建议扩展分析：收入确认质量、应收/存货变动（见 C-③ 交叉验证）。")
    elif ocf is None:
        lines.append("数据不足：[经营现金流字段不可得]")
    elif np_v is None or np_v <= 0:
        lines.append("数据不足：[净利润非正，无法计算覆盖比]")
    lines.append("")
    lines.append(_law10_hint(
        "现金流是利润的「含金量」检验——利润好看但现金流持续弱于利润，"
        "通常意味着应收膨胀、存货积压或收入确认激进。",
        (
            f"本次经营现金流/净利润 = {cf_ratio:.2f}，若仅看单期就认定利润质量差，"
            "可能忽略季节性备货——应对比连续 4 期趋势。"
            if cf_ratio is not None else
            "本次现金流覆盖比不可得，不宜用净利润同比单独判断利润含金量。"
        ),
        [
            "对比应收增速 vs 营收增速（CV-2）",
            "对比存货增速 vs 营收增速",
            "查看连续 4 期现金流覆盖比趋势方向",
        ],
    ))
    if cf_ratio is not None and cf_ratio < 0.8:
        lines.append("")
        lines.append("**[扩展激活 · 现金流覆盖 < 0.8]** 建议深度扫描收入确认质量："
                     "核对应收账龄、收入确认政策变更、大客户集中度变化。")
    lines.append("")

    # =================================================================
    # 4c. 财务质量（4 题）
    # =================================================================
    lines.append("### 4c. 财务质量")
    lines.append("")

    # C-① 近 3 年营收 CAGR
    lines.append("#### C-① 近 3 年营收 CAGR")
    if len(fin_rev_list) >= 2 and cagr is not None and cagr_years_span is not None:
        d0 = _fmt_end_date(fin_rev_list[0].get("end_date")) or "首期"
        d1 = _fmt_end_date(fin_rev_list[-1].get("end_date")) or "末期"
        lines.append(f"近 {cagr_years_span:.1f} 年营收 CAGR：**{cagr:+.2f}%**（{d0} → {d1}）。")
        if rev_yoy is not None:
            recent_trend = "加速" if rev_yoy > cagr + 3 else (
                "减速" if rev_yoy < cagr - 3 else "持平")
            lines.append(f"近一年趋势：{recent_trend}（最近一期同比 {rev_yoy:+.2f}% vs CAGR {cagr:+.2f}%）。")
    elif len(fin_rev_list) >= 2:
        lines.append("数据不足：[营收数据异常（首期或末期为零/负）]")
    else:
        lines.append("数据不足：[财务数据少于 2 期]")
    lines.append("")
    lines.append(_law10_hint(
        "营收 CAGR 是估值模型中增长率假设的锚——CAGR 的稳定性直接影响 DCF/g 值的置信度。",
        (
            f"本次 CAGR {cagr:+.2f}%、最近一期同比 {rev_yoy:+.2f}%，"
            "若机械外推历史 CAGR 至未来，可能在增速已放缓时高估。"
            if cagr is not None and rev_yoy is not None else
            "本次 CAGR 或近一年同比不可得，不宜用单季利润波动外推长期增长率。"
        ),
        [
            "对比净利润 CAGR 与营收 CAGR 是否同步（利润增速 > 收入增速 = 利润率扩张）",
            "结合行业景气度判断增长是行业性还是公司特异性",
            "关注近两季趋势：加速/减速背后的原因是什么",
        ],
    ))
    lines.append("")

    # C-② 杜邦拆解 ROE
    lines.append("#### C-② 杜邦拆解 ROE")
    roe_v = _safe_num(latest_fin.get("roe"))
    npm = _safe_num(latest_fin.get("netprofit_margin") or latest_fin.get("np_margin"))
    tat = _safe_num(latest_fin.get("asset_turnover") or latest_fin.get("assets_turn"))
    em = _safe_num(latest_fin.get("equity_multiplier") or latest_fin.get("em"))
    # 若杜邦字段不可得，尝试从已有数据计算
    if npm is None and np_cur is not None and rev_cur is not None and rev_cur > 0:
        npm = np_cur / rev_cur * 100
    dupont_available = npm is not None and tat is not None and em is not None
    if roe_v is not None:
        lines.append(f"ROE：**{roe_v:.2f}%**。")
        if dupont_available:
            lines.append(f"- **净利润率：** {npm:.2f}%（{'高利润率模式' if npm > 15 else '低利润率/高周转模式' if npm < 5 else '中等利润率'}）")
            lines.append(f"- **资产周转率：** {tat:.4f}（{'重资产' if tat < 0.5 else '轻资产/高周转' if tat > 1.5 else '中等周转'}）")
            lines.append(f"- **权益乘数：** {em:.2f}（{'高杠杆' if em > 3 else '低杠杆' if em < 1.5 else '中等杠杆'}）")
            dupont_roe = npm / 100 * tat * em * 100
            lines.append(f"- 杜邦 ROE 校验：{dupont_roe:.2f}%（{'与 ROE 一致' if abs(dupont_roe - roe_v) < 0.5 else '与 ROE 存在差异，可能存在口径问题'}）")
        else:
            lines.append("数据不足：[杜邦拆解字段不可得；fina_indicator 不含净利率/周转率/权益乘数，需 income + balance 表]")
        # ROE 变化超 ±5pp → 提示完整杜邦
        roe_prev_v = _safe_num(prev_fin.get("roe"))
        if roe_prev_v is not None and abs(roe_v - roe_prev_v) > 5:
            lines.append(f"⚠️ ROE 近两期变化超 ±5pp（{roe_prev_v:.2f}% → {roe_v:.2f}%），建议完整杜邦拆解。")
    else:
        lines.append("数据不足：[ROE 字段不可得]")
    lines.append("")
    lines.append(_law10_hint(
        "杜邦拆解回答「ROE 从哪来」——高净利率（品牌/技术壁垒）> 高周转（运营效率）> 高杠杆（财务风险）。",
        (
            f"本次 ROE {roe_v:.2f}%"
            + (f"、净利润率 {npm:.2f}%、周转 {tat:.4f}、权益乘数 {em:.2f}"
               if dupont_available else "")
            + "，若只看 ROE 绝对值不看结构，可能把高杠杆驱动的 ROE 误判为经营优秀。"
            if roe_v is not None else
            "本次 ROE 不可得，不宜用 PE 或营收增速间接替代 ROE 结构分析。"
        ),
        [
            "若 ROE 变化 > 5pp，追溯三大驱动因子的各自贡献变化",
            "对比同行 ROE 结构（高杠杆在加息周期更脆弱）",
            "关注权益乘数的负债结构（有息负债 vs 经营负债）",
        ],
    ))
    lines.append("")

    # C-③ 经营现金流/净利润 + 应收/存货增速对比 + CV-2
    lines.append("#### C-③ 现金流覆盖 + 应收/存货交叉验证")
    if ocf is not None and np_v is not None and np_v > 0:
        cf_ratio = ocf / np_v
        lines.append(f"- 经营现金流/净利润：**{cf_ratio:.2f}**")
    else:
        lines.append("数据不足：[经营现金流或净利润字段不可得，无法计算覆盖比]")
    # 应收增速 vs 营收增速 (CV-2)
    ar_cur = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    ar_prev = _safe_num(prev_fin.get("accounts_receiv") or prev_fin.get("ar"))
    inv_cur = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    inv_prev = _safe_num(prev_fin.get("inventory") or prev_fin.get("inventories"))
    rev_growth: float | None = rev_yoy
    ar_growth: float | None = None
    inv_growth: float | None = None
    if rev_growth is None and rev_cur is not None and rev_prev is not None and rev_prev > 0:
        rev_growth = (rev_cur - rev_prev) / rev_prev * 100
    if ar_cur is not None and ar_prev is not None and ar_prev > 0:
        ar_growth = (ar_cur - ar_prev) / ar_prev * 100
        lines.append(f"- 应收账款增速：**{ar_growth:+.2f}%**")
        if rev_cur is not None and rev_prev is not None and rev_prev > 0:
            rev_growth = (rev_cur - rev_prev) / rev_prev * 100
            lines.append(f"- 营收增速：**{rev_growth:+.2f}%**")
            if ar_growth > rev_growth * 1.5:
                cv2_status = "divergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 远超营收增速 {rev_growth:+.2f}%，"
                              "可能存在收入确认激进或回款恶化")
            elif ar_growth > rev_growth:
                cv2_status = "divergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 略高于营收增速 {rev_growth:+.2f}%，"
                              "关注回款节奏，暂未触发 1.5× 预警")
            else:
                cv2_status = "convergence"
                cv2_detail = (f"应收增速 {ar_growth:+.2f}% 低于营收增速 {rev_growth:+.2f}%，"
                              "收入增长质量较高")
            lines.append("")
            lines.append(_cv(cv2_status, "CV-2", "营收增长 vs 应收账款增长", cv2_detail, "中"))
    else:
        lines.append("数据不足：[缺少资产负债表应收/存货字段；需 balancesheet 或 akshare 财务摘要]")
    # 存货增速
    if inv_cur is not None and inv_prev is not None and inv_prev > 0:
        inv_growth = (inv_cur - inv_prev) / inv_prev * 100
        lines.append(f"- 存货增速：**{inv_growth:+.2f}%**")
        if rev_growth is not None and inv_growth > rev_growth * 1.5:
            lines.append("⚠️ 存货增速超营收增速 1.5×，关注存货积压风险。")
            lines.append("**[扩展激活 · 报表风险]** 存货扩张异常：建议核对产品滞销、渠道压货或会计政策变更。")
    lines.append("")
    c3_pitfall = (
        f"本次应收增速 {ar_growth:+.2f}% vs 营收增速 {rev_growth:+.2f}%，"
        "若仅因应收上升就认定收入造假，可能忽略大客户账期正常延长——需结合账龄结构验证。"
        if ar_growth is not None and rev_growth is not None else
        (
            f"本次现金流/净利润 = {cf_ratio:.2f}，若忽视应收/存货字段缺失，"
            "可能漏掉利润质量交叉验证。"
            if cf_ratio is not None else
            "本次缺少应收/存货与现金流数据，不宜单独用净利润同比判断收入质量。"
        )
    )
    lines.append(_law10_hint(
        "应收增速 > 营收增速是经典的利润质量预警信号——激进赊销可以短期推高营收，"
        "但最终会以坏账或回款恶化暴露。存货积压则可能意味着产品滞销。",
        c3_pitfall,
        [
            "查看连续 4 期以上应收/营收增速对比趋势",
            "查账龄结构（1 年以内应收占比，需财报附注）",
            "结合经营现金流方向确认（利润增长+现金流恶化=红色信号）",
        ],
    ))
    lines.append("")

    # C-④ 扣非/净利润
    lines.append("#### C-④ 扣非/净利润")
    profit_dedt = _safe_num(latest_fin.get("profit_dedt"))
    ratio_c4: float | None = None
    if profit_dedt is not None and np_v is not None and np_v > 0:
        ratio_c4 = profit_dedt / np_v
        quality_label = "健康" if ratio_c4 >= 0.9 else (
            "存在非经常性损益" if ratio_c4 >= 0.7 else "非经常性损益扭曲严重")
        lines.append(f"扣非净利润/净利润：**{ratio_c4:.2f}**（{quality_label}）。")
        if ratio_c4 < 0.7:
            lines.append("⚠️ 扣非/净利润 < 0.7，净利润被非经常性损益显著抬高。请查阅最新财报附注中「非经常性损益」明细。")
        lines.append(f"扣非净利润：{_fmt_v2(profit_dedt)}；净利润：{_fmt_v2(np_v)}。")
    elif np_v is not None and np_v <= 0:
        lines.append("数据不足：[净利润非正，扣非/净利润比值无意义]")
    else:
        lines.append("数据不足：[扣非净利润或净利润字段不可得]")
    lines.append("")
    lines.append(_law10_hint(
        "扣非/净利润比值反映利润的「可持续性」——卖资产、政府补贴、投资收益等非经常性损益"
        "不具有重复性，以此为基础的 PE 估值会产生误导。",
        (
            f"本次扣非/净利润 = {ratio_c4:.2f}（扣非 {_fmt_v2(profit_dedt)} / 净利 {_fmt_v2(np_v)}），"
            "若仅因单期扣非偏低就认定利润质量差，可能忽略一次性资产处置的偶发性。"
            if ratio_c4 is not None else
            "本次扣非或净利润不可得，不宜用 PE 分位单独判断盈利可持续性。"
        ),
        [
            "查看连续 4 期扣非/净利润比值趋势",
            "查阅财报「非经常性损益」明细（政府补贴/资产处置/投资收益占比）",
            "对比同行扣非/净利润比值（行业特征如地产/金融需特殊处理）",
        ],
    ))
    if ratio_c4 is not None and ratio_c4 < 0.7:
        lines.append("")
        lines.append("**[扩展激活 · 报表风险]** 扣非/净利润 < 0.7：建议查阅非经常性损益明细并对比连续 4 期趋势。")
    lines.append("")

    # =================================================================
    # 4d. 估值与预期（3 题 + LAW 15）
    # =================================================================
    lines.append("### 4d. 估值与预期")
    lines.append("")

    # D-① PE/PB 5 年历史分位
    lines.append("#### D-① PE/PB 历史分位")
    if pe_seq and current_pe is not None:
        from lib.valuation import valuation_summary

        vs = valuation_summary(
            pe_seq, pb_seq, ps_seq=ps_seq or None,
            dv_ratio=dv_ratio, window_label=val_window_label,
        )
        pe_info = vs["pe"]
        pb_info = vs["pb"]
        ps_info = vs.get("ps", {})
        if pe_info.get("current") is not None:
            pct_str = f"，{vs.get('window_label', val_window_label)} {pe_info['pct']:.1f}% 分位" if pe_info.get("pct") is not None else ""
            lines.append(f"- PE(TTM)：**{pe_info['current']:.2f}x**{pct_str}，处于历史**{pe_info.get('zone', '未知')}**区间。")
        else:
            lines.append(f"- PE(TTM)：{pe_info.get('reason', '不可得')}")
        if pb_info.get("current") is not None:
            pct_str = f"，{vs.get('window_label', val_window_label)} {pb_info['pct']:.1f}% 分位" if pb_info.get("pct") is not None else ""
            lines.append(f"- PB：**{pb_info['current']:.2f}x**{pct_str}，处于历史**{pb_info.get('zone', '未知')}**区间。")
        else:
            lines.append(f"- PB：{pb_info.get('reason', '不可得')}")
        if ps_info.get("current") is not None:
            pct_str = f"，{val_window_label} {ps_info['pct']:.1f}% 分位" if ps_info.get("pct") is not None else ""
            lines.append(f"- PS(TTM)：**{ps_info['current']:.2f}x**{pct_str}。")
        else:
            lines.append("数据不足：[估值序列无 ps/ps_ttm 字段]")
        if vs.get("dv_ratio") is not None:
            lines.append(f"- 股息率：**{vs['dv_ratio']:.2f}%**（最近交易日 dv_ratio）")
        else:
            lines.append("数据不足：[daily_basic 无 dv_ratio 股息率字段]")
        for w in vs.get("warnings", []):
            lines.append(f"⚠️ {w}")
    else:
        lines.append("数据不足：[估值历史序列不可得，建议配置 Tushare Token 获取 daily_basic]")
    pe_extreme = pe_pct is not None and (pe_pct >= 80 or pe_pct <= 20)
    pb_extreme = pb_pct_ext is not None and (pb_pct_ext >= 80 or pb_pct_ext <= 20)
    if pe_extreme:
        zone = "偏高（≥80% 分位）" if pe_pct >= 80 else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PE 处于历史 {zone}，建议触发完整预期差分析（见 D-③）。")
    if pb_extreme:
        zone = "偏高（≥80% 分位）" if pb_pct_ext >= 80 else "偏低（≤20% 分位）"
        lines.append(f"⚠️ PB 处于历史 {zone}，建议结合 D-③ 与资产质量验证预期差。")
    if pe_extreme or pb_extreme:
        lines.append("**[扩展激活 · 估值极端]** 完整预期差分析：① 隐含 g vs 历史 CAGR；② 一致预期（若可得）；③ 增长拐点催化剂。")
    lines.append("")
    d1_pitfall = (
        f"本次 PE 历史分位 {pe_pct:.1f}%、PB {pb_pct_ext:.1f}%，"
        "若把低分位直接等同于「便宜」，可能忽略盈利下修导致的「低 PE 陷阱」。"
        if pe_pct is not None and pb_pct_ext is not None else
        (
            f"本次 PE 历史分位 {pe_pct:.1f}%，需结合 PB 与盈利趋势判断是否为价值陷阱。"
            if pe_pct is not None else
            "本次估值分位不可得，不宜用当前 PE 绝对值替代历史分位判断。"
        )
    )
    lines.append(_law10_hint(
        "历史分位回答「当前估值在自身历史中处于什么位置」——极端分位不直接等于买卖信号，"
        "但意味着市场定价中包含了某种极端预期，需要验证这种预期是否合理。",
        d1_pitfall,
        [
            "PE 与 PB 分位是否一致（CV-3 已在模块 1 落地）",
            "对比行业中位数 PE（见 D-②）",
            "极低分位时检查是否有大额非经常性损益压低 PE",
        ],
    ))
    lines.append("")

    # D-② PE vs 行业中位数
    lines.append("#### D-② PE vs 行业中位数")
    premium: float | None = None
    ind_median: float | None = None
    if current_pe is not None and industry_peers.get("sufficient"):
        peers = industry_peers.get("peers", [])
        peer_pes = [
            float(p.get("pe_ttm")) for p in peers
            if p.get("pe_ttm") is not None and float(p.get("pe_ttm")) > 0
        ]
        if peer_pes:
            from lib.valuation import median_of
            ind_median = median_of([float(x) for x in peer_pes])
            premium = (current_pe - ind_median) / ind_median * 100
            lines.append(f"- 公司 PE(TTM)：**{current_pe:.2f}x**")
            lines.append(f"- 行业中位数 PE：**{ind_median:.2f}x**（{len(peer_pes)} 家同行）")
            lines.append(f"- 溢价/折价：**{premium:+.1f}%**（{'溢价' if premium > 0 else '折价'}）")
            if premium > 30:
                lines.append("公司 PE 显著高于行业，需验证：是否具备远超同行的盈利增长或护城河。")
            elif premium < -30:
                lines.append("公司 PE 显著低于行业，需验证：是否存在未被市场定价的负面因素。")
        else:
            lines.append("数据不足：[同行 PE 数据不可得]")
    elif current_pe is not None:
        lines.append(f"公司 PE(TTM)：**{current_pe:.2f}x**。")
        lines.append("数据不足：[同行数量不足 3 家或行业数据不可得，无法计算行业中位 PE]")
    else:
        lines.append("数据不足：[当前 PE 不可得]")
    lines.append("")
    d2_pitfall = (
        f"本次 PE {current_pe:.2f}x vs 行业中位 {ind_median:.2f}x（溢价 {premium:+.1f}%），"
        "若把溢价直接等同于「高估应回避」，可能忽略龙头合理溢价与成长性差异。"
        if premium is not None and current_pe is not None and ind_median is not None else
        (
            f"本次 PE {current_pe:.2f}x 但缺少 ≥3 家同行中位数，"
            "不宜用绝对 PE 水平判断行业相对贵贱。"
            if current_pe is not None else
            "本次 PE 不可得，不宜用 PB 分位替代行业相对估值判断。"
        )
    )
    lines.append(_law10_hint(
        "行业相对估值回答「市场给公司的定价是否比同行更高」——溢价可能来自"
        "更强的护城河/更高的增长预期，也可能只是市场情绪/流动性的暂时结果。",
        d2_pitfall,
        [
            "结合 A-② 竞争位置判断溢价合理性",
            "对比 ROE 行业中位（高质量公司值得高估值）",
            "观察溢价变化方向：扩大或收窄？",
        ],
    ))
    lines.append("")

    # D-③ LAW 15 隐性预期差
    lines.append("#### D-③ 隐性预期差（LAW 15）")
    ig: dict[str, Any] = {}
    if current_pe is not None and current_pe > 0:
        erp_data = ms.get("erp") or {}
        risk_free_raw = erp_data.get("dgs10")
        y10_source = ""
        if erp_data.get("source") and "+" in str(erp_data.get("source", "")):
            _, y10_source = str(erp_data["source"]).split("+", 1)
        risk_free_is_default = risk_free_raw is None
        if risk_free_is_default:
            risk_free = 0.025
            y10_source = "默认值（FRED/akshare 国债数据不可得）"
        else:
            risk_free = risk_free_raw / 100.0
        from lib.valuation import implied_growth
        ig = implied_growth(current_pe, risk_free, erp=0.06)
        if ig.get("g_implied") is not None:
            lines.append(f"- 当前 PE(TTM)：**{ig['pe']}x**")
            rf_label = (
                f"{ig['risk_free_rate'] * 100:.2f}% [推测，待验证：{y10_source}]"
                if risk_free_is_default else
                f"{ig['risk_free_rate'] * 100:.2f}%（{'FRED.DGS10 +' + y10_source if y10_source else y10_source}）"
            )
            lines.append(f"- 10Y 国债收益率：**{rf_label}**")
            lines.append(f"- ERP 假设：**6%**（保守基准）")
            lines.append(f"- 折现率 r：**{ig['r'] * 100:.2f}%**" if ig.get("r") else "- 折现率：不可得")
            lines.append(f"- **市场隐含增长率 g_implied：约 {ig['g_implied'] * 100:.2f}%**")
            lines.append("")
            cagr_text = f"{cagr:+.2f}%" if cagr is not None else "不可得"
            cagr_years_label = f"{cagr_years_span:.1f}" if cagr_years_span is not None else "?"
            np_cagr_text = f"{np_cagr:+.2f}%" if np_cagr is not None else "不可得"
            np_cagr_years_label = f"{np_cagr_years_span:.1f}" if np_cagr_years_span is not None else "?"
            lines.append(f"- 实际近 {cagr_years_label} 年营收 CAGR：{cagr_text}")
            lines.append(f"- 实际近 {np_cagr_years_label} 年净利润 CAGR：{np_cagr_text}")
            lines.append("- 一致预期：无可靠数据，跳过")
            lines.append("")
            g_implied_pct = ig["g_implied"] * 100
            ref_cagr = cagr if cagr is not None else np_cagr
            ref_label = "营收" if cagr is not None else ("净利润" if np_cagr is not None else None)
            ref_text = cagr_text if cagr is not None else np_cagr_text
            if risk_free_is_default:
                lines.append(
                    "**解读：** 10Y 国债使用默认假设 2.5% [推测，待验证]，"
                    "g_implied 与 CAGR 的方向性对比仅供参考，须先获取真实无风险利率。"
                )
            elif ref_cagr is not None and abs(g_implied_pct - ref_cagr) > 5:
                if g_implied_pct > ref_cagr:
                    lines.append(
                        f"**解读：** 市场隐含增长（{g_implied_pct:.2f}%）> 实际{ref_label} CAGR（{ref_text}），"
                        "市场定价偏乐观，需验证增长加速依据。"
                    )
                else:
                    lines.append(
                        f"**解读：** 市场隐含增长（{g_implied_pct:.2f}%）< 实际{ref_label} CAGR（{ref_text}），"
                        "市场定价偏悲观，可能存在低估（需结合风险评估）。"
                    )
                if cagr is not None and np_cagr is not None and abs(cagr - np_cagr) > 5:
                    lines.append(
                        f"补充：营收 CAGR（{cagr_text}）与净利润 CAGR（{np_cagr_text}）分化较大，"
                        "解读时优先核对利润率变化与非经常性损益。"
                    )
            elif ref_cagr is not None:
                lines.append(
                    f"**解读：** 市场隐含增长 ≈ 实际{ref_label} CAGR，定价基本反映历史增长，关注增长率拐点。"
                )
            else:
                lines.append("**解读：** 缺少实际 CAGR 对比，仅呈现隐含增长率供参考。")
            if ig.get("warning"):
                lines.append(f"\n⚠️ {ig['warning']}")
            if pe_extreme or pb_extreme:
                lines.append("")
                lines.append("**[扩展激活 · 完整预期差]** 估值处于历史极端区间："
                             "请逐项验证 g_implied 假设、盈利增速拐点、以及行业相对估值（D-②）是否一致。")
        else:
            lines.append(f"数据不足：[{ig.get('error', '隐含增长率计算失败')}]")
    else:
        lines.append("数据不足：[PE 非正或不可得，无法计算隐含增长率]")
    lines.append("")
    g_implied = ig.get("g_implied")
    d3_pitfall = (
        f"本次 PE {current_pe:.2f}x → g_implied 约 {g_implied * 100:.2f}%，"
        f"营收 CAGR {cagr:+.2f}%"
        + (f"、净利润 CAGR {np_cagr:+.2f}%" if np_cagr is not None else "")
        + "；若把两者差距直接等同于「高估/低估」，"
        "可能忽略 ERP 假设（6%）与永续增长简化模型的局限。"
        if current_pe and g_implied is not None and cagr is not None else
        (
            f"本次 g_implied 约 {g_implied * 100:.2f}%，但缺少可比 CAGR，"
            "不宜单独用隐含增长率做方向性结论。"
            if current_pe and g_implied is not None else
            "本次 PE 或 g_implied 不可得，戈登反推不适用。"
        )
    )
    lines.append(_law10_hint(
        "g_implied 回答「当前 PE 隐含了多高的永续增长预期」——是预期差分析的定量锚点。",
        d3_pitfall,
        [
            "核对 10Y 国债与 ERP 假设是否匹配当前宏观环境",
            "对比 g_implied 与近 3 年营收/净利润 CAGR、管理层指引增速",
            "PE>50 或国债为默认值时，仅作方向性参考，不作精确估值结论",
        ],
    ))
    lines.append("")

    # =================================================================
    # 同行可比公司表
    # =================================================================
    if industry_peers.get("sufficient"):
        lines.append("### 同行可比公司")
        lines.append("")
        target = industry_peers.get("target") or {}
        rankings = industry_peers.get("rankings") or {}
        lines.append(f"行业：{industry_peers.get('industry_name', '?')}（{len(industry_peers.get('peers', []))} 家同行）")
        lines.append("")
        lines.append("| 公司 | PE(TTM) | PB | ROE(%) | 营收增速(%) |")
        lines.append("|------|---------|-----|--------|------------|")
        # 目标公司行
        target_pe = _fmt_v2(target.get("pe_ttm"), "x") if target.get("pe_ttm") is not None else "-"
        target_pb = _fmt_v2(target.get("pb"), "x") if target.get("pb") is not None else "-"
        target_roe = _fmt_peer_metric(target.get("roe"))
        target_ry = _fmt_peer_metric(target.get("revenue_yoy"), signed=True)
        lines.append(f"| **本公司** | **{target_pe}** | **{target_pb}** | **{target_roe}** | **{target_ry}** |")
        for p in industry_peers.get("peers", [])[:10]:
            p_pe = _fmt_v2(p.get("pe_ttm"), "x") if p.get("pe_ttm") is not None else "-"
            p_pb = _fmt_v2(p.get("pb"), "x") if p.get("pb") is not None else "-"
            p_roe = _fmt_peer_metric(p.get("roe"))
            p_ry = _fmt_peer_metric(p.get("revenue_yoy"), signed=True)
            name = p.get("name", "") or p.get("symbol", "?")
            lines.append(f"| {name} | {p_pe} | {p_pb} | {p_roe} | {p_ry} |")
        lines.append("")
        # 分位排名
        rk_lines = []
        for metric, label in [("pe_ttm", "PE"), ("pb", "PB"), ("roe", "ROE"), ("revenue_yoy", "营收增速")]:
            pct_key = f"{metric}_pct"
            rk_key = f"{metric}_rank"
            tot_key = f"{metric}_total"
            pct_v = rankings.get(pct_key)
            rk_v = rankings.get(rk_key)
            tot_v = rankings.get(tot_key)
            if pct_v is not None:
                rk_lines.append(f"- {label}：分位 **{pct_v}%**（排名 {rk_v}/{tot_v}）")
        if rk_lines:
            lines.append("**分位排名（在同行中的位置）：**")
            lines.extend(rk_lines)
            lines.append("")
        lines.append("> 分位排名越高，表示在同行中数值越高。PE/PB 分位高 = 估值高于多数同行。ROE/营收增速分位高 = 盈利能力或增长优于同行。")

    # =================================================================
    # 12题回答状态表（P0-3 升级）
    # =================================================================
    lines.append("\n### 12题回答状态\n")
    lines.append("| # | 问题 | 状态 | 回答摘要 |")
    lines.append("|----|------|------|---------|")

    # Track question data availability at end of function
    # A-① 行业景气度
    _a1_ok = bool(sw and sw.get("return_20d_pct") is not None)
    _a1_s = f"申万板块近20日{sw['return_20d_pct']:+.2f}%" if _a1_ok else "数据不足"
    lines.append(f"| A-① | 行业景气度 | {'✅' if _a1_ok else '❌'} | {_a1_s} |")

    # A-② 竞争位置
    _a2_ok = bool(latest_fin.get("revenue") is not None and industry_peers.get("sufficient")
                  and industry_peers.get("rankings", {}).get("revenue_yoy_pct") is not None)
    _a2_ry_pct = industry_peers.get("rankings", {}).get("revenue_yoy_pct")
    _a2_s = f"营收增速分位{_a2_ry_pct:.1f}%" if _a2_ok else "数据不足"
    lines.append(f"| A-② | 竞争位置 | {'✅' if _a2_ok else '❌'} | {_a2_s} |")

    # A-③ 毛利率 vs 行业中位数
    _a3_gm = _fin_field_num(latest_fin, "gross_margin", "grossprofit_margin")
    _a3_ok = _a3_gm is not None
    _a3_s = f"毛利率{_a3_gm:.2f}%" if _a3_ok else "数据不足"
    lines.append(f"| A-③ | 毛利率 vs 行业中位数 | {'✅' if _a3_ok else '❌'} | {_a3_s} |")

    # B-① 护城河来源
    _b1_roe = _safe_num(latest_fin.get("roe"))
    _b1_ok = _b1_roe is not None
    _b1_s = f"ROE={_b1_roe:.2f}%" if _b1_ok else "数据不足"
    lines.append(f"| B-① | 护城河来源 | {'✅' if _b1_ok else '❌'} | {_b1_s} |")

    # B-② 增长驱动力
    _b2_ok = rev_cur is not None and rev_prev is not None and rev_prev > 0
    _b2_s = f"营收同比{rev_yoy:+.2f}%" if _b2_ok else "数据不足"
    lines.append(f"| B-② | 增长驱动力 | {'✅' if _b2_ok else '❌'} | {_b2_s} |")

    # B-③ 现金流模式（与核心判断摘要一致：回溯 ocf_val）
    _b3_ok = ocf_val is not None and np_v is not None and np_v > 0
    _b3_r = ocf_val / np_v if _b3_ok else None
    _b3_s = f"OCF/净利={_b3_r:.2f}" if _b3_ok else "数据不足"
    lines.append(f"| B-③ | 现金流模式 | {'✅' if _b3_ok else '❌'} | {_b3_s} |")

    # C-① 近 3 年营收 CAGR
    _c1_ok = len(fin_rev_list) >= 2 and cagr is not None
    _c1_s = f"CAGR={cagr:+.2f}%" if _c1_ok else "数据不足"
    lines.append(f"| C-① | 近3年营收CAGR | {'✅' if _c1_ok else '❌'} | {_c1_s} |")

    # C-② 杜邦拆解 ROE（须净利率+周转+权益乘数）
    _c2_roe = _safe_num(latest_fin.get("roe"))
    _c2_npm = _safe_num(latest_fin.get("netprofit_margin") or latest_fin.get("np_margin"))
    _c2_tat = _safe_num(latest_fin.get("asset_turnover") or latest_fin.get("assets_turn"))
    _c2_em = _safe_num(latest_fin.get("equity_multiplier") or latest_fin.get("em"))
    _c2_ok = (
        _c2_roe is not None and _c2_npm is not None
        and _c2_tat is not None and _c2_em is not None
    )
    _c2_s = (
        f"ROE={_c2_roe:.2f}%，npm×tat×em"
        if _c2_ok else "数据不足"
    )
    lines.append(f"| C-② | 杜邦拆解ROE | {'✅' if _c2_ok else '❌'} | {_c2_s} |")

    # C-③ 现金流覆盖 + 应收/存货交叉验证
    _c3_ar = _safe_num(latest_fin.get("accounts_receiv") or latest_fin.get("ar"))
    _c3_inv = _safe_num(latest_fin.get("inventory") or latest_fin.get("inventories"))
    _c3_ok = (
        ocf_val is not None and np_v is not None and np_v > 0
        and _c3_ar is not None and _c3_inv is not None
    )
    _c3_s = f"OCF/净利={_b3_r:.2f}，含应收+存货" if _c3_ok else "数据不足"
    lines.append(f"| C-③ | 现金流覆盖+应收/存货 | {'✅' if _c3_ok else '❌'} | {_c3_s} |")

    # C-④ 扣非/净利润
    _c4_pd = _safe_num(latest_fin.get("profit_dedt"))
    _c4_ok = _c4_pd is not None and np_v is not None and np_v > 0
    _c4_r = _c4_pd / np_v if _c4_ok else None
    _c4_s = f"扣非/净利={_c4_r:.2f}" if _c4_ok else "数据不足"
    lines.append(f"| C-④ | 扣非/净利润 | {'✅' if _c4_ok else '❌'} | {_c4_s} |")

    # D-① PE/PB 历史位置（须含历史位置百分比与中位数）
    _d1_pe_median = _historical_pe_median(val_cache, dims)
    _d1_ok = bool(pe_seq) and current_pe is not None and pe_pct is not None
    if _d1_ok and _d1_pe_median is not None:
        _d1_s = f"PE={current_pe:.2f}x，历史位置{pe_pct:.1f}%（中位数 {_d1_pe_median:.2f}x）"
    elif _d1_ok:
        _d1_s = f"PE={current_pe:.2f}x，历史位置{pe_pct:.1f}%"
    else:
        _d1_s = "数据不足"
    lines.append(f"| D-① | PE/PB历史分位 | {'✅' if _d1_ok else '❌'} | {_d1_s} |")

    # D-② PE vs 行业中位数
    _d2_ok = current_pe is not None and industry_peers.get("sufficient")
    _d2_s = f"PE={current_pe:.2f}x" if _d2_ok else "数据不足"
    lines.append(f"| D-② | PE vs行业中位数 | {'✅' if _d2_ok else '❌'} | {_d2_s} |")

    # D-③ 隐性预期差
    _d3_implied = None
    try:
        _d3_implied = ig.get("g_implied")  # type: ignore[union-attr]
    except (NameError, AttributeError):
        pass
    _d3_ok = current_pe is not None and current_pe > 0 and _d3_implied is not None
    _d3_s = f"g_implied={_d3_implied * 100:.2f}%" if _d3_ok else "数据不足"
    lines.append(f"| D-③ | 隐性预期差(LAW15) | {'✅' if _d3_ok else '❌'} | {_d3_s} |")

    lines.append("")
    lines.append("> ✅ = 有可用数据，❌ = 数据不足。状态反映数据完整性，不反映结论正误。")

    lines.append("")
    lines.append("🔍 **待独立验证:** 基本面分析基于第三方数据源（Tushare/akshare），应逐项与公司年报/季报原始数据交叉核对。行业分类可能因数据源口径不同存在差异。估值分位/隐含增长率不构成买卖判断。")
    return "\n".join(lines)


def _law10_hint(why: str, pitfall: str, next_steps: list[str]) -> str:
    """LAW 10 分析提示块（每题末尾固定格式）。"""
    lines = [
        "> [分析提示]",
        f"> - **为什么重要：** {why}",
        f"> - **常见分析误区：** {pitfall}",
    ]
    for i, step in enumerate(next_steps, 1):
        lines.append(f"> - **下一步交叉验证 {i}：** {step}")
    return "\n".join(lines)


def _periods_per_year(fin_list: list[dict]) -> int:
    """估算每年报告期数（4=季报，2=半年报，1=年报）。"""
    if len(fin_list) < 2:
        return 4
    dates = sorted(set(str(r.get("end_date", ""))[:6] for r in fin_list if r.get("end_date")))
    if len(dates) <= 1:
        return 4
    return min(4, len(dates))


def _compute_metric_cagr(
    fin_list: list[dict], field: str,
) -> tuple[float | None, float | None]:
    """从已排序财务列表计算 CAGR（%）及年跨度。"""
    rows = [r for r in fin_list if _safe_num(r.get(field)) is not None]
    if len(rows) < 2:
        return None, None
    first_v = _safe_num(rows[0].get(field))
    last_v = _safe_num(rows[-1].get(field))
    if first_v is None or last_v is None or first_v <= 0:
        return None, None
    span = max(1, (len(rows) - 1) / _periods_per_year(rows))
    if span >= 0.5:
        return ((last_v / first_v) ** (1 / span) - 1) * 100, span
    return (last_v - first_v) / first_v * 100, span


def _section_static_fundamentals(
    dims: dict[str, dict], collection: dict, *, val_cache: dict | None = None,
) -> str:
    # 委托给 Phase 2 分层基本面
    symbol = collection.get("symbol", "")
    return _section_fundamentals_layered(dims, collection, symbol, val_cache=val_cache)


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


def _v3_bull_bear_implied_growth(
    dims: dict[str, dict], market_structure: dict,
) -> tuple[dict[str, Any], float | None, float | None]:
    """复用 D-③：当前 PE + implied_growth + 实际 CAGR。"""
    val_data = _get_dim_data(dims, "valuation")
    current_pe: float | None = None
    if val_data and isinstance(val_data, list):
        from lib.technical import sort_kline_asc
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
        from lib.technical import sort_kline_asc
        fin_list = sort_kline_asc(fin)
        cagr, _ = _compute_metric_cagr(fin_list, "revenue")
        np_cagr, _ = _compute_metric_cagr(fin_list, "net_profit")
    return ig, cagr, np_cagr


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
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD

    lines = ["## 5. 市场分歧", ""]
    pe_pct, pb_pct, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    sw = market_structure.get("sw_index") or {}
    nb = market_structure.get("northbound") or {}
    industry_peers = collection.get("industry_peers") or {}
    rankings = industry_peers.get("rankings") or {}
    target = industry_peers.get("target") or {}
    fin = _get_dim_data(dims, "financials")
    latest_fin: dict = {}
    if fin and isinstance(fin, list):
        from lib.technical import sort_kline_asc
        latest_fin = sort_kline_asc(fin)[-1]

    # ── gather raw data for chains ──────────────────────────────────
    nb_v = _safe_num(nb.get("net_sum_10d"))
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
    mf = market_structure.get("moneyflow") or {}
    m_v = _safe_num(mf.get("net_sum_5d"))
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
    lines.append("🔍 **待独立验证:** 逻辑链为数据驱动的叙事框架，非方向判断；预期差须与财报 PDF 交叉核对。")
    return "\n".join(lines)


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

    lines = ["## 7. 风险与不确定性", ""]
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
    for idx, (slot_name, default_hint) in enumerate(_KNOWN_UNKNOWN_SLOTS, 1):
        lines.append(f"{idx}. **{slot_name}：** {default_hint}")
    scanner_unknowns = risk_data.get("known_unknowns") or []
    if scanner_unknowns:
        lines.append(f"4. **扫描器补充：** " + "；".join(scanner_unknowns[:3]))

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
                lines.append(f"5. **近期治理事件:** {'；'.join(gov_lines)}")

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


def _section_left_right_probability(
    collection: dict, symbol: str, dims: dict[str, dict], market_structure: dict,
    *, val_cache: dict | None = None,
) -> str:
    lines = ["## 6. 左侧/右侧概率判断", ""]
    lines.append("### 当前趋势位置（描述性参考，非单一结论）")
    kline = _get_dim_data(dims, "kline")
    trend_label = ""
    if kline and isinstance(kline, list):
        from lib.technical import sort_kline_asc, compute
        tech = compute(sort_kline_asc(kline))
        if "error" not in tech:
            trend_label = tech["trend"]["alignment"].get("trend_label", "")
            lines.append(f"- **技术结构:** {trend_label}")
    lines.append("- **阶段对照（均未选定，仅供概率权重参考）:**")
    lines.append(f"  {_v3_trend_stage_hints(trend_label)}")
    lines.append("")
    lines.append("### 左侧概率的主要支撑依据")
    left_items: list[str] = []
    pe_pct, pb_pct, _ = _v3_valuation_percentiles(dims, val_cache)
    from lib.valuation import ZONE_HIGH_THRESHOLD, ZONE_LOW_THRESHOLD
    if pe_pct is not None and pe_pct < ZONE_LOW_THRESHOLD:
        left_items.append(f"① PE 历史分位偏低（{pe_pct:.1f}%），证据强度：⚠️")
    erp = market_structure.get("erp")
    if erp and erp.get("percentile_5y") is not None and erp["percentile_5y"] >= 70:
        left_items.append(f"② ERP 5年分位偏高（{erp['percentile_5y']}%），证据强度：⚠️")
    if not left_items:
        left_items.append("① 左侧参考指标数据不足或未达到阈值，证据强度：❓")
    lines.append("")
    lines.append("### 右侧概率的主要支撑依据")
    right_items: list[str] = []
    if kline and isinstance(kline, list):
        from lib.technical import sort_kline_asc, compute
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
        from lib.technical import sort_kline_asc
        fin_sorted = sort_kline_asc(fin_lr)
        if len(fin_sorted) >= 2:
            rev_now = _safe_num(fin_sorted[-1].get("revenue"))
            rev_prev = _safe_num(fin_sorted[-2].get("revenue"))
            if rev_now is not None and rev_prev is not None and rev_prev > 0:
                rev_yoy_lr = (rev_now - rev_prev) / rev_prev * 100
                if rev_yoy_lr > 100:
                    continuation_hits.append(f"季度营收同比 {rev_yoy_lr:+.1f}%（>100%）")
    if kline and isinstance(kline, list):
        from lib.technical import sort_kline_asc, compute
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
    mf = market_structure.get("moneyflow") or {}
    pe_pct_lr, _, _ = _v3_valuation_percentiles(dims, val_cache)
    cv7_lr = _v3_cv7_block(pe_pct_lr, mf.get("net_sum_5d"))
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


def _section_technical_brief(
    dims: dict[str, dict], *, val_cache: dict | None = None,
) -> str:
    lines = ["## 8. 附录", ""]
    pe_table = _pe_band_markdown_table(dims, val_cache)
    if pe_table:
        lines.extend([pe_table, ""])
    lines.extend(["### 技术分析精简", ""])
    kline = _get_dim_data(dims, "kline")
    if not kline or not isinstance(kline, list):
        lines.append("- 趋势：K 线不可得")
        lines.append("- 量：—")
        lines.append("- 支撑阻力：—")
        return "\n".join(lines)
    from lib.technical import sort_kline_asc, compute
    tech = compute(sort_kline_asc(kline))
    if "error" in tech:
        lines.append(f"- 趋势：{tech.get('message', '计算失败')}")
        lines.append("- 量：—")
        lines.append("- 支撑阻力：—")
        return "\n".join(lines)
    trend = tech["trend"]["alignment"].get("trend_label", "—")
    sentences = tech["trend"].get("summary_sentences", [])
    vol_s = sentences[1] if len(sentences) > 1 else "量价关系见完整 K 线"
    sup = tech.get("support_resistance", {})
    sr = sup.get("summary", "—") if isinstance(sup, dict) else "—"
    lines.append(f"- **趋势:** {trend}")
    lines.append(f"- **量:** {vol_s}")
    lines.append(f"- **支撑阻力:** {sr}")
    return "\n".join(lines)


def _wrap_details(summary: str, content: str) -> str:
    if not content:
        return content
    return f"<details>\n<summary>{summary}</summary>\n\n{content}\n\n</details>"


def _report_toc() -> str:
    entries = [
        ("研究问题卡", "0-研究问题卡"),
        ("当前状态快照", "1-当前状态快照"),
        ("动态驱动", "2-动态驱动分析"),
        ("市场结构", "3-市场结构分析"),
        ("静态基本面（12题）", "4-静态基本面分析"),
        ("Bull/Bear 情景", "5-市场分歧"),
        ("左/右概率", "6-左侧右侧概率判断"),
        ("风险与不确定性", "7-风险与不确定性"),
        ("技术简报", "8-附录"),
        ("PE Band（5年轨道）", "pe-band5年轨道"),
        ("引用来源", "引用来源references"),
    ]
    lines = ["## 目录", ""]
    lines.extend(f"- [{label}](#{anchor})" for label, anchor in entries)
    return "\n".join(lines)


def _research_framework_mermaid() -> str:
    return """```mermaid
flowchart TD
  Q0[研究问题卡] --> S1[当前状态快照]
  S1 --> D2[动态驱动]
  D2 --> M3[市场结构]
  M3 --> F4[静态基本面]
  F4 --> B5[Bull/Bear 情景]
  B5 --> P6[左/右概率]
  P6 --> R7[风险与不确定性]
  R7 --> T8[技术简报]
  T8 --> A9[引用来源]
```"""


def _pe_band_markdown_table(
    dims: dict[str, dict], val_cache: dict | None = None,
) -> str:
    if val_cache is not None and "pe_band" in val_cache:
        band = val_cache["pe_band"]
    else:
        val_data = _get_dim_data(dims, "valuation")
        if not val_data or not isinstance(val_data, list):
            return ""
        from lib.valuation import pe_band_series
        band = pe_band_series(val_data)
        if val_cache is not None:
            val_cache["pe_band"] = band
    if not band.get("n_samples"):
        return ""
    years = band.get("years", 5)

    def _cell(v: Any) -> str:
        return str(v) if v is not None else "—"

    lines = [
        f"### PE Band（{years}年轨道）",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 样本数 | {_cell(band.get('n_samples'))} |",
        f"| 均值 (μ) | {_cell(band.get('mean'))} |",
        f"| +1σ | {_cell(band.get('upper_1σ'))} |",
        f"| -1σ | {_cell(band.get('lower_1σ'))} |",
        f"| +2σ | {_cell(band.get('upper_2σ'))} |",
        f"| -2σ | {_cell(band.get('lower_2σ'))} |",
        f"| 当前 PE | {_cell(band.get('current_pe'))} |",
        f"| 当前位置 | {_cell(band.get('current_position'))} |",
    ]
    return "\n".join(lines)


def _classify_sellside_rating(rating: str) -> str:
    """卖方评级归类（LAW 6：输出侧避免「买入」「目标价」字面）。"""
    s = str(rating)
    if "卖" in s or "减持" in s:
        return "看空"
    if "中性" in s:
        return "中性"
    if "增持" in s or "持有" in s:
        return "温和看多"
    if "买" in s:
        return "偏多"
    return "其他"


def _section_research_summary(
    collection: dict[str, Any], symbol: str, dims: dict,
) -> str:
    """机构研报与盈利预测展示段。

    数据来自 collect_research() → dims["research"] → research_summary。
    三层权限降级展示：
      1️⃣ 有评级+卖方预期价位（Tushare 10000+积分 / report_rc）
      2️⃣ 仅业绩预告（Tushare 2000+积分 / forecast）
      3️⃣ 全部不可得 → 无展示
    """
    # collection, symbol unused in v2 legacy; kept for signature consistency with v3 sections
    research_dim = dims.get("research", {})
    summary = research_dim.get("research_summary") or {}
    status = summary.get("status", "no_data")

    if status == "no_data":
        return ""

    lines: list[str] = []
    body: list[str] = []

    if status == "ok":
        ratings = summary.get("latest_ratings") or []
        if ratings:
            buckets: dict[str, int] = {}
            for r in ratings:
                label = _classify_sellside_rating(r.get("rating", ""))
                buckets[label] = buckets.get(label, 0) + 1
            parts = [f"{k} {v}" for k, v in buckets.items() if v]
            body.append(
                f"- **机构覆盖:** 近半年 {len(ratings)} 条评级（{' / '.join(parts)}）"
            )

        tp = summary.get("target_price_range")
        if tp:
            upper_note = ""
            if tp.get("avg_upper") is not None:
                upper_note = f"（卖方上限均值 {tp['avg_upper']} 元）"
            body.append(
                f"- **卖方预期价位:** {tp['min']} – {tp['max']} 元{upper_note}"
            )

        eps_forecasts = summary.get("eps_forecasts", [])
        if eps_forecasts:
            eps_rows = " | ".join(
                f"{e['quarter']}: {e['avg_eps']}（{e['n_analysts']}家）"
                for e in eps_forecasts[:4]
            )
            body.append(f"- **EPS预测（均值）:** {eps_rows}")

        if not body:
            return ""

    elif status == "ok_guidance_only" and summary.get("company_guidance"):
        g = summary["company_guidance"]
        pct_min = g.get("pct_change_min")
        pct_max = g.get("pct_change_max")
        profit_min = g.get("profit_min_100m")
        profit_max = g.get("profit_max_100m")
        guide_type = g.get("type", "")

        body.append(f"- **公司业绩预告:** {guide_type}")
        _pct_min = f"{pct_min}" if pct_min is not None else "?"
        _pct_max = f"{pct_max}" if pct_max is not None else "?"
        if profit_min is not None:
            body.append(
                f"  - 预计归母净利 **{profit_min}–{profit_max} 亿元**"
                f"（同比 {_pct_min}%–{_pct_max}%）"
            )
        else:
            body.append(
                f"  - 同比变动 {_pct_min}%–{_pct_max}%（利润率变动未披露）"
            )

    elif status == "ok_limited":
        body.append(f"- {summary.get('summary_text', '东方财富研报记录（无结构化评级摘要）')}")

    else:
        return ""

    lines.append("## 机构观点与盈利预测\n")
    lines.extend(body)

    # Template C: SentimentCard note
    sentiment_card = _get_analysis_cards(collection).get("sentiment")
    if sentiment_card and isinstance(sentiment_card, dict):
        eps_mean = sentiment_card.get("eps_forecast_mean")
        eps_high = sentiment_card.get("eps_forecast_high")
        eps_low = sentiment_card.get("eps_forecast_low")
        eps_count = sentiment_card.get("eps_forecast_count", 0)
        if eps_mean is not None:
            eps_range = ""
            if eps_low is not None and eps_high is not None:
                eps_range = f", range [{eps_low}-{eps_high}]"
            lines.append(
                f"\n> **研报情绪:** EPS一致预期 {eps_mean} (n={eps_count}){eps_range}"
            )
        slot_text = sentiment_card.get("sentiment_slot", "")
        if slot_text:
            lines.append(f"> *{slot_text}*")

    from datetime import datetime
    source_label = {
        "ok": "Tushare report_rc（10000+积分/特色大数据）",
        "ok_guidance_only": "Tushare forecast（2000+积分）",
        "ok_limited": "akshare（东方财富研报摘要，免注册）",
    }.get(status, "")
    if source_label:
        lines.append(
            f"\n> **数据来源:** {source_label} | 获取日期: {datetime.now().strftime('%Y-%m-%d')}"
        )

    lines.append(
        "\n🔍 **待独立验证:** 机构评级存在利益冲突，卖方预期价位不代表股价必然到达。"
        "业绩预告为公司单方披露，未经审计。"
    )
    return "\n".join(lines)


def _section_core_tension(
    collection: dict,
    symbol: str,
    dims: dict[str, dict],
    market_structure: dict,
    *,
    val_cache: dict | None = None,
) -> str:
    """模块 4–5 之间的核心矛盾小结（P2a，数据驱动填空）。"""
    pe_pct, _, pe_zone = _v3_valuation_percentiles(dims, val_cache)
    ig, cagr, np_cagr = _v3_bull_bear_implied_growth(dims, market_structure)
    ref_cagr = cagr if cagr is not None else np_cagr
    ref_label = "营收" if cagr is not None else ("净利润" if np_cagr is not None else None)
    variables: list[str] = []
    if pe_pct is not None:
        variables.append(
            f"估值历史区间位置（当前 {pe_pct:.1f}%，{pe_zone or '—'}）能否维持"
        )
    if ig.get("g_implied") is not None and ref_cagr is not None and ref_label:
        g_pct = ig["g_implied"] * 100
        variables.append(
            f"隐含增长 g_implied {g_pct:.1f}% 与实际{ref_label} CAGR {ref_cagr:+.1f}% 的缺口"
        )
    sw = market_structure.get("sw_index") or {}
    if sw.get("stock_vs_industry_pct") is not None:
        variables.append(
            f"个股相对行业超额 {sw['stock_vs_industry_pct']:+.2f}% 的可持续性"
        )
    if len(variables) < 2:
        return ""
    name = collection.get("name") or symbol
    lines = [
        f"> **核心矛盾小结** — 围绕 {name}（{symbol}）当前市场分歧，实质上集中在：",
    ]
    for i, var in enumerate(variables[:3], 1):
        lines.append(f"> {i}. {var}；")
    lines.append(
        "> 其他估值、资金和情绪的争议，本质上都在围绕上述变量摇摆。"
    )
    lines.append("")
    return "\n".join(lines)


def render_report_v3(collection: dict[str, Any], symbol: str, mode: str = "full") -> str:
    """v0.1.3 九模块研究备忘录。mode="brief" 仅输出精简简报。"""
    dims = _index_dims(collection)
    market_structure = collection.get("market_structure") or {}
    val_cache: dict = {}
    risk_data = _v3_build_risk_report(
        collection, dims, market_structure, val_cache=val_cache,
    )

    if mode == "brief":
        parts: list[str] = [
            _header_v2(collection, symbol),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        parts.extend([
            _section_executive_summary(collection, symbol, dims, val_cache=val_cache),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_bull_bear(
                collection, symbol, dims, market_structure, risk_data, val_cache=val_cache,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(collection, symbol, dims, market_structure, risk_data),
            ),
            _references_appendix(collection),
            _risk_footer(),
        ])
    else:
        parts: list[str] = [
            _header_v2(collection, symbol),
        ]
        extras = _render_engine_extras(collection)
        if extras:
            parts.append("\n".join(extras))
        parts.extend([
            _report_toc(),
            _section_research_question(collection, symbol, val_cache=val_cache),
            _section_snapshot(collection, symbol, dims, val_cache=val_cache),
            _section_dynamic_drivers(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_market_structure(
                collection, symbol, market_structure, val_cache=val_cache,
            ),
            _section_events_timeline(collection),
            _section_research_summary(collection, symbol, dims),
            _wrap_details(
                "展开：静态基本面（12题）",
                _section_static_fundamentals(dims, collection, val_cache=val_cache),
            ),
            _section_core_tension(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _section_bull_bear(
                collection, symbol, dims, market_structure, risk_data, val_cache=val_cache,
            ),
            _section_left_right_probability(
                collection, symbol, dims, market_structure, val_cache=val_cache,
            ),
            _wrap_details(
                "展开：风险与不确定性",
                _section_risk_uncertainty(collection, symbol, dims, market_structure, risk_data),
            ),
            _section_technical_brief(dims, val_cache=val_cache),
            _references_appendix(collection),
            _risk_footer(),
        ])
    return "\n\n".join(p for p in parts if p)


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
    pe_pct_s = "0" if pe_pct is None else str(pe_pct)
    pb_pct_s = "0" if pb_pct is None else str(pb_pct)
    ps_pct_s = "0" if ps_pct is None else str(ps_pct)
    pe_v = "0" if pe_val is None else str(pe_val)
    pb_v = "0" if pb_val is None else str(pb_val)
    ps_v = "0" if ps_val is None else str(ps_val)

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


def _html_research(research_md: str) -> str:
    """机构观点 HTML 段；无数据时返回空字符串。"""
    if not research_md:
        return ""
    import html as _html_mod
    body_lines: list[str] = []
    for line in research_md.splitlines():
        if line.startswith("## "):
            continue
        if line.startswith("> "):
            body_lines.append(
                f'<div class="vnote" style="margin-top:var(--space-3)">'
                f'{_html_mod.escape(line[2:])}</div>'
            )
        elif line.startswith("- "):
            body_lines.append(
                f'<div style="font-size:var(--text-sm);margin-bottom:var(--space-2)">'
                f'{_html_mod.escape(line[2:])}</div>'
            )
        elif line.startswith("  - "):
            body_lines.append(
                f'<div style="font-size:var(--text-sm);margin-left:var(--space-4);'
                f'margin-bottom:var(--space-1);color:var(--tx-s)">'
                f'{_html_mod.escape(line[4:])}</div>'
            )
        elif line.strip():
            body_lines.append(
                f'<div style="font-size:var(--text-sm);color:var(--tx-s)">'
                f'{_html_mod.escape(line)}</div>'
            )
    if not body_lines:
        return ""
    return f'''<section id="research">
  <div class="sh"><span class="st">机构观点与盈利预测</span><div class="sd"></div><span class="ss">卖方一致预期 · 公司业绩预告</span></div>
  <div class="card">
    {"".join(body_lines)}
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

    note = "营收/净利润字段为空（akshare接口降级）。" if not any(r.get("revenue") is not None for r in recent) else "财务数据来自第三方数据源，应与公司年报/季报交叉核对。"
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
        result["pe_above_median"] = (pe.get("current") is not None and pe.get("median") is not None
                                      and pe["current"] > pe["median"])

    pb = summary.get("pb", {})
    if pb.get("current") is not None:
        result["pb_val"] = f"{pb['current']:.2f}x"
        result["pb_pct"] = f"{pb['pct']:.1f}" if pb.get("pct") is not None else None
        result["pb_median"] = f"{pb['median']:.2f}" if pb.get("median") is not None else None
        result["pb_above_median"] = (pb.get("current") is not None and pb.get("median") is not None
                                      and pb["current"] > pb["median"])

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

    research_md = _section_research_summary(collection, symbol, dims)
    research_sec = _html_research(research_md)
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
{research_sec}
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

