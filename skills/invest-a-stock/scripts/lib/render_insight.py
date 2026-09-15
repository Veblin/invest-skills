"""Reader-first Markdown and offline HTML renderers for Insight ReportModel.

两个补充区块只做「如实呈现」：``本次新增发现`` 的数字原样来自 store 快照对比
（不重算），``分析链`` 只描述事实之间的同向/不同向关系并给出替代解释，**不写因果**。
"""
from __future__ import annotations

from html import escape
from typing import Any

_NO_DISCOVERY_LINE = "- 本次无新增研究发现。"
_NO_CHAIN_LINE = (
    "- 当前 Facts 不足以构成可验证的分析链（需两个以上事实并列出替代解释）；"
    "不预置机制叙述。"
)
_CHAIN_STATUS_LABEL = {
    "consistent": "一致性证据（不代表因果）",
    "mechanism_unconfirmed": "机制未证实",
}
_CHAIN_SECTION_NOTE = "本节只描述事实之间的同向/不同向关系；一致不等于因果，机制未证实不等于机制不存在。"


def _name(model: dict[str, Any]) -> str:
    return next((str(f["value"]) for f in model["facts"] if f["id"] == "basic.name"), model["symbol"])


def _beijing(timestamp: Any) -> str:
    """UTC/ISO → 北京时间标签；失败截断回退，不 ISO 直出（防同报告混时区）。"""
    if not timestamp:
        return "不可得"
    text = str(timestamp)
    try:
        from lib.shared_dates import fmt_fetched_at

        return fmt_fetched_at(text)
    except Exception:  # noqa: BLE001 - 格式化失败不该阻断渲染
        return text[:16]


def _source_label(model: dict[str, Any], fact_ids: list[str]) -> str:
    facts = {fact["id"]: fact for fact in model["facts"]}
    labels: list[str] = []
    for fact_id in fact_ids:
        fact = facts.get(fact_id)
        if not fact:
            continue
        source = ", ".join(fact.get("source_ids") or ["unknown"])
        labels.append(f"{fact_id} / {source} / {fact.get('as_of')}")
    return "；".join(labels) or "来源不可得"


def _fmt_number(value: Any) -> str:
    return "-" if value is None else str(value)


def _discovery_lines(model: dict[str, Any]) -> list[str]:
    """「本次新增发现」的 markdown 行（不含章节标题）。"""
    block = model.get("discoveries") or {}
    if block.get("status") != "changed":
        # 三条「无」路径（store 不可用 / 无历史 / 无显著变化）渲染同一固定串，
        # 不暴露 reason，也不出现「不可得」——0914 判定规则 #6。
        return [_NO_DISCOVERY_LINE]
    lines: list[str] = []
    old_label, new_label = block.get("old_at_label"), block.get("new_at_label")
    if old_label and new_label:
        lines.append(f"[来源: store 快照对比 {old_label} → {new_label}]")
    for item in block.get("items") or []:
        pct = item.get("pct")
        pct_text = f" ({pct:+.1f}%)" if isinstance(pct, (int, float)) else ""
        lines.append(
            f"- **{item.get('category_label')}** {item.get('label')}: "
            f"{_fmt_number(item.get('old'))} → {_fmt_number(item.get('new'))}{pct_text}"
        )
    events = block.get("events")
    if isinstance(events, dict) and events:
        parts: list[str] = []
        count_change = events.get("count_change")
        if isinstance(count_change, int) and count_change:
            parts.append(f"窗口内事件数 {count_change:+d}")
        if events.get("new_types"):
            parts.append("新增类型: " + "、".join(str(t) for t in events["new_types"]))
        if events.get("removed_types"):
            parts.append("消失类型: " + "、".join(str(t) for t in events["removed_types"]))
        window = events.get("window_days_changed")
        if isinstance(window, dict):
            parts.append(f"窗口 {window.get('old')} → {window.get('new')} 日")
        if parts:
            lines.append("- **事件** " + "；".join(parts))
    unchanged = block.get("unchanged_count")
    if isinstance(unchanged, int) and unchanged > 0:
        lines.append(f"（另有 {unchanged} 项关键字段无显著变化）")
    return lines or [_NO_DISCOVERY_LINE]


