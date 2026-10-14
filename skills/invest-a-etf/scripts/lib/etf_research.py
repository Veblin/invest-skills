"""按需经营证据链；原文准备、领域验证与阅读块，不另建封存/缓存系统。

事实和因果解释由研究者提交，机器只核绑定、时点、覆盖与消费一致性。
URL/元数据不能证明已读：入口核实际文件、提取正文及所引片段，再随旧 seal 冻结。
"""
from __future__ import annotations

import copy
import hashlib
import html
import importlib.util
import json
import math
import re
import time
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def _date(value: object) -> date:
    return date.fromisoformat(str(value))


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_research(research: object) -> list[str]:
    try:
        return _validate_research(research)
    except (TypeError, AttributeError, KeyError, ValueError):
        return ["研究合同结构无效（对象、列表、ID、日期或预算类型不符）"]


def _validate_research(research: object) -> list[str]:
    """离线合同检查；legacy payload 无 research 时不调用，不推定原因充分。"""
    errors: list[str] = []
    if not isinstance(research, dict):
        return ["研究输入须为对象"]
    r = research
    if r.get("schema_version") != 1:
        errors.append("研究 schema_version 须为 1")
    try:
        cutoff = _date(r.get("as_of"))
        if _date(r.get("holdings_as_of")) > cutoff:
            errors.append("持仓日期晚于研究截止")
    except ValueError:
        return [*errors, "研究/持仓日期须为 ISO 日期"]
    if not re.fullmatch(r"\d{6}", str(r.get("symbol", ""))):
        errors.append("研究标的须为六位代码")
    if not _text(r.get("question")):
        errors.append("缺研究问题")
    selected = r.get("selection")
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(x, dict) for x in selected)):
        return [*errors, "selection 须为非空证券对象列表"]
    codes = [x.get("code") for x in selected]
    if (any(not re.fullmatch(r"\d{6}", str(c)) for c in codes)
            or len(set(codes)) != len(codes)):
        errors.append("选择证券代码无效或重复")
    if any(not _text(x.get("reason")) for x in selected):
        errors.append("每个取证对象须说明与问题的关联")
    budget = r.get("budget") or {}
    limit, target = budget.get("max_names"), budget.get("target_cov_pct")
    if (type(limit) is not int or limit < 1 or len(codes) > limit
            or type(target) not in (int, float) or not math.isfinite(target)
            or not 0 < target <= 100):
        errors.append("研究预算无效或选取数量超出显式预算")
    sources = r.get("sources")
    facts = r.get("facts")
    insights = r.get("insights")
    if any(not isinstance(xs, list) or any(not isinstance(x, dict) for x in xs)
           for xs in (sources, facts, insights)):
        return [*errors, "sources/facts/insights 须为对象列表"]
    if not sources or not facts:
        errors.append("研究输入缺实际原文或事实（不能用空研究块表示已完成）")
    sm = {s.get("id"): s for s in sources}
    fm = {f.get("id"): f for f in facts}
    for xs, mapping, label in ((sources, sm, "来源"), (facts, fm, "事实")):
        if len(xs) != len(mapping) or any(not _text(x.get("id")) for x in xs):
            errors.append(f"{label} ID 缺失或重复")
    for s in sources:
        if (urlparse(str(s.get("url", ""))).scheme not in ("http", "https")
                or any(not _text(s.get(k)) for k in ("title", "locator", "excerpt"))
                or not re.fullmatch(r"[0-9a-f]{64}", str(s.get("sha256", "")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(s.get("text_sha256", "")))
                or s.get("source_type") != "original"):
            errors.append(f"来源 {s.get('id')} 缺实际原文、哈希或定位")
        try:
            if _date(s.get("published_date")) > cutoff:
                errors.append(f"来源 {s.get('id')} 披露晚于研究截止")
            read_at = datetime.fromisoformat(str(s.get("read_at")))
            read_day = read_at.astimezone(ZoneInfo("Asia/Shanghai")).date() if read_at.tzinfo else None
            if read_day is None or read_day > cutoff:
                errors.append(f"来源 {s.get('id')} 读取时间无时区或晚于研究截止")
            elif read_day < _date(s.get("published_date")):
                errors.append(f"来源 {s.get('id')} 读取早于公开披露，时间证据矛盾")
        except ValueError:
            errors.append(f"来源 {s.get('id')} 披露/读取时间缺失或无效")
    for f in facts:
        refs = f.get("source_ids")
        if (not _text(f.get("statement")) or not isinstance(refs, list) or not refs
                or any(sid not in sm for sid in refs)):
            errors.append(f"事实 {f.get('id')} 缺陈述或有效来源绑定")
        if f.get("kind") not in ("company", "industry", "event"):
            errors.append(f"事实 {f.get('id')} 类型无效")
        if f.get("kind") == "company" and f.get("code") not in codes:
            errors.append(f"事实 {f.get('id')} 证券不在本题选择范围")
        try:
            start, end = _date(f.get("period_start")), _date(f.get("period_end"))
            if start > end or end > cutoff:
                errors.append(f"事实 {f.get('id')} 期间倒置或晚于截止")
        except ValueError:
            errors.append(f"事实 {f.get('id')} 缺有效完整期间")
    if any(not any(f.get("kind") == "company" and f.get("code") == c for f in facts) for c in codes):
        errors.append("所选证券缺公司原文事实，不得把选择覆盖冒充取证覆盖")
    if not insights:
        errors.append("缺解释与条件结论；事实表不能替代分析")
    insight_ids = [i.get("id") for i in insights]
    if len(set(insight_ids)) != len(insight_ids) or any(not _text(x) for x in insight_ids):
        errors.append("判断 ID 缺失或重复")
    for i in insights:
        for key in ("title", "interpretation", "transmission", "etf_link",
                    "conditional_conclusion", "alternative", "falsifier", "boundary"):
            if not _text(i.get(key)):
                errors.append(f"判断 {i.get('id')} 缺 {key}")
        refs = i.get("fact_ids")
        if (not isinstance(refs, list) or not refs or any(fid not in fm for fid in refs)):
            errors.append(f"判断 {i.get('id')} 事实引用无效")
        icodes = i.get("codes")
        if (not isinstance(icodes, list) or not icodes or any(c not in codes for c in icodes)
                or len(icodes) != len(set(icodes))):
            errors.append(f"判断 {i.get('id')} 关联证券无效")
        if i.get("scope") != "selected_holdings":
            errors.append(f"判断 {i.get('id')} 不得将所选样本推成全组合")
        for pair in i.get("comparisons") or []:
            if not isinstance(pair, dict) or pair.get("left") not in fm or pair.get("right") not in fm:
                errors.append(f"判断 {i.get('id')} 同期比较引用无效")
                continue
            left, right = fm[pair["left"]], fm[pair["right"]]
            if (left.get("period_start", "")[5:], left.get("period_end", "")[5:]) != (
                    right.get("period_start", "")[5:], right.get("period_end", "")[5:]):
                errors.append(f"判断 {i.get('id')} 同期比较窗口混期（累计/单季不可混比）")
        # 显式历史解释边：事件发生/生效日期不得晚于被解释财报期末。
        for link in i.get("historical_links") or []:
            if not isinstance(link, dict) or link.get("cause") not in fm or link.get("effect") not in fm:
                errors.append(f"判断 {i.get('id')} 历史传导引用无效")
                continue
            cause, effect = fm[link["cause"]], fm[link["effect"]]
            try:
                if _date(cause["period_start"]) > _date(effect["period_end"]):
                    errors.append(f"判断 {i.get('id')} 用未来事件解释过去业绩")
            except (ValueError, KeyError):
                errors.append(f"判断 {i.get('id')} 历史传导期间无效")
    return errors


def load_research_input(path: str | Path, symbol: str) -> dict:
    """取数前验证材料；原始文件留本机，封存实际读取片段和机械哈希。"""
    path = Path(path)
    r = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_research(r)
    if not isinstance(r, dict) or r.get("symbol") != symbol:
        errors.append("研究材料与请求标的不一致")
    if errors:
        raise ValueError("；".join(errors))
    r = copy.deepcopy(r)
    for s in r["sources"]:
        raw_path = path.parent / s.get("path", "")
        text_path = path.parent / s.get("text_path", "")
        if not raw_path.is_file() or not text_path.is_file():
            raise ValueError(f"来源 {s['id']} 缺实际原文文件/提取正文")
        raw = raw_path.read_bytes()
        text = text_path.read_bytes()
        if (hashlib.sha256(raw).hexdigest() != s["sha256"]
                or hashlib.sha256(text).hexdigest() != s.get("text_sha256")):
            raise ValueError(f"来源 {s['id']} 原文/正文哈希不符")
        if s["excerpt"] not in text.decode("utf-8"):
            raise ValueError(f"来源 {s['id']} 所引片段不在实际正文")
        # 路径只是本地准备信息；离线消费者依赖封存片段与原文哈希。
        s.pop("path", None)
        s.pop("text_path", None)
    return r


def attach_research(payload: dict, research: dict, *, financial_getter=None) -> dict:
    """仅显式选取的公司走已有公开财务桥；不生成个股报告、不扩采。"""
    from etf_data import build_evidence_budget, build_data_status
    from lib.financials import dedupe_by_end_date, normalize_end_date, parse_end_date
    errors = validate_research(research)
    if errors:
        raise ValueError("；".join(errors))
    if financial_getter is None:
        from data_bridge import get_financials
        financial_getter = get_financials
    r = copy.deepcopy(research)
    h = payload.get("holdings") or {}
    if h.get("status") != "ok" or h.get("report_date") != r["holdings_as_of"]:
        raise ValueError("研究权重日期与本次实际披露持仓不一致/持仓不可得，须重新准备材料")
    if r["as_of"] > str(payload.get("generated_at", ""))[:10]:
        raise ValueError("研究截止晚于本次报告生成日")
    by_code = {str(x.get("code")): x for x in h.get("rows") or []}
    chosen = [x["code"] for x in r["selection"]]
    if any(c not in by_code or by_code[c].get("pct") is None for c in chosen):
        raise ValueError("取证证券不在本次披露持仓或缺权重，禁止补零/重归一化")
    for c in chosen:
        v = by_code[c]["pct"]
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100:
            raise ValueError("披露权重无效")
    subset = {**h, "rows": [by_code[c] for c in chosen]}
    sm = {s["id"]: s for s in r["sources"]}
    evidence = {}
    for c in chosen:
        facts = [f for f in r["facts"] if f.get("kind") == "company" and f.get("code") == c]
        if facts:
            src = sm[facts[0]["source_ids"][0]]
            evidence[c] = {"业务证据": "；".join(f["statement"] for f in facts),
                           "期间": f"{facts[0]['period_start']}—{facts[0]['period_end']}",
                           "关联命题": r["question"], "来源": dict(src)}
    # 参数和对象均由本题明确给出；预算用于记录，不截断显式对象。
    budget = build_evidence_budget(payload["symbol"], holdings=subset,
                                   evidence=evidence,
                                   research_question=r["question"],
                                   target_cov_pct=100, max_names=len(chosen))
    r["coverage"] = {
        "codes": chosen, "weight_pct": round(sum(by_code[c]["pct"] for c in chosen), 2),
        "holdings_as_of": h["report_date"],
        "denominator": (h.get("coverage") or {}).get("denominator") or "基金资产净值",
        "holding_scope": h.get("holding_scope"),
        "weights": {c: by_code[c]["pct"] for c in chosen},
        "note": "仅所选证券的历史披露权重；非当前完整持仓，不推为全组合盈利或预期收益。",
    }
    r["selection_budget"] = {**budget["budget"], **r["budget"],
                             "selected_cov_pct": r["coverage"]["weight_pct"],
                             "stop_reason": "explicit_question_selection",
                             "note": "对象由研究问题显式选择；预算是本题限制，不扩采、不截断问题对象"}
    budget["budget"] = dict(r["selection_budget"])
    budget["shortfall_note"] = (
        f"问题所选对象权重 {r['coverage']['weight_pct']}% 未达本题目标 {r['budget']['target_cov_pct']}%；"
        "本次不扩采，不能误称完整披露耗尽。"
        if r["coverage"]["weight_pct"] < r["budget"]["target_cov_pct"] else None)
    r["financials"], r["financial_observations"] = {}, []
    cutoff = r["as_of"].replace("-", "")
    started = time.monotonic()
    for c in chosen:
        try:
            result = financial_getter(c) or {"status": "missing", "note": "财务入口未返回有效数据"}
        except Exception as exc:
            result = {"status": "missing", "note": f"财务入口失败：{type(exc).__name__}"}
        r["financials"][c] = result
        eligible = []
        raw_rows = result.get("data") if isinstance(result.get("data"), list) else result.get("rows") or []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            ed = normalize_end_date(str(row.get("end_date") or ""))
            ad = normalize_end_date(str(row.get("ann_date") or ""))
            if parse_end_date(ed) and ed <= cutoff and parse_end_date(ad) and ad <= cutoff:
                eligible.append(row)
        normalized = dedupe_by_end_date(eligible)
        # 只展示同截止已知披露，不拆单季、不平均个股增速、不裁决来源冲突。
        for row in sorted(normalized, key=lambda x: normalize_end_date(x["end_date"]))[-2:]:
            r["financial_observations"].append({
                "code": c, "end_date": row["end_date"], "ann_date": row["ann_date"],
                "values": {k: row.get(k) for k in
                           ("revenue", "net_profit", "n_cashflow_act", "grossprofit_margin",
                            "accounts_receiv", "inventory")},
                "source": (result.get("_meta") or {}).get("source") or result.get("source"),
            })
    r["financial_collection_sec"] = round(time.monotonic() - started, 3)
    r["financial_note"] = "共享财务接口是采集渠道；字段合并的披露日可能不齐，核心经营判断回核原文。无披露日的行仅留底稿，不作确认性观察；不同源冲突并列，不自动裁决。"
    r["status"] = "prepared"
    r["narrative_md"], r["appendix_md"] = render_research_markdown(r)
    payload["research"] = r
    payload["holdings_evidence"] = budget
    # 复用既有状态生产者，只替换本次新增取证维度；其他状态不因重复组装变化。
    status = build_data_status(identity=(payload.get("profile") or {}).get("identity"),
                              profile=payload.get("profile") or {}, quote=payload.get("quote") or {},
                              kline=payload.get("kline") or {}, share_history=payload.get("share_history") or {},
                              extra_dims=payload)
    payload.setdefault("data_status", {}).setdefault("dimensions", {})["holdings_evidence"] = (
        status["dimensions"]["holdings_evidence"])
    return r


def render_research_markdown(r: dict) -> tuple[str, str]:
    """冻结输入的确定性阅读块；正文讲判断，数值与定位置附录。"""
    cov = r["coverage"]
    lines = ["## 经营与产业证据链", "", f"研究问题：{r['question']}", "",
             f"取证范围：持仓报告期末 {r['holdings_as_of']} 的所选证券，合计 {cov['weight_pct']:.2f}% 基金资产净值。"
             "覆盖以原披露为分母，未重归一化；不能代表当前全组合。"
             "[来源: research.coverage；基金披露持仓经原有接口采集]", ""]
    for i in r["insights"]:
        refs = "、".join(i["fact_ids"])
        weight = round(sum(cov["weights"][c] for c in i["codes"]), 2)
        lines += [f"### {i['title']}", "", f"[事实] 依据 {refs}（原文与期间见附录）。[来源: research.facts]",
                  "", "[分析] [证据: C] [推测，待验证]", "",
                  i["interpretation"], "", i["transmission"], "",
                  f"{i['etf_link']} 相关证券 {','.join(i['codes'])} 的历史披露权重合计 {weight:.2f}%。"
                  "[来源: Python calc: sum(research.coverage.weights[code])；原披露分母]", "",
                  f"可能结论：[推测，待验证] {i['conditional_conclusion']}", "",
                  f"竞争解释：{i['alternative']}", "",
                  f"削弱或推翻判断的观察：{i['falsifier']}", "",
                  f"判断边界：{i['boundary']}", ""]
    appendix = ["## 附录：经营证据底稿", "", "### 定向选择与事实", ""]
    for x in r["selection"]:
        appendix += [f"- {x['code']}：{x['reason']}"]
    appendix += [""]
    sm = {s["id"]: s for s in r["sources"]}
    for f in r["facts"]:
        sources = "；".join(f"{sid} {sm[sid]['locator']}" for sid in f["source_ids"])
        appendix += [f"**{f['id']}**（{f['period_start']}—{f['period_end']}）：{f['statement']}"
                     f" [来源: {sources}]", ""]
    appendix += ["### 实读原文与定位", ""]
    for s in r["sources"]:
        appendix += [f"- {s['id']} [{s['title']}]({s['url']})；{s['locator']}；"
                     f"发布 {s['published_date']}；读取 {s['read_at']}；原文 SHA256 {s['sha256']}。"]
    appendix += ["", "### 共享财务原料与口径", "", r["financial_note"], ""]
    for c, raw in r["financials"].items():
        channel = (raw.get("_meta") or {}).get("source") or raw.get("source") or "未取得"
        meta = raw.get("_meta") or {}
        attempted = [s.get("source") for s in meta.get("all_sources") or [] if isinstance(s, dict)]
        if not attempted:
            attempted = raw.get("attempted_sources") or []
        appendix += [f"- {c}：状态 {raw.get('status', 'unknown')}；渠道 {channel}；"
                     f"原料记录的源 {attempted}；采集时点 {meta.get('fetched_at', '未留痕')}。"
                     "完整原始行与来源版本在同代 research.financials，"
                     "接口可得不等于原始披露已核。"]
    appendix += ["", "已知披露日的共享原料观察（累计报表字段，不拆单季；不替代上述原文）：", "",
                 "| 证券 | 报告期 | 披露日 | 收入(元) | 归母净利润(元) | 经营现金流(元) | 毛利率(%) |",
                 "|---|---|---|---|---|---|---|"]
    def fmt(value):
        return f"{value:.2f}" if type(value) in (int, float) and math.isfinite(value) else "未知"
    for o in r["financial_observations"]:
        vals = o["values"]
        numbers = " | ".join(fmt(vals.get(k)) for k in
                             ("revenue", "net_profit", "n_cashflow_act", "grossprofit_margin"))
        appendix.append(f"| {o['code']} | {o['end_date']} | {o['ann_date']} | {numbers} |")
    appendix += ["", "[来源: research.financial_observations / research.financials._meta；"
                 "Tushare路径为fina_indicator并按income、cashflow等原字段补表，具体原料/冲突保留于payload；"
                 "其他渠道保持实际_meta.source，不冒充Tushare或原始披露]"]
    return "\n".join(lines).strip(), "\n".join(appendix).strip()


def render_research_section(md_html: str, research: dict | None = None) -> str:
    """HTML 阅读块的 canonical 模板；渲染与 QC 共用，保持覆盖和判断边界。"""
    def esc(value):
        return html.escape(str(value) if value is not None else "", quote=True)

    scope = ""
    if research is not None:
        cov = research.get("coverage") or {}
        scope = (f'<div class="card" data-operating-research="1">'
                 f'<b>经营证据链范围</b>：{esc(research.get("question"))}<br>'
                 f'持仓期末 {esc(cov.get("holdings_as_of"))}；所选证券覆盖 '
                 f'{esc(cov.get("weight_pct"))}% 基金资产净值。'
                 f'{esc(cov.get("note"))} 判断限于上述证据范围。</div>')
    return f'''<section id="research">
  <div class="sh"><span class="st">研究备忘</span><div class="sd"></div><span class="ss">事实、解释与判断边界</span></div>
  {scope}
  <div class="card">
    <article class="md-body">
      {md_html}
    </article>
  </div>
</section>'''


class _ResearchHTMLParser(HTMLParser):
    """只取实际研究 section 的元素/文本，不把注释或脚本中的副本当证据。"""
    def __init__(self, *, full_report: bool = False):
        super().__init__(convert_charrefs=True)
        self.sections: list[list[tuple]] = []
        self.depth = 0
        self.ancestors: list[tuple[str, dict]] = []
        self.nonrendered = False
        self.full_report = full_report

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            if not self.depth and dict(attrs).get("id") == "research":
                self.sections.append([])
                self.depth = 1
                # 模板/隐藏祖先中的 canonical 副本不是读者实际消费的研究节。
                # canonical 完整报告的 live DOM 路径为 html/body/div.app/main.main；
                # 拒绝额外包装（如 template、textarea、iframe），不能把其中的
                # 类似标签文本当已渲染内容。独立模板正控不带祖先。
                if self.full_report:
                    if ([tag for tag, _ in self.ancestors] != ["html", "body", "div", "main"]
                            or self.ancestors[-1][1].get("class") != "main"
                            or self.ancestors[-2][1].get("class") != "app"):
                        self.nonrendered = True
                for ancestor_tag, ancestor_attrs in self.ancestors:
                    if (ancestor_tag in {"template", "head", "noscript"}
                            or "hidden" in ancestor_attrs or "inert" in ancestor_attrs
                            or "style" in ancestor_attrs):
                        # canonical 外层布局无 inline style；新增样式须重新渲染复检。
                        self.nonrendered = True
            elif self.depth:
                self.depth += 1
        if self.depth:
            self.sections[-1].append(("start", tag, tuple(sorted(attrs))))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.ancestors.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if self.depth:
            self.sections[-1].append(("end", tag))
            if tag == "section":
                self.depth -= 1
        for i in range(len(self.ancestors) - 1, -1, -1):
            if self.ancestors[i][0] == tag:
                del self.ancestors[i:]
                break

    def handle_data(self, data):
        if self.depth and data.strip():
            self.sections[-1].append(("text", data.strip()))


def research_html_findings(payload: dict, html_text: str, md_text: str) -> list[dict]:
    """核验实际 HTML 范围、限定和嵌入全文；哈希/marker 不能替代内容对照。"""
    if "research" not in payload:
        return []
    errors = []
    # MD 先与重新生成的冻结研究正文/附录核对，再核 HTML 消费同一 MD。
    if research_findings(payload, md_text):
        errors.append("HTML 对照输入未通过冻结经营研究合同")
    else:
        # 共享 QC 可从其他 skill 启动：显式路径加载零依赖 MD 渲染器，
        # 不假定 ETF 私有 lib 已经加入 sys.path。
        spec = importlib.util.spec_from_file_location(
            "_etf_md_research_qc", Path(__file__).with_name("etf_md.py"))
        if spec is None or spec.loader is None:
            errors.append("HTML 经营研究对照渲染器不可用")
        else:
            md_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(md_mod)
            expected = _ResearchHTMLParser()
            expected.feed(render_research_section(md_mod.render_markdown(md_text), payload["research"]))
            actual = _ResearchHTMLParser(full_report=True)
            actual.feed(html_text)
            if actual.depth or actual.nonrendered or actual.sections != expected.sections:
                errors.append("HTML 经营研究覆盖范围、限定状态或嵌入正文/附录与冻结输入不一致；须重新渲染并复检")
    return [{"id": "etf-operating-research-html", "severity": "error", "message": e}
            for e in errors]


def research_findings(payload: dict, text: str) -> list[dict]:
    """QC 消费同代合同与实际正文；不存在研究块的 legacy 不宣称已验证。"""
    if "research" not in payload:
        return []
    r = payload["research"]
    errors = validate_research(r)
    if errors:
        return [{"id": "etf-operating-research", "severity": "error", "message": e} for e in errors]
    if isinstance(r, dict):
        h = payload.get("holdings") or {}
        if r.get("symbol") != payload.get("symbol") or r.get("holdings_as_of") != h.get("report_date"):
            errors.append("研究标的/持仓时期与本代不一致")
        if r.get("as_of", "") > str(payload.get("generated_at", ""))[:10]:
            errors.append("研究截止晚于本代报告生成日")
        by_code = {str(x.get("code")): x.get("pct") for x in h.get("rows") or []}
        cov = r.get("coverage") or {}
        codes = [x.get("code") for x in r.get("selection") or [] if isinstance(x, dict)]
        if (not codes or any(c not in by_code or by_code[c] is None for c in codes)
                or cov.get("weights") != {c: by_code.get(c) for c in codes}):
            errors.append("研究覆盖未绑定实际持仓权重")
        elif cov.get("weight_pct") != round(sum(by_code[c] for c in codes), 2):
            errors.append("研究样本覆盖数值不符，禁止重归一化")
        if not errors:
            try:
                narrative, appendix = render_research_markdown(r)
                for name, expected in (("正文", narrative), ("附录", appendix)):
                    if expected not in text:
                        errors.append(f"报告{ name }缺同代经营证据链或已替换；须从冻结输入生成并重新复检")
                if r.get("narrative_md") != narrative or r.get("appendix_md") != appendix:
                    errors.append("封存阅读块与结构化研究输入不一致")
            except (KeyError, TypeError, ValueError):
                errors.append("封存研究块不完整")
    return [{"id": "etf-operating-research", "severity": "error", "message": e} for e in errors]
