"""Reader-first Markdown and offline HTML renderers for Insight ReportModel.

两个补充区块只做「如实呈现」：``本次新增发现`` 的数字原样来自 store 快照对比
（不重算），``分析链`` 只描述事实之间的同向/不同向关系并给出替代解释，**不写因果**。
"""
from __future__ import annotations

from html import escape
from typing import Any

# 「无新增发现」有两种情形，对读者含义不同，必须分开表述：
#   · 有基线且无变化 → 给出对比窗口与无变化项数，**可核验**
#   · 无基线（首次运行 / store 不可用）→ 说明「无可对比快照」，不假装「无变化」
_NO_BASELINE_LINE = "- 本次无可对比的历史快照，未生成新增发现。"
# status=changed 但每条都被过滤掉时的兜底：**有**基线、**有**变化，只是无可渲染行。
# 与 _NO_BASELINE_LINE 语义相反，不得混用（原实现引用了一个从未定义的
# _NO_DISCOVERY_LINE，走到该分支即 NameError）。
_NO_RENDERABLE_LINE = (
    "- 本次记录到关键字段变化，但无可展示的变化条目（明细见同代 .insight.json）。"
)
_NO_CHAIN_LINE = (
    "- 当前 Facts 不足以构成可验证的分析链（需两个以上事实并列出替代解释）；"
    "不预置机制叙述。"
)
_CHAIN_STATUS_LABEL = {
    "consistent": "一致性证据（不代表因果）",
    "mechanism_unconfirmed": "机制未证实",
}
_CHAIN_SECTION_TITLE = "研究问题与证伪条件"
_CHAIN_SECTION_NOTE = "每条给出一个当前证据尚不能回答的问题、竞争解释与可观测的证伪条件；一致不等于因果。"

# 分析合成（analysis.json 注入）：读者面向的最深内容，位置紧跟核心矛盾——
# 引擎结论在前、合成在后，但不把价值埋进文末（2026-09-16 用户审阅：full 报告
# 有价值段落落在 1156 行的第 940 行之后）。
_SYNTHESIS_SECTION_TITLE = "分析合成（Claude 撰写）"
# 纯文本，md/html 逐字共用：HTML 侧走 escape()，任何 markdown 标记都会显示成
# 字面星号；两格式各写一份带标记的版本又会漂移（见下方同源注释）。
_SYNTHESIS_SECTION_NOTE = (
    "本节由 analysis.json 提供，属模型撰写的合成内容，非引擎确定性 Finding："
    "其中的数字与判断未经引擎来源校验，也不参与本产物的完成度判定；"
    "引用前请回查下方证据底稿与原始来源。"
)
_SYNTHESIS_LABEL_INJECTED = "已注入"
_SYNTHESIS_LABEL_ABSENT = "未注入（仅引擎结论）"


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


# 审查意见 #7：`valuation.tushare.daily_basic` 这类 source ID 对系统有用、对读者无用。
# 转为可读来源名；未收录的回退原始 ID（不隐藏，只是不美化）。
_SOURCE_LABELS = {
    "tushare.daily_basic": "Tushare·日线指标",
    "tushare.daily": "Tushare·日线行情",
    "tushare.fina_indicator": "Tushare·财务指标",
    "tushare.fina_mainbz": "Tushare·主营构成",
    "tushare.stock_basic": "Tushare·股票基础信息",
    "tushare.top10_floatholders": "Tushare·十大流通股东",
    "akshare.stock_individual_notice_report": "东财·个股公告",
    "tencent_finance": "腾讯财经",
    "test.fixture": "测试夹具",
}
# 按长度降序匹配，避免 "tushare.daily" 抢先命中 "tushare.daily_basic"
_SOURCE_KEYS = sorted(_SOURCE_LABELS, key=len, reverse=True)


def _source_human(source_id: str) -> str:
    for key in _SOURCE_KEYS:
        if key in source_id:
            return _SOURCE_LABELS[key]
    return source_id