def _chain_lines(model: dict[str, Any]) -> list[str]:
    """「分析链」的 markdown 行（不含章节标题）。"""
    chains = model.get("analysis_chains") or []
    if not chains:
        return [_NO_CHAIN_LINE]
    lines: list[str] = []
    for chain in chains:
        status = _CHAIN_STATUS_LABEL.get(chain.get("association_status"), "关联边界未标注")
        lines += [
            "",
            f"- **{chain.get('id')}** ｜ {status}",
            f"  - 涉及事实：{'、'.join(f'`{fid}`' for fid in chain.get('fact_ids') or [])}",
            f"  - 关系：{chain.get('relation')}",
            f"  - {chain.get('mechanism')}",
        ]
        alternatives = chain.get("alternatives") or []
        if alternatives:
            marks = "①②③④⑤"
            joined = " ".join(f"{marks[i] if i < len(marks) else '-'} {text}" for i, text in enumerate(alternatives))
            lines.append(f"  - 替代解释：{joined}")
        verification = chain.get("verification") or {}
        if verification:
            lines.append(f"  - 验证动作：**{verification.get('event')}** — {verification.get('test')}")
        if chain.get("note"):
            lines.append(f"  - {chain['note']}")
    return lines


def render_insight_markdown(model: dict[str, Any]) -> str:
    """Render concise analysis, never raw module tables as the reading surface."""
    title = f"# {_name(model)} ({model['symbol']}) — 研究要点"
    status = "分析完成" if model["completion"] == "complete" else "分析未完成（证据不足）"
    profile = model.get("profile") or {}
    profile_text = "；".join(f"{key}={value}" for key, value in profile.items()) or "未提供"
    lines = [
        title, "",
        "> ⚠️ 风险提示：本报告是基于可追溯数据的研究整理，不构成任何投资建议、买卖指令或目标价预测。", "",
        f"**产物状态：** {status} ｜ **数据时间：** {_beijing(model.get('fetched_at'))} ｜ **研究档案：** {profile_text}",
        "",
        "## 可得结论",
    ]
    if model["findings"]:
        for finding in model["findings"]:
            mark = {"strong": "✅", "medium": "⚠️", "weak": "❓"}.get(finding["evidence_strength"], "❓")
            lines += [f"- {mark} **{finding['claim']}** [来源: {_source_label(model, finding['fact_ids'])}]",]
            if finding["counter_fact_ids"]:
                lines.append(f"  - 反证/限制：{_source_label(model, finding['counter_fact_ids'])}")
    else:
        lines.append("- 当前没有满足来源、反证与可解释性门槛的结论。")
    tension = model["core_tension"]
    lines += ["", "## 核心矛盾", f"**{tension['claim']}**"]
    if tension["fact_ids"]:
        lines.append(f"[来源: {_source_label(model, tension['fact_ids'])}]")
    lines += ["", "## 本次新增发现"]
    lines += _discovery_lines(model)
    lines += ["", "## 分析链", _CHAIN_SECTION_NOTE]
    lines += _chain_lines(model)
    lines += ["", "## 支持、反证与关联边界"]
    lines.append("所有 Finding 仅描述可用 Facts 的位置或同向/不同向关系；未使用识别证据时不将关联表述为因果。")
    lines += ["", "## 观察节点与更新规则"]
    observations = [finding.get("verification") for finding in model["findings"] if finding.get("verification")]
    if observations:
        for observation in observations[:3]:
            lines.append(f"- **{observation.get('event', '后续披露')}：** {observation.get('test', '核对相关事实')}。")
    else:
        lines.append("- 等待补齐可验证数据后再建立观察节点。")
    lines += ["", "## 已知未知与补证路径"]
    if model["gaps"]:
        for gap in model["gaps"][:3]:
            attempted = "、".join(str(item) for item in gap.get("attempted_sources") or [] if item) or "未记录"
            lines.append(f"- **{gap['dimension']}：** {gap['reason']}；已尝试：{attempted}。")
    else:
        lines.append("- 当前采集维度未报告关键缺口；这不等同于相关信息不存在。")
    lines += ["", "## 证据底稿", "<details><summary>展开 Facts 与来源清单</summary>", ""]
    for fact in model["facts"]:
        formula = f"；公式: `{fact['formula']}`" if fact.get("formula") else ""
        lines.append(f"- `{fact['id']}` = {fact['value']}（{fact['basis']}；截至 {fact['as_of']}；来源: {', '.join(fact['source_ids'])}{formula}）")
    lines += ["", "</details>", "", "> ⚠️ 免责声明：数据可能存在滞后、缺失或口径差异；请以公司公告和原始来源为准。本报告不构成投资建议。", ""]
    return "\n".join(lines)


