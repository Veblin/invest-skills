"""
报告渲染器。

将分析结果渲染为符合 9 条 LAWs 的 Markdown 报告。

核心函数：
- render_report: 七维度报告
- save_html: 保存为 HTML
- render_compare_report: 对比报告
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def render_report(
    dimensions: list[dict[str, Any]],
    metadata: dict[str, Any],
    quality: Any | None = None,
) -> str:
    """渲染完整的七维度 Markdown 学习报告。

    Args:
        dimensions: collect_all 返回的 dimensions 列表
        metadata: {symbol, asset_type, fetched_at, sources}
        quality: 可选的 ReportQuality（来自 quality_scorer）

    Returns:
        符合全部 9 条 LAWs 的 Markdown 字符串
    """
    symbol = metadata.get("symbol", "未知")
    asset_type = metadata.get("asset_type", "stock")
    fetched_at = metadata.get("fetched_at", datetime.now(timezone.utc).isoformat())
    sources = metadata.get("sources", [])

    market_label = _market_label(asset_type)
    name = _dereference_name(dimensions, symbol)

    lines: list[str] = []

    # === 标题 ===
    lines.append(f"# {name}({symbol}) 投资学习分析报告")
    lines.append("")

    # === 质量评级（如可用）===
    if quality is not None:
        try:
            lines.append(f"> 📊 数据质量: {'★' * int(quality.overall_stars)}{'☆' * (5 - int(quality.overall_stars))} "
                         f"({quality.tier_display})")
        except Exception:
            pass
        lines.append(f"> 📅 采集时间: {fetched_at}")
        lines.append("")

    # === 首部风险声明 ===
    lines.append("## ⚠️ 风险声明")
    lines.append("")
    lines.append("> 本报告是**学习工具**的输出，**不构成投资建议**。所有分析性判断基于公开数据，")
    lines.append("> 每个数据点均标注来源——请自行交叉验证。LLM 生成的分析解释可能存在错误，")
    lines.append("> 关键决策请参考原始财报和官方公告。")
    lines.append("")
    lines.append("> **本报告不提供：**买卖建议、目标价预测、仓位建议、投资价值综合评分。")
    lines.append("> **技术指标**（MA/MACD）仅用于描述市场状态，不构成交易信号。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # === 七维度 ===
    dimension_order = [
        "basic_info", "fundamental", "industry",
        "valuation", "technical", "institutional",
        "sentiment", "macro",
    ]

    dim_index = 0
    for dim_name in dimension_order:
        dim = _find_dimension(dimensions, dim_name)
        if dim is None:
            continue
        dim_index += 1

        display = dim.get("display", dim.get("dimension", "未知维度"))
        status = dim.get("status", "unknown")
        meta = dim.get("_meta", {})

        # 维度权重星
        weight_stars = _dimension_weight_stars(dim_name)

        # 状态图标
        status_icon = {"available": "", "degraded": " ⚠️ 数据降级", "missing": " ⚠️ 数据不可得"}.get(status, "")

        lines.append(f"## {_dim_number(dim_index)}、{display} {weight_stars}{status_icon}")
        lines.append("")

        if status == "missing":
            error = dim.get("error", "数据不可得")
            attempted = meta.get("fallback_chain", meta.get("attempted_sources", []))
            lines.append(f"> ⚠️ 该维度数据不可得。{error}")
            if attempted:
                sources_str = " → ".join(str(s) for s in attempted)
                lines.append(f"> 尝试了: {sources_str}")
            lines.append("")
            lines.append(f"### 🔍 待独立验证项（{display}）")
            lines.append("")
            lines.append(f"- [ ] 自行查询 {display} 相关数据（建议: 东方财富/同花顺/公司公告）")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        # 渲染数据
        data = dim.get("data", {})
        if data:
            lines.append(_render_dimension_data(dim_name, data, meta, asset_type))
        else:
            lines.append(f"_该维度的结构化数据暂未渲染。查看原始数据。_")
            lines.append("")

        # 数据来源
        source_info = meta.get("source", "unknown")
        confidence = meta.get("confidence", "medium")
        confidence_star = {"high": "★★★★☆", "medium": "★★★☆☆", "low": "★★☆☆☆"}.get(confidence, "★★☆☆☆")
        lines.append(f"> 📡 数据来源: {source_info} | 可信度: {confidence_star}")
        if meta.get("warning"):
            lines.append(f"> ⚠️ {meta['warning']}")
        lines.append("")

        # 待验证项
        lines.append(f"### 🔍 待独立验证项（{display}）")
        lines.append("")
        lines.extend(_verification_items(dim_name))
        lines.append("")
        lines.append("---")
        lines.append("")

    # === 尾部：数据源清单 ===
    lines.append("## 📡 数据源清单")
    lines.append("")
    lines.append("| 维度 | 数据源 | 可信度 | 采集时间 | 状态 |")
    lines.append("|------|--------|--------|---------|------|")
    for dim in dimensions:
        display = dim.get("display", dim.get("dimension", ""))
        meta = dim.get("_meta", {})
        src = meta.get("source", "none")
        conf = meta.get("confidence", "low")
        ts = meta.get("fetched_at", "")
        status = "✅" if dim.get("status") == "available" else ("⚠️" if dim.get("status") == "degraded" else "❌")
        lines.append(f"| {display} | {src} | {_conf_label(conf)} | {ts[:16] if ts else '-'} | {status} |")
    lines.append("")

    # 质量评级
    if quality is not None:
        try:
            lines.append(f"**综合数据质量**: {'★' * int(quality.overall_stars)}{'☆' * (5 - int(quality.overall_stars))} "
                         f"({quality.tier_display})")
            for dn, dq in quality.dimension_qualities.items():
                lines.append(f"- {dq.display_name}: {'★' * int(dq.stars)}{'☆' * (5 - int(dq.stars))} — "
                             f"{', '.join(dq.adjustments) if dq.adjustments else '无异常'}")
        except Exception:
            pass
        lines.append("")

    # === 尾部风险声明 ===
    lines.append("---")
    lines.append("")
    lines.append("## ⚠️ 重要声明")
    lines.append("")
    lines.append("- 本报告由 AI 辅助生成，所有分析性判断基于公开数据。每个数据点均标注来源，")
    lines.append("  请自行交叉验证。LLM 生成的分析解释可能存在错误或遗漏。")
    lines.append("- **本报告不构成投资建议。** 不提供买卖建议、目标价预测、仓位建议。")
    lines.append("- 技术指标（MA/MACD）仅用于描述市场状态，不构成交易信号。")
    lines.append("- 单一来源的数据标注了不确定性。请优先采信多源交叉验证的数据。")
    lines.append("- 投资决策请基于原始财报、官方公告和独立研究。")
    lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# 维度数据渲染
# ------------------------------------------------------------------

def _render_dimension_data(dim_name: str, data: dict, meta: dict, asset_type: str) -> str:
    """将维度数据呈现为 Markdown 表格/文本。"""
    lines: list[str] = []

    if dim_name == "basic_info":
        lines.extend(_render_basic_info(data, meta))
    elif dim_name == "fundamental":
        lines.extend(_render_financials(data, meta))
    elif dim_name == "industry":
        lines.extend(_render_industry(data, meta))
    elif dim_name == "valuation":
        lines.extend(_render_valuation(data, meta))
    elif dim_name == "technical":
        lines.extend(_render_technical(data, meta))
    elif dim_name == "institutional":
        lines.extend(_render_institutional(data, meta))
    elif dim_name == "sentiment":
        lines.extend(_render_sentiment(data, meta))
    elif dim_name == "macro":
        lines.extend(_render_macro(data, meta))
    else:
        lines.append(f"```json")
        lines.append(_safe_json_summary(data))
        lines.append(f"```")
        lines.append("")

    return "\n".join(lines)


def _render_basic_info(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    src = meta.get("source", "unknown")
    ts = meta.get("fetched_at", "")[:10]

    lines.append("| 项目 | 内容 | 数据来源 |")
    lines.append("|------|------|---------|")
    for key, label in [("name", "公司名称"), ("industry", "所属行业"), ("area", "地区"),
                       ("total_market_value", "总市值"), ("list_date", "上市日期")]:
        val = data.get(key, "-")
        if val:
            lines.append(f"| {label} | {val} | [来源: {src} / {ts}] |")

    governance = data.get("governance", {})
    if governance:
        pledge = governance.get("pledge_ratio")
        if pledge is not None:
            flag = " ⚠️ >50%" if float(pledge) > 50 else (" ⚡ >30%" if float(pledge) > 30 else "")
            lines.append(f"| 质押比例 | {pledge}%{flag} | [来源: {src} / {ts}] |")
        goodwill = governance.get("goodwill_ratio")
        if goodwill is not None:
            flag = " ⚠️ >30%" if float(goodwill) > 30 else ""
            lines.append(f"| 商誉/净资产 | {goodwill}%{flag} | [来源: {src} / {ts}] |")

    lines.append("")
    return lines


def _render_financials(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    fin = data.get("financials", data)
    src = meta.get("source", "unknown")
    ts = meta.get("fetched_at", "")[:10]

    lines.append("### 盈利与成长")
    lines.append("")
    lines.append("| 指标 | 值 | 数据来源 |")
    lines.append("|------|-----|---------|")
    for key, label in [("revenue", "营业收入"), ("net_income", "归母净利润"),
                       ("roe", "ROE"), ("operating_margin", "营业利润率"),
                       ("operating_cf", "经营现金流")]:
        val = fin.get(key)
        if val is not None:
            unit = "%" if key in ("roe", "operating_margin") else "亿元"
            if key in ("roe", "operating_margin"):
                lines.append(f"| {label} | {val}% | [来源: {src} / {ts}] |")
            else:
                lines.append(f"| {label} | {val} | [来源: {src} / {ts}] |")

    lines.append("")
    return lines


def _render_industry(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    industry = data.get("industry_name", "未知")
    sector = data.get("sector", "")
    peers = data.get("peers", [])
    src = meta.get("source", "unknown")

    lines.append(f"- 行业: {industry}")
    if sector:
        lines.append(f"- 板块: {sector}")
    lines.append(f"- 同业数量: {data.get('peer_count', len(peers))} 家")
    if peers:
        peer_names = ", ".join(p.get("名称", p.get("name", "")) for p in peers[:8])
        lines.append(f"- 主要同业: {peer_names} [来源: {src}]")
    lines.append("")
    return lines


def _render_valuation(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    quote = data.get("quote", {})
    src = meta.get("source", "unknown")
    ts = meta.get("fetched_at", "")[:10]

    if quote:
        lines.append("| 指标 | 当前值 | 数据来源 |")
        lines.append("|------|--------|---------|")
        for key, label in [("price", "最新价"), ("change_pct", "涨跌幅"),
                           ("pe_ratio", "PE(TTM)"), ("pb_ratio", "PB"),
                           ("total_mv", "总市值")]:
            val = quote.get(key)
            if val is not None:
                unit = "%" if key == "change_pct" else ("元" if key == "price" else "")
                lines.append(f"| {label} | {val}{unit} | [来源: {src} / {ts}] |")
        lines.append("")

    return lines


def _render_technical(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    ts = data.get("technical_summary", {})

    if ts.get("error"):
        lines.append(f"技术指标计算失败: {ts['error']}")
        return lines

    lines.append("### 均线状态")
    lines.append(f"- 最新收盘价: {ts.get('latest_close', '-')}")
    lines.append(f"- MA5: {ts.get('MA5', '-')} | MA10: {ts.get('MA10', '-')} | MA20: {ts.get('MA20', '-')} | MA60: {ts.get('MA60', '-')}")
    lines.append(f"- 价格相对 MA60: {ts.get('price_vs_MA60', '-')}")
    lines.append("")

    macd = ts.get("MACD")
    if macd:
        lines.append("### MACD 状态")
        lines.append(f"- DIF: {macd.get('DIF', '-')} | DEA: {macd.get('DEA', '-')} | 柱: {macd.get('MACD_bar', '-')}")
        lines.append(f"- DIF 在零轴{'上方' if macd.get('DIF_above_zero') else '下方'}")
        lines.append(f"- DIF 在 DEA {'上方' if macd.get('DIF_above_DEA') else '下方'}")
        lines.append("")

    lines.append("> ⚠️ 均线和 MACD 仅用于理解市场状态，不构成交易信号。")
    lines.append("")
    return lines


def _render_institutional(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    nb = data.get("northbound_flow", {})
    if nb and "error" not in nb:
        recent = nb.get("recent_20_days", {})
        lines.append(f"- 北向资金近 20 日净流向: {recent.get('net_total', '-'):+.1f}亿元")
        lines.append(f"- 净流入天数: {recent.get('positive_days', '-')}/{recent.get('total_days', '-')}")
    elif nb:
        lines.append(f"- 北向资金: {nb.get('error', '数据不可得')}")
    lines.append("")
    return lines


def _render_sentiment(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    sent = data.get("sentiment", {})
    if sent and "error" not in sent:
        for k, v in sent.items():
            if k not in ("_meta", "error", "attempted_sources"):
                lines.append(f"- {k}: {v}")
    else:
        lines.append(f"- 情绪数据: {sent.get('error', '不可得')}")
    lines.append("")
    return lines


def _render_macro(data: dict, meta: dict) -> list[str]:
    lines: list[str] = []
    global_m = data.get("global_macro", {})
    if global_m:
        lines.append("### 全球宏观")
        for k, v in global_m.items():
            if k not in ("_meta", "error", "attempted_sources"):
                if isinstance(v, dict) and "latest" in v:
                    lines.append(f"- {k}: {v['latest']} [来源: {v.get('source', v.get('series_id', '-'))}]")
        lines.append("")

    china = data.get("china_macro", {})
    if china and "error" not in china:
        lines.append("### 中国宏观")
        for k, v in china.items():
            if k not in ("_meta", "error", "attempted_sources"):
                lines.append(f"- {k}: {v}")
        lines.append("")

    fx = data.get("fx_rates", {})
    if fx and "error" not in fx:
        lines.append("### 汇率")
        for k, v in fx.items():
            if k not in ("_meta", "error", "attempted_sources"):
                lines.append(f"- {k}: {v}")
        lines.append("")

    return lines


# ------------------------------------------------------------------
# HTML 导出
# ------------------------------------------------------------------

def save_html(markdown: str, path: str) -> str:
    """将 Markdown 报告保存为 HTML 文件（简单包装）。"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>投资学习分析报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; line-height: 1.8; color: #333; }}
  h1 {{ border-bottom: 3px solid #c41230; padding-bottom: 10px; }}
  h2 {{ border-bottom: 1px solid #eee; padding-bottom: 5px; margin-top: 30px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #f5f5f5; }}
  blockquote {{ border-left: 3px solid #c41230; margin: 10px 0; padding: 5px 15px; background: #fff5f5; }}
  code {{ background: #f0f0f0; padding: 2px 5px; border-radius: 3px; }}
  pre {{ background: #f8f8f8; padding: 15px; border-radius: 5px; overflow-x: auto; }}
</style>
</head>
<body>
{_markdown_to_html(markdown)}
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    return path


def _markdown_to_html(md: str) -> str:
    """简单的 Markdown → HTML 转换（不依赖外部库）。"""
    import re

    lines = md.split("\n")
    html_lines: list[str] = []
    in_table = False
    in_code = False
    in_list = False

    for line in lines:
        # 代码块
        if line.startswith("```"):
            if in_code:
                html_lines.append("</pre>")
                in_code = False
            else:
                html_lines.append("<pre>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line)
            continue

        # 表格
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            cells = [c.strip() for c in line.strip().split("|")[1:-1]]
            if all(c.replace("-", "").replace(":", "").strip() == "" for c in cells):
                continue  # 分隔行跳过
            tag = "th" if in_table and html_lines[-1] == "<table>" else "td"
            html_lines.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # 标题
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("> "):
            html_lines.append(f"<blockquote>{line[2:]}</blockquote>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("---"):
            html_lines.append("<hr>")
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{line}</p>")

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


# ------------------------------------------------------------------
# 对比报告
# ------------------------------------------------------------------

def render_compare_report(reports: list[dict]) -> str:
    """渲染双标的对比报告。

    Args:
        reports: [{symbol, dimensions, metadata}, ...]

    Returns:
        Markdown 对比报告
    """
    lines: list[str] = []
    symbols = [r.get("symbol", "?") for r in reports]

    lines.append(f"# 对比分析: {' vs '.join(symbols)}")
    lines.append("")
    lines.append("> ⚠️ 本报告是学习工具的输出，不构成投资建议。")
    lines.append("")

    # 通用维度并排
    dimension_order = ["fundamental", "valuation", "technical"]
    for dim_name in dimension_order:
        title = _dim_display(dim_name)
        lines.append(f"## {title}")
        lines.append("")

        rows: list[list[str]] = []
        headers = ["指标"]

        for i, report in enumerate(reports):
            headers.append(symbols[i])
            headers.append("来源")

        for i, report in enumerate(reports):
            dim = _find_dimension(report.get("dimensions", []), dim_name)
            if dim:
                data = dim.get("data", {})
                meta = dim.get("_meta", {})
                src = meta.get("source", "unknown")
                ts = meta.get("fetched_at", "")[:10]
                if dim_name == "fundamental":
                    fin = data.get("financials", data)
                    for key, label in [("revenue","营收"),("net_income","净利润"),("roe","ROE")]:
                        val = fin.get(key, "-")
                        row_found = False
                        for row in rows:
                            if row[0] == label:
                                row.extend([str(val), f"[{src}/{ts}]"])
                                row_found = True
                                break
                        if not row_found:
                            new_row = [label] + ["-"] * (i * 2) + [str(val), f"[{src}/{ts}]"]
                            rows.append(new_row)
                elif dim_name == "valuation":
                    quote = data.get("quote", {})
                    for key, label in [("pe_ratio","PE"),("pb_ratio","PB"),("price","最新价")]:
                        val = quote.get(key, "-")
                        row_found = False
                        for row in rows:
                            if row[0] == label:
                                row.extend([str(val), f"[{src}/{ts}]"])
                                row_found = True
                                break
                        if not row_found:
                            new_row = [label] + ["-"] * (i * 2) + [str(val), f"[{src}/{ts}]"]
                            rows.append(new_row)

        # 打印表格
        header_str = "| " + " | ".join(headers) + " |"
        sep = "|" + "|".join(["------"] * len(headers)) + "|"
        lines.append(header_str)
        lines.append(sep)
        for row in rows:
            row_str = "| " + " | ".join(str(c) for c in row) + " |"
            lines.append(row_str)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## ⚠️ 重要声明")
    lines.append("- 对比分析中每个数字均标注了数据来源和时间戳")
    lines.append("- 本报告不构成投资建议，不提供买卖建议或目标价")
    lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _find_dimension(dimensions: list[dict], name: str) -> dict | None:
    for d in dimensions:
        if d.get("dimension") == name:
            return d
    return None


def _market_label(asset_type: str) -> str:
    return {"stock": "A股", "hk": "港股", "etf": "ETF"}.get(asset_type, asset_type)


def _dereference_name(dimensions: list[dict], symbol: str) -> str:
    """从 basic_info 中获取公司名称。"""
    dim = _find_dimension(dimensions, "basic_info")
    if dim:
        data = dim.get("data", {})
        name = data.get("name", "")
        if name:
            return str(name)
    return symbol


def _dim_number(index: int) -> str:
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}
    return mapping.get(index, str(index))


def _dimension_weight_stars(dim_name: str) -> str:
    weights = {
        "basic_info": "★★★☆☆",
        "fundamental": "★★★★★",
        "industry": "★★★★☆",
        "valuation": "★★★☆☆",
        "technical": "★★★☆☆",
        "institutional": "★★★★☆",
        "sentiment": "★★★☆☆",
        "macro": "★★★☆☆",
    }
    return weights.get(dim_name, "★★★☆☆")


def _dim_display(dim_name: str) -> str:
    mapping = {
        "basic_info": "基本信息", "fundamental": "财务健康度",
        "industry": "行业产业链", "valuation": "估值",
        "technical": "市场状态", "institutional": "机构分析师",
        "sentiment": "情绪舆情", "macro": "宏观政策",
    }
    return mapping.get(dim_name, dim_name)


def _verification_items(dim_name: str) -> list[str]:
    items = {
        "basic_info": [
            "- [ ] 在东方财富/同花顺核对公司基本信息和控股股东",
            "- [ ] 查阅巨潮资讯最新公告",
        ],
        "fundamental": [
            "- [ ] 在东方财富/同花顺交叉核对 ROE 和毛利率",
            "- [ ] 阅读最新年报'经营讨论与分析'章节",
            "- [ ] 对比同行业 3 家公司的同指标",
        ],
        "industry": [
            "- [ ] 查阅最新行业研报，核对竞争格局",
            "- [ ] 关注行业协会/政府部门最新政策文件",
        ],
        "valuation": [
            "- [ ] 在东方财富/同花顺核对 PE/PB 当前值",
            "- [ ] 查阅券商一致预期，对比自己的 DCF 假设",
            "- [ ] 思考估值中枢是否可能因行业变化而下移",
        ],
        "technical": [
            "- [ ] 技术指标仅反映历史交易行为，不预测未来方向",
        ],
        "institutional": [
            "- [ ] 北向资金数据有 T+1 延迟",
            "- [ ] 机构持仓数据基于最新季报（可能有滞后）",
        ],
        "sentiment": [
            "- [ ] 股吧情绪噪声大，不可作为独立决策依据",
        ],
        "macro": [
            "- [ ] FRED 月度数据有 2-4 周滞后",
            "- [ ] 宏观分析不预测短期市场走势",
        ],
    }
    return items.get(dim_name, ["- [ ] 自行验证本维度关键数据"])


def _conf_label(confidence: str) -> str:
    return {"high": "★★★★☆", "medium": "★★★☆☆", "low": "★★☆☆☆"}.get(confidence, "???☆☆☆")


def _safe_json_summary(data: dict, max_depth: int = 2) -> str:
    """安全地将字典转为 JSON 摘要字符串。"""
    import json
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(data)


# ------------------------------------------------------------------
# 测试入口
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Mock 数据测试渲染管道
    mock_dimensions = [
        {
            "dimension": "basic_info",
            "display": "官方基本信息",
            "status": "available",
            "data": {
                "name": "贵州茅台",
                "industry": "白酒",
                "area": "贵州",
                "total_market_value": "2.1万亿",
                "list_date": "2001-08-27",
            },
            "_meta": {
                "source": "akshare.stock_individual_info_em",
                "source_group": "eastmoney",
                "fetched_at": "2026-06-10T10:00:00",
                "fallback_chain": ["efinance", "akshare"],
                "confidence": "medium",
                "latency_ms": 320,
                "success": True,
                "error_type": None,
            },
        },
        {
            "dimension": "fundamental",
            "display": "财务报告",
            "status": "available",
            "data": {
                "financials": {
                    "revenue": 1687,
                    "net_income": 882,
                    "roe": 31.2,
                    "operating_cf": 921,
                },
            },
            "_meta": {
                "source": "akshare.stock_financial_abstract",
                "source_group": "eastmoney",
                "fetched_at": "2026-06-10T10:00:01",
                "fallback_chain": ["efinance", "akshare"],
                "confidence": "medium",
                "latency_ms": 450,
                "success": True,
                "error_type": None,
            },
        },
        {
            "dimension": "sentiment",
            "display": "情绪舆情",
            "status": "degraded",
            "data": {
                "sentiment": {"error": "股吧数据接口暂时不可用"},
            },
            "_meta": {
                "source": "none",
                "source_group": "unknown",
                "fetched_at": "2026-06-10T10:00:02",
                "fallback_chain": ["akshare.stock_guba_hot_rank"],
                "confidence": "low",
                "latency_ms": 150,
                "success": False,
                "error_type": "empty",
            },
        },
    ]

    report = render_report(
        dimensions=mock_dimensions,
        metadata={
            "symbol": "600519",
            "asset_type": "stock",
            "fetched_at": "2026-06-10T10:00:00",
            "sources": ["akshare"],
        },
    )

    print(report[:3000])
    print("\n... (报告生成完毕)")