def _format_date(as_of: Any) -> str:
    text = str(as_of or "")
    if len(text) == 8 and text.isdigit():          # 20260630
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _format_value(fact: dict[str, Any]) -> str:
    """按 unit 格式化数值——底稿里 276916580000.0 对读者没有意义。"""
    value = fact.get("value")
    unit = fact.get("unit")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if unit == "CNY/share":
            return f"{value:,.2f} 元"
        if unit == "CNY":
            return f"{value / 1e8:,.2f} 亿元"
        if unit == "percent":
            return f"{value:.1f}%" if "分位" in str(fact.get("basis") or "") else f"{value:+.2f}%"
        if unit == "ratio":
            return f"{value:.3f}"
        if unit == "x":
            return f"{value:.2f}x"
        if unit == "count":
            return f"{int(value)} 条"
    return str(value)


def _source_label(model: dict[str, Any], fact_ids: list[str]) -> str:
    facts = {fact["id"]: fact for fact in model["facts"]}
    labels: list[str] = []
    for fact_id in fact_ids:
        fact = facts.get(fact_id)
        if not fact:
            continue
        source = "、".join(_source_human(s) for s in (fact.get("source_ids") or ["unknown"]))
        labels.append(f"{fact.get('basis') or fact_id}｜{source}｜{_format_date(fact.get('as_of'))}")
    return "；".join(labels) or "来源不可得"


def _fmt_number(value: Any) -> str:
    return "-" if value is None else str(value)


def _discovery_lines(model: dict[str, Any]) -> list[str]:
    """「本次新增发现」的 markdown 行（不含章节标题）。"""
    block = model.get("discoveries") or {}
    if block.get("status") != "changed":
        if block.get("reason") == "no_material_change":
            old_label = block.get("old_at_label")
            unchanged = block.get("unchanged_count")
            if old_label and isinstance(unchanged, int):
                return [f"- 相对 {old_label} 快照，{unchanged} 项关键字段无显著变化。"]
        # 无基线（无历史 / store 不可用）——不暴露内部 reason，也不声称「无变化」
        return [_NO_BASELINE_LINE]
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
    return lines or [_NO_RENDERABLE_LINE]


def _chain_lines(model: dict[str, Any]) -> list[str]:
    """「分析链」的 markdown 行（不含章节标题）。"""
    chains = model.get("analysis_chains") or []
    if not chains:
        return [_NO_CHAIN_LINE]
    lines: list[str] = []
    for chain in chains:
        status = _CHAIN_STATUS_LABEL.get(chain.get("association_status"), "关联边界未标注")
        # 首屏先给「该研究什么」——问题 → 事实 → 机制 → 竞争解释 → 证伪条件。
        # 原实现以链 ID 开头、以「方向不一致」这类工程陈述为主，读者看完
        # 知道了一堆事实却不知道下一步该验证什么。
        lines += [
            "",
            f"- **待回答的问题：** {chain.get('question') or chain.get('relation')}",
            f"  - 当前事实：{chain.get('relation')}（{status}）",
            f"  - {chain.get('mechanism')}",
        ]
        alternatives = chain.get("alternatives") or []
        if alternatives:
            marks = "①②③④⑤"
            joined = " ".join(f"{marks[i] if i < len(marks) else '-'} {text}" for i, text in enumerate(alternatives))
            lines.append(f"  - 竞争解释：{joined}")
        verification = chain.get("verification") or {}
        if verification:
            lines.append(f"  - 证伪条件与窗口：**{verification.get('event')}** — {verification.get('test')}")
        lines.append(f"  - 涉及事实：{'、'.join(f'`{fid}`' for fid in chain.get('fact_ids') or [])}")
        # chain["note"]（"本条为工程约定，无同行评审先例；不得作为核心论证。"）刻意
        # **不渲染**：它是给审计与维护者看的，属程序规则说明，对读者无信息量。
        # 该约束保留在 model 侧车与 insight_model 模块 docstring 中。
    return lines


def _synthesis_label(model: dict[str, Any]) -> str:
    """状态卡上的「分析合成」字段。与 completion 是两条独立状态轴，不互相推导。"""
    block = model.get("synthesis") or {}
    if block.get("status") == "injected":
        count = block.get("section_count")
        suffix = f"（{count} 段）" if isinstance(count, int) and count > 0 else ""
        return f"{_SYNTHESIS_LABEL_INJECTED}{suffix}"
    return _SYNTHESIS_LABEL_ABSENT