def render_insight_html(model: dict[str, Any]) -> str:
    """Single-file HTML with finding-to-evidence navigation and offline controls."""
    facts = model["facts"]
    findings = model["findings"]
    cards = "".join(
        f'<article class="finding"><h3>{escape(finding["claim"])}</h3><p><b>证据强度：</b>{escape(finding["evidence_strength"])} · <a href="#evidence-{escape(finding["id"])}">查看事实与反证</a></p></article>'
        for finding in findings
    ) or '<article class="finding"><h3>暂无可交付结论</h3><p>当前证据不足，优先查看缺口与补证路径。</p></article>'
    fact_rows = "".join(
        f'<tr data-group="{escape(fact["id"].split(".")[0])}"><td id="fact-{escape(fact["id"])}"><code>{escape(fact["id"])}</code></td><td>{escape(str(fact["value"]))}</td><td>{escape(fact["basis"])}</td><td>{escape(str(fact["as_of"]))}</td><td title="公式：{escape(str(fact.get("formula") or "原始字段"), quote=True)}">{escape(", ".join(fact["source_ids"]))}</td></tr>'
        for fact in facts
    )
    evidence = "".join(
        f'<article class="evidence" id="evidence-{escape(finding["id"])}"><h3>{escape(finding["id"])}</h3><p><b>支持：</b>{escape(_source_label(model, finding["fact_ids"]))}</p><p><b>反证/限制：</b>{escape(_source_label(model, finding["counter_fact_ids"]))}</p><p><b>未知：</b>{escape("、".join(finding["unknown_ids"]) or "无")}</p></article>'
        for finding in findings
    )
    gaps = "".join(f'<li><b>{escape(gap["dimension"])}：</b>{escape(str(gap["reason"]))}；已尝试：{escape("、".join(str(x) for x in gap.get("attempted_sources") or [] if x) or "未记录")}</li>' for gap in model["gaps"]) or "<li>未报告关键缺口；不等同于信息完整。</li>"
    status = "分析完成" if model["completion"] == "complete" else "分析未完成（证据不足）"
    # 与 markdown 同源的文本：同一 model 键、同一格式化函数，防止两条渲染路径漂移。
    discoveries_html = escape("\n".join(_discovery_lines(model)))
    chains_html = escape("\n".join(_chain_lines(model)))
    fetched_label = escape(_beijing(model.get("fetched_at")))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(_name(model))} — 研究要点</title>
<style>:root{{--bg:#10131a;--card:#171c26;--text:#e9edf5;--muted:#aeb9cc;--line:#303a4b;--accent:#7db1ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:24px}}section{{margin:24px 0}}.status,.finding,.evidence{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:10px 0}}.finding h3,.evidence h3{{margin:0 0 8px;font-size:16px}}a{{color:var(--accent)}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}}select{{padding:7px;background:var(--card);color:var(--text);border:1px solid var(--line)}}.note{{color:var(--muted)}}.plain{{margin:0;white-space:pre-wrap;font:inherit;color:inherit}}@media print{{body{{background:white;color:black}}.status,.finding,.evidence{{border-color:#aaa;background:white}}}}</style></head><body><main>
<h1>{escape(_name(model))} ({escape(model["symbol"])}) — 研究要点</h1><div class="status"><b>产物状态：</b>{status} ｜ <b>数据时间：</b>{fetched_label} ｜ <b>契约：</b>{escape(model["report_contract_version"])}</div>
<p class="note">⚠️ 本页用于研究与数据核验，不构成任何投资建议、买卖指令或目标价预测。</p><section><h2>可得结论</h2>{cards}</section>
<section><h2>核心矛盾</h2><div class="finding">{escape(model["core_tension"]["claim"])}</div></section>
<section><h2>本次新增发现</h2><div class="finding"><pre class="plain">{discoveries_html}</pre></div></section>
<section><h2>分析链</h2><div class="finding"><p class="note">{escape(_CHAIN_SECTION_NOTE)}</p><pre class="plain">{chains_html}</pre></div></section>
<section><h2>证据与反证</h2>{evidence}</section>
<section><h2>数据探索</h2><label>筛选事实维度 <select id="dimension"><option value="all">全部</option><option value="valuation">估值</option><option value="financials">财务</option><option value="technical">技术</option><option value="quote">行情</option><option value="basic">基本信息</option></select></label><p class="note">字段来源单元格可悬停查看公式；筛选状态始终可见，离线可用。</p><table><thead><tr><th>Fact</th><th>数值</th><th>口径</th><th>截至</th><th>来源 / 公式</th></tr></thead><tbody id="facts">{fact_rows}</tbody></table></section>
<section><h2>已知未知与补证路径</h2><ul>{gaps}</ul></section><p class="note">免责声明：数据可能存在滞后、缺失或口径差异，请以公司公告及原始来源为准。</p></main><script>document.getElementById('dimension').addEventListener('change',function(){{for(const row of document.querySelectorAll('#facts tr'))row.hidden=this.value!=='all'&&row.dataset.group!==this.value;}});</script></body></html>'''