def _synthesis_sections(model: dict[str, Any]) -> list[dict[str, Any]]:
    """仅当 status 为 injected 时返回段列表；否则空列表（渲染层零 diff）。"""
    block = model.get("synthesis") or {}
    if block.get("status") != "injected":
        return []
    return [sec for sec in (block.get("sections") or []) if isinstance(sec, dict)]


def _synthesis_lines(model: dict[str, Any]) -> list[str]:
    """「分析合成」的 markdown 行（不含章节标题）。

    体例与 full 侧 _render_analysis_appendix 逐字一致，避免两个消费同一份
    analysis.json 的模式在措辞上漂移。
    """
    lines: list[str] = []
    for i, sec in enumerate(_synthesis_sections(model)):
        mod = str(sec.get("module") or sec.get("position") or f"section-{i}")
        title = str(sec.get("title") or mod).strip()
        facts = str(sec.get("facts_md") or "").strip()
        amd = str(sec.get("analysis_md") or "").strip()
        ev = str(sec.get("evidence_tag") or "").strip()
        lines += [f"### {title}（{mod}）", ""]
        if facts:
            lines += ["**[事实]**", "", facts, ""]
        if amd:
            lines += ["**[分析]**", "", amd, ""]
        if ev:
            lines += [f"**证据等级：** {ev}", ""]
    return lines


def _html_synthesis(model: dict[str, Any]) -> str:
    """分析合成的 HTML 分区。未注入 → 空串。

    段内容走 lib.md_subset（analysis_schema 已保证 md 子集合法），与
    render_html._html_analysis 同手法；卡片 id 用 synthesis- 前缀，与
    Finding 的 evidence- 锚点互不干扰（report_qc 依赖后者做配对校验）。
    """
    sections = _synthesis_sections(model)
    if not sections:
        return ""
    from lib.md_subset import MarkdownSubsetError, render_markdown

    cards: list[str] = []
    for i, sec in enumerate(sections):
        mod = str(sec.get("module") or sec.get("position") or f"section-{i}")
        try:
            facts_html = render_markdown(str(sec.get("facts_md") or ""))
            ana_html = render_markdown(str(sec.get("analysis_md") or ""))
        except MarkdownSubsetError as exc:
            ana_html = f'<div class="note">分析段 md 子集校验失败：{escape(str(exc))}</div>'
            facts_html = ""
        cards.append(
            f'<article class="finding" id="synthesis-{escape(str(i))}">'
            f'<h3>{escape(str(sec.get("title") or mod))}</h3>'
            f'<p class="note">{escape(mod)} · 证据等级：{escape(str(sec.get("evidence_tag") or "—"))}</p>'
            f"<div>{facts_html}{ana_html}</div></article>"
        )
    return (
        f'<section id="synthesis"><h2>{escape(_SYNTHESIS_SECTION_TITLE)}</h2>'
        f'<p class="note">{escape(_SYNTHESIS_SECTION_NOTE)}</p>'
        + "".join(cards) + "</section>"
    )


def render_insight_markdown(model: dict[str, Any]) -> str:
    """Render concise analysis, never raw module tables as the reading surface."""
    title = f"# {_name(model)} ({model['symbol']}) — 研究要点"
    status = "分析完成" if model["completion"] == "complete" else "分析未完成（证据不足）"
    profile = model.get("profile") or {}
    profile_text = "；".join(f"{key}={value}" for key, value in profile.items()) or "未提供"
    lines = [
        title, "",
        "> ⚠️ 风险提示：本报告是基于可追溯数据的研究整理，不构成任何投资建议、买卖指令或目标价预测。", "",
        f"**产物状态：** {status} ｜ **分析合成：** {_synthesis_label(model)}"
        f" ｜ **数据时间：** {_beijing(model.get('fetched_at'))} ｜ **研究档案：** {profile_text}",
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
    # 分析合成紧跟核心矛盾：引擎结论（可得结论/核心矛盾）在前，人写合成在后，
    # 但不落到文末——「有价值的内容必须在阅读面靠前」是本次改动的出发点。
    if _synthesis_sections(model):
        lines += ["", f"## {_SYNTHESIS_SECTION_TITLE}", _SYNTHESIS_SECTION_NOTE]
        lines += _synthesis_lines(model)
    lines += ["", "## 本次新增发现"]
    lines += _discovery_lines(model)
    lines += ["", f"## {_CHAIN_SECTION_TITLE}", _CHAIN_SECTION_NOTE]
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
        sources = "、".join(_source_human(s) for s in fact["source_ids"])
        lines.append(
            f"- **{fact['basis']}** = {_format_value(fact)}"
            f"（`{fact['id']}`；截至 {_format_date(fact['as_of'])}；来源: {sources}{formula}）")
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
        f'<tr data-group="{escape(fact["id"].split(".")[0])}"><td id="fact-{escape(fact["id"])}"><code>{escape(fact["id"])}</code></td><td>{escape(_format_value(fact))}</td><td>{escape(fact["basis"])}</td><td>{escape(_format_date(fact["as_of"]))}</td><td title="公式：{escape(str(fact.get("formula") or "原始字段"), quote=True)}">{escape("、".join(_source_human(s) for s in fact["source_ids"]))}</td></tr>'
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
    # 与 markdown 同位：核心矛盾之后、本次新增发现之前（两格式同序）
    synthesis_html = _html_synthesis(model)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(_name(model))} — 研究要点</title>
<style>:root{{--bg:#10131a;--card:#171c26;--text:#e9edf5;--muted:#aeb9cc;--line:#303a4b;--accent:#7db1ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:24px}}section{{margin:24px 0}}.status,.finding,.evidence{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:10px 0}}.finding h3,.evidence h3{{margin:0 0 8px;font-size:16px}}a{{color:var(--accent)}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}}select{{padding:7px;background:var(--card);color:var(--text);border:1px solid var(--line)}}.note{{color:var(--muted)}}.plain{{margin:0;white-space:pre-wrap;font:inherit;color:inherit}}@media print{{body{{background:white;color:black}}.status,.finding,.evidence{{border-color:#aaa;background:white}}}}</style></head><body><main>
<h1>{escape(_name(model))} ({escape(model["symbol"])}) — 研究要点</h1><div class="status"><b>产物状态：</b>{status} ｜ <b>分析合成：</b>{escape(_synthesis_label(model))} ｜ <b>数据时间：</b>{fetched_label} ｜ <b>契约：</b>{escape(model["report_contract_version"])}</div>
<p class="note">⚠️ 本页用于研究与数据核验，不构成任何投资建议、买卖指令或目标价预测。</p><section><h2>可得结论</h2>{cards}</section>
<section><h2>核心矛盾</h2><div class="finding">{escape(model["core_tension"]["claim"])}</div></section>
{synthesis_html}
<section><h2>本次新增发现</h2><div class="finding"><pre class="plain">{discoveries_html}</pre></div></section>
<section><h2>{escape(_CHAIN_SECTION_TITLE)}</h2><div class="finding"><p class="note">{escape(_CHAIN_SECTION_NOTE)}</p><pre class="plain">{chains_html}</pre></div></section>
<section><h2>证据与反证</h2>{evidence}</section>
<section><h2>数据探索</h2><label>筛选事实维度 <select id="dimension"><option value="all">全部</option><option value="valuation">估值</option><option value="financials">财务</option><option value="technical">技术</option><option value="quote">行情</option><option value="basic">基本信息</option></select></label><p class="note">字段来源单元格可悬停查看公式；筛选状态始终可见，离线可用。</p><table><thead><tr><th>Fact</th><th>数值</th><th>口径</th><th>截至</th><th>来源 / 公式</th></tr></thead><tbody id="facts">{fact_rows}</tbody></table></section>
<section><h2>已知未知与补证路径</h2><ul>{gaps}</ul></section><p class="note">免责声明：数据可能存在滞后、缺失或口径差异，请以公司公告及原始来源为准。</p></main><script>document.getElementById('dimension').addEventListener('change',function(){{for(const row of document.querySelectorAll('#facts tr'))row.hidden=this.value!=='all'&&row.dataset.group!==this.value;}});</script></body></html>'''
