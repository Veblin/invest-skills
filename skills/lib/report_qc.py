"""report_qc.py — 统一研报质量检查器（v0.2.3）。

对所有 report 类型（stock/etf/journal/gap-scan）做分层质量检查，
输出单一判定 PASS / WARN / FAIL。offline-first：默认不联网，
只跑 lint + 结构 + ETF derived 合理性；`--verify-data` 可选联网
执行 audit / quality / rigor（仅 stock）。

分层：
    lint      全部      措辞合规（复用 invest-a-stock lib/lint.py + YAML 规则）
    structure 全部      报告类型特定结构校验（章节/标签存在性）
    completion stock    自动化研究快照的分析交付完成度（占位符/同代 analysis.json/
                        Bull-Bear 与左-右依据不得为空）
    derived   etf+stock 16 个 derived 字段合理性（值域 + 小数位）；stock 报告
                       仅当引用衍生字段（含 v0.2.7 E1 板块同步性 6 字段）时启用
    audit     stock     数据点抽取 + 偏差判定（--verify-data）
    quality   stock     7 指标质地检查（--verify-data）
    rigor     stock     市值/估值/跨源验算（--verify-data）
    readability      stock+etf  R-A1 可读性指标组（篇幅/长句/术语密度/结论四要素）。
                               §3.4 定义为软建议，状态封顶 warn（不阻断交付）
    conclusion-evidence stock+etf R-A2 结论段证据等级 + R-A6 [事实]→[分析] 对偶。
    etf-generation   etf        同代 payload 封存 / 产物（md/html/decision/payload）
                               sha256 与 QC 记录侧车绑定 / 报告断言 × payload 实况
                               （无基准 TE、隔夜 NAV 错价、缺权重=0、错月拼当期、
                               身份未核称已核）。离线只读；未执行项 warn（≠PASS）
    structure       全部       必备标签/区块 + 表列错位（表体行列数 ≠ 表头 →
                               渲染器静默缺列/截断；etf=error，其余=warning）
    etf-generation 追加 (2026-10-10 A8)：BOLL 带宽按 (upper−lower)/mid 独立复算
                               （原始值复算为准，不以底稿自述值为真值）；同代派生
                               数字对拍；同类产品表期间声明与数值对拍；份额窗口
                               对拍；「排名 N/M」与组内极值重算；已排除估值摘要
                               却在结论区断言低位。自由文本因果判断不在机器层。

`--verify-data` 的 network_used 只反映**实际发起**的联网层（stock collector），
不再等于该标志本身（ETF 数据核验为离线同代消费）。

QC 记录侧车（`--record`）把本代产物的 sha256 与机器结论、分层签认
（数据/原文/人工数字·合规·逻辑，默认 not_executed）落盘为 `<md ts>.qc.json`；
任一产物变更 → 旧记录 stale（重验入口 = 重跑 `--record`）。记录不含自身哈希。

用法：
    uv run python skills/lib/report_qc.py <file>
    uv run python skills/lib/report_qc.py --latest
    uv run python skills/lib/report_qc.py --dir reports/
    uv run python skills/lib/report_qc.py <file> --verify-data --json
    uv run python skills/lib/report_qc.py <etf md> --fail-on error --record \
        --signoff manual_numbers=pass:"逐项对照 payload 复算"
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402

# 四维证据标签语法唯一来源（R12 round-4/5）：analysis_schema 的掩码与本节
# 结构行豁免/证据判定共用同一封闭语法，禁止各自复制正则。
from evidence_tags import (  # noqa: E402
    EVIDENCE_LABEL_PATTERN,
    FOUR_DIM_SEQUENCE_PATTERN as _FOUR_DIM_SEQUENCE,
    FOUR_DIM_TAG_LINE_RE as _FOUR_DIM_TAG_RE,
    RENDER_STRENGTH_TOKEN_PATTERN as _RENDER_STRENGTH_TOKEN,
    SOURCE_DIM_PATTERN as _SOURCE_DIM,
    TIMING_DIM_PATTERN as _TIMING_DIM,
    CROSS_DIM_PATTERN as _CROSS_DIM,
    strip_strength_tag_spans as _strip_strength_tag_spans,
)


# ── 数据模型 ──────────────────────────────────────────────────────────────


@dataclass
class LayerResult:
    """单个检查层的结果。"""

    layer: str
    status: str                       # pass | warn | fail | skip
    findings_count: int = 0
    details: list[dict] = field(default_factory=list)


@dataclass
class QCResult:
    """单个报告的统一 QC 结果。"""

    report_path: str
    report_type: str                  # stock | etf | journal | gap_scan | pulse | unknown
    overall: str                      # PASS | WARN | FAIL
    layers: list[LayerResult] = field(default_factory=list)
    network_used: bool = False

    def to_dict(self) -> dict:
        return {
            "report_path": self.report_path,
            "report_type": self.report_type,
            "overall": self.overall,
            "network_used": self.network_used,
            "layers": [asdict(l) for l in self.layers],
        }


# ── 报告类型检测 ──────────────────────────────────────────────────────────


def _classify_by_symbol(symbol: str) -> str:
    """代码前缀 → 标的类型（前缀政策集中管理于 codes.is_etf_symbol）。

    920xxx 等北交所基金/股票无法区分 → 按 stock 处理（股票报告必须走
    audit/quality/rigor；基金报告误走 stock 检查只会产生可见告警，优于
    静默跳过）。无法识别前缀时按 stock 兜底（报告内容仍可 lint）。
    """
    try:
        from .codes import is_etf_symbol  # 同包相对导入（正常路径）
    except ImportError:  # pragma: no cover
        from codes import is_etf_symbol  # noqa: E402  sys.path 裸导入

    return "etf" if is_etf_symbol(symbol) else "stock"


# 复盘纪要的文件名（**本工具生成的格式**：`{YYYYMMDD}-review.md`）。
# ⚠️ 与 `skills/lib/decision_review.REVIEW_NAME_RE`（owner）保持一致；此处不 import
# 是为了不让**被广泛打包**的 report_qc 多一个模块依赖——包内缺那个模块会让 QC 闸门
# 整个不可用（正是 R0~R2 review 反复出现的那类分发形态缺陷）。
# 用 `\d{8}` 而非裸 `-review.md`：后者**内容无关**，用户把真报告存成
# `2026-09-10-review.md`（报告风格时间戳）就会被套上放宽档。
_REVIEW_MEMO_RE = re.compile(r"^\d{8}-review\.md$")


def detect_report_type(report_path: Path) -> str:
    """从路径推断报告类型。

    优先按目录名匹配（gap-scan / journal / pulse），再按
    `{6位代码}-{名称}` 目录或扁平文件名匹配代码前缀。
    """
    # 复盘纪要（R2/T8-3）：落点在报告同目录，必须先于目录/代码前缀判定
    # （否则会被认成标的研报）
    if _REVIEW_MEMO_RE.match(report_path.name):
        return "review"

    parts = report_path.parts
    if "gap-scan" in parts:
        return "gap_scan"
    if "journal" in parts:
        return "journal"
    if "pulse" in parts:
        return "pulse"

    parent_dir = report_path.parent.name
    m = re.match(r"^(\d{6})-", parent_dir)
    if m:
        return _classify_by_symbol(m.group(1))

    fname = report_path.name
    m = re.match(r"^(\d{6})[-_]", fname)
    if m:
        return _classify_by_symbol(m.group(1))

    if "gap" in fname.lower():
        return "gap_scan"
    return "unknown"


def _extract_symbol(report_path: Path) -> str:
    """从路径提取 6 位标的代码（找不到返回空串）。"""
    parent_dir = report_path.parent.name
    m = re.match(r"^(\d{6})-", parent_dir)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{6})[-_]", report_path.name)
    return m.group(1) if m else ""


# ── 结构检查规则表 ────────────────────────────────────────────────────────

# 每个条目: (rule_id, pattern, severity, message)；缺失即记 finding，层状态置 warn
_STRUCTURE_REQUIREMENTS: dict[str, list[tuple[str, str, str, str]]] = {
    "stock": [
        ("structure-fact", r"\[事实\]", "warning", "报告应包含 [事实] 块引用数据来源（SOP-QC）"),
        ("structure-analysis", r"\[分析\]", "warning", "报告应包含 [分析] 块（基于事实的逻辑推演）"),
        ("structure-evidence", r"\[证据强度", "warning", "报告应包含 [证据强度:] 四维标注（SOP-EV）"),
        ("structure-source", r"\[来源:", "warning", "报告应标注 [来源:] 数据来源"),
        ("structure-risk-statement", r"不构成投资建议", "warning", "报告应包含风险声明（不构成投资建议）"),
    ],
    "etf": [
        ("structure-fact", r"\[事实\]", "warning", "报告应包含 [事实] 块引用数据来源（SOP-QC）"),
        ("structure-analysis", r"\[分析\]", "warning", "报告应包含 [分析] 块（基于事实的逻辑推演）"),
        ("structure-evidence", r"\[证据强度", "warning", "报告应包含 [证据强度:] 四维标注（SOP-EV）"),
        ("structure-risk-statement", r"不构成投资建议", "warning", "报告应包含风险声明（不构成投资建议）"),
    ],
    "journal": [
        # 买入路径四维（逻辑完整性/数据盲点/仓位匹配/风险收益比）与
        # 卖出路径四维（一致性/情绪化检测/参考点独立性/机会成本，v0.2.5 D2）双支持
        ("journal-logic", r"逻辑完整性|一致性", "warning", "journal 应包含评估维度（逻辑完整性或一致性）"),
        ("journal-blindspot", r"数据盲点|情绪化检测|情绪检测", "warning", "journal 应包含评估维度（数据盲点或情绪化检测）"),
        ("journal-position", r"仓位匹配|参考点独立性", "warning", "journal 应包含评估维度（仓位匹配或参考点独立性）"),
        ("journal-rr", r"风险收益比|机会成本", "warning", "journal 应包含评估维度（风险收益比或机会成本）"),
    ],
    "gap_scan": [
        ("gap-title", r"跳空缺口", "warning", "gap-scan 报告应包含'跳空缺口'标题"),
        ("gap-summary", r"(扫描摘要|统计|命中)", "warning", "gap-scan 报告应包含扫描摘要/命中统计"),
    ],
    "pulse": [],
    # 复盘纪要（R2/T8-3）：**按设计不含** [事实]/[分析]/[证据强度]——它明确不做
    # 推演，只对照当时写下的假设与证伪条件的当前状态。故只要求风险声明；
    # 若套用 etf/stock 的结构检查会稳定产出 4 条误报。
    "review": [
        ("structure-risk-statement", r"不构成投资建议", "warning",
         "复盘纪要应包含风险声明（不构成投资建议）"),
    ],
    "unknown": [],
}


def _check_structure(text: str, report_type: str) -> LayerResult:
    """结构层：按报告类型检查必备章节/标签存在性 + 表列错位（2026-10-10 A8）。

    表列错位在 ETF 为 error（md 渲染器静默缺列/截断 = 数据丢失），其余报告类型为
    warning（先暴露不阻断；WARN 仍可交付）。stock Insight 报告走独立分支（早退，
    不含表列检查，行为未变）。
    """
    layer = LayerResult(layer="structure", status="pass")
    if report_type == "stock" and _is_insight_report(text):
        required = ("可得结论", "核心矛盾", "观察节点与更新规则", "已知未知与补证路径", "不构成任何投资建议")
        for label in required:
            if label not in text:
                layer.findings_count += 1
                layer.details.append({"id": "insight-structure", "severity": "error",
                                      "message": f"Insight 缺少必要区块: {label}"})
        if layer.findings_count:
            layer.status = "fail"
        return layer
    for rule_id, pattern, severity, message in _STRUCTURE_REQUIREMENTS.get(report_type, []):
        if re.search(pattern, text):
            continue
        layer.findings_count += 1
        layer.details.append({"id": rule_id, "severity": severity, "message": message})
    # 表列错位（2026-10-10，A8）：ETF 为 error——md 渲染器（etf_md）对列数不齐的
    # 表体行静默补列/截断（数据丢失无报错）；其余类型 warning——先暴露不阻断，
    # 「WARN 仍可交付」的既有可交付面不变（兼容存量报告，不静默升级为失败）。
    if report_type in ("stock", "etf", "journal", "gap_scan", "pulse", "review"):
        sev = "error" if report_type == "etf" else "warning"
        for f in table_alignment_findings(text, severity=sev):
            layer.findings_count += 1
            layer.details.append(f)
    if layer.findings_count:
        layer.status = ("fail" if any(d.get("severity") == "error" for d in layer.details)
                        else "warn")
    return layer


# ── 股票报告交付完成度 ────────────────────────────────────────────────────

# v0.2.8 起，标准的自动化股票报告使用「研究快照」标题；它不是最终研究成品，
# 必须由同代 analysis.json 完成可追溯的分析合成。这里同时要求风险提示中的
# 「自动化引擎生成」字样，避免把用户手写的研究备忘录误判为待合成快照。
_AUTOMATED_STOCK_SNAPSHOT_RE = re.compile(
    # 公司名允许为空（v0.3.0 A4）：basic_info 采集失败时渲染器输出
    # `# 600176  研究快照`（双空格，render_markdown/_v2.py）。旧式 `\s+.+?\s+`
    # 要求名字 ≥1 字符 → 该标题不命中 → _check_stock_completion 落 skip 分支 →
    # 强制侧车闸门**静默失效**（fail-open），恰在数据覆盖最差时放行。
    r"^#\s+\d{6}\s+.*?\s+研究快照\s*$", re.M
)
_AUTOMATED_ENGINE_NOTICE_RE = re.compile(r"本报告由自动化引擎生成")

# 仅捕捉明确表示「尚待模型填写」的模板残留。不能把「待独立验证」「数据不可得」
# 这类有意保留的不确定性误作未完成报告。
_TEMPLATE_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 包在方括号里的「待模型填写」残留。**不含**裸 `分析提示`：`> [分析提示]`
    # 是 _law10_hint 的体例标签（_v3.py 的「每题末尾固定格式」），每份 full 报告
    # 都带，命中它会让完成度门禁对任何报告恒 FAIL。真正的未填提示由后面的
    # `分析提示（Claude 填写）` 规则精确捕捉。
    re.compile(
        r"\[\s*(?:待\s*(?:Claude|AI|LLM)(?:\s+report)?(?:\s+阶段)?\s*"
        r"(?:填充|填写|补充|验证)?|待(?:填|填写|填充)|TODO|TBD|FIXME)\s*\]",
        re.I,
    ),
    # 带具体待核内容的方括号占位；普通正文中的「待 Claude 核对」不应触发。
    re.compile(r"\[\s*待\s*(?:Claude|AI|LLM)\s+核对[^\]\n]{0,24}\s*\]", re.I),
    re.compile(r"分析提示\s*[（(]\s*(?:Claude|AI|LLM)[^）)]{0,24}[）)]", re.I),
    re.compile(
        r"待\s*(?:Claude|AI|LLM)(?:\s+report)?(?:\s+阶段)?\s*"
        r"(?:填充|填写|补充|验证)",
        re.I,
    ),
)

_MARKDOWN_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
_EMPTY_BASIS_RE = re.compile(
    r"(?:当前数据)?\s*(?:未形成(?:明确)?|尚未形成|暂无|无|没有)\s*"
    r"(?:明确)?\s*(?:多头|空头|bull|bear|左侧|右侧)?\s*"
    r"(?:逻辑链|支撑依据|依据|证据|基础)|"
    # 渲染器的「左/右侧参考指标数据不足」哨兵：尾缀「或未达到阈值」曾使
    # remaining 判定为非空 → 无实质依据的节逃过 error 级 completion-empty-basis
    # （两侧只差这 6 个字，同份输入左侧放行、右侧报错）。
    # ⚠️ 尾缀现无生产发出方（左侧渲染器已改为含实测值与阈值的实质句），
    # 但**勿删**：本仓会对存量报告复检（--latest/--dir），历史报告里带该尾缀的
    # 行仍须判为空依据。删除会使这些报告逃过 completion-empty-basis。
    r"(?:左|右)侧参考指标数据不足(?:或未达到阈值)?",
    re.I,
)


def _same_generation_analysis_path(report_path: Path) -> Path:
    """返回 ``report.md`` 的同代 ``report.analysis.json`` 路径。"""
    return report_path.with_suffix(".analysis.json")


def _sidecar_validation_error(path: Path) -> str | None:
    """返回侧车不合格原因；复用正式 analysis schema 以避免协议漂移。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"无法读取或解析 JSON（{exc}）"
    if not isinstance(raw, list) or not raw:
        return "顶层必须是至少含一个分析段的数组"
    try:
        errors = _validate_analysis_sections_path_safe(raw)
    except Exception as exc:  # pragma: no cover - 分发包缺模块时 fail-closed
        return f"无法校验 analysis schema（{exc}）"
    if errors:
        return "; ".join(errors[:3])
    return None


def _validate_analysis_sections_path_safe(raw: list[dict]) -> list[str]:
    """以 canonical stock lib 的 schema 校验 sidecar，隔离 ``lib`` 名称冲突。

    shared QC 在源码仓库中可作为顶层 ``report_qc`` 导入，某些 harness 又已将
    ``skills.lib`` 注册成 ``lib``；而 stock 的 ``analysis_schema`` 依赖
    ``lib.md_subset``。加载期间短暂把 canonical alias 暴露为 ``lib``，即可沿用
    同一份 ``validate_sections``（包括 position/evidence_tag/Markdown 子集），
    随后无条件恢复调用方的模块命名空间。
    """
    package = _load_invest_lib()
    previous_lib = sys.modules.get("lib")
    previous_md_subset = sys.modules.get("lib.md_subset")
    sys.modules["lib"] = package
    try:
        analysis_schema = importlib.import_module("_invest_lib.analysis_schema")
        return analysis_schema.validate_sections(raw)
    finally:
        if previous_lib is None:
            sys.modules.pop("lib", None)
        else:
            sys.modules["lib"] = previous_lib
        if previous_md_subset is None:
            sys.modules.pop("lib.md_subset", None)
        else:
            sys.modules["lib.md_subset"] = previous_md_subset


def _basis_section_kind(title: str) -> str | None:
    """识别需要实际内容的多空/左-右依据小节；合并标题不作猜测。"""
    lower = title.lower()
    if "bull/bear" in lower or "多空" in title:
        return None
    if "多头" in title or "bull" in lower:
        return "Bull"
    if "空头" in title or "bear" in lower:
        return "Bear"
    if "左侧" in title and ("依据" in title or "概率" in title):
        return "左侧"
    if "右侧" in title and ("依据" in title or "概率" in title):
        return "右侧"
    return None


def _section_body(lines: list[str], start: int, level: int) -> list[str]:
    """提取标题后的正文，遇到同级或更高层级标题即停止。"""
    body: list[str] = []
    for line in lines[start + 1:]:
        match = _MARKDOWN_HEADING_RE.match(line)
        if match and len(match.group(1)) <= level:
            break
        body.append(line)
    return body


def _basis_is_empty(body: list[str]) -> bool:
    """判断依据节是否没有实质内容或只写了明确的「没有逻辑链」占位句。"""
    content = [line.strip() for line in body if line.strip() and line.strip() != "---"]
    if not content:
        return True
    # 明确的「当前数据未形成明确空头逻辑链」与渲染器的
    # 「左/右侧参考指标数据不足」都不是实际依据。只有这些 sentinel 时视为
    # 空；同节若另有实质论据则保守放行，避免把数据缺口说明误报为全节为空。
    if not all(_EMPTY_BASIS_RE.search(line) for line in content):
        return False
    for line in content:
        # 同一行可以先声明部分指标不可得、再给出可用的事实依据；只剥离
        # sentinel、来源标签、证据等级与 Markdown 装饰后仍有文字，就不是空节。
        remaining = _EMPTY_BASIS_RE.sub("", line)
        remaining = re.sub(r"\[来源\s*[:：][^\]]*\]", "", remaining)
        remaining = re.sub(r"证据强度\s*[:：]\s*[✅⚠️❓]", "", remaining)
        remaining = re.sub(r"[>\-*①②③④⑤⑥\s\[\]：:，,。.！!；;]+", "", remaining)
        if remaining:
            return False
    return True


def _check_stock_completion(report_path: Path, text: str) -> LayerResult:
    """检查股票研究成品是否仍是未完成的自动化快照。

    这是独立于 lint profile 的 error 级交付门禁：``--fail-on error`` 也不能
    放过未填模板或缺少合成侧车的报告。手写/已完成的老式研究备忘录不以文件名
    推断，只有标题和自动化声明同时出现才要求同代 sidecar。
    """
    layer = LayerResult(layer="completion", status="skip")
    lines = text.splitlines()

    for line_no, line in enumerate(lines, start=1):
        if any(pattern.search(line) for pattern in _TEMPLATE_MARKER_PATTERNS):
            layer.findings_count += 1
            layer.details.append({
                "id": "completion-template-placeholder",
                "severity": "error",
                "line": line_no,
                "message": "报告保留了待模型填写的模板占位，分析合成尚未完成",
            })

    engine_notice = bool(_AUTOMATED_ENGINE_NOTICE_RE.search(text))
    is_automated_snapshot = bool(
        _AUTOMATED_STOCK_SNAPSHOT_RE.search(text) and engine_notice
    )
    if engine_notice and not is_automated_snapshot and not _is_insight_report(text):
        # C1-c：标题漂移守卫——含引擎声明却认不出快照标题时，同代侧车闸门
        # 会静默失效（本函数注释记录过同类事故）；改为 error 强制发现，不再
        # 落 skip 被 _compute_overall 过滤成假 PASS。
        layer.findings_count += 1
        layer.details.append({
            "id": "completion-snapshot-title-unrecognized",
            "severity": "error",
            "message": ("报告含自动化引擎声明但标题未被识别为研究快照"
                        "（`# {6位代码} {名称} 研究快照`）——标题漂移会使同代"
                        "分析侧车校验失效，须核对"),
        })
    if is_automated_snapshot:
        sidecar = _same_generation_analysis_path(report_path)
        if not sidecar.is_file():
            layer.findings_count += 1
            layer.details.append({
                "id": "completion-analysis-sidecar-missing",
                "severity": "error",
                "message": f"自动化研究快照缺少同代分析侧车: {sidecar.name}",
            })
        else:
            validation_error = _sidecar_validation_error(sidecar)
            if validation_error:
                layer.findings_count += 1
                layer.details.append({
                    "id": "completion-analysis-sidecar-invalid",
                    "severity": "error",
                    "message": f"自动化研究快照的同代分析侧车不合格: {validation_error}",
                })

    for index, line in enumerate(lines):
        match = _MARKDOWN_HEADING_RE.match(line)
        if not match:
            continue
        kind = _basis_section_kind(match.group(2))
        if kind is None:
            continue
        body = _section_body(lines, index, len(match.group(1)))
        if _basis_is_empty(body):
            layer.findings_count += 1
            layer.details.append({
                "id": "completion-empty-basis",
                "severity": "error",
                "line": index + 1,
                "message": f"{kind} 依据节为空或仅声明无逻辑链，不能作为完成的研究交付",
            })

    # C2-c：档案-正文风格一致性（warning）——正文自评风格须与同代 profile 的
    # ``--style`` 档案一致（反例：600519 profile=价值 vs 正文自评「成长」）。
    profile_path = report_path.with_suffix(".profile.json")
    if profile_path.is_file():
        try:
            import json as _json
            _prof = _json.loads(profile_path.read_text(encoding="utf-8")) or {}
            # 侧车真实结构为嵌套 {"profile": {"style": ...}}（research_profile.py）；
            # 兼容旧扁平写法。
            prof_style = ((_prof.get("profile") or {}).get("style")
                          or _prof.get("style"))
        except Exception:
            prof_style = None
        m_style = re.search(r"自评风格\s*([^\s×（(]+)", text)
        if prof_style and m_style and m_style.group(1) != prof_style:
            layer.findings_count += 1
            layer.details.append({
                "id": "completion-style-mismatch",
                "severity": "warning",
                "message": (f"正文自评风格「{m_style.group(1)}」与同代档案风格「{prof_style}」"
                            "不一致——正文风格表述须与档案/引擎 style_match 一致（C2-c）"),
            })

    if layer.findings_count:
        layer.status = "fail"
    elif is_automated_snapshot or any(
        _basis_section_kind(match.group(2))
        for line in lines if (match := _MARKDOWN_HEADING_RE.match(line))
    ):
        layer.status = "pass"
    return layer


def _is_insight_report(text: str) -> bool:
    """Insight has a compact, deliberate structure rather than legacy [事实]/[分析] blocks."""
    return "— 研究要点" in text and "## 可得结论" in text and "## 证据底稿" in text


def _check_insight_contract(report_path: Path, text: str) -> LayerResult:
    """Validate the sidecars which make an Insight report auditable.

    The manifest is intentionally checked without importing the stock renderer:
    report_qc is shared by distributable packages and must remain usable when a
    sibling skill is absent.
    """
    layer = LayerResult(layer="insight-contract", status="skip")
    if not _is_insight_report(text):
        return layer
    layer.status = "pass"
    expected = {
        "facts": report_path.with_suffix(".facts.json"),
        "insight": report_path.with_suffix(".insight.json"),
        "manifest": report_path.with_suffix(".report.json"),
    }
    payloads: dict[str, dict] = {}
    for kind, path in expected.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("顶层不是对象")
            payloads[kind] = payload
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            layer.findings_count += 1
            layer.details.append({"id": f"insight-{kind}-sidecar-invalid", "severity": "error",
                                  "message": f"Insight 缺少或无法读取 {path.name}: {exc}"})
    facts = payloads.get("facts", {}).get("facts")
    insight = payloads.get("insight", {})
    manifest = payloads.get("manifest", {})
    if facts is not None and (not isinstance(facts, list) or any(
            not isinstance(fact, dict) or not fact.get("id") or not fact.get("source_ids")
            or not fact.get("as_of") for fact in facts)):
        layer.findings_count += 1
        layer.details.append({"id": "insight-facts-untraceable", "severity": "error",
                              "message": "Insight Facts 必须包含 ID、来源和截至日期"})
    if insight and (insight.get("mode") != "insight" or insight.get("completion") not in {"complete", "insufficient"}):
        layer.findings_count += 1
        layer.details.append({"id": "insight-status-invalid", "severity": "error",
                              "message": "Insight sidecar 的 mode 或 completion 非法"})
    # 分析合成层：产物自称「已注入」时，同代侧车必须存在且过正式 schema——
    # 否则「已注入」只是一个无从追溯的字符串（缺键 = 旧产物，跳过）。
    synthesis = insight.get("synthesis") if isinstance(insight, dict) else None
    if isinstance(synthesis, dict) and synthesis.get("status") not in (None, "injected", "absent"):
        layer.findings_count += 1
        layer.details.append({"id": "insight-synthesis-status-invalid", "severity": "error",
                              "message": f"Insight synthesis.status 非法：{synthesis.get('status')!r}"})
    elif isinstance(synthesis, dict) and synthesis.get("status") == "injected":
        sidecar = _same_generation_analysis_path(report_path)
        if not sidecar.is_file():
            layer.findings_count += 1
            layer.details.append({"id": "insight-analysis-sidecar-missing", "severity": "error",
                                  "message": f"声明「分析合成已注入」但同代侧车不存在：{sidecar.name}"})
        else:
            reason = _sidecar_validation_error(sidecar)
            if reason:
                layer.findings_count += 1
                layer.details.append({"id": "insight-analysis-sidecar-invalid", "severity": "error",
                                      "message": f"同代分析侧车不合格：{reason}"})
    registered = manifest.get("analysis_sidecar") if isinstance(manifest, dict) else None
    if registered and not (report_path.parent / str(registered)).is_file():
        layer.findings_count += 1
        layer.details.append({"id": "insight-manifest-analysis-mismatch", "severity": "error",
                              "message": f"Manifest 登记的 analysis_sidecar {registered} 不存在"})
    if manifest and (manifest.get("mode") != "insight" or manifest.get("report") != report_path.name):
        layer.findings_count += 1
        layer.details.append({"id": "insight-manifest-mismatch", "severity": "error",
                              "message": "Insight manifest 未绑定当前同代 Markdown"})
    html_name = manifest.get("html") if manifest else None
    if html_name:
        html_path = report_path.parent / str(html_name)
        try:
            html_text = html_path.read_text(encoding="utf-8")
            for finding in insight.get("findings") or []:
                finding_id = finding.get("id") if isinstance(finding, dict) else None
                if finding_id and f"evidence-{finding_id}" not in html_text:
                    raise ValueError(f"HTML 缺少 Finding 锚点 {finding_id}")
        except (OSError, ValueError) as exc:
            layer.findings_count += 1
            layer.details.append({"id": "insight-html-pair-mismatch", "severity": "error",
                                  "message": f"Insight HTML 未与同代 Findings 配对: {exc}"})
    if layer.findings_count:
        layer.status = "fail"
    return layer


# ── ETF / 板块同步性 derived 字段校验 ─────────────────────────────────────

# 16 个引擎 derived 字段的值域（宽松，避免误报；主要抓数量级错误/全零/位数异常）
_ETF_DERIVED_RANGES: dict[str, tuple[float, float]] = {
    "nav_vs_ma20_pct": (-60.0, 60.0),
    "nav_vs_ma60_pct": (-60.0, 60.0),
    "nav_vs_boll_mid_pct": (-60.0, 60.0),
    "boll_position_pct": (-5.0, 105.0),   # BOLL 带内位置可略越界
    "nav_to_boll_lower_pct": (-60.0, 60.0),
    "nav_to_boll_upper_pct": (-60.0, 60.0),
    "boll_bandwidth_pct": (0.0, 100.0),
    "daily_volatility_pct": (0.0, 20.0),
    # v0.2.6 D 类字段（compute_history_stats 输出）：年内低点偏离可高可负、ATR 占比上限宽松
    "dist_to_ytd_low_pct": (-100.0, 500.0),
    "atr14_pct": (0.0, 60.0),
    # v0.2.7 E1 板块同步性引擎（sector_sync.py）6 字段：
    # β 对板块日收益（A 股涨跌停 ±20% 上限、小票可更宽）；R² 与特质方差占比 ∈ [0,1]；
    # 板块内离散度为当日横截面收益标准差（%）；CSAD γ2 按小数收益回归（CCK 量级
    # −0.3~−5，放宽防误报）；下行相关差为两相关系数之差（各 ∈ (−1,1)）。
    "sector_beta_60d": (-5.0, 10.0),
    "sector_r2_60d": (0.0, 1.0),
    "idio_var_share": (0.0, 1.0),
    "sector_dispersion": (0.0, 20.0),
    "csad_gamma2": (-20.0, 20.0),
    "downside_corr_gap": (-2.0, 2.0),
}

# 报告文本中形如 "nav_vs_ma20_pct: -15.36" 或 "nav_vs_ma20_pct: -15.36%" 的引用
_DERIVED_PATTERN = re.compile(
    r"(nav_vs_ma20_pct|nav_vs_ma60_pct|nav_vs_boll_mid_pct|boll_position_pct|"
    r"nav_to_boll_lower_pct|nav_to_boll_upper_pct|boll_bandwidth_pct|daily_volatility_pct|"
    r"dist_to_ytd_low_pct|atr14_pct|"
    r"sector_beta_60d|sector_r2_60d|idio_var_share|sector_dispersion|"
    r"csad_gamma2|downside_corr_gap)"
    r"[：:]\s*([+-]?\d+\.?\d*)%?"
)

# 中文标签 → 字段名（ETF 报告模板表格行用 "| NAV vs MA20 偏离 | -15.36% |" 形式）。
# 模板措辞存在漂移变体，均收录：无"偏离"（515880 式）、"NAV 距 BOLL 下轨"
# （588000 式，带 "NAV " 前缀）。板块同步性标签（v0.2.7 E1）为 stock 报告模板。
_DERIVED_CN_LABELS: dict[str, str] = {
    "NAV vs MA20 偏离": "nav_vs_ma20_pct",
    "NAV vs MA60 偏离": "nav_vs_ma60_pct",
    "NAV vs BOLL 中轨偏离": "nav_vs_boll_mid_pct",
    "NAV vs MA20": "nav_vs_ma20_pct",
    "NAV vs MA60": "nav_vs_ma60_pct",
    "NAV vs BOLL 中轨": "nav_vs_boll_mid_pct",
    "BOLL 位置": "boll_position_pct",
    "NAV 距 BOLL 下轨": "nav_to_boll_lower_pct",
    "NAV 距 BOLL 上轨": "nav_to_boll_upper_pct",
    "距 BOLL 下轨": "nav_to_boll_lower_pct",
    "距 BOLL 上轨": "nav_to_boll_upper_pct",
    "BOLL 带宽": "boll_bandwidth_pct",
    "日均波动率": "daily_volatility_pct",
    "距年内低点": "dist_to_ytd_low_pct",
    "ATR14 占比": "atr14_pct",
    "板块 Beta(60日)": "sector_beta_60d",
    "板块 R²(60日)": "sector_r2_60d",
    "特质方差占比": "idio_var_share",
    "板块内离散度": "sector_dispersion",
    "CSAD γ2": "csad_gamma2",
    "下行相关差": "downside_corr_gap",
}
# 仅匹配表格行（以 | 开头、数值后跟 | 收尾）：衍生值只在模板表格渲染，
# 散文中的指标名词（如 "距 BOLL 下轨仅 6.41%，BOLL 带宽 54%"）天然排除。
# 交替顺序长串优先（"NAV vs MA20 偏离" 先于 "NAV vs MA20"）。
# 中段允许至多一个 |（标签格与数值格的分隔符），但禁止两个以上：
# "| 日均波动率 | 暂无 | 16.381% |" 不得把第三格数字认作本字段值（review fix #3）。
_DERIVED_CN_PATTERN = re.compile(
    r"\|[^|\d\n]*?("
    r"NAV vs MA20 偏离|NAV vs MA60 偏离|NAV vs BOLL 中轨偏离|"
    r"NAV vs MA20|NAV vs MA60|NAV vs BOLL 中轨|"
    r"NAV 距 BOLL 下轨|NAV 距 BOLL 上轨|BOLL 位置|距 BOLL 下轨|距 BOLL 上轨|"
    r"BOLL 带宽|日均波动率|距年内低点|ATR14 占比|"
    r"板块 Beta\(60日\)|板块 R²\(60日\)|特质方差占比|板块内离散度|CSAD γ2|下行相关差)"
    r"(?:[^|\d\-+.\n]*?\|)?[^|\d\-+.\n]*?([+-]?\d+\.?\d*)%?[^|\d\n]*?\|"
)
# 已知标签行检测（值可缺失）：标签命中即算「措辞正常」——
# "| NAV vs MA20 偏离 | — |" 是引擎 derived=None 的合法渲染，不视为漂移
_DERIVED_CN_LABEL_ONLY = re.compile(
    r"\|[^|\n]*?("
    r"NAV vs MA20 偏离|NAV vs MA60 偏离|NAV vs BOLL 中轨偏离|"
    r"NAV vs MA20|NAV vs MA60|NAV vs BOLL 中轨|"
    r"NAV 距 BOLL 下轨|NAV 距 BOLL 上轨|BOLL 位置|距 BOLL 下轨|距 BOLL 上轨|"
    r"BOLL 带宽|日均波动率|距年内低点|ATR14 占比|"
    r"板块 Beta\(60日\)|板块 R²\(60日\)|特质方差占比|板块内离散度|CSAD γ2|下行相关差)[^|\n]*\|"
)
# 存在性检测：表格行出现衍生指标名词（已知或未知标签）→ 用于漂移判定
_DERIVED_CN_ROW_PRESENT = re.compile(
    r"\|[^|\n]*(NAV vs MA|BOLL 位置|BOLL 带宽|日均波动率|距 BOLL|距年内低点|ATR14|"
    r"板块 Beta|板块 R²|特质方差|板块内离散度|CSAD|下行相关)[^|\n]*\|"
)


def _extract_derived_values(text: str) -> dict[str, str]:
    """从报告文本提取 derived 字段值（字段名 + 中文标签两种形式）。"""
    values: dict[str, str] = {}
    for field_name, raw in _DERIVED_PATTERN.findall(text):
        values[field_name] = raw
    for label, raw in _DERIVED_CN_PATTERN.findall(text):
        field_name = _DERIVED_CN_LABELS.get(label)
        if field_name and field_name not in values:
            values[field_name] = raw
    return values


def _check_etf_derived(text: str) -> LayerResult:
    """derived 层：ETF/stock 报告中的 derived 字段值域合理性（v0.2.7 E1 板块同步性 6 字段入白名单）。"""
    layer = LayerResult(layer="derived", status="skip")
    found = _extract_derived_values(text)
    label_rows = _DERIVED_CN_LABEL_ONLY.findall(text)     # 已知标签行（含值缺失）
    present_rows = _DERIVED_CN_ROW_PRESENT.findall(text)  # 全部指标行（含未知标签）
    drift = len(present_rows) - len(label_rows)
    if drift > 0:
        # 存在措辞漂移/未知标签的指标行 → 无论其他行是否有效，该行未被校验（假绿防护）
        layer.status = "warn"
        layer.findings_count = 1
        layer.details.append({
            "id": "derived-template-drift",
            "severity": "warning",
            "message": "报告存在标签与引擎命名不匹配的衍生指标行，字段未被校验",
        })

    if found:
        if layer.status != "warn":
            layer.status = "pass"
        for field_name, raw in found.items():
            try:
                value = float(raw)
            except ValueError:
                layer.findings_count += 1
                layer.details.append({
                    "id": f"derived-{field_name}",
                    "severity": "warning",
                    "message": f"字段 {field_name} 值 '{raw}' 无法解析为数值",
                })
                continue
            lo, hi = _ETF_DERIVED_RANGES.get(field_name, (-1e9, 1e9))
            if not (lo <= value <= hi):
                layer.findings_count += 1
                layer.details.append({
                    "id": f"derived-{field_name}",
                    "severity": "warning",
                    "message": f"字段 {field_name} 值 {value} 超出合理范围 [{lo}, {hi}]",
                })
            elif abs(round(value, 2) - value) > 1e-6:
                layer.findings_count += 1
                layer.details.append({
                    "id": f"derived-{field_name}",
                    "severity": "info",
                    "message": f"字段 {field_name} 值 {value} 未保留两位小数（引擎输出 round(…, 2)）",
                })
        # 仅 warning 级发现（超范围/无法解析/漂移）翻转状态；info 级（位数）不阻塞
        if any(d["severity"] == "warning" for d in layer.details):
            layer.status = "warn"
    elif not present_rows:
        return layer  # 报告未引用衍生字段 → skip
    # 已知标签行但值缺失（"—"/"暂无"，引擎 derived=None 渲染）→ 合法，不视为漂移
    return layer


# ── sourcing 层（v0.3.0 T6-2/T6-3）：F2 派生表述来源 + F4 §N 引用存在性 ────

# F2：加工组派生表述词（倍数/百分点/个点/成数/约百分数）——命中行前 _F2_SOURCE_WINDOW
# 行内无 [来源:] 即 warning（人工复核语义，非 error——D1=A 边界不破）。
# 词表为最小集（R1 子计划 §2）：不含裸「%」以免海量误报；扩展词表须补测试。
_F2_PATTERN = re.compile(
    r"(?:[+-]?\d+(?:\.\d+)?\s*(?:倍|个百分点|个点|bp)|"
    r"近?(?:六成|七成|八成|九成)|五成以上|过半|"
    r"约\s*[+-]?\d+(?:\.\d+)?\s*%)"
)
_F2_SOURCE_WINDOW = 3  # 行内或前 N 行含 [来源: …] 即视为有源
_SECTION_REF_RE = re.compile(r"§\s*(\d+(?:\.\d+)?)")
_SECTION_HEAD_RE = re.compile(r"^#{2,4}\s*(\d+(?:\.\d+)?)[\s.、]")
# F4 豁免：指向**外部规范**的 §N（如「共享规范 report-conventions.md §2.3」）不是
# 本文节号引用（R1 审查 F4：repo 内全部误报均为该形态）。前缀近距匹配，宁漏勿扰。
# 只认**文档指针**（.md 文件名 / 规范 / 附件）——通用引用动词（说明/参见/详见/遵循）
# 不是外部线索：「详见 §5」是最惯用的本文交叉引用写法，豁免它会让 F4 恰好在最自然
# 的措辞上失明（R1 审查 F4 二次收窄）。
_EXTERNAL_REF_PREFIX_RE = re.compile(
    r"(?:规范|conventions\.md|\.md|附件)\s*$"
)


def _check_sourcing(text: str) -> LayerResult:
    """sourcing 层：F2 派生词缺来源（warning）+ F4 §N 引用指向不存在节（warning）。"""
    layer = LayerResult(layer="sourcing", status="pass")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if not _F2_PATTERN.search(ln):
            continue
        window = "\n".join(lines[max(0, i - _F2_SOURCE_WINDOW): i + 1])
        if re.search(r"\[来源\s*[:：]", window):
            continue
        layer.findings_count += 1
        layer.details.append({
            "id": "f2-derived-claim-no-source",
            "severity": "warning",
            "line": i + 1,
            "message": f"派生表述疑似缺来源标注（前 {_F2_SOURCE_WINDOW} 行内无 [来源:]）："
                       f"{ln.strip()[:60]}",
        })
    refs: set[str] = set()
    for m in _SECTION_REF_RE.finditer(text):
        prefix = text[max(0, m.start() - 14):m.start()]
        if _EXTERNAL_REF_PREFIX_RE.search(prefix):
            continue  # 外部规范引用（report-conventions.md §N 等）不参与本文节号校验
        refs.add(m.group(1))
    heads = set()
    for ln in lines:
        m = _SECTION_HEAD_RE.match(ln)
        if m:
            heads.add(m.group(1))
    for ref in sorted(refs - heads, key=lambda s: tuple(int(x) for x in s.split("."))):
        layer.findings_count += 1
        layer.details.append({
            "id": "f4-section-ref-missing",
            "severity": "warning",
            "message": f"正文引用 §{ref} 但报告无对应标题节",
        })
    if layer.findings_count:
        layer.status = "warn"
    return layer


# ── R-A1 / R-A2 / R-A6 指标组（v0.3.0 A3 移植） ─────────────────────────────
# 自 invest-a-stock/scripts/lib/report_qc.py（旧 228 行模块）移植，使
# `invest.py qc-report` 与第 0 层准出走同一实现——此前两条通道用的是两份
# 实现，对同一文件可给出相反裁决（旧版完全没有第 0 层闸门）。
#
# 契约边界（report-conventions §3.4）：R-A1 可读性指标组是**软建议**——
# 「不触发 lint error，不作合规阻断」。故 `_check_readability` 的状态
# **封顶 warn**：即便 readability-length 等 finding 为 error 级，也不得让
# `_compute_overall` 判 FAIL，否则会把软建议升级成交付阻断。R-A2 / R-A6 是
# error 级实质缺陷，单独成层并保留 error→fail 映射。

READABILITY_MAX_CHARS = 20_000         # 篇幅上限（字符）
READABILITY_LONG_SENT_CHARS = 45       # 长句阈值（字符）
READABILITY_LONG_RATIO_WARN = 0.30     # 长句占比告警阈值
_TERM_GLOSSARY = {
    "趋势", "动能", "资金流", "估值", "分位", "净利差", "毛利率", "净利率", "ROE",
    "ROIC", "WACC", "FCF", "FCFF", "DCF", "同比", "环比", "汇率", "PMI", "CPI",
    "PPI", "VIX", "SOX", "北向", "两融", "基差", "β", "beta", "复合增速",
    "(EP|PE|PB|PS)(TTM)?", "折溢价", "席位", "龙虎榜", "胜率", "赔率",
}

# C1-c（v0.3.1 收尾）：结论/主判断段标题族——封闭集，锚定行首标题起始词。
# 反例：600519 2026-10-02 报告主判断标题为 `## 主判断：…`，旧 regex 只认
# 「(主要|核心)?结论」开头 → 全报告零命中 → conclusion-evidence 层静默 pass
# （D04「主判断未被 QC 定位」）。覆盖：结论族（含 insight `## 可得结论`，
# render_insight.py）+ 判断族（主/主要/核心/首要判断）+ D4 体例 H2 括注
# `（结论）/（主要结论）/（结论与交易结构）`；否定式（「非单一结论」）与
# 模块内 H3（「因子交叉验证结论」）不命中。
_CONCLUSION_HEAD_RE = re.compile(
    r"^#{2,3}\s*(?:可得结论|重要发现（5 分钟阅读区）|(?:主要|核心)?结论|(?:主要|核心|首要|主)?判断)"
    r"|^##\s[^\n]*（(?:主要|核心)?结论(?:与交易结构)?）",
    re.M,
)
_SENT_SPLIT_RE = re.compile(r"[。！？!?]")
_EVIDENCE_TAG_RE = re.compile(r"\[(来源|证据|证据强度|事实)\s*[:：]")
_FACT_MARK_RE = re.compile(r"\[事实\]")
_ANALYSIS_MARK_RE = re.compile(r"\[分析\]")
# R-A6 节边界 = `## `，与 lint `_SECTION_HEADER_RE`（lint.py:98）**逐字对齐**：
# 同一份报告在两通道必须给同一裁决。lint 走 `stop_at_section_header` + `^##\s`
# 且**先 strip 再匹配**；原实现用 `^#{2,4}\s` 且不 strip → `### ` 下的 [分析]
# 在 qc 侧报 error、lint 侧 0 命中（reports/ 全量实测 3 篇相反裁决）。
# 注意 `### `/`#### ` 不再是本规则的节边界——与 lint 一致。
# 另注：R-A2 `conclusion_evidence_findings` 仍用 `^#{2,4}`，那是**有意**含 ####
# （乐观/悲观情景子标题不应被当结论断言扫描），语义不同，勿合并。
_RA_SECTION_BOUNDARY_RE = re.compile(r"^##\s")
_FACT_LOOKBACK_LINES = 50   # 与 lint structure-analysis-without-fact 同规则

# 全量审查 #3（P0-2）：畸形字符类 [来源:|[-−]?… 修复（原内容意外跨越
# '['-'-' 码位区间——过宽）；词族与真实模板措辞对齐（含条件词「若…则」）
_SUMMARY_ELEMS = {
    "数据": re.compile(r"(?:来源|数据|数值|同比|环比)|[−-]?\d+(?:\.\d+)?(?:%|亿|万|元|倍|x|X)?"),
    "逻辑": re.compile(r"因为|由于|因此|所以|分析|意味着|表明|映射|传导|解释|佐证|支撑|推断|归因|若|如果"),
    "分歧": re.compile(r"分歧|争议|不同观点|不同解读|矛盾|相反|另类路径|不确定性来源"),
    "风险": re.compile(r"风险|不确定性|警示|关注点|留意|注意|制约|下行|回撤|假设失效|承压"),
}
# 结论段结构行（表行/分隔/标题/代码围栏）不算断言（全量审查：FP 源）。
# R2 二轮（2026-10-04 独立复检）：`>` 引用行**不再整体跳过**——引用包装不能
# 吞断言（反例：`> 公司盈利持续改善` 曾被整行跳过、完整 QC 仅 WARN）。
# 仅**逐字命中渲染器/规范常量**的引用行保留豁免（下）。
_STRUCT_LINE_RE = re.compile(r"^(\||---|```|#{2,})")
# 引用行豁免白名单（逐字常量，非前缀/非词表）：
#   - `_concise.py:885` 容器说明（段数可变，单独正则逐字匹配）
#   - 「不构成投资建议」类免责句（report_qc 测试夹具与规范常量）
#   - 多情景条件性区间免责句（LAW 6a 必附句）
_QUOTE_CONTAINER_RE = re.compile(
    r"^> 结论先行区：以下 \d+ 段是本次分析的核心判断，数据底稿与其余分析注记见文末。$")
_QUOTE_STRUCT_EXACT = frozenset({
    "> 以上不构成投资建议",
    "> 以上不构成投资建议。",
    "> 以上为条件性估值区间（含假设前提与概率权重），仅供参考，不构成投资建议。",
})


def _is_quote_structure(line: str) -> bool:
    """引用行是否为逐字已知的渲染器/规范常量（R2 二轮，封闭语法）。"""
    return line in _QUOTE_STRUCT_EXACT or bool(_QUOTE_CONTAINER_RE.match(line))
# R-A2 渲染器标签行豁免（R2/#36，2026-10-01 独立核查记录 / 10-02 实施）：渲染器
# 按各宿主体例输出 `**[事实]**`/`**[分析]**`/`**证据等级：** {标签}`
# （_concise.py、render_risk.py、render_insight.py；字段非空才逐行输出）。这些标签行是渲染结构、不是断言——
# 旧实现把它们当结论段缺标签断言：以「结论」开头的卡含渲染标签时必报 3 条 error
# （标题带后缀再 +1；遇 ###/#### 子标题停止扫描，计数可能更少）。
# 豁免从严：仅整行恰为标签时生效；标签后跟正文的行（如 participant_scan
# `**[事实]** 各类参与者…`）仍按断言扫描。
_RENDER_LABEL_RE = re.compile(r"^\*\*\[(?:事实|分析)\]\*\*\s*$")
# 证据等级行合法结构（R2 收紧为封闭语法；R12 round-5 收紧为**四维齐全序列**）：
#   **证据等级：** <等级> [<四维序列>]
#   等级 = A-D（一至二位）/ L1-L4（同 analysis_schema._EVIDENCE_RE 语义）；
#   四维序列 = 强度→来源→时效→交叉、图标-注解配对（唯一来源 = 顶部
#   `from evidence_tags import` 的 `FOUR_DIM_SEQUENCE_PATTERN`）。
#   缺维度/半截序列/图标注解矛盾的尾巴一律不豁免——如 `B 🕐 近 30 日`、
#   `B ✅该标的有望走强`（词表外文字）仍按断言扫描。
_RENDER_EVIDENCE_RE = re.compile(
    r"^\*\*证据等级[:：]\*\*\s+" + EVIDENCE_LABEL_PATTERN + r"\s*$"
)


# R2 二轮（2026-10-04 独立复检）：结构豁免必须是**封闭语法**——两轮反例
# （`**公司盈利持续改善（近30日）**`、`（数据）`、`（999%增长）`、
# `**证据强度：✅ 强**（公司盈利持续改善，来源数据）` 均曾 PASS）证明
# 「括注含信号词/数字」的宽判据不可用。现改为：
#   ① 元数据括注 = **逐段全匹配**封闭语法（日期/报告期形态 + 模板常量短语表），
#      不再「包含即豁免」；
#   ② 加粗小标题 = 主体属封闭名词表（或登记正则）+ 括注合语法 / 裸小标题
#      封闭清单；其余整行加粗按断言扫描；
#   ③ 证据强度尾括注 = 元数据语法或**证据覆盖说明精确表**（实测语料逐字）。
# 短语表为模板常量枚举（非语义信号词）；新增模板须显式登记（fail-closed）。
_METADATA_SEGMENT_RES = (
    re.compile(r"\d{4}-\d{2}-\d{2}(?:\s*(?:收盘|收盘口径|采集|盘中采集|起))?"),
    re.compile(r"\d{4}-\d{2}-\d{2}\s*至\s*\d{4}-\d{2}-\d{2}"),
    re.compile(r"(?:截至|自)\s*\d{4}-\d{2}-\d{2}(?:\s*收盘)?"),
    re.compile(r"报告期\s*\d{4}-\d{2}-\d{2}"),
    re.compile(r"最新收盘\s*\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:中报|三季报|年报)?(?:与二季度)?"),
    re.compile(r"\d+\s*个交易日"),
)
_METADATA_SEGMENTS_EXACT = frozenset({
    "中报累计口径", "封存序列口径", "封存序列", "封存字段", "封存快照字段",
    "均为封存快照字段", "可逐项复算", "固定快照回放", "非收盘定格",
    "如实披露", "口径分离", "口径纪律",
    "假设前提", "概率权重见下", "假设一致性检验", "非买卖建议",
    "每项带可观测证伪条件", "利润池分布代理指标",
})
_METADATA_SPLIT_RE = re.compile(r"\s*[，,；;、+]\s*")


def _metadata_paren_ok(inner: str) -> bool:
    """括注是否为结构元数据：每段（按，；、+ 切分）须**整体**命中封闭语法
    （日期/报告期正则或模板常量短语）；含实质文字即不豁免。"""
    segments = _METADATA_SPLIT_RE.split(inner.strip())
    if not segments or any(not seg for seg in segments):
        return False
    return all(
        seg in _METADATA_SEGMENTS_EXACT
        or any(r.fullmatch(seg) for r in _METADATA_SEGMENT_RES)
        for seg in segments
    )


# 单维强度元数据的渲染器体例（R12 round-5 与 SOP 四维方括号分开）：
#   `**证据强度：✅ 强**` / `**证据强度：⚠️ 中**` / `**证据强度：❓ 弱**`
#   （图标-强度字配对；允许裸强度字；引擎「数据不足」哨兵同为封闭常量）。
#   旧引擎单维方括号 `[证据强度: ⚠️ 中]` 不属合法四维标签，不再走四维解析；
#   生产者同步改出本渲染器体例。
_RENDER_STRENGTH_HEAD_RE = re.compile(
    r"^\*\*证据强度[:：]\s*(?:" + _RENDER_STRENGTH_TOKEN + r")\*\*(.*)$"
)
# 证据覆盖说明（实测语料全量形式，逐字精确表）：解释标签覆盖的数据范围
# （序列/字段/复核/差异），非语义信号词。新变体须显式登记。
_STRENGTH_COVERAGE_EXACT = frozenset({
    "估值/盈利序列为封存字段直读",
    "估值/盈利序列为封存字段直读；ROE 受跨源差异影响",
    "估值/盈利序列为封存字段直读；二季度单季为券商点评口径；ROE 受跨源差异影响",
    "估值、盈利、历史序列均为封存字段直读，可逐项复算",
    "价格/估值/盈利均可复核；机制归因为解释，非观测",
    "ROE 数值受跨源差异影响",
})


def _strength_paren_ok(inner: str) -> bool:
    return inner.strip() in _STRENGTH_COVERAGE_EXACT or _metadata_paren_ok(inner)


def _is_render_strength(line: str) -> bool:
    """`**证据强度：✅ 强**…` 行是否仅由元数据构成（结构行，单维渲染器体例）。

    标签后的实质文本（如 `**证据强度：✅ 强**公司盈利持续改善`）不豁免——
    返回 False 后该行按断言扫描（R2 反例）。可选 tail 仅接受
    「来源→时效→交叉」三维修全序列（R12 round-5：与 SOP 四维方括号同规则；
    半截序列不再豁免）。尾括注须为逐字覆盖说明或元数据语法——
    `（公司盈利持续改善，来源数据）` 两者皆非（R2 二轮反例）。
    """
    m = _RENDER_STRENGTH_HEAD_RE.match(line)
    if not m:
        return False
    rest = m.group(1).strip()
    if not rest:
        return True
    consumed = False
    # 可选 tail：来源→时效→交叉三维修全、固定顺序（head 已给强度维）；
    # 半截/乱序/图标注解矛盾的尾巴不豁免（R12 round-5）。
    seq = re.match(r"^\s*" + _SOURCE_DIM + r"\s*" + _TIMING_DIM + r"\s*" + _CROSS_DIM,
                   rest)
    if seq:
        rest = rest[seq.end():].strip()
        consumed = True
    paren = re.match(r"^[（(]([^（）()]{1,80})[）)]", rest)
    if paren and _strength_paren_ok(paren.group(1)):
        rest = rest[paren.end():].strip()
        consumed = True
    # 可选第二档：`／⚠️ 中（…）`（实测语料：双强度并列行）
    second = re.match(
        r"^[/／]\s*[⚠❓]️?\s*[中弱](?:\s*[（(]([^（）()]{1,80})[）)])?", rest)
    if second:
        inner = second.group(1)
        if inner is None or _strength_paren_ok(inner):
            rest = rest[second.end():].strip()
            consumed = True
    return consumed and not rest


# 四维标注行（R10，SKILL.md:148 格式）：`[证据强度: ✅ 强 🌐多源 🕐近 30 日 ✓✗]`
# ——注入 analysis 的 SOP-EV 标签行；封闭语法、无自由文本（括注一律不允许，
# 需要说明请写进正文并带来源）。R12 round-4 定义收敛到共享模块
# `evidence_tags.FOUR_DIM_TAG_LINE_RE`（与 analysis_schema 掩码同源，防漂移），
# 顶部以 `_FOUR_DIM_TAG_RE` 别名导入；round-5 收紧为四维齐全、固定顺序、
# 图标-注解配对（缺/重复/矛盾不豁免）；单维引擎元数据另走
# `_is_render_strength` 的 `**证据强度：…**` 体例。


# 加粗小标题的封闭语法（R2 二轮）：
#   主体 ∈ 封闭名词表（或登记正则）→ 允许「主体（元数据括注）」或「主体：」；
#   裸小标题（无括注/冒号）仅限实测语料封闭清单。
# `**公司盈利持续改善（近30日）**`（主体不在表）/`**行情（999%增长）**`
# （括注不合语法）→ 按断言扫描（R2 二轮反例）。
_BOLD_HEADING_ALLOWLIST = frozenset({
    "关键观察节点", "关键观察点", "盲点", "逻辑链", "明示缺口", "核心矛盾",
    "对抗性假设与证伪条件",
})
_HEADING_BASE_ALLOWLIST = frozenset({
    "本次问题与数据截止", "估值", "估值锚", "行情", "盈利", "事实", "行业位置",
    "明示缺口", "边界", "支持", "对抗性假设检验", "对抗性假设与证伪条件",
    "多情景参考价", "多情景估值参考", "交易结构", "口径", "读法", "判别式",
})
_HEADING_BASE_RES = (
    re.compile(r"截至\s*\d{4}-\d{2}-\d{2}\s*收盘的并列量值"),
)
_BOLD_ONLY_RE = re.compile(r"^\*\*([^*。！？!?]+)\*\*$")


def _is_bold_heading(line: str) -> bool:
    """整行加粗是否为结构小标题（封闭语法，R2 二轮）。

    合法形态：① 裸小标题 ∈ 封闭清单；② `主体（元数据括注）`（主体 ∈ 封闭
    名词表/登记正则，括注合元数据语法）；③ `主体：`（同主体约束）。
    其余（含 `**公司盈利持续改善…**` 全变体）按断言扫描。
    """
    m = _BOLD_ONLY_RE.match(line)
    if not m:
        return False
    body = m.group(1).strip()
    if body in _BOLD_HEADING_ALLOWLIST:
        return True
    core = body[:-1].strip() if body.endswith(("：", ":")) else body
    if not core:
        return False
    mm = re.fullmatch(r"([^（）()]{1,40})[（(]([^（）()]{1,100})[）)]", core)
    if mm:
        base, inner = mm.group(1).strip(), mm.group(2)
        if (base in _HEADING_BASE_ALLOWLIST
                or any(r.fullmatch(base) for r in _HEADING_BASE_RES)):
            return _metadata_paren_ok(inner)
        return False
    if body.endswith(("：", ":")):
        return (core in _HEADING_BASE_ALLOWLIST
                or any(r.fullmatch(core) for r in _HEADING_BASE_RES))
    return False


def _evidence_ge_c(ln: str, fact_ids: frozenset[str] | None = None) -> bool:
    """断言证据等级 ≥C（全量审查 P0-2：死代码「tagged==0 且无 out」不可达——
    tagged==0 时 out 必有内容。改为逐行判定：来源标注（可核验）或 [证据: A/B/C]；
    [证据: D] 为 <C）。
    C1-c：`[事实: F{n}]` 引用（analysis.json facts 契约）视为 ≥C 级可追溯证据。
    R2 二轮（2026-10-04）：提供 `fact_ids`（同代侧车 `.analysis.json` 的事实
    编号全集）时，**引用编号必须真实存在**——`[事实: F999999]` 不是证据
    （反例：不存在编号曾被当 ≥C 证据、整层 pass(0)）。未提供事实表（无侧车
    的单文件校验）时保持既有行为。
    R12 round-4（2026-10-04 独立复检）：**证据强度标签本身不是证据**——旧判据
    「任意含 `[证据强度: ✅` 的行即 ≥C」使 `[证据强度: ✅ 公司盈利增长999%]`
    自授通过结论证据层。强度标签只是元数据（合法的纯标签行在扫描前置已按
    结构行豁免），断言仍须 [来源:] / [证据: A-C] / 真实 [事实: F…] 绑定。
    且判定前先剥掉行内**整个** `[证据强度: …]` 命名空间（含非法/嵌套形态，
    如 `[证据强度: ✅ 强 [来源: 假] 断言]`）——标签内的来源/事实字样不是证据。"""
    evidence_scope = _strip_strength_tag_spans(ln)
    ref = re.search(r"\[事实\s*[:：]\s*(F\d+)\]", evidence_scope)
    if ref and (fact_ids is None or ref.group(1) in fact_ids):
        return True
    if re.search(r"\[来源\s*[:：]", evidence_scope):
        return True
    m = re.search(r"\[证据\s*[:：]\s*([A-Da-d])", evidence_scope)
    if m:
        return m.group(1).upper() in ("A", "B", "C")
    return False


def _body_lines(md: str) -> list[str]:
    """去掉命令/引用外的纯正文行（标题也算正文）。

    仅 stock full 的「审计底稿」折叠不计入主阅读篇幅。其他报告的 details
    （如 insight Facts、brief/concise 展开块）仍是报告正文，须计入指标。
    标签可在同一行开闭；未闭合的审计底稿视为延续到文末。

    注意本组指标是**软建议**（`_check_readability` 封顶 warn），故本改动是
    度量口径对齐（长度指标与阅读面设计同义），不是放宽阻断。
    """
    tag_re = re.compile(r"<\s*(/?)\s*details\b[^>]*>", re.IGNORECASE)
    stack: list[bool] = []
    visible: list[str] = []
    end = 0
    for tag in tag_re.finditer(md):
        if not any(stack):
            visible.append(md[end:tag.start()])
        was_hidden = any(stack)
        if tag.group(1):
            if stack:
                stack.pop()
        else:
            summary = md[tag.end():]
            stack.append(bool(re.match(r"\s*<summary>\s*审计底稿", summary, re.IGNORECASE)))
        if was_hidden and not any(stack):
            visible.append("\n")
        end = tag.end()
    if not any(stack):
        visible.append(md[end:])

    lines: list[str] = []
    for ln in "".join(visible).splitlines():
        ln = re.sub(r"</?summary\b[^>]*>", "", ln, flags=re.IGNORECASE)
        if ln.strip() and not ln.lstrip().startswith(("#", ">", "|", "```")):
            lines.append(ln)
    return lines


def readability_metrics(md: str) -> dict:
    """可读性指标组（全 Python 引擎计算）。"""
    body = "\n".join(_body_lines(md))
    total_chars = len(body)

    sentences = [s for s in _SENT_SPLIT_RE.split(body) if s.strip()]
    if not sentences:
        sentences = [body]
    long_ratio = sum(
        1 for s in sentences if len(s) > READABILITY_LONG_SENT_CHARS) / len(sentences)

    term_hits = 0
    for pat in _TERM_GLOSSARY:
        term_hits += len(re.findall(pat, body, re.IGNORECASE))
    term_density = round(term_hits / total_chars * 1000, 2) if total_chars else 0.0

    # 结论摘要要素：在「主要/核心结论」段内查找；无结论段标题 → 不判缺
    # （全量审查：旧实现无标题也报缺要素——对前置引擎输出假阳性）
    # C1-c 配套：标题族扩展后首个命中可能是 `重要发现（5 分钟阅读区）` 这类
    # **容器段**（段内只有引用行指针）——取其后的首个**有实质内容**的段，
    # 否则退回首段；全部无内容才按未知处理。
    summary_elements = {k: False for k in _SUMMARY_ELEMS}
    seg_pick: str | None = None
    m_any = None
    for m in _CONCLUSION_HEAD_RE.finditer(md):
        m_any = m
        tail = md[m.end():]
        next_head = re.search(r"^#{2,4}\s", tail, re.M)
        cand = tail if not next_head else tail[: next_head.start()]
        if seg_pick is None:
            seg_pick = cand
        if any(ln.strip() and not ln.strip().startswith(">")
               for ln in cand.splitlines()):
            seg_pick = cand
            break
    if m_any is None:
        summary_elements = {k: None for k in _SUMMARY_ELEMS}  # 无结论段 → 未知
    else:
        for k, pat in _SUMMARY_ELEMS.items():
            summary_elements[k] = bool(pat.search(seg_pick or ""))

    return {
        "total_chars": total_chars,
        "sentences": len(sentences),
        "long_sentence_ratio": round(long_ratio, 4),
        "term_density_permille": term_density,
        "summary_elements": summary_elements,
    }


# ── R14（2026-10-05）：按层级扫核心判断全部子段 + 表格断言检查 + 事件分析段 ──
#
# 反例（`三轮收敛_20261005/pre-review/review-probes.json` 与四份真实变异）：
# ① `## 重要发现（5 分钟阅读区）` 段内注入 `### 竞争解释` + `公司盈利持续改善`
#    ——旧实现遇任意 `^#{2,4}` 标题即停，子段成为扫描盲区，qc PASS；
# ② 同一断言包进 `| 判断 |` 两行表 —— 旧实现 `_STRUCT_LINE_RE` 豁免全部 `|`
#    行，qc PASS。
# 修复原则（不新增协议/入口）：
#  - 段边界改为「下一个同级或更高级标题」——子段（更深标题及其内容）继续扫；
#  - 表格只在「真表头/分隔行、**全数据格**数据行、行内有效证据绑定行」时豁免；
#    其余行按断言行要求 ≥C 级证据绑定（round-8 删除「短标签+数值」分支——
#    Codex round-7 复检反例 `| 公司盈利持续改善 | 999 |` 曾被该分支放行）；
#  - 登记结构表（事件时间线）表头按逐字常量识别、**数据行逐行结构核验**
#    （round-8 删除整块豁免——Codex 复制表头注入「确定影响」断言曾被整块放行）；
#  - 机器只检查来源与缺口纪律（是否绑定、绑定是否合法）与登记表的结构词表，
#    不声称理解表内文字的因果含义；标题格等自由文本属机器边界（见交接）。
_HEADING_ANY_RE = re.compile(r"^(#{1,6})\s", re.M)
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")
_PURE_NUM_CELL_RE = re.compile(
    r"^[+-]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|pp|PP|个百分点|亿元|万元|亿|万|元|美元|倍|[xX]|个|日|天|年|月|次|条|笔|家|只|股|点|bp|BP)?$")
_PURE_RANGE_CELL_RE = re.compile(
    r"^[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%|pp|亿元|万元|亿|万|元|倍)?"
    r"\s*[–~～-]\s*"
    r"[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%|pp|亿元|万元|亿|万|元|倍)?$")
_PURE_DATE_CELL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TABLE_PLACEHOLDER_CELLS = frozenset({"—", "–", "--", "-", "N/A", "n/a", "/", "…", "...", ""})


def _table_cells(row: str) -> list[str]:
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_table_separator(row: str) -> bool:
    cells = _table_cells(row)
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)


def _is_numeric_cell(cell: str) -> bool:
    return bool(_PURE_NUM_CELL_RE.match(cell) or _PURE_RANGE_CELL_RE.match(cell)
                or _PURE_DATE_CELL_RE.match(cell))


def _is_data_cell(cell: str) -> bool:
    """纯数据单元格：数值/日期/占位符。

    R14 round-7：**不再**把「含来源/证据/事实字样」的单元格当数据——来源
    字符串不能作为整行免审依据（Codex `table_fake_source`：嵌套在强度标签
    里的 `[来源: fake]` 曾使整行按数据行放行）。
    """
    return cell in _TABLE_PLACEHOLDER_CELLS or _is_numeric_cell(cell)


def _row_is_data_like(row: str) -> bool:
    """表数据行判据（R14 round-8）：**全部**单元格为纯数据（数值/日期/占位符）。

    round-7 的「短标签 + 数值/日期」分支已删除——Codex round-7 复检反例
    `| 公司盈利持续改善 | 999 |` 曾按数据行放行（preflight 仅因数字未绑定
    拦截、final QC 仍 WARN）；「短长度+数字」判据本身即漏洞面——8 字以内的
    短断言（如 `| 盈利改善 | 999 |`）与指标名在词形上不可区分。故整行免审
    只认「全数据格」这一种纯粹结构；任何含文字格的混合行一律按断言处理，
    其唯一豁免通路是**行内有效证据绑定**（`[来源:]` 依据合格 / `[证据: A–C]`
    / 真实 `[事实: F…]`）。真实数据表由全数据格（渲染器指标行，如业绩全景
    的纯数值行）或行内绑定（情景/底稿表）承载；登记结构表（事件时间线）
    另走逐行结构核验 `_event_timeline_row_ok`。
    """
    cells = [c for c in _table_cells(row) if c]
    return all(_is_data_cell(c) for c in cells)


# ── 表列错位（2026-10-10，A8）：与 etf_md 渲染契约同拆格语义 ───────────────

#: 渲染器契约（`skills/invest-a-etf/scripts/lib/etf_md.py`）：表行须**首尾都有 `|`**；
#: 表体行与表头列数不齐时渲染器静默「缺列留空 / 多列丢弃」——数据丢失无报错通道。
#: QC 在此拦截；拆格顺序（先剥首尾 `|`、再按非转义 `|` 切、`\|` 还原）必须与
#: etf_md._split_table_row 一致（等价性由 tests 的对拍用例锁定；跨包不 import：
#: report_qc 随全部技能包分发，不得依赖仅 ETF 包存在的模块）。
_MD_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


def _md_split_row(row: str) -> list[str]:
    """表格行拆格（`\\|` 转义安全）：与 etf_md._split_table_row 同语义。"""
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    sentinel = None
    if "\\|" in s:
        sentinel = "\x02"
        s = s.replace("\\|", sentinel)
    cells = s.split("|")
    if sentinel is not None:
        cells = [c.replace(sentinel, "|") for c in cells]
    return [c.strip() for c in cells]


def _md_tables(md: str) -> list[dict]:
    """识别渲染器契约下的表格块。

    返回 ``[{start: 表头行号(1-based), header: [cells], rows: [(line_no, cells)]}]``。
    成块条件与 etf_md 一致：表头行匹配 `|…|` 且下一行是 `|:--|--:|` 式分隔行；
    表体延续到首个不匹配 `|…|` 的行（空行/散落单行 `| a | b |` 不算表——
    测试夹具大量使用单行管道文本）。
    """
    lines = md.splitlines()
    tables: list[dict] = []
    i = 0
    n = len(lines)
    while i < n - 1:
        header_line = lines[i].strip()
        if _MD_TABLE_ROW_RE.match(header_line) and _MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            rows: list[tuple[int, list[str]]] = []
            j = i + 2
            while j < n and lines[j].strip() and _MD_TABLE_ROW_RE.match(lines[j].strip()):
                rows.append((j + 1, _md_split_row(lines[j])))
                j += 1
            tables.append({"start": i + 1, "header": _md_split_row(header_line), "rows": rows})
            i = j
        else:
            i += 1
    return tables


def table_alignment_findings(md: str, *, severity: str = "warning") -> list[dict]:
    """表列错位：表头/分隔行、表体行与表头列数不一致 → findings。

    含未转义 `|` 的单元格（如代码式 ``|涨跌|``）会被渲染器切成多格 →
    多列丢弃；缺列被留空。两者都是静默数字丢失，机器准出须拦截。
    """
    out: list[dict] = []
    for t in _md_tables(md):
        want = len(t["header"])
        sep_line_no = t["start"] + 1
        sep_cells = _md_split_row(md.splitlines()[sep_line_no - 1])
        if len(sep_cells) != want:
            out.append({
                "id": "table-header-misaligned", "severity": severity, "line": t["start"],
                "message": (f"表头列数（{want}）与分隔行列数（{len(sep_cells)}）不一致"
                            f"（表头行 {t['start']}）"),
            })
        for line_no, cells in t["rows"]:
            if len(cells) == want:
                continue
            out.append({
                "id": "table-row-misaligned", "severity": severity, "line": line_no,
                "message": (f"表格行列数（{len(cells)}）与表头（{want}，表头行 {t['start']}）不一致："
                            "渲染时缺列留空/多列丢弃（数字可能静默丢失）"),
            })
    return out


def _heading_level(match_text: str) -> int:
    return len(match_text) - len(match_text.lstrip("#"))


def _segment_after_heading(md: str, m: re.Match) -> tuple[list[str], int]:
    """标题所属段的行与起始行号——扫到**同级或更高级**标题为止（R14）。

    旧实现遇任意 `^#{2,4}` 即停：注入的 H3/H4 子段成为扫描盲区（反例①）。
    """
    level = _heading_level(m.group(0))
    seg_start = md.find("\n", m.end())
    seg_start = len(md) if seg_start == -1 else seg_start + 1
    tail = md[seg_start:]
    end = len(tail)
    for h in _HEADING_ANY_RE.finditer(tail):
        if len(h.group(1)) <= level:
            end = h.start()
            break
    line_base = md[:seg_start].count("\n") + 1
    return tail[:end].splitlines(), line_base


def _scan_segment_assertions(
    lines: list[str], line_base: int, fact_ids: frozenset[str] | None, *,
    where: str, id_missing: str, id_weak: str,
    id_missing_table: str, id_weak_table: str,
    extra_exempt_res: tuple[re.Pattern, ...] = (),
    source_basis_ok: "Callable[[str], bool] | None" = None,
    exempt_headers: tuple[tuple[str, ...], ...] = (),
    exempt_block_start: int | None = None,
) -> list[dict]:
    """段内逐行断言证据扫描（共享实现：结论段 / 事件分析段）。

    - 结构行/渲染器标签行/四维标签行/封闭加粗小标题/引用常量照旧豁免；
    - 表行改走 `_scan_table_rows`（真表头/分隔行、全数据格行、行内有效证据行
      豁免；登记结构表逐行结构核验；其余按断言；邻近来源不豁免——R14
      round-7/round-8）；
    - `source_basis_ok`（事件段 R13）：来源绑定即使语法合法、但其依据不合格
      （仅标题/URL、或自述未读/未取得）时同样按 <C 证据处理；
    - `exempt_block_start`（R14/MC-02）：登记表结构豁免只授予**该行号开始的
      块**——事件段传入引擎表块起点（人工分析区内的复制表不获豁免）；
    - `where` 用于消息（「结论段」/「事件分析段」），机器只查来源纪律。
    """
    out: list[dict] = []
    weak_lines: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if _TABLE_ROW_RE.match(stripped):
            j = i + 1
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j].strip()):
                j += 1
            _scan_table_rows(lines, i, j, line_base, fact_ids, out,
                             where=where, id_missing=id_missing_table,
                             id_weak=id_weak_table, source_basis_ok=source_basis_ok,
                             exempt_headers=exempt_headers,
                             exempt_block_start=exempt_block_start)
            i = j
            continue
        i += 1
        if (not stripped or _STRUCT_LINE_RE.match(stripped)
                or any(r.match(stripped) for r in extra_exempt_res)
                or _is_quote_structure(stripped)
                or _RENDER_LABEL_RE.match(stripped)
                or _RENDER_EVIDENCE_RE.match(stripped)
                or _FOUR_DIM_TAG_RE.match(stripped)
                or _is_render_strength(stripped)
                or _is_bold_heading(stripped)):
            continue
        if _EVIDENCE_TAG_RE.search(ln):
            basis_bad = source_basis_ok is not None and not source_basis_ok(ln)
            if basis_bad or not _evidence_ge_c(ln, fact_ids):
                weak_lines.append((line_base + i - 1, stripped[:80]))
        else:
            out.append({
                "id": id_missing,
                "severity": "error",
                "line": line_base + i - 1,
                "message": f"{where}断言缺少证据标签（[来源: / [证据: A–C / [事实: F…]；"
                           "证据强度标签仅元数据、不构成证据）"
                           "——无 ≥C 级证据的断言不得进入该段（R-A2/R13）；"
                           "若该行属渲染器固定注记（无标签属合法）：登记既有豁免表"
                           "后重发；若为实质断言：补证据标签。"
                           "豁免表为 fail-closed 逐字白名单（新变体不自动豁免）",
                "context": stripped[:80],
            })
    if weak_lines:
        lines_txt = "；".join(f"L{ln}: {ctx}" for ln, ctx in weak_lines[:3])
        out.append({
            "id": id_weak,
            "severity": "error",
            "line": weak_lines[0][0],
            "message": (f"{where}存在 <C 级证据断言（D 级/未标等级/来源依据不合格——"
                        "如仅标题、URL 或自述未读，不构成原文已核证据）——不满足"
                        "「无 ≥C 级证据不入该段」，标注「证据弱，仅作观察」"
                        f"（R-A2/R13）：{lines_txt}"),
            "context": lines[0][:80] if lines else "",
        })
    return out


def _scan_table_rows(
    lines: list[str], start: int, end: int, line_base: int,
    fact_ids: frozenset[str] | None, out: list[dict], *,
    where: str, id_missing: str, id_weak: str,
    source_basis_ok: "Callable[[str], bool] | None" = None,
    exempt_headers: tuple[tuple[str, ...], ...] = (),
    exempt_block_start: int | None = None,
) -> None:
    """表块行断言扫描（R14）：真表头/分隔行、数据行、登记结构表逐行核验豁免。

    R14 round-7：**取消「整表邻近来源」豁免**——邻近（含块外前后）的
    `[来源:]` 字符串不再给整表免审（Codex：来源字符串不能作整表/整行免审
    依据，且邻近来源会吞掉不相关断言）。非数据行一律按自身行内证据判定。
    R14 round-8：**登记结构表（`exempt_headers`）不再整块豁免**——表头行按
    逐字常量豁免，数据行须通过 `_registered_table_row_ok` 的逐行结构核验
    （Codex round-7 `event_copied_header`：复制事件表头插入「确定影响」断言
    曾被整块放行，validate/preflight=0 且 final QC WARN）。数据行判据同步
    收紧为「全数据格」或行内有效证据（见 `_row_is_data_like`）。
    R14（2026-10-07 主线收尾）：登记事件表的行级结构豁免还须通过
    `_registered_event_block_integrity_ok` 的**完整性与块位校验**（表块须为
    事件段首个引擎表块 [`exempt_block_start`]、紧跟固定来源尾注、行数与 N
    一致、行指纹自洽）——Codex round-8 `event_title_assertion`、2026-10-07
    footer 变体与 `r14-self-fingerprint-attack`（**[分析]** 区自算指纹的伪造
    表）在旧判据下 validate/preflight=0、final QC conclusion-evidence pass；
    契约不符的块退回普通断言行检查（fail-closed）。行指纹只作**完整性**
    校验（防行级篡改/外形复制），不证明来源身份；人工分析区不获该豁免。
    """
    block = [lines[i] for i in range(start, end)]
    header = tuple(_table_cells(block[0])) if block else ()
    header_registered = (header in exempt_headers
                         and exempt_block_start is not None
                         and start == exempt_block_start)
    if header_registered:
        header_registered = _registered_event_block_integrity_ok(
            lines, start, end, header)
    data_rows = [(i, lines[i]) for i in range(start, end)
                 if not _is_table_separator(lines[i])]
    if not data_rows:
        return
    # 首行只有紧随真实分隔行的多列表才可视为表头；无表头/单列块不能免审。
    # 仅表头+分隔的块没有数据行承载，表头也须检查
    # （登记结构表的表头行本身按逐字常量豁免——它是渲染器结构，不是断言）
    header_skippable = (len(data_rows) >= 2 and len(header) >= 2
                        and start + 1 < end and _is_table_separator(lines[start + 1]))
    for k, (i, row) in enumerate(data_rows):
        if k == 0 and (header_skippable or header_registered):
            continue
        if not any(_table_cells(row)):
            continue
        if _row_is_data_like(row):
            continue
        if header_registered and _registered_table_row_ok(row, header):
            continue
        if _EVIDENCE_TAG_RE.search(row):
            if ((source_basis_ok is not None and not source_basis_ok(row))
                    or not _evidence_ge_c(row, fact_ids)):
                out.append({
                    "id": id_weak,
                    "severity": "error",
                    "line": line_base + i,
                    "message": (f"{where}表格行存在 <C 级证据断言（含来源依据不合格——"
                                "如仅标题/URL/自述未读，或嵌套在强度标签内）——"
                                "表包装不能免除证据绑定（R14）"),
                    "context": row.strip()[:80],
                })
        else:
            out.append({
                "id": id_missing,
                "severity": "error",
                "line": line_base + i,
                "message": (f"{where}表格行缺少证据绑定——表格包装不能免除断言"
                            "证据要求；邻近来源与表头身份不构成整表/整行豁免（R14）"),
                "context": row.strip()[:80],
            })


def conclusion_evidence_findings(
    md: str, *, fact_ids: frozenset[str] | None = None,
) -> list[dict]:
    """R-A2：结论段逐条断言须带证据标签；<C 级证据的断言不得进入结论段。

    - 标题支持「核心结论」（真实模板 `## 核心结论`——旧 regex 只匹配主要/结论
      → 210/210 真实报告未检到结论段）
    - 死代码移除：旧「tagged==0 且 not out」不可达（tagged==0 → out 必有行）——
      改为逐行 _evidence_ge_c 判定，D 级/未达标行报 level error
    - 结构行（表行/---/#### 标题/```）排除；**`>` 引用行不再整体跳过**，
      仅逐字命中渲染器/规范常量的引用行豁免（`_is_quote_structure`）——
      引用包装不能吞断言（反例：`> 公司盈利持续改善`）
    - 段边界含 ####（乐观/悲观情景子标题内容不再误扫）
    - R2（#36）：标题**整行**跳过（`## 结论：…` 的后缀文字属标题、不是断言）；
      渲染器自出的纯结构标签行（`**[事实]**`/`**[分析]**`/`**证据等级：** {标签}`）
      不算断言——两者此前都被当缺标签断言（带后缀标题的卡必报 3-4 条 error）
    - C1-c（v0.3.1 收尾）：标题族扩展（主判断/可得结论/…，见 `_CONCLUSION_HEAD_RE`）
      且 `finditer` **扫全部**核心判断段（原实现只扫首个匹配）；新格式
      `[事实: F{n}]` 引用视为 ≥C 级证据；`**证据强度：…**` 渲染标签行与纯加粗
      小标题行豁免（600519 主判断段实测：`**证据强度：✅ 强**🌐…` 行旧实现会报 FP）。
    - R2 二轮（2026-10-04 独立复检）：签名/加粗/证据强度三类结构豁免改为**封闭语法**
      （`_is_render_strength` / `_is_bold_heading`）；四维标注行（R10）按封闭
      语法豁免（`_FOUR_DIM_TAG_RE`）；`fact_ids` 提供时（同代侧车事实表）
      `[事实: F…]` 必须引用真实存在的编号（F999999 不是证据）。
    - R12 round-4（2026-10-04 独立复检）：**强度标签不是证据**——`_evidence_ge_c`
      不再接受任意 `[证据强度: ✅` 行（该判据曾使 `[证据强度: ✅ 公司盈利增长999%]`
      通过本层）；只有纯四维元数据标签整行（`_FOUR_DIM_TAG_RE`，共享封闭语法）
      在结构豁免前置被跳过，其余行程的断言须有 [来源/证据/事实] 绑定。
    - R14（2026-10-05）：段边界改为**同级或更高级标题**（子段不再截断扫描，
      反例①`### 竞争解释` 注入不再成为盲区）；表行改由 `_scan_table_rows`
      判定（反例②表包装断言不再被 `_STRUCT_LINE_RE` 整体豁免）。
    - R14 round-8（2026-10-05）：数据行判据收紧为「全数据格」；登记结构表
      （事件时间线）改为**表头 + 逐行结构核验**（反例：短标签+数字行
      `| 公司盈利持续改善 | 999 |` 与复制事件表头注入）。
    """
    out: list[dict] = []
    for m in _CONCLUSION_HEAD_RE.finditer(md):
        lines, line_base = _segment_after_heading(md, m)
        out.extend(_scan_segment_assertions(
            lines, line_base, fact_ids, where="结论段",
            id_missing="wording-conclusion-evidence",
            id_weak="wording-conclusion-evidence-level",
            id_missing_table="wording-conclusion-table-evidence",
            id_weak_table="wording-conclusion-table-evidence-level",
            extra_exempt_res=_CONCLUSION_EXTRA_EXEMPT))
    return out


# 结论段补充结构豁免（封闭语法，R14）：模板卡片的裸标记行 `[事实]`/`[分析]`
# 是渲染器结构（内容在后续行；标签后跟正文的行仍按断言扫描）。
_BARE_MARKER_RE = re.compile(r"^\[(?:事实|分析)\]$")
_CONCLUSION_EXTRA_EXEMPT = (_BARE_MARKER_RE,)


# ── R13/R14（2026-10-05）：事件时间线段断言扫描 ──────────────────────────
# 反例：round-5 银行 final `## 3a. 事件时间线` 的 [分析] 段断言「治理变动
# 本身不改变盈利路径与估值锚点」而公告原文未读取——标题/类型分类线索不是
# 已核影响（AGENTS 约束 3；report-conventions §9.4.4）。机器只检查来源与
# 缺口纪律（断言行有无合法绑定、绑定编号是否存在），不判断文字中的因果。
_EVENT_SECTION_HEAD_RE = re.compile(r"^#{2,3}\s[^\n]*事件时间线", re.M)
# 渲染器结构行（封闭常量：分类摘要导语/占位槽），本身不含断言
_EVENT_FACT_LEAD_RE = re.compile(r"^\*\*\[事实\]\*\* 近 \d+ 日公告按类型归类如下：$")
_EVENT_PLACEHOLDER_RE = re.compile(
    r"^⏭️ \*\*(?:行业|市场)事件\*\*: ⏭️ 来源缺口：暂无稳定 API$")
_EVENT_EXTRA_EXEMPT = (_EVENT_FACT_LEAD_RE, _EVENT_PLACEHOLDER_RE)
# R13（2026-10-05 round-6 / 2026-10-05 round-7 收紧）：事件段内来源绑定须为
# 「原文档级/字段级可核依据」——**仅以标题/URL/链接为依据**，或**标签自述
# 未读/未核/未取得/不存在**（Codex `event_absent_source`：
# `治理变动不影响盈利与估值。[来源: 公告原文未读取]` 曾按有效证据放行）的
# 绑定，均不构成 ≥C 证据。否定词只在描述来源取得/核验状态时使绑定失效，内容字段缺失不等于来源未读。
_EVENT_SOURCE_TAG_RE = re.compile(r"\[来源\s*[:：]([^\[\]]*)\]")
_EVENT_TITLE_BASIS_RE = re.compile(r"标题|网址|[Uu][Rr][Ll]|链接")
_EVENT_ORIGINAL_BASIS_RE = re.compile(r"原文|正文|全文|披露文件")
# 缺失词必须描述来源取得/核验状态，不能把“缺失字段按空值处理”当未读来源。
_EVENT_SOURCE_UNAVAILABLE_RE = re.compile(
    r"(?:原文|正文|全文|披露文件|来源|材料)\s*[（(]?\s*(?:尚)?"
    r"(?:未(?:逐条)?(?:读|复核|核|取得|获取|提供|覆盖)|不在|不存在于|"
    r"(?:不存在|不可得|缺失)(?=$|[，,；;。)）]))"
    r"|(?:未(?:读(?:取)?|复核|核(?:验)?|取得|获取|提供)|不存在|不可得|缺失)\s*(?:的)?(?:公告)?(?:原文|正文|全文|披露文件|来源|材料)"
)
# 渲染器固定结构表（逐字表头元组）：事件时间线表由 `_section_events_timeline`
# 以封存数据渲染——表头按此常量识别表种；**数据行逐行结构核验**
# （R14 round-8：round-7 的整块豁免被复制表头注入绕过——Codex
# `event_copied_header`；词表与生产者 taxonomy 同步，一致性由测试对照 YAML 锁定）。
_EVENT_TIMELINE_HEADER: tuple[str, ...] = ("日期", "类型", "公告标题", "涉及维度（类型默认）")
_EVENT_EXEMPT_HEADERS: tuple[tuple[str, ...], ...] = (_EVENT_TIMELINE_HEADER,)
# 类型格词表：taxonomy `event_types.*.label` 全集（生产者 analysis_templates.
# event_type_label 的标签源）。未映射类型直出原始键 → 另放行英文键形态。
_EVENT_TYPE_LABELS = frozenset({
    "ST/退市风险", "业绩快报", "业绩预告", "关联交易", "其他公告", "减持", "分红",
    "回购", "增发", "增持", "定期报告", "对外投资", "对外担保", "并购重组",
    "异动/停复牌", "权益变动", "监管风险", "程序性公告", "股权激励", "诉讼仲裁",
    "质押冻结", "重大合同", "限售解禁", "首发/招股",
})
# 维度格词表：taxonomy `event_types.*.impact_dimension` 值集
_EVENT_DIMENSION_LABELS = frozenset({"估值", "收入", "治理", "现金流"})
# event_type_label 未命中 taxonomy 时直出原始类型键（生产封存数据为英文键）
_EVENT_RAW_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# 渲染器截断行（>15 条事件）：| ... | ... | （另有 N 条事件未展示） | ... | ... |
_EVENT_HIDDEN_ROW_RE = re.compile(r"^（另有 \d+ 条事件未展示）$")


def _event_timeline_row_ok(row: str) -> bool:
    """事件时间线数据行结构核验（R14 round-8，逐行；生产者同步）。

    生产者 `render_markdown._v3._section_events_timeline` 的行形态：
    `| {date} | {event_type_label(type)} | {title} | {dimension_hint|impact_dimension} |`
    （超 15 条另有 `| ... | ... | （另有 N 条事件未展示） | ... | ... |`）。
    核验：日期格 = YYYY-MM-DD；类型格 ∈ taxonomy 中文标签或原始英文键（未映射
    回落）；维度格 ∈ taxonomy 维度词表或空/占位；标题格非空。标题为封存原文，
    机器不判其文字语义（如实记录的残留边界）——但复制本表头、把「确定影响」
    断言写进类型/维度格的构造不再构成数据行（Codex round-7 `event_copied_header`）。
    """
    cells = _table_cells(row)
    if (len(cells) == 5 and cells[0] == cells[1] == cells[3] == cells[4] == "..."
            and _EVENT_HIDDEN_ROW_RE.match(cells[2])):
        return True
    if len(cells) != 4:
        return False
    date, etype, title, dimension = cells
    if not _PURE_DATE_CELL_RE.match(date):
        return False
    if etype not in _EVENT_TYPE_LABELS and not _EVENT_RAW_TYPE_RE.match(etype):
        return False
    if not title or title in _TABLE_PLACEHOLDER_CELLS:
        return False
    if (dimension and dimension not in _EVENT_DIMENSION_LABELS
            and dimension not in _TABLE_PLACEHOLDER_CELLS):
        return False
    return True


def _registered_table_row_ok(row: str, header: tuple[str, ...]) -> bool:
    """登记结构表的行级核验入口（当前仅事件时间线；新增表种时在此登记）。"""
    if header == _EVENT_TIMELINE_HEADER:
        return _event_timeline_row_ok(row)
    return False


# 生产者固定来源尾注（`_section_events_timeline` 在表块后逐字输出；
# N = 封存事件总数，行指纹 = 数据行规范串 sha256 前 32 hex；2026-10-07 主线
# 收尾起带指纹——旧产物无指纹形态：`[来源: akshare stock_individual_notice_report / N 条事件]`
_EVENT_TABLE_SOURCE_LINE_RE = re.compile(
    r"^\[来源:\s*akshare stock_individual_notice_report\s*/\s*(\d+)\s*条事件"
    r"(?:；行指纹 sha256:([0-9a-f]{32}))?\]$")


def _event_table_fingerprint(rows: list[list[str]]) -> str:
    """事件表数据行规范指纹（R14，2026-10-07 主线收尾）。

    与生产者 `lib.events.event_table_fingerprint` 同一规范：每行 cell 去首尾
    空白后以 ``|`` 连接、行间 ``\\n``、UTF-8 sha256 取前 32 hex。跨包各留一份
    实现（避免 QC 强依赖 stock 包），一致性由渲染器↔检查器耦合测试
    （`test_event_table_rows_satisfy_qc_structure`）锁定——两处任一漂移即红。
    """
    canon = "\n".join("|".join(str(c).strip() for c in row) for row in rows)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def _registered_event_block_integrity_ok(
    lines: list[str], start: int, end: int, header: tuple[str, ...],
) -> bool:
    """登记事件表的豁免须通过「完整性校验」（R14/MC-02，2026-10-07 主线收尾）。

    仅凭逐字表头 + 行结构核验，复制表头插入的「无来源影响断言」会被当数据行
    放行（Codex round-8 `event_title_assertion`）；仅凭固定尾注与条数的外形，
    把尾注一并复制仍可冒充（footer 变体）；把整块放进人工分析区并**用公开
    算法自算行指纹**同样自洽（`r14-self-fingerprint-attack`）——故本层只按
    **完整性**收紧（表块紧跟来源尾注、条数一致、行指纹与报告实际数据行重算
    值一致），**不宣称证明来源身份**。

    来源身份的完整关闭分布在消费链前端与块位规则：
    ① `analysis_schema` 入口拒绝自由 facts_md/analysis_md 伪造引擎事件表元数据
       （表头/公告来源尾注）——分析文本不再能把伪造表带进任何产物；
    ② `exempt_block_start`（见 `_scan_table_rows`）把豁免限定为事件段**首个**
       引擎表块——人工分析区（`**[分析]**` 之后）的复制表一律按普通断言逐行
       要求有效证据；
    ③ 本函数只作完整性 fail-closed（行级增删/1:1 替换/复制外形即失配）。

    机器边界（如实记录）：文本级检查无法证明标题来自封存源；重写整块并重算
    指纹属「连贯伪造报告」，需 QC 直读封存 events 的协议（范围外，未在本轮）。
    """
    if header != _EVENT_TIMELINE_HEADER:
        return False
    total = fp = None
    for ln in lines[end:]:
        s = ln.strip()
        if not s:
            continue
        m = _EVENT_TABLE_SOURCE_LINE_RE.match(s)
        if m is None:
            return False
        total, fp = int(m.group(1)), m.group(2)
        break
    if total is None or not fp:
        return False
    date_rows = hidden_rows = 0
    data_cells: list[list[str]] = []
    for row in lines[start:end]:
        if _is_table_separator(row):
            continue
        cells = _table_cells(row)
        if not cells:
            continue
        first = cells[0].strip()
        if first == "日期":
            continue  # 逐字表头行不参与指纹与计数
        data_cells.append(cells)
        if (len(cells) == 5 and first == "..."
                and _EVENT_HIDDEN_ROW_RE.match(cells[2].strip())):
            hidden_rows += 1
        elif _PURE_DATE_CELL_RE.match(first):
            date_rows += 1
        else:
            return False  # 非日期/截断数据行不属生产者契约
    if hidden_rows:
        # 渲染器截断形态：15 行数据 + 1 截断行，N 为封存总数（必然 >15）
        if not (hidden_rows == 1 and date_rows == 15 and total > 15):
            return False
    elif not (total <= 15 and date_rows == total):
        return False
    return _event_table_fingerprint(data_cells) == fp


def _event_source_basis_ok(line: str) -> bool:
    """事件段的来源绑定是否可作 ≥C 证据（R13 依据判据，round-7 收紧）。"""
    for m in _EVENT_SOURCE_TAG_RE.finditer(line):
        inner = m.group(1)
        if _EVENT_SOURCE_UNAVAILABLE_RE.search(inner):
            return False  # 标签自述未读/未取得/不存在：不构成证据
        if (_EVENT_TITLE_BASIS_RE.search(inner)
                and not _EVENT_ORIGINAL_BASIS_RE.search(inner)):
            return False  # 仅标题/URL 依据：不是原文档级证据
    return True


def event_analysis_evidence_findings(
    md: str, *, fact_ids: frozenset[str] | None = None,
) -> list[dict]:
    """事件时间线段逐行证据扫描（R13 配套，2026-10-05）。

    覆盖 `## 3a. 事件时间线`（v0.2 体例 `## 六、事件催化（事件时间线）` 同
    命中）整段（含更深子段）：断言须有 [来源/证据/事实] 绑定。分类列表
    子弹与表格数据行照常由其自身绑定/结构豁免；本层不判断「影响是否成立」，
    只要求「写了什么就绑定什么」——未读原文的影响判断在正文中应降级为
    「影响未知/不纳入经营结论」（§9.4.4 纪律）。
    """
    out: list[dict] = []
    for m in _EVENT_SECTION_HEAD_RE.finditer(md):
        lines, line_base = _segment_after_heading(md, m)
        out.extend(_scan_segment_assertions(
            lines, line_base, fact_ids, where="事件分析段",
            id_missing="wording-event-analysis-evidence",
            id_weak="wording-event-analysis-evidence-level",
            id_missing_table="wording-event-analysis-table-evidence",
            id_weak_table="wording-event-analysis-table-evidence-level",
            extra_exempt_res=_EVENT_EXTRA_EXEMPT,
            source_basis_ok=_event_source_basis_ok,
            exempt_headers=_EVENT_EXEMPT_HEADERS,
            exempt_block_start=_engine_event_block_start(lines)))
    return out


def _engine_event_block_start(lines: list[str]) -> int | None:
    """事件段内引擎事件表块的起始行号（R14/MC-02 块位规则）。

    渲染器契约：`## 3a. 事件时间线` 标题后**首个**逐字表头块即引擎表；其后
    的分类摘要与 `**[分析]**`（渲染器逐字输出）属于人工分析区——该区内即使
    出现复制表头 + 自洽尾注/行指纹，也不构成引擎表，不获结构豁免
    （Codex `r14-self-fingerprint-attack`：自由分析区自算指纹仍应逐行查证据）。
    分析区之前找不到逐字表头块 → None（无豁免，fail-closed）。
    """
    analysis_start = next(
        (idx for idx, ln in enumerate(lines) if ln.strip() == "**[分析]**"), None)
    for idx, ln in enumerate(lines):
        if tuple(_table_cells(ln.strip())) != _EVENT_TIMELINE_HEADER:
            continue
        if analysis_start is not None and idx > analysis_start:
            continue  # 人工分析区内的复制表头不是引擎表
        return idx
    return None


def fact_analysis_pair_findings(md: str) -> list[dict]:
    """R-A6：[分析] 节段内须有前置 [事实] 块（对偶强制）。

    与 lint `structure-analysis-without-fact` 同规则：50 行回溯、遇 `## ` 节段
    边界停止（跨节段的 [事实] 不满足本节的 [分析]）。边界判定先 strip 再匹配，
    与 lint `_previous_lines_window` 一致。
    """
    out: list[dict] = []
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if not _ANALYSIS_MARK_RE.search(ln):
            continue
        found = False
        for j in range(i - 1, max(i - 1 - _FACT_LOOKBACK_LINES, -1), -1):
            if _RA_SECTION_BOUNDARY_RE.match(lines[j].strip()):
                break
            if _FACT_MARK_RE.search(lines[j]):
                found = True
                break
        if not found:
            out.append({
                "id": "structure-fact-analysis-pair",
                "severity": "error",
                "line": i + 1,
                "message": f"[分析] 节段内缺少前置 [事实] 块（{_FACT_LOOKBACK_LINES} 行回溯）"
                           "——[事实]→[分析] 对偶强制（R-A6）",
                "context": ln.strip()[:80],
            })
    return out


def readability_findings(md: str) -> list[dict]:
    met = readability_metrics(md)
    out: list[dict] = []
    if met["total_chars"] > READABILITY_MAX_CHARS:
        out.append({"id": "readability-length", "severity": "error", "line": 0,
                    "message": f"正文篇幅 {met['total_chars']} 字符超限"
                               f"（>{READABILITY_MAX_CHARS}）"})
    if met["long_sentence_ratio"] > READABILITY_LONG_RATIO_WARN:
        out.append({"id": "readability-long-sentence", "severity": "warning", "line": 0,
                    "message": f"长句占比 {met['long_sentence_ratio']:.1%}"
                               f"（阈值 {READABILITY_LONG_RATIO_WARN:.0%}）"})
    missing = [k for k, v in met["summary_elements"].items() if v is False]
    if missing:
        # 全量审查：真实模板措辞（如「…分歧…若…则…」条件结构）已纳入词族——
        # 仍缺 1 项降 warning（可能为措辞风格而非结构缺失），缺 ≥2 项 error
        sev = "warning" if len(missing) == 1 else "error"
        out.append({"id": "readability-summary-elements", "severity": sev, "line": 0,
                    "message": f"主要结论段缺少要素：{('、'.join(missing))}"
                               "——要求'数据-逻辑-分歧-风险'四要素齐全"})
    return out


def _check_readability(text: str) -> LayerResult:
    """R-A1 可读性指标组（report-conventions §3.4 的载体）。

    **状态封顶 warn**：§3.4 明文「不触发 lint error，不作合规阻断」，
    故 error 级 finding 亦只置 warn——软建议不得成为交付阻断。
    """
    layer = LayerResult(layer="readability", status="pass")
    findings = readability_findings(text)
    layer.details = findings
    layer.findings_count = len(findings)
    if findings:
        layer.status = "warn"
    return layer


_SCENARIO_WORDS = ("乐观", "中性", "悲观")
_ASSUMPTION_RE = re.compile(r"(假设|前提|情景设定|测算依据|参数设定)")
_PROBABILITY_RE = re.compile(r"(概率|权重|可能性|概率权重)")


def law6a_scenario_findings(text: str) -> list[dict]:
    """LAW 6a：多情景估值参考价须附**假设前提** + **概率权重**。

    v0.3.0 全量重审 F-U7-5：LAW 6a 的实质要件此前在规则引擎中**零实现**——
    唯一机器机制只是全文级「不构成投资建议」存在性检查（warning、file scope），
    既不校验概率权重也不校验假设前提。而 report-conventions.md §2.1 明文规定：
    「多情景估值参考价须假设前提 + 概率权重 +『仅供参考，不构成投资建议』」
    「**不允许不标注假设前提的单一目标价数字**」。

    触发条件（保守，避免误伤普通叙述）：报告**同时**出现 乐观 + 中性 + 悲观
    三个情景词——这是「三情景估值」的形态标记；缺任一即不触发。
    通过条件：全文出现假设指示（假设/前提/情景设定…）**且**概率指示（概率/权重…）。
    """
    if not all(w in text for w in _SCENARIO_WORDS):
        return []

    missing: list[str] = []
    if not _ASSUMPTION_RE.search(text):
        missing.append("假设前提")
    if not _PROBABILITY_RE.search(text):
        missing.append("概率权重")
    if not missing:
        return []

    line = next(
        (i for i, ln in enumerate(text.splitlines(), 1) if "乐观" in ln),
        1,
    )
    return [{
        "id": "law6a-scenario-missing-context",
        "severity": "error",
        "line": line,
        "message": (
            f"多情景估值（乐观/中性/悲观）缺少「{'、'.join(missing)}」标注（LAW 6a）："
            "须标注各情景的假设前提与概率权重，并注明「仅供参考，不构成投资建议」"
        ),
    }]


def _check_law6a_scenarios(text: str) -> LayerResult:
    """LAW 6a 三情景上下文层：见 ``law6a_scenario_findings``。"""
    layer = LayerResult(layer="law6a-scenarios", status="pass")
    findings = law6a_scenario_findings(text)
    layer.details = findings
    layer.findings_count = len(findings)
    if any(d["severity"] == "error" for d in findings):
        layer.status = "fail"
    elif findings:
        layer.status = "warn"
    return layer


def _check_conclusion_evidence(
    text: str, report_type: str = "", *, fact_ids: frozenset[str] | None = None,
) -> LayerResult:
    """R-A2 结论段证据等级 + R-A6 [事实]→[分析] 对偶。

    二者是 error 级实质缺陷（R-A6 与 lint structure-analysis-without-fact
    同规则），故保留 error→fail 映射。
    C1-c：stock 报告若连结论/主判断段都定位不到，说明检查无扫描对象（如注入
    前快照）——追加 warning，不得静默 PASS（「找不到适用对象不能 PASS」；
    注入前快照属预期出现本提示）。
    R2 复审（2026-10-04）：原判据还要求「且无 [事实: F…] 引用」才告警——
    其他位置的 F 编号不能证明结论段已被扫描（反例：标题改为未识别的
    「研究摘要」但保留 [事实: F1]，检查层仍 pass(0)）。现只看标题族是否命中。
    R2 二轮：`fact_ids`（同代侧车事实编号全集）透传给逐行证据判定——
    不存在的事实编号按 <C 处理。
    R14（2026-10-05）：同一层内追加事件时间线段扫描（C1/A04 第三批验收：
    「事件分析R13相关无证据断言被检查」）——事件分析段与结论段同走
    `_scan_segment_assertions`（按层级全子段 + 表格行断言检查）。
    """
    layer = LayerResult(layer="conclusion-evidence", status="pass")
    findings = (conclusion_evidence_findings(text, fact_ids=fact_ids)
                + event_analysis_evidence_findings(text, fact_ids=fact_ids)
                + fact_analysis_pair_findings(text))
    if report_type == "stock" and not _CONCLUSION_HEAD_RE.search(text):
        findings.append({
            "id": "wording-conclusion-section-missing",
            "severity": "warning",
            "line": 0,
            "message": ("未定位到结论/主判断段——R-A2 检查无扫描对象"
                        "（其他位置的 [事实: F…] 引用不构成扫描对象），不得视为通过"
                        "（注入前快照预期出现本提示）"),
        })
    layer.details = findings
    layer.findings_count = len(findings)
    if any(d["severity"] == "error" for d in findings):
        layer.status = "fail"
    elif findings:
        layer.status = "warn"
    return layer


# ── 主检查流程 ────────────────────────────────────────────────────────────


def _load_analysis_fact_ids(path: Path) -> frozenset[str] | None:
    """读同代侧车 `.analysis.json` 的事实编号全集（R2 二轮）。

    侧车命名与 `invest.py::_write_analysis_sidecar` 同代约定：
    `<报告>.final.md` → `<报告>.final.analysis.json`（with_suffix 替换 `.md`）。
    缺失/损坏/空表 → None（保持既有「无事实表」行为，不阻断）。
    说明：编号在 analysis 协议里是**段内**作用域，本处取全集用于存在性检查
    （能拦 F999999；「引用了别的段的编号」属更细粒度问题，另案）。
    """
    sidecar = path.with_suffix(".analysis.json")
    try:
        if not sidecar.exists():
            return None
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return None
        ids = {
            str(f.get("id"))
            for section in payload if isinstance(section, dict)
            for f in (section.get("facts") or []) if isinstance(f, dict) and f.get("id")
        }
        return frozenset(ids) if ids else None
    except Exception:
        return None


_INVEST_LIB_CACHE = None  # importlib 加载的 _invest_lib 包（惰性）


def _load_invest_lib():
    """将 invest-a-stock/scripts/lib 整体加载为 ``_invest_lib`` 别名包。

    不用 ``from lib import ...`` —— 当 skills/lib 被 pytest 作为包导入时，
    ``lib`` 名称会解析到 skills/lib，导致模块错位。别名包方案同时支持
    子模块间的相对导入（``from .industry import ...``）。
    """
    global _INVEST_LIB_CACHE
    if _INVEST_LIB_CACHE is not None:
        return _INVEST_LIB_CACHE
    scripts = ensure_invest_a_scripts_on_path()
    lib_dir = scripts / "lib"
    init_path = lib_dir / "__init__.py"
    has_initializer = init_path.is_file()
    if not lib_dir.is_dir():
        raise RuntimeError(f"无法加载 lib 包: {lib_dir}")
    if has_initializer:
        spec = importlib.util.spec_from_file_location(
            "_invest_lib", init_path, submodule_search_locations=[str(lib_dir)]
        )
    else:
        # 独立分发包的 lib 为标准 namespace 包；别名仍仅指向本次解析的目录。
        spec = importlib.util.spec_from_loader("_invest_lib", loader=None, is_package=True)
        if spec is not None:
            spec.submodule_search_locations = [str(lib_dir)]
    if spec is None or (has_initializer and spec.loader is None):  # pragma: no cover
        raise RuntimeError(f"无法加载 lib 包: {lib_dir}")
    mod = importlib.util.module_from_spec(spec)
    # 必须先把模块注册进 sys.modules，否则模块内 @dataclass / 相对导入
    # 会因查不到模块而失败（AttributeError: 'NoneType'）
    sys.modules[mod.__name__] = mod
    try:
        if has_initializer:
            spec.loader.exec_module(mod)
    except Exception:
        # v0.3.0 D4（D11 同族清扫）：exec 失败时清掉残破别名包，避免其它按
        # `_invest_lib.X` 取数的路径拿到半初始化模块（本函数自身下次调用会
        # 重新注册，故此前不会永久缓存；清理使失败面收敛到本函数）。
        sys.modules.pop(mod.__name__, None)
        raise
    _INVEST_LIB_CACHE = mod
    return mod


def _load_lint_module():
    """返回 _invest_lib 包下的 lint 模块。"""
    _load_invest_lib()
    return importlib.import_module("_invest_lib.lint")


# severity 排序：fail_on 阈值比较用（error=2 > warning=1 > info=0）
_SEVERITY_RANK = {"error": 2, "warning": 1, "info": 0}


def _run_lint_layer(report_path: Path, profile: str, fail_on: str = "warning") -> LayerResult:
    """lint 层：复用 invest-a-stock lib/lint.py（lazy import 保持模块可独立导入）。"""
    layer = LayerResult(layer="lint", status="pass")
    try:
        lint_mod = _load_lint_module()
    except Exception as exc:  # pragma: no cover — 依赖环境问题
        # 不可静默 skip：skip 会被 _compute_overall 过滤成假 PASS，掩盖环境故障
        layer.status = "warn"
        layer.details.append({"id": "lint-unavailable", "severity": "info",
                              "message": f"lint 模块不可用: {exc}"})
        return layer

    try:
        findings = lint_mod.lint_file(report_path, profile=profile)
    except lint_mod.RulesLoadError as exc:
        layer.status = "warn"
        layer.details.append({"id": "lint-rules-unavailable", "severity": "info",
                              "message": f"合规规则无法加载: {exc}"})
        return layer

    layer.findings_count = len(findings)
    layer.details = [
        {
            "id": f.rule_id,
            "severity": f.severity,
            "line": f.line,
            "message": f.message,
        }
        for f in findings
    ]
    threshold = _SEVERITY_RANK.get(fail_on, 1)
    if any(_SEVERITY_RANK.get(f.severity, 2) >= threshold for f in findings):
        layer.status = "fail"
    elif any(_SEVERITY_RANK.get(f.severity, 2) >= 1 for f in findings):
        # 低于 fail_on 阈值但仍有 error/warning 级发现（如 --fail-on error 时的 warning）
        layer.status = "warn"
    # info 级发现仅记录在 details，不翻转层状态（假红防护）
    return layer


def _load_stock_module(module_name: str):
    """返回 _invest_lib 包下的 stock lib 模块（见 _load_invest_lib）。"""
    _load_invest_lib()
    return importlib.import_module(f"_invest_lib.{module_name}")


# ══════════════════════════════════════════════════════════════════════════
# QC 记录侧车（B06）：产物哈希绑定 + 分层签认分离
#
# 负责人定点补充（2026-10-08）：旧 verify 只比文件名/payload 哈希/HTML 内嵌
# 哈希/decision.report_ts，不绑定 MD/HTML/decision **实际内容**——改 MD 一行
# 仍可沿用旧的 manual PASS，保留 meta 哈希改 HTML 数字仍自称同代。本机制用
# **最小侧车**（同目录 `<ts>.qc.json`）记录这一代实际产物的 sha256 与本次
# 机器准出结论，并把「数据/原文/人工三层」签认分别记录（默认 not_executed，
# 机器 PASS 不得代签）。
#
# 边界：不造第二套 ReportIR / 终稿状态机；侧车只存哈希与签认记录，**不含
# 自身哈希**（无自引用哈希环）；旧记录在产物变更后即判 stale（重验入口 =
# 重跑 `report_qc.py <md> --record`）。
# ══════════════════════════════════════════════════════════════════════════

QC_RECORD_SUFFIX = ".qc.json"
QC_RECORD_VERSION = 1
QC_RECORD_ARTIFACTS = ("md", "html", "payload", "decision")

#: 签认层词表：数据复核 / 原文核验 / 人工三层（数字·合规·逻辑）各自记录。
QC_SIGNOFF_LAYERS = ("data_review", "original_materials",
                     "manual_numbers", "manual_compliance", "manual_logic")
QC_SIGNOFF_STATES = ("not_executed", "pass", "fail", "partial")


def sha256_file(path: str | Path) -> str | None:
    """文件 sha256（不存在返回 None）。"""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def qc_record_path(report_path: str | Path) -> Path:
    """QC 记录侧车路径（与报告 md 同目录同 ts）。"""
    p = Path(report_path)
    return p.parent / f"{p.stem}{QC_RECORD_SUFFIX}"


def generation_artifacts(report_path: str | Path) -> dict[str, dict | None]:
    """同一代产物（md/html/payload/decision）的 {sha256,bytes}；缺 = None。"""
    p = Path(report_path)
    d, ts = p.parent, p.stem
    out: dict[str, dict | None] = {}
    for name, path in (
        ("md", p),
        ("html", d / f"{ts}.html"),
        ("payload", d / f"{ts}.payload.json"),
        ("decision", d / f"{ts}.decision.json"),
    ):
        h = sha256_file(path)
        out[name] = ({"sha256": h, "bytes": Path(path).stat().st_size}
                     if h is not None else None)
    return out


def _normalize_signoffs(raw: dict | None) -> dict[str, dict]:
    """规范化签认记录：未知层/非法状态拒绝（fail-loud），默认 not_executed。"""
    out: dict[str, dict] = {
        layer: {"status": "not_executed", "note": None, "recorded_at": None}
        for layer in QC_SIGNOFF_LAYERS
    }
    for layer, val in (raw or {}).items():
        if layer not in QC_SIGNOFF_LAYERS:
            raise ValueError(f"未知签认层 {layer!r}（可用: {', '.join(QC_SIGNOFF_LAYERS)}）")
        if isinstance(val, str):
            val = {"status": val}
        status = (val or {}).get("status", "not_executed")
        if status not in QC_SIGNOFF_STATES:
            raise ValueError(f"签认状态非法 {status!r}（可用: {', '.join(QC_SIGNOFF_STATES)}）")
        out[layer] = {
            "status": status,
            "note": (val or {}).get("note"),
            "recorded_at": (val or {}).get("recorded_at"),
        }
    return out


def build_qc_record(report_path: str | Path, result: QCResult, *,
                    signoffs: dict | None = None,
                    previous: dict | None = None) -> dict:
    """构建 QC 记录（不落盘）：产物哈希 + 本次机器结论 + 分层签认。

    ``previous`` = 覆盖前的旧记录核验结果（verify_qc_record）：存在且非 none
    时写入 ``supersedes``——**旧签认不继承**（新记录 signoffs 只取本次入参），
    同时留下「本次记录替代了什么状态」的可审计痕迹。
    """
    p = Path(report_path)
    rec = {
        "schema_version": QC_RECORD_VERSION,
        "record": p.name,
        "ts": p.stem,
        "report": p.name,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "artifacts": generation_artifacts(p),
        "payload_hash": None,
        "machine_qc": {
            "overall": result.overall,
            "report_type": result.report_type,
            "network_used": bool(result.network_used),
            "layers": [{"layer": l.layer, "status": l.status} for l in result.layers],
        },
        "signoffs": _normalize_signoffs(signoffs),
        "note": "机器准出与人工/原文/数据签认分别记录；机器 PASS 不代替人工复检。"
                "本记录不含自身哈希（避免自引用环）；任一产物变更即失效，重验入口："
                f"report_qc.py <md> --record（本记录 {p.stem}{QC_RECORD_SUFFIX} 可覆盖重写）。",
    }
    if previous and previous.get("status") not in (None, "none"):
        rec["supersedes"] = {
            "status": previous.get("status"),
            "recorded_at": previous.get("recorded_at"),
            "machine_overall": previous.get("machine_overall"),
            "note": "旧记录签认不继承（沿用旧人工 PASS 的通道不存在）；"
                    "本次签认见 signoffs（未显式 --signoff 即为未执行）",
        }
    payload_path = p.parent / f"{p.stem}.payload.json"
    if payload_path.is_file():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            meta = payload.get("_meta") if isinstance(payload, dict) else None
            rec["payload_hash"] = meta.get("payload_hash") if isinstance(meta, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            rec["payload_hash"] = None
    return rec


def write_qc_record(report_path: str | Path, result: QCResult, *,
                    signoffs: dict | None = None) -> tuple[Path, dict]:
    """落盘 QC 记录侧车；返回 (路径, 记录)。

    写前先核验旧记录（覆盖即替代）：旧状态与旧签认写入 ``supersedes``，
    但**新记录的 signoffs 只取本次入参**——陈旧产物重跑 --record 不会
    携带旧人工 PASS；机器 FAIL 也照实记录（不作伪 PASS）。
    """
    previous = verify_qc_record(report_path)
    rec = build_qc_record(report_path, result, signoffs=signoffs, previous=previous)
    path = qc_record_path(report_path)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path, rec


def load_qc_record(report_path: str | Path) -> tuple[dict | None, list[str]]:
    """读取 QC 记录侧车（不存在/损坏 → (None, [原因])）。"""
    path = qc_record_path(report_path)
    if not path.exists():
        return None, [f"无 QC 记录侧车: {path.name}"]
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"QC 记录读取/解析失败: {exc}"]
    if not isinstance(rec, dict):
        return None, ["QC 记录不是 JSON 对象"]
    if rec.get("ts") != Path(report_path).stem:
        return None, [f"QC 记录 ts={rec.get('ts')!r} 与报告 ts 不符（张冠李戴）"]
    errors = _qc_record_structure_errors(rec, Path(report_path))
    if errors:
        return None, errors
    return rec, []


def _qc_record_structure_errors(rec: dict, report_path: Path) -> list[str]:
    """先验证记录合同，禁止缺项伪装 valid 或损坏字段触发消费方异常。"""
    errors: list[str] = []
    if type(rec.get("schema_version")) is not int or rec["schema_version"] != QC_RECORD_VERSION:
        errors.append("QC 记录 schema_version 缺失或不支持")
    for key in ("report", "record"):
        if rec.get(key) != report_path.name:
            errors.append(f"QC 记录 {key} 与报告文件名不符")
    if not isinstance(rec.get("recorded_at"), str) or not rec["recorded_at"].strip():
        errors.append("QC 记录缺 recorded_at")
    artifacts = rec.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("QC 记录缺 artifacts 对象")
    else:
        for name in QC_RECORD_ARTIFACTS:
            if name not in artifacts:
                errors.append(f"QC 记录缺 artifacts.{name}")
                continue
            item = artifacts[name]
            if item is None:
                continue
            if (not isinstance(item, dict)
                    or not isinstance(item.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                    or type(item.get("bytes")) is not int or item["bytes"] < 0):
                errors.append(f"QC 记录 artifacts.{name} 非法（须 sha256/bytes）")
    machine = rec.get("machine_qc")
    if not isinstance(machine, dict):
        errors.append("QC 记录缺 machine_qc 对象")
    else:
        if machine.get("overall") not in ("PASS", "WARN", "FAIL"):
            errors.append("QC 记录 machine_qc.overall 非法")
        if not isinstance(machine.get("report_type"), str) or not machine["report_type"]:
            errors.append("QC 记录 machine_qc.report_type 非法")
        if type(machine.get("network_used")) is not bool:
            errors.append("QC 记录 machine_qc.network_used 非法")
        layers = machine.get("layers")
        if not isinstance(layers, list) or any(
                not isinstance(layer, dict) or not isinstance(layer.get("layer"), str)
                or layer.get("status") not in ("pass", "warn", "fail", "skip")
                for layer in layers):
            errors.append("QC 记录 machine_qc.layers 非法")
    signoffs = rec.get("signoffs")
    if not isinstance(signoffs, dict):
        errors.append("QC 记录缺 signoffs 对象")
    else:
        if set(signoffs) != set(QC_SIGNOFF_LAYERS):
            errors.append("QC 记录 signoffs 层缺失或未知")
        for name, item in signoffs.items():
            if not isinstance(item, dict) or item.get("status") not in QC_SIGNOFF_STATES:
                errors.append(f"QC 记录 signoffs.{name} 状态非法")
    payload_hash = rec.get("payload_hash")
    if payload_hash is not None and (not isinstance(payload_hash, str)
                                    or re.fullmatch(r"[0-9a-f]{64}", payload_hash) is None):
        errors.append("QC 记录 payload_hash 非法")
    return errors


def verify_qc_record(report_path: str | Path) -> dict:
    """核验 QC 记录与当前产物的绑定状态。

    返回 ``{"status": "none|valid|stale|invalid", "issues", "changed",
    "recorded_at", "machine_overall", "signoffs"}``：
    - ``none``    无记录（未记录 → 未执行，不等于 PASS）；
    - ``valid``   各产物哈希与记录一致（机器结论与签认见字段，整体通过与否另判）；
    - ``stale``   任一产物变更/新增/丢失 或 payload_hash 不符 → 旧记录失效，须重验；
    - ``invalid`` 记录损坏/ts 不符/结构非法。
    """
    p = Path(report_path)
    out: dict = {"status": "none", "issues": [], "changed": [],
                 "recorded_at": None, "machine_overall": None, "signoffs": {}}
    rec, errs = load_qc_record(p)
    if rec is None:
        if qc_record_path(p).exists():
            out["status"] = "invalid"
        out["issues"].extend(errs)
        return out
    recorded = rec.get("artifacts")
    if not isinstance(recorded, dict):
        out["status"] = "invalid"
        out["issues"].append("QC 记录缺 artifacts 块")
        return out
    current = generation_artifacts(p)
    changed: list[str] = []
    for name in QC_RECORD_ARTIFACTS:
        r, c = recorded.get(name), current.get(name)
        if r is None and c is None:
            continue
        if r is None and c is not None:
            changed.append(f"{name}:记录后新增（记录未覆盖）")
        elif r is not None and c is None:
            changed.append(f"{name}:丢失（记录时存在）")
        elif r.get("sha256") != (c or {}).get("sha256"):
            changed.append(f"{name}:内容已变更（sha256 不符）")
    rec_payload_hash = rec.get("payload_hash")
    if rec_payload_hash and not any(c.startswith("payload:") for c in changed):
        payload_path = p.parent / f"{p.stem}.payload.json"
        cur_hash = None
        if payload_path.is_file():
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                meta = payload.get("_meta") if isinstance(payload, dict) else None
                cur_hash = meta.get("payload_hash") if isinstance(meta, dict) else None
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                cur_hash = None
        if cur_hash != rec_payload_hash:
            changed.append("payload_hash:与记录不符（封存内容变更）")
    out["recorded_at"] = rec.get("recorded_at")
    out["machine_overall"] = (rec.get("machine_qc") or {}).get("overall")
    out["signoffs"] = rec.get("signoffs") or {}
    out["changed"] = changed
    if changed:
        out["status"] = "stale"
        out["issues"].append(
            "旧签认失效：产物在记录后变更 → " + "；".join(changed)
            + "（重验入口：report_qc.py <md> --record）")
    else:
        out["status"] = "valid"
    return out


# ── ETF 域 QC 层（B06）：同代 payload/产物哈希 + 报告断言 × payload 对照 ──

#: 确认性跟踪指标断言（TD/TE 数字出现即需 tracking.status=available）
_ETF_TE_CLAIM_RE = re.compile(
    r"(?:跟踪误差|跟踪差异|(?<![A-Za-z])(?:TD|TE)(?![A-Za-z]))"
    r"[^。；;\n]{0,24}?(-?\d+(?:\.\d+)?)\s*%"
    r"|(-?\d+(?:\.\d+)?)\s*%\s*(?:的)?"
    r"(?:跟踪误差|跟踪差异|(?<![A-Za-z])(?:TD|TE)(?![A-Za-z]))", re.I)
#: 断言豁免 = **同命题否证**（R3）：否证措辞必须直接贴附该族断言的**对象/谓词**
#: （跟踪误差/身份核验/折溢价/缺失权重/拼当期组合），其他维度的缺口或待核不
#: 构成豁免（负责人反例：「身份已核验且宏观背景待核」「跟踪误差为0.20%而盘口
#: 有缺口」——「宏观背景/盘口」的待核/缺口不是该命题的否定）。
#: 贴附距离是有限规则（不冒充自然语言万能核验）：已知硬约束反例必须被拦截、
#: 真实否定（「跟踪误差不可得」「未做原文核验」「缺权重不得按 0」）必须被保护；
#: 五族正反例见 tests/test_b06_qc_record.py 与 R3 负责人断言反例。
_ETF_MISPRICE_CLAIM_RE = re.compile(
    r"(?:折溢价?|溢价|折价)[^。；;\n]{0,30}?(?:错价|定价错误|被高估|被低估|低估|高估)"
    r"|(?:错价|被高估|被低估|定价错误)[^。；;\n]{0,30}?(?:折溢价|溢价|折价)")
_ETF_ZERO_WEIGHT_RE = re.compile(
    r"缺(?:失|口)?[^。；;\n]{0,12}?(?:按|计为|视为|当作)\s*0"
    r"|前十大合计[^。；;\n]{0,12}?100(?:\.0+)?\s*%")
_ETF_STALE_MIX_CLAIM_RE = re.compile(
    r"(?:当期|当前)[^。；;\n]{0,12}?(?:完整组合|完整持仓|全部成分)"
    r"|成分(?:与|和)权重[^。；;\n]{0,10}?拼")
#: 身份核验完成声明（R6，2026-10-11 复查）：只收**完成态**断言。
#: 旧式 `(?:原文核验|已读原文)` 会把边界/任务名词「原文核验边界」
#: （159796 缺口表行，仅描述待核范围）误判为完成声明 → FAIL。完成态限定为
#: 「身份已核/核验通过」「原文核验通过/已完成/已核/完毕」「已完成原文核验」
#: 「已读原文」；任务式/名称式（完成原文核验、原文核验材料/边界）不检，
#: 未知留人工。同分句否证（未做/未核验）豁免机制不变。
_ETF_IDENTITY_VERIFIED_RE = re.compile(
    r"身份[^。；;\n|]{0,12}?(?:已核|核验通过|已验证|verified)"
    r"|原文核验(?:通过(?!率)|已完成|已通过|已核|完毕|完成(?!度))"
    r"|已完成原文核验"
    r"|已读原文")
_HTML_GENERATION_META_RE = re.compile(
    r'name="invest-generation"\s+content="([0-9a-f]{8,})"')


#: 分句边界（句读/换行/表格格）：否证检索限定在断言所在分句内，防跨句保护。
#: 括号允许出现在否证的**贴附间隙**里（「身份（待核）」），故不作边界；
#: 转折连词（但/然而）也不作边界——是否豁免由「否证对象是否就是断言对象」
#: 判定（R3），不再靠连接词枚举。
_CLAUSE_BOUNDARY_RE = re.compile(r"[。！？!?；;，,、\n|]")
_TRACKING_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]")
_TRACKING_RESEARCH_RE = re.compile(
    r"研究偏离|研究数值|研究口径|research_only\s*=\s*true"
    r"|not_confirmatory\s*=\s*true|非确认", re.I)

#: 每族断言的**同命题否证**（对象/谓词贴附，间隙上限几字符）：
#: - ``tracking``：跟踪误差/差异/TD/TE ↔ 不可得/不可算/未核/停算/不构成…；
#: - ``mispricing``：折溢价 ↔ 不意味/不等同/不构成…低估/高估（双向）；
#: - ``zero_weight``：缺失/权重 ↔ 不按/不得按/不重归/不视为…；
#: - ``stale_mix``：拼/当作 ↔ 不可/不得/不能…；
#: - ``identity``：身份/原文核验 ↔ 待核/未核/未做/未经…
#: 「缺口/待核」不再是通用豁免词：只有贴附在**本族对象**上才算否证。
_ETF_SAME_OBJECT_EXEMPT_RE: dict[str, re.Pattern] = {
    "tracking": re.compile(
        r"(?:跟踪(?:误差|差异|偏离)|TD|TE)[^。；;\n]{0,8}?"
        r"(?:不可得|不可算|不可确认|未确认|非确认|未核|未做|未经|停算|停止|不适用"
        r"|不构成|不等于|不意味|未必|无法计算|无法确认|不能计算|不能确认)"
        r"|(?:不可得|不可算|不可确认|未确认|非确认|未核|未做|未经|停算|停止|不适用"
        r"|不构成|不等于|不意味|无法计算|无法确认|不能计算|不能确认)"
        r"[^。；;\n]{0,6}?(?:跟踪(?:误差|差异|偏离)|TD|TE)", re.I),
    "mispricing": re.compile(
        r"(?:折溢价?|折价)[^。；;\n]{0,16}?"
        r"(?:不意味|不等同|不等于|不构成|不自动|未必|不能|不得|无法|不)"
        r"[^。；;\n]{0,16}?(?:错价|定价错误|被高估|被低估|低估|高估)"
        r"|(?:不意味|不等同|不等于|不构成|不自动|未必|不能|不得|无法)"
        r"[^。；;\n]{0,8}?(?:错价|定价错误|被高估|被低估|低估|高估|折溢价?|折价)"),
    "zero_weight": re.compile(
        r"(?:缺(?:失|口)?|权重|前十大)[^。；;\n]{0,10}?"
        r"(?:不按|不得按|不重归|不视为|不计为|不当(?:作)?|不当作|不可按|不能按"
        r"|不得|不可|不能)"
        r"|(?:不得|不可|不能|不)[^。；;\n]{0,4}?按\s*0"),
    "stale_mix": re.compile(
        r"(?:不可|不得|不能|勿|禁止|不应|无法)[^。；;\n]{0,8}?"
        r"(?:拼|当作|视为|等同|混(?:为|作))"),
    "identity": re.compile(
        r"(?:身份|原文核验|已读原文)[^。；;\n]{0,6}?"
        r"(?:待核|未核|未验证|未做|未经|未执行|未确认|不适用|未完成|未核验|未读)"
        r"|(?:未做|未经|未执行|未完成|尚未|有待|待|未)"
        r"[^。；;\n]{0,6}?(?:身份|原文核验|已读原文)"),
    # valuation（2026-10-10）：估值低位断言 ↔ 同分句否证/排除（不采纳/不构成
    # 结论/口径不一/不作判定）——真实否定（「不判定低位」「口径不一不作结论」）
    # 不得误伤；其他维度（如盘口/份额）的否定不构成豁免（同 R3 对象贴附纪律）。
    "valuation": re.compile(
        r"(?:低位|偏低|极低|低估|便宜|低估值)[^。；;\n|]{0,10}?"
        r"(?:不构成|不采纳|不作为|不进入|未采纳|不成立|存疑|口径不一|未核|不判定|不可判)"
        r"|(?:不构成|不采纳|不作为|不进入|未采纳|不判定|不认定|无法判定|不可判)"
        r"[^。；;\n|]{0,10}?(?:低位|偏低|极低|低估|便宜|低估值|水平判定)"),
    # extreme_window（2026-10-10，R6 复查）：组内极值断言的**同分句**限制/否定。
    # 三种合法形态（均在断言所在分句内）：
    #   ① 对象在前、否定在后：「组内最小不作判断」（R6 原防漏）；
    #   ② 否定在前、对象在后：「不判断…组内最小」「不作…排名结论」（2026-10-10
    #      正控误伤修复——root-qc-probes-final unmatched_no_rank_valid）；
    #   ③ 「不可比/窗口起止不一致/实际覆盖不同」+ 否定或限定。
    # 分句作用域由 _claim_exempt_near 保证：「不能判断A；本标的组内最小」的另一个
    # 分句否定不得豁免（；是分句边界）。全篇其他位置（含表头）出现「实际覆盖」
    # 不构成豁免（旧实现按全文搜索 → 漏检）。
    "extreme_window": re.compile(
        r"(?:组内|极值|排名|最大|最小|最高|最低)[^。；;\n|]{0,10}?"
        r"(?:不|未|无法|不能|不宜|不可|不作)"
        r"[^。；;\n|]{0,6}?(?:判断|判定|比较|排名|极值|结论|成立|可比)"
        r"|(?:不|未|无法|不能|不宜|不可|不作|无从|不得|避免)"
        r"[^。；;\n|]{0,12}?(?:判断|判定|比较|排名|作结|下结论|极值|组内|最大|最小|最高|最低)"
        r"|(?:不可比|不具可比性|窗口(?:起止)?不一致|实际覆盖不同|共同窗口(?:不一致|缺失|不同))"
        r"[^。；;\n|]{0,12}?(?:不|未|仅|限|留待|待|故|因此|只)"),
}


def _claim_exempt_near(line: str, match: re.Match, *, family: str) -> bool:
    """**同分句 + 同命题否证**豁免（B-F04；R3 改为对象贴附判定）。

    历史口径（行级 ±24 字符邻域 → R1 分句 + 通用否定词表 → R2 分句 + 括号/
    转折/表格格边界）都是作用域近似。R2 的通用否定词表仍会放过两类漏检
    （R3 负责人反例）：「身份已核验且宏观背景待核。」「跟踪误差为 0.20% 而
    盘口有缺口。」——「宏观背景」「盘口」的待核/缺口是**其他维度**的措辞，
    不是被匹配命题（身份已核 / 跟踪误差数字）的否定。R3 改为：否证必须
    贴附**本族对象**（表格 ``_ETF_SAME_OBJECT_EXEMPT_RE[family]``），且
    检索限定在断言所在分句内。真实否定仍通过（「跟踪误差不可得」「未做
    原文核验」「缺权重不得按 0」「不可拼成「当期完整组合」」）。
    """
    pat = _ETF_SAME_OBJECT_EXEMPT_RE.get(family)
    if pat is None:  # pragma: no cover — 未知族不允许静默豁免
        return False
    # tracking 表格的研究标签和数字可分处不同格；仍以句号/分号隔开命题，
    # 不允许前句否证或研究标签保护后句确认性断言。
    boundary = _TRACKING_BOUNDARY_RE if family == "tracking" else _CLAUSE_BOUNDARY_RE
    lo = 0
    for m in boundary.finditer(line, 0, match.start()):
        lo = m.end()
    right = boundary.search(line, match.end())
    hi = right.start() if right else len(line)
    clause = line[lo:hi]
    # 价格基准研究 TD/TE 与确认性跟踪误差是不同口径；只保护缩写研究数值。
    research = (family == "tracking" and re.search(r"\b(?:TD|TE)\b", match.group(), re.I)
                and not re.search(r"跟踪误差|跟踪差异", match.group())
                and _TRACKING_RESEARCH_RE.search(clause))
    return bool(pat.search(clause) or research)


def _first_unexempt_claim(line: str, pattern: re.Pattern, *, family: str):
    """逐项核验同一行，首个否证不得掩盖后续断言（五族共用）。"""
    return next((m for m in pattern.finditer(line)
                 if not _claim_exempt_near(line, m, family=family)), None)


def _claim_scan_lines(md: str) -> list[str]:
    """断言扫描行：**含表格行与引用行**，仅剔除代码块。

    与 ``_body_lines``（可读性口径：剔表格/引用/标题）不同——断言检查若沿用
    正文口径会 fail-open：实测报告把「跟踪误差 0.52%」写进表格行时检查器
    静默漏检（2026-10-08 现场）。此处只排除 ``` 围栏内内容。
    """
    out: list[str] = []
    in_fence = False
    for ln in md.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ln.strip():
            out.append(ln)
    return out


def _load_etf_lib_module(stem: str):
    """按显式路径加载 invest-a-etf lib 模块（etf_research / etf_generation）。

    返回 ``(module, None)`` 或 ``(None, 原因)``——**永不抛**：模块缺失/加载失败
    必须降级为调用方 finding，而不是冒到 ``_check_etf_generation`` 的兜底
    （那会退化成通用 etf-generation-check-error 硬阻断；review #4，2026-10-14）。

    ⚠️ 缺失判定**不能用** ``spec.loader is None``：``spec_from_file_location``
    对不存在的路径同样返回带 SourceFileLoader 的 spec（实测 is_file 是唯一
    可靠预检），执行时才抛 FileNotFoundError。

    ⚠️ 相对导入（review #14 实测）：**包内副本**的构建器改写给 lib 文件的是
    相对导入（``from ._invest_path import …``）；裸文件加载时 ``__package__``
    为空 → ImportError。故以「合成包 + 子模块」方式加载：为该 lib 目录建一个
    __path__ 指向它的包别名，模块名取 ``<alias>.<stem>``（父子关系自动成立，
    相对导入解析回本目录；兄弟模块互也可达）。同文件已按任意注册名加载时优先
    复用同一实例（A04 合同：同 canonical 文件不双实例）。etf lib 目录同时插入
    sys.path——主仓库副本用**裸绝对导入**（``from _invest_path import …``），
    需要它。
    """
    try:
        from invest_path import _loaded_module_by_path, invest_a_etf_lib_dir

        lib = invest_a_etf_lib_dir()
    except Exception as exc:  # noqa: BLE001
        return None, f"invest-a-etf lib 引导不可用: {type(exc).__name__}"
    target = lib / f"{stem}.py"
    if not target.is_file():
        return None, f"{stem}.py 缺失（{target}）"
    existing = _loaded_module_by_path(target)
    if existing is not None:
        return existing, None
    s = str(lib)
    if s not in sys.path:
        sys.path.insert(0, s)
    alias = "_etf_qc_lib_" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]
    if alias not in sys.modules:
        import types

        pkg = types.ModuleType(alias)
        pkg.__path__ = [s]
        sys.modules[alias] = pkg
    mod_name = f"{alias}.{stem}"
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached, None
    spec = importlib.util.spec_from_file_location(mod_name, target)
    if spec is None or spec.loader is None:
        return None, f"{stem}.py 无法构造加载器（{target}）"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 —— 残破模块不得留在 sys.modules
        sys.modules.pop(mod_name, None)
        return None, f"{stem}.py 加载失败: {type(exc).__name__}: {exc}"
    return mod, None


def _load_etf_payload_for_qc(report_path: Path) -> tuple[dict | None, list[str]]:
    """读取并校验同代 ETF payload——封存契约**单一来源**（etf_generation）。

    review #14（2026-10-14）：原先与 etf_generation.load_etf_payload/
    verify_etf_payload 重复实现（版本常量与字段检查两处各自维护），版本/字段
    漂移会让 QC 错杀或漏放；现全量委派。缺文件消息保留 QC 体例（basename；
    委派返回的错误串做路径归一——stock/ETF 双包输出须字节相等，绝对路径不入
    findings）。
    """
    payload_path = report_path.parent / f"{report_path.stem}.payload.json"
    if not payload_path.is_file():
        return None, [f"无同代冻结 payload: {payload_path.name}"]
    gen_mod, load_reason = _load_etf_lib_module("etf_generation")
    if gen_mod is None:
        return None, [f"封存契约模块（etf_generation）不可用: {load_reason}"]
    payload, errors = gen_mod.load_etf_payload(payload_path)
    errors = [e.replace(str(payload_path), payload_path.name) for e in errors]
    return payload, errors


# ── ETF 同代数值一致性与独立复算（2026-10-10，A8） ────────────────────────
# 设计边界（不宣称机器万能）：只做**可机械判定**的结构性核对——从**同代**
# payload 的原始值独立复算 / 与正文引用值对拍 / 期间与排名断言核对；
# 自由文本因果判断、口径选择合理性仍归三层人工复检（delivery-qc §6）。
# 「同代」由 _load_etf_payload_for_qc 的封存校验保证：不同代 payload 不参与
# 比对，历史 payload 的 derived 值不被当作真值（BOLL 以原始值复算为准）。

#: 两位小数容差：报告/底稿均按引擎 `round(…, 2)` 输出；允许 0.02 的复算边界
#: 余量（引擎用未舍入的中间量、QC 用 payload 的 4 位舍入原始值复算，仅可能
#: 在 .xx5 边界差 0.01；0.02 仍远小于任何真实公式/口径错误的量级差）。
_BOLL_RECOMPUTE_TOL = 0.02

#: 三值同现的轨道引用（模板形态「上轨 X / 中轨 Y / 下轨 Z」；分隔符任意短串）。
_BOLL_TRIPLE_RE = re.compile(
    r"上轨\s*([+-]?\d+(?:\.\d+)?)[^。；\n]{0,16}?中轨\s*([+-]?\d+(?:\.\d+)?)"
    r"[^。；\n]{0,16}?下轨\s*([+-]?\d+(?:\.\d+)?)")

#: 同代对拍覆盖的 payload 派生字段（kline.derived 8 + history.stats 2）。
#: boll_bandwidth_pct 由独立复算检查承担（真值 = 原始值复算，而非底稿自述值）。
_ETF_DERIVED_PAYLOAD_SCOPE: tuple[str, ...] = (
    "nav_vs_ma20_pct", "nav_vs_ma60_pct", "nav_vs_boll_mid_pct",
    "boll_position_pct", "nav_to_boll_lower_pct", "nav_to_boll_upper_pct",
    "daily_volatility_pct", "dist_to_ytd_low_pct", "atr14_pct",
)
_ETF_STATS_DERIVED = ("dist_to_ytd_low_pct", "atr14_pct")

_NUM_TOKEN_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _q2_equal(a: float, b: float) -> bool:
    """两位小数口径一致（精确相等，或按 2 位舍入后相等）。"""
    return abs(a - b) < 1e-6 or round(a, 2) == round(b, 2)


def _finite_number(v) -> bool:
    """有限实数（bool/NaN/±Inf/字符串/None 均 false）。"""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    return math.isfinite(float(v))


def _etf_boll_findings(payload: dict, text: str) -> list[dict]:
    """BOLL 带宽独立复算：`(upper − lower) / mid`（A1）。

    真值来自同代 payload 的**原始值**；payload 的 derived 与正文引用值都只是
    被核对对象——历史 payload 的旧公式值不得作为真值（A8 反例：7.45 = 除以
    下轨的旧口径）。缺原始值/零中轨（引擎新口径：mid ≤ 0 或缺值时**不输出**
    带宽键）是**合法停算**——只有「停算却仍给数」才是 error；无任何数值时
    整体 skip（R3 反例：零中轨无数值曾被无条件报错）。NaN/±Inf 判为非法数值。
    """
    kline = payload.get("kline")
    if not isinstance(kline, dict):
        return []
    up, mid, low = (kline.get("boll_upper"), kline.get("boll_mid"),
                    kline.get("boll_lower"))
    derived = kline.get("derived") or {}
    claimed = derived.get("boll_bandwidth_pct")
    md_cited = _extract_derived_values(text).get("boll_bandwidth_pct")
    if claimed is None and md_cited is None and up is None and mid is None and low is None:
        return []
    out: list[dict] = []
    if claimed is not None and not _finite_number(claimed):
        out.append({
            "id": "etf-boll-uncomputable", "severity": "error",
            "message": f"payload derived.boll_bandwidth_pct={claimed!r} 非有限数值"
                       "（NaN/Inf/非法类型）：不得作为带宽数值"})
        claimed = None
    if not (isinstance(up, (int, float)) and isinstance(mid, (int, float))
            and isinstance(low, (int, float))):
        if claimed is not None or md_cited is not None:
            out.append({
                "id": "etf-boll-uncomputable", "severity": "warning",
                "message": ("payload 缺 BOLL 原始值（boll_upper/boll_mid/boll_lower），"
                            "带宽数值不可独立复算：缺值应停算，不得给数值"
                            "（既有数值无法核验）")})
        return out
    if not _finite_number(mid) or not _finite_number(up) or not _finite_number(low):
        if claimed is not None or md_cited is not None:
            out.append({
                "id": "etf-boll-uncomputable", "severity": "error",
                "message": ("BOLL 原始值含非有限数值（NaN/Inf）：带宽=(upper−lower)/mid "
                            "不可计算，正文/底稿不得给带宽数值")})
        return out
    if mid <= 0:
        # 引擎新口径（mid ≤ 0 不输出带宽键）= 合法停算；只有仍给出数值才错。
        if claimed is not None or md_cited is not None:
            out.append({
                "id": "etf-boll-zero-mid", "severity": "error",
                "message": ("payload BOLL 中轨 ≤ 0：带宽=(upper−lower)/mid 未定义/除零，"
                            "按纪律应停算——正文/底稿仍给出带宽数值即违规")})
        return out
    recomputed = round((up - low) / mid * 100, 2)
    # 同代数字一致：正文引用的原始轨道值须等于 payload。只认**三值同现**的模板
    # 形态（「上轨 X … 中轨 Y … 下轨 Z」）——单标签正则会把散文句
    # 「距 BOLL 上轨 6.15% 空间」误判成轨道值（假阳性防护）。
    # review #7（2026-10-14）：同一错误 triple 在摘要+附录重复引用曾产出 N 条重复
    # finding；按本层惯例（「每字段取首个命中」，见 _etf_derived_payload_findings）
    # 至多一条 band-mismatch——跳出匹配循环，其后的复算/正文检查不受影响。
    mismatch_reported = False
    for m in _BOLL_TRIPLE_RE.finditer(text):
        for label, name, pv, val_raw in (("上轨", "boll_upper", up, m.group(1)),
                                         ("中轨", "boll_mid", mid, m.group(2)),
                                         ("下轨", "boll_lower", low, m.group(3))):
            if not _q2_equal(float(val_raw), float(pv)):
                out.append({
                    "id": "etf-boll-band-mismatch", "severity": "error",
                    "message": (f"正文引用 BOLL {label} {val_raw} ≠ 同代 payload "
                                f"{name}={pv}（同代数字不一致）")})
                mismatch_reported = True
                break
        if mismatch_reported:
            break
    if claimed is not None and abs(float(claimed) - recomputed) > _BOLL_RECOMPUTE_TOL:
        out.append({
            "id": "etf-boll-formula-mismatch", "severity": "error",
            "message": (f"payload derived.boll_bandwidth_pct={claimed} 与按 "
                        f"(upper−lower)/mid 复算 {recomputed}% 不一致"
                        f"（原始值 {up}/{mid}/{low}）——不得以底稿自述值为真值，"
                        "须按 A1 公式重算")})
    if md_cited is not None:
        try:
            md_val = float(md_cited)
        except ValueError:
            md_val = None
        if md_val is not None and not _finite_number(md_val):
            md_val = None
        if md_val is not None and abs(md_val - recomputed) > _BOLL_RECOMPUTE_TOL:
            out.append({
                "id": "etf-boll-report-mismatch", "severity": "error",
                "message": (f"报告引用 BOLL 带宽 {md_cited}%，同代 payload 原始值"
                            f"（{up}/{mid}/{low}）按 (upper−lower)/mid 复算为 "
                            f"{recomputed}%（独立复算不一致）")})
    return out


def _etf_derived_payload_findings(payload: dict, text: str) -> list[dict]:
    """同代数字一致：正文引用的派生字段值必须等于同代 payload 值。

    仅覆盖 payload 结构明确拥有的字段（kline.derived + history.stats 两字段）；
    正文引用而 payload 无该值 → error（数字无同代底稿，fail-closed）。
    `_extract_derived_values` 每字段取首个命中，重复引用不同值不在本层覆盖
    （已知边界，人工复检仍须按 delivery-qc 第 1 层逐项核）。
    """
    cited = _extract_derived_values(text)
    if not cited:
        return []
    kderived = (payload.get("kline") or {}).get("derived") or {}
    stats = (payload.get("history") or {}).get("stats") or {}
    out: list[dict] = []
    for field in _ETF_DERIVED_PAYLOAD_SCOPE:
        raw = cited.get(field)
        if raw is None:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if field in _ETF_STATS_DERIVED:
            pv, source = stats.get(field), "history.stats"
        else:
            pv, source = kderived.get(field), "kline.derived"
        if pv is None:
            out.append({
                "id": "etf-derived-not-in-payload", "severity": "error",
                "message": (f"正文引用 {source}.{field}={raw}，但同代 payload 无该值"
                            "（数字无同代底稿，不得引用未封存数值）")})
        elif not _q2_equal(val, float(pv)):
            out.append({
                "id": "etf-derived-mismatch", "severity": "error",
                "message": (f"正文 {field}={raw} ≠ 同代 payload {source}.{field}={pv}"
                            "（同代数字与底稿不一致）")})
    return out


#: R3（2026-10-11 复查收紧）：不可确认行的全窗口流量数字须有**本行/本单元格**
#: 明确限定。判定分三层，禁止「整行任一格出现 '部分'」式泛匹配：
#: ① 数字所在单元格 → 一般限定词即成立（限定与数字同格贴附）；
#: ② 其他单元格 → 限定词须与本行流量/窗口语境**同格**绑定（窗口/资金流/…/
#:   合计），或命中自带聚合语义的强短语（部分区间合计/可确认段小计/剔除异常段）；
#: ③ 任何位置的明确否定（「不是部分合计」「并非不可确认」「无不可确认段」）
#:    不构成限定。其他指标的「部分」（成交额部分样本）不豁免资金流合计；
#: 跨行/全篇文字不参与（未知留人工由 _peer_row_explicitly_unconfirmed 前置保证）。
_ETF_FLOW_PARTIAL_LABEL_RE = re.compile(r"部分|不可确认|未确认|可确认段|小计|剔除")
_ETF_FLOW_STRONG_QUALIFIER_RE = re.compile(
    r"部分区间(?:合计|小计)|部分合计|可确认段(?:合计|小计)"
    r"|窗口不可确认|窗口未确认|全窗(?:口)?不可确认|剔除异常(?:段|区间)")
_ETF_FLOW_CONTEXT_RE = re.compile(r"窗口|资金流|流量|流入|流出|全窗|合计|申赎")
_ETF_FLOW_LABEL_NEG_RE = re.compile(r"(?:不是|并非|不属于|非|无|没有|未)\s*$")


def _peer_row_explicitly_unconfirmed(row: dict) -> bool:
    """行是否有**明确** False 的窗口确认状态（缺字段＝旧 payload，留人工不拦）。"""
    fw = row.get("flow_window")
    return row.get("confirmable") is False or (
        isinstance(fw, dict) and fw.get("confirmable") is False)


def _peer_flow_cell_has_partial_label(cells: list[str], number_idx: int) -> bool:
    """本行是否对全窗口资金流数字给出直接限定（R3 复查；见上方词表注释）。

    ``number_idx`` = 资金流数字所在单元格下标（数字格内一般限定词直接成立）。
    """
    for i, cell in enumerate(cells):
        for m in _ETF_FLOW_STRONG_QUALIFIER_RE.finditer(cell):
            if not _ETF_FLOW_LABEL_NEG_RE.search(cell[:m.start()]):
                return True
        for m in _ETF_FLOW_PARTIAL_LABEL_RE.finditer(cell):
            if _ETF_FLOW_LABEL_NEG_RE.search(cell[:m.start()]):
                continue  # 明确否定不构成限定
            if i == number_idx or _ETF_FLOW_CONTEXT_RE.search(cell):
                return True
    return False


def _etf_peers_table_findings(payload: dict, text: str) -> list[dict]:
    """同类产品表：期间声明与数值须与同代 payload 冻结行一致（A8 期间一致）。

    期间口径（engine-handoff §1.4，A3/A4）：payload 行以 `window_days` +
    `flow_total_e`（正名）描述**本代封存的窗口**；`flow_20d_e` 只是 window_days==20
    时的兼容别名（其余窗口为 None）。因此表头「N 日资金流」列：

    - N 与 payload window_days 一致 → 与 `flow_total_e`（缺省回退 flow_20d_e）对拍；
    - N 不一致 → error（期间声明与底稿不一致），**不再对该列做数值对拍**——
      否则 120 日合法表会被误判 fabricated（R2 反例）；
    - 别名缺失（旧 payload 或未取数）且无正名值 → 不报 fabricated（无法核对即
      不冒充；只有「同一窗口下该行确为不可得」才拦），但整行无任何窗口字段时
      该行不参与对拍。

    `share_change_span`（= flow_window.segments）缺失 → warning（期间同起止
    无法核对，未执行 ≠ PASS）。不校验「flow 合计 ≈ 份额变化×价格」——口径注记
    明确二者不必相等（价格差异），不构成一致性约束。

    R3（2026-10-11 复查，限定词判定见
    ``_peer_flow_cell_has_partial_label``）：行的 `confirmable=False`（行级或
    flow_window 级**明确** False）时，flow 合计只含可确认段（部分合计）——全窗口
    资金流列的数字必须带本行/本单元格的「部分/不可确认」限定（如「可确认段小计」
    「窗口不可确认」），否则 error；**其他指标**的「部分」（成交额部分样本）与
    明确否定（不是部分合计/并非不可确认）不豁免，别处/跨行文字不豁免。旧
    payload 缺确认字段 → 留人工（不拦）；已确认完整行不拦；本规则只针对全窗口
    资金流列（近 5 日列与份额列不在内，分子窗口证据不同）。
    """
    peers = payload.get("peers")
    if not isinstance(peers, dict) or not peers.get("available"):
        return []
    flow = peers.get("flow") or {}
    window_days = flow.get("window_days")
    rows = [r for r in (flow.get("rows") or []) if isinstance(r, dict)]
    out: list[dict] = []
    for r in rows:
        if r.get("share_change_pct") is not None and r.get("share_change_span") is None:
            out.append({"id": "etf-peer-span-missing", "severity": "warning",
                        "message": (f"peers 行 {r.get('symbol')} 有份额变化值但未披露"
                                    "实际覆盖（share_change_span）：与窗口是否同起止"
                                    "无法核对（未执行 ≠ PASS）")})
    by_symbol = {r.get("symbol"): r for r in rows if r.get("symbol")}
    if not by_symbol:
        return out

    def _flow_value(row: dict) -> tuple[float | None, bool]:
        """(本代窗口合计值, 该行是否提供正名/别名键)。"""
        if "flow_total_e" in row:
            return row.get("flow_total_e"), True
        if "flow_20d_e" in row:
            return row.get("flow_20d_e"), True
        return None, False

    for table in _md_tables(text):
        header = [h.replace("**", "").strip() for h in table["header"]]
        has_flow_context = any("资金流" in h for h in header)
        # idx → ("flow", declared_days|None) / ("flow5", None) / ("share", None)
        col_map: dict[int, tuple[str, int | None]] = {}
        for idx, h in enumerate(header):
            m = re.search(r"(\d+)\s*日", h)
            declared_days = int(m.group(1)) if m else None
            # 模板用「N日资金流(亿) | 近5日(亿)」：缩写仅在本表有明确
            # 资金流列时成立。成交额/平均规模等具名指标不能仅凭「5日+亿」入选。
            abbreviated_flow = has_flow_context and re.fullmatch(
                r"(?:近|最近|过去)?\s*5\s*日\s*[（(]\s*亿(?:元)?\s*[）)]", h)
            if declared_days == 5 and ("资金流" in h or abbreviated_flow):
                col_map[idx] = ("flow5", None)
            elif "资金流" in h:
                col_map[idx] = ("flow", declared_days)
            elif "份额变化" in h and re.search(r"[%％]|百分比|百分率", h):
                # share_change_pct 只核验明确的百分比列；绝对份额/无单位
                # 不可与百分比对拍，当前 peer schema 不提供对应绝对份额字段。
                col_map[idx] = ("share", None)
        if not col_map:
            continue
        for line_no, cells in table["rows"]:
            sym = next((c.strip() for c in cells[:2] if c.strip() in by_symbol), None)
            if not sym:
                continue
            row = by_symbol[sym]
            for idx, (kind, declared) in col_map.items():
                if idx >= len(cells):
                    continue
                cell = cells[idx].strip()
                m = _NUM_TOKEN_RE.search(cell.replace("，", "").replace(",", ""))
                if m is None:
                    continue
                val = float(m.group(0))
                if kind == "flow" and declared is not None:
                    if isinstance(window_days, int) and declared != window_days:
                        out.append({
                            "id": "etf-peer-window-mismatch", "severity": "error",
                            "message": (f"同类产品表头声明「{header[idx]}」"
                                        f"（{declared} 日），同代 payload 窗口为 "
                                        f"{window_days} 日（期间声明与底稿不一致）")})
                        continue  # 不同窗口不做数值对拍（否则合法长窗口被误判）
                    if window_days is None:
                        continue  # 旧 payload 无窗口字段：无法核对，不冒充
                if (kind == "flow" and _peer_row_explicitly_unconfirmed(row)
                        and not _peer_flow_cell_has_partial_label(cells, idx)):
                    # R3：不可确认行的全窗口流量数字若无本行/本单元格限定 →
                    # 无条件全窗数字冒充确认合计（别处泛称不可确认不豁免）。
                    out.append({
                        "id": "etf-peer-partial-flow-unlabeled", "severity": "error",
                        "line": line_no,
                        "message": (f"同类产品表 {sym} 行全窗口资金流数字「{m.group(0)}」"
                                    "无「部分/不可确认」限定：该行 confirmable=False，"
                                    "同代 flow 合计只含可确认段（部分合计）——本行/本"
                                    "单元格须标「可确认段小计」或说明部分区间/不可确认，"
                                    "不得以无条件全窗数字呈现")})
                field = {"flow": "flow_total_e", "flow5": "flow_5d_e",
                         "share": "share_change_pct"}[kind]
                if kind == "flow":
                    pv, has_keys = _flow_value(row)
                    if not has_keys:
                        continue  # 旧 schema 无窗口合计键：跳过而非报 fabricated
                else:
                    pv, has_keys = row.get(field), (field in row)
                if pv is None:
                    if has_keys:
                        out.append({
                            "id": "etf-peer-row-fabricated", "severity": "error",
                            "message": (f"同类产品表 {sym} 行填了数值 {m.group(0)}，但"
                                        f"同代 payload 该行 {field}=None（不可得/失败行"
                                        "不得填数）")})
                elif _finite_number(pv) and not _q2_equal(val, float(pv)):
                    out.append({
                        "id": "etf-peer-row-mismatch", "severity": "error",
                        "message": (f"同类产品表 {sym} 行 {field}={m.group(0)} ≠ 同代 "
                                    f"payload {pv}（同代数字不一致）")})
    return out


def _etf_share_window_findings(payload: dict, text: str) -> list[dict]:
    """份额窗口：报告引用的窗口起止/行数须与同代 payload 可对上（engine §1.3）。

    同代 payload 有两个合法窗口：`date_range`（detail_rows 跨度，兼容字段）与
    `summary.flow_window{start,end}`（flow 合计与份额变化的**共同起止**；两者
    尾部可差 T+1 延迟即「实际覆盖」披露，故都接受）。行数声明同样接受
    `row_count`（detail 行数）或 `flow_window.trading_days/segments`。
    """
    sh = payload.get("share_history")
    if not isinstance(sh, dict):
        return []
    summary = sh.get("summary") or {}
    row_count = summary.get("row_count")
    flow_window = summary.get("flow_window") or {}
    accepted: list[list[str]] = []
    dr = sh.get("date_range")
    if isinstance(dr, str) and "~" in dr:
        accepted.append([d.strip().replace("-", "") for d in dr.split("~")[:2]])
    fw_start, fw_end = flow_window.get("start"), flow_window.get("end")
    if isinstance(fw_start, str) and isinstance(fw_end, str):
        accepted.append([fw_start.replace("-", ""), fw_end.replace("-", "")])
    counts = {c for c in (row_count, flow_window.get("trading_days"),
                          flow_window.get("segments")) if isinstance(c, int)}
    if not accepted and not counts:
        return []
    out: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if "share_history" not in line and "份额窗口" not in line:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*[~～]\s*(\d{4}-\d{2}-\d{2})", line)
        if m and accepted:
            got = [m.group(1).replace("-", ""), m.group(2).replace("-", "")]
            if got not in accepted:
                accepted_txt = "；".join(
                    f"{a[0][:4]}-{a[0][4:6]}-{a[0][6:]} ~ {a[1][:4]}-{a[1][4:6]}-{a[1][6:]}"
                    for a in accepted)
                out.append({
                    "id": "etf-share-window-mismatch", "severity": "error", "line": line_no,
                    "message": (f"报告份额窗口 {m.group(1)} ~ {m.group(2)} 与同代 payload "
                                f"窗口均不符（{accepted_txt}；date_range=明细跨度、"
                                "flow_window=flow/份额共同起止）")})
        mr = re.search(r"(\d+)\s*个?数据行", line)
        if mr and counts and int(mr.group(1)) not in counts:
            out.append({
                "id": "etf-share-rowcount-mismatch", "severity": "error",
                "line": line_no,
                "message": (f"报告称 {mr.group(1)} 个数据行，同代 payload 行数口径为 "
                            f"{sorted(counts)}（row_count/flow_window）均不符")})
    return out


#: 组内极值断言：① 指标锚定形态（降幅/涨幅/份额…组内最小）；② 主体锚定形态
#: （本标的/主标的/该产品…组内最小——另须明确份额变化指标）。匹配自身不得
#: 跨越 _CLAUSE_BOUNDARY_RE 的边界，否则同分句否证检索会误含前一分句。
_ETF_GROUP_EXTREME_RE = re.compile(
    r"(?:降幅|涨幅|份额变化|份额)[^。！？!?；;，,、\n|]{0,16}?组内\s*(最大|最小|最高|最低)"
    r"|(?:本标的|主标的|该产品)[^。！？!?；;，,、\n|]{0,16}?组内\s*(最大|最小|最高|最低)")

#: share_change_pct 的可判定指标：变化/涨跌幅；裸「份额」或总量不代表变化率。
#: 显式绝对单位仍优先排除（「份额变化（万份）」不能拿百分比核验）。
_ETF_SHARE_CHANGE_METRIC_RE = re.compile(
    r"份额\s*(?:的\s*)?(?:变化|变动|涨幅|降幅|增幅|增长率)")
_ETF_ABSOLUTE_SHARE_RE = re.compile(
    r"总份额|份额\s*(?:总量|总额|数量|余额)|绝对份额|[万亿]\s*份"
    r"|[（(]\s*份\s*[）)]|\d\s*份(?!额)")

#: R2（2026-10-11 复查）：份额极值分支的非份额口径词——出现在**断言分句**内即
#: 保守跳过留人工（「净值/收益/价格…涨幅组内最高」不得拿份额变化值裁决）。
_ETF_EXTREME_OTHER_CALIBER_RE = re.compile(
    r"净值|收益|回报|价格|股价|估值|市盈率|市净率|折溢价|溢价|折价|波动|振幅|成交|换手")

#: 非本代窗口期间限定（无法与份额数据窗口对齐 → 不裁决，留人工）。
_ETF_EXTREME_OTHER_PERIOD_RE = re.compile(
    r"年初至今|今年以来|年内|本月|上月|本周|上周|近一?周|近一?月|近一?季")
_ETF_CLAIM_DAY_RE = re.compile(r"(?<!\d)(\d+)\s*(?:个\s*)?(?:交易\s*)?日")


def _claim_clause(line: str, match: re.Match) -> str:
    """断言所在分句（逗号/分号/表格格/换行为界）——逐声明独立的作用域。"""
    lo = 0
    for b in _CLAUSE_BOUNDARY_RE.finditer(line, 0, match.start()):
        lo = b.end()
    right = _CLAUSE_BOUNDARY_RE.search(line, match.end())
    return line[lo:(right.start() if right else len(line))]


def _extreme_claim_subject(clause: str, main: str | None,
                           cohort: set[str]) -> str | None:
    """断言主体：分句内**唯一**候选（本标的/主标的→主标的；6 位成员代码）。

    多候选（对照句）或无候选 → None：留人工，不猜主标的（R1 复查要求）。
    """
    candidates = {c for c in cohort if c and c in clause}
    if main and re.search(r"本标的|主标的", clause):
        candidates.add(main)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _extreme_claim_window_ok(clause: str, window_days) -> bool:
    """断言自带的期间限定须与本代份额窗口对齐；不能确认对齐 → 跳过留人工。"""
    if _ETF_EXTREME_OTHER_PERIOD_RE.search(clause):
        return False
    days = _ETF_CLAIM_DAY_RE.findall(clause)
    return (not days or (isinstance(window_days, int)
                         and all(int(d) == window_days for d in days)))


def _etf_rank_findings(payload: dict, text: str) -> list[dict]:
    """极值排名：正文「排名 N/M」与「组内最大/最小」须与同代 payload 重算一致。

    排名只比对明确的主标的 20 日声明与 `rs.rank_20d`，其他主体/期间
    留给人工核验，不能套用主标的的 20 日值。组内极值：
    **零是合法极值**（「组内降幅最小」在存在 0.00 成员时并非本标的——R1 反例，
    不得过滤零）；主体为零也可判。方向语义只对「降幅」倒置（降幅最小=数值最大）,
    其余（涨幅/份额变化）按字面 max/min——不把泛称一律倒置。真正的混合
    符号（同时存在正负非零值）下「最大/最小」读法不唯一 → 跳过留人工。

    R1/R2（2026-10-11 复查）：组内极值逐**声明**核验（finditer，句内多条各自
    判定；首条正确/被否证不得 break 遮蔽后续），分句独立识别主体/指标/限定；
    豁免只作用本条声明。份额分支只锚定**本分句明确份额变化指标**（排除绝对
    份额/总量，无净值/收益/价格等其他口径词、期间可对齐）；其他口径留人工，
    不拿份额值裁决；主体不唯一/未知同样留人工，不猜主标的。
    """
    peers = payload.get("peers")
    if not isinstance(peers, dict) or not peers.get("available"):
        return []
    out: list[dict] = []
    rs = peers.get("rs") or {}
    rank20 = rs.get("rank_20d") or {}
    rank, total = rank20.get("rank"), rank20.get("total")
    if isinstance(rank, int) and isinstance(total, int):
        for line in _claim_scan_lines(text):
            # 按断句限定主体/期间，逐条核验；其他产品或第一条正确声明
            # 不得遮蔽后续错误的主标的声明。
            for clause in re.split(r"[。；;，,！!？?]", line):
                codes = set(re.findall(r"(?<!\d)\d{6}(?!\d)", clause))
                symbol = str(payload.get("symbol") or "")
                if codes and codes != {symbol}:
                    continue
                if not (symbol in codes or re.search(r"主标的|本标的", clause)):
                    continue
                periods = re.findall(r"(?<!\d)(\d+)\s*(?:个\s*)?(?:交易\s*)?日", clause)
                if periods != ["20"] or re.search(r"年度|年收益|年回报", clause):
                    continue
                if not re.search(r"收益|RS|相对强弱|相对", clause):
                    continue
                for m in re.finditer(r"排名\s*[:：]?\s*(\d+)\s*/\s*(\d+)", clause):
                    # 期间必须修饰本条收益排名，不能来自句中的波动/规模描述。
                    if not re.search(
                            r"(?<!\d)20\s*(?:个\s*)?(?:交易\s*)?日\s*(?:的\s*)?"
                            r"(?:净值\s*)?(?:累计\s*)?(?:收益|回报|RS|相对强弱|相对)"
                            r"(?:率)?\s*(?:的\s*)?$", clause[:m.start()]):
                        continue
                    if (int(m.group(1)), int(m.group(2))) != (rank, total):
                        out.append({
                            "id": "etf-rank-mismatch", "severity": "error",
                            "message": (f"正文「{m.group(0)}」≠ 同代 payload rank_20d "
                                        f"{rank}/{total}（主标的 20 日）：「{clause.strip()[:60]}」")})
    flow = peers.get("flow") or {}
    raw_rows = flow.get("rows") or []
    window_days = flow.get("window_days")
    flow_rows = [r for r in raw_rows
                 if isinstance(r, dict)]
    vals = {r.get("symbol"): r.get("share_change_pct") for r in flow_rows
            if isinstance(r.get("symbol"), str) and r.get("symbol")
            and _finite_number(r.get("share_change_pct"))}
    row_symbols = {r["symbol"] for r in flow_rows
                   if isinstance(r.get("symbol"), str) and r.get("symbol")}
    cohort = {c for c in (peers.get("peers") or []) if isinstance(c, str) and c}
    if payload.get("symbol"):
        cohort.add(payload["symbol"])
    cohort.update(row_symbols)
    if len(cohort) >= 2 or len(raw_rows) >= 2:
        # R6：组内极值/排名必须确有**共同起止/覆盖**——各产品各自同窗 ≠ 产品间
        # 共同窗口，且全组员数值可得、窗口明确可确认。缺共同窗时拒绝
        # 机械判定（透明拒绝，不按 values 无条件重排名）；报告已披露限制
        # （逐项实际覆盖/不可比）则不另报。
        windows: set[tuple[str, str]] = set()
        window_missing = False
        coverage_incomplete = (not cohort.issubset(row_symbols)
                               or len(flow_rows) != len(raw_rows)
                               or any(not isinstance(r.get("symbol"), str)
                                      or not r.get("symbol")
                                      or not _finite_number(r.get("share_change_pct"))
                                      for r in flow_rows))
        unconfirmed = False
        confirmation_missing = False
        invalid_window = False
        for r in flow_rows:
            fw = r.get("flow_window")
            if isinstance(fw, dict) and fw.get("start") and fw.get("end"):
                start, end = str(fw["start"]), str(fw["end"])
                windows.add((start, end))
                invalid_window = invalid_window or start > end
            else:
                window_missing = True
            states = [fw.get("confirmable") if isinstance(fw, dict) else None]
            if "confirmable" in r:
                states.append(r["confirmable"])
            unconfirmed = unconfirmed or any(s is False for s in states)
            confirmation_missing = confirmation_missing or any(s is not True for s in states)
        common_window = flow.get("common_window")
        if isinstance(common_window, dict):
            unconfirmed = unconfirmed or common_window.get("confirmable") is False
            confirmation_missing = confirmation_missing or common_window.get("confirmable") is not True
        windows_differ = len(windows) > 1
        known_invalid = coverage_incomplete or unconfirmed or invalid_window or windows_differ
        comparable = (bool(windows) and not window_missing and not confirmation_missing
                      and not known_invalid)
        has_pos = any(v > 0 for v in vals.values())
        has_neg = any(v < 0 for v in vals.values())
        if not comparable or not (has_pos and has_neg):
            # R1/R2（2026-10-11 复查）：逐**声明**扫描——旧实现每行只 search 首条
            # 并随即 break：首条正确或被否证即终止全篇，遮蔽同句逗号后与后续行的
            # 错误声明。现改为 finditer 逐条核验，分句（含逗号/分号）独立识别
            # 主体/指标/限定；豁免只跳过本条声明，不终止后续扫描。
            for line in _claim_scan_lines(text):
                for m in _ETF_GROUP_EXTREME_RE.finditer(line):
                    head = m.group(0)
                    clause = _claim_clause(line, m)
                    # 指标锚定（R2）：份额分支只认本分句明确份额口径——指标短语
                    # （组1 形态）须自带「份额」；主体短语（组2 形态）须分句含
                    # 「份额」。其他口径/无份额词 → 留人工，不拿份额值裁决。
                    anchored = ("份额" in head if m.group(1) is not None
                                else "份额" in clause)
                    if not anchored:
                        continue
                    if (not _ETF_SHARE_CHANGE_METRIC_RE.search(clause)
                            or _ETF_ABSOLUTE_SHARE_RE.search(clause)):
                        continue  # 绝对份额/裸份额缺对应证据，留人工，不套用变化百分比
                    if _ETF_EXTREME_OTHER_CALIBER_RE.search(clause):
                        continue
                    if not _extreme_claim_window_ok(clause, window_days):
                        continue
                    subject = _extreme_claim_subject(clause, payload.get("symbol"), cohort)
                    if not subject:
                        continue
                    # 同分句否证/限制豁免（Codex R6 复查：不得用全篇词语豁免——
                    # 仅当**极值断言所在分句**明确否定/限制该比较时才豁免；
                    # 表头或别处出现的「实际覆盖」不构成豁免）。仅跳过本条声明。
                    if _claim_exempt_near(line, m, family="extreme_window"):
                        continue
                    if not comparable:
                        reasons = []
                        if coverage_incomplete:
                            reasons.append("组员缺行、缺份额变化值或覆盖不完整")
                        if unconfirmed:
                            reasons.append("组员或共同窗口 confirmable=False（流量不可确认）")
                        if invalid_window:
                            reasons.append("组员窗口起止无效")
                        if windows_differ:
                            reasons.append("各产品 flow_window 起止不一致（"
                                           + "；".join(f"{s}~{e}" for s, e in sorted(windows)) + "）")
                        if window_missing or confirmation_missing:
                            reasons.append("payload 未提供全组员共同覆盖及明确确认状态")
                        reason = "；".join(reasons)
                        out.append({
                            # 已知起止不同且仍无条件断言极值 → error（不可比产品不得判）；
                            # 窗口未知（旧/缺字段）→ warning（未执行 ≠ PASS）。
                            "id": "etf-extreme-window-unverified",
                            "severity": "error" if known_invalid else "warning",
                            "message": (f"正文「{head}」无共同窗口证据：{reason}——各组员各自"
                                        "同窗 ≠ 产品间共同窗口，不得按共同窗口无条件写组内"
                                        "极值；须在**断言同句**写明限制（如「窗口不同，不判断"
                                        "组内最大/最小」）或逐项披露实际覆盖")})
                        continue
                    if has_pos and has_neg:
                        continue  # 真混合符号：极值语义不唯一，留人工
                    # 关键词取断言短语内的实际用词（正则交替可能先命中「份额」——
                    # 「份额降幅…组内最小」的语义是降幅，须按短语内 降幅/涨幅 判）
                    if "降幅" in head:
                        keyword = "降幅"
                    elif "涨幅" in head:
                        keyword = "涨幅"
                    else:
                        keyword = "份额"
                    want_max = (m.group(1) or m.group(2)) in ("最大", "最高")
                    if keyword == "降幅":  # 降幅最小/最低 = 数值最大（最接近零）
                        want_max = not want_max
                    target = max(vals.values()) if want_max else min(vals.values())
                    got = vals.get(subject)
                    if got is not None and abs(got - target) > 1e-9:
                        out.append({
                            "id": "etf-extreme-claim-mismatch", "severity": "error",
                            "message": (f"正文「{head}」与同代 payload 不符：{subject} "
                                        f"份额变化 {got}%，组内"
                                        f"{'最大' if want_max else '最小'}为 {target}%"
                                        "（零参与极值；真混合符号才留人工）"
                                        f"：「{line.strip()[:60]}」")})
    return out


#: 「估值摘要不构成结论」排除锚点（报告自述已把外部估值摘要排除出结论）。
_ETF_VALUATION_EXCLUSION_RE = re.compile(
    r"不构成[^\n。；]{0,14}定量结论|不作为[^\n。；]{0,12}结论|不进入任何结论"
    r"|不足以支撑定量结论|不采[纳用][^\n。；]{0,10}结论|不能直接采[纳用]"
    r"|仅作背景[^\n。；]{0,12}(?:不|未)")
#: 估值水平断言（低位/偏低/低估类；「估值低于同行」等比较句不命中）。
_ETF_VALUATION_LOW_CLAIM_RE = re.compile(
    r"(?:估值|市盈率|PE|市净率|PB|分位)[^。；;\n|]{0,12}?"
    r"(?:偏低|极低|低位|中低位|低估|便宜)"
    r"|(?:低估值|估值低(?:位|估)|处于低位估值)")
#: 结论区标题族（含 ETF 三行核心判断与致命一击：前者为精简模板首屏，
#: 后者为原报告的违规载体——同类「核心结论区」）。
_ETF_CONCLUSION_REGION_HEAD_RE = re.compile(
    r"结论|核心(?:发现|判断)|判断|致命一击|最脆弱|五问|多空")


def _conclusion_region_line_numbers(md: str) -> list[int]:
    """结论区行号：h2 标题命中标题族 → 其下至下一个 h2（含更深子标题行）。"""
    out: list[int] = []
    in_region = False
    for i, ln in enumerate(md.splitlines(), 1):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m and len(m.group(1)) <= 2:
            in_region = bool(_ETF_CONCLUSION_REGION_HEAD_RE.search(m.group(2)))
            continue
        if in_region:
            out.append(i)
    return out


def _etf_valuation_excluded_findings(payload: dict, text: str) -> list[dict]:
    """已排除估值摘要却在结论区称低位（A8 可机械反例）。

    三条件齐备才判：① payload 指数 PE 不可得（本代估值证据缺失）；
    ② 报告自述把外部估值摘要排除出结论；③ 结论区出现无来源的估值低位断言
    （同分句否证/「口径不一」等措辞照常豁免——真实否定不得误伤）。
    带 `[来源:]` 的低位断言不在此拦截（可能为本次已读一手材料，交人工复检）。
    """
    profile = payload.get("profile")
    if not isinstance(profile, dict) or profile.get("index_pe") is not None:
        return []
    if not _ETF_VALUATION_EXCLUSION_RE.search(text):
        return []
    region_lines = set(_conclusion_region_line_numbers(text))
    if not region_lines:
        return []
    for line_no, line in enumerate(text.splitlines(), 1):
        if line_no not in region_lines:
            continue
        m = _first_unexempt_claim(line, _ETF_VALUATION_LOW_CLAIM_RE, family="valuation")
        if not m:
            continue
        if re.search(r"\[来源\s*[:：]", line):
            continue
        return [{
            "id": "etf-valuation-excluded-claim", "severity": "error",
            "message": ("报告已声明估值摘要不构成结论（排除锚点），且本代 payload "
                        "index_pe 不可得，但结论区仍以无来源表述断言估值低位："
                        f"「{line.strip()[:70]}」——自相矛盾的可机械反例")}]
    return []


def etf_generation_findings(report_path: Path, *,
                            record_refresh: bool = False) -> list[dict]:
    """ETF 同代消费与断言对照（B06 + 2026-10-10 A8，离线）：返回 findings 列表。

    检查面：payload 存在/封存有效/对象一致 → QC 记录侧车绑定（产物 sha256、
    旧签认失效）→ HTML 内嵌代际哈希 → 报告正文断言 × payload 实况
    （无基准 TE、隔夜 NAV 错价、缺权重=0、错月拼当期、身份未核称已核）
    → 同代数值一致性/独立复算（BOLL 带宽按 (u−l)/m 从原始值复算、派生字段与
    轨道值对拍、同类产品表期间与数值、份额窗口、排名与组内极值、
    已排除估值证据却在结论区称低位）。
    未执行/不可判定项按 severity=warning 返回（未执行 ≠ PASS）。

    ``record_refresh=True``（调用方本次带 ``--record``，侧车将被覆盖重写）：
    记录侧车的自身状态（missing/stale/invalid）不再计为内容失败——否则
    陈旧产物重跑需连续两次（第一次必然被旧记录判 stale 而 FAIL），且第一次
    落盘的 machine_qc 反映的是旧记录而非当前内容。payload/HTML/正文断言等
    实质检查在此模式下照常执行（刷新记录不得绕过内容/封存/HTML 错误）。
    """
    findings: list[dict] = []
    text = report_path.read_text(encoding="utf-8")
    symbol = _extract_symbol(report_path)
    payload_path = report_path.parent / f"{report_path.stem}.payload.json"
    payload, pay_errs = _load_etf_payload_for_qc(report_path)
    if payload is None:
        if payload_path.is_file():
            # 文件在但不可用（损坏/封存哈希不符/篡改）→ 机器应失败
            findings.append({
                "id": "etf-payload-invalid", "severity": "error",
                "message": "同代 payload 存在但校验失败（" + "；".join(pay_errs)
                           + "）：封存哈希不符或结构损坏（篡改/非同代）"})
        else:
            findings.append({
                "id": "etf-payload-missing", "severity": "warning",
                "message": "无同代冻结 payload（" + "；".join(pay_errs)
                           + "）：无法核验同代输入绑定（未执行 ≠ PASS；"
                             "新报告须 report --payload-out 冻结）"})
    else:
        if symbol and payload.get("symbol") != symbol:
            findings.append({
                "id": "etf-payload-symbol", "severity": "error",
                "message": f"payload.symbol={payload.get('symbol')!r} ≠ 报告标的 {symbol!r}"
                           "（对象不一致）"})
        meta = payload.get("_meta") or {}
        payload_hash = str(meta.get("payload_hash") or "")
        html_path = report_path.parent / f"{report_path.stem}.html"
        if html_path.is_file():
            html_text = html_path.read_text(encoding="utf-8")
            m = _HTML_GENERATION_META_RE.search(html_text)
            if not m:
                findings.append({
                    "id": "etf-html-generation-missing", "severity": "error",
                    "message": "HTML 缺 invest-generation 代际标记（回采渲染或旧产物）"})
            elif m.group(1) != payload_hash:
                findings.append({
                    "id": "etf-html-generation-mismatch", "severity": "error",
                    "message": f"HTML 内嵌代际哈希 {m.group(1)[:12]}… ≠ payload "
                               f"{payload_hash[:12]}…（非同代/已被改动）"})
        # 报告断言 × payload 实况
        tracking = (payload.get("profile") or {}).get("tracking") or {}
        if tracking.get("status") != "available":
            for line in _claim_scan_lines(text):
                m = _first_unexempt_claim(line, _ETF_TE_CLAIM_RE, family="tracking")
                if m:
                    findings.append({
                        "id": "etf-tracking-claim", "severity": "error",
                        "message": "正文以数字断言确认性 跟踪误差/差异，但 payload "
                                   f"tracking.status={tracking.get('status')!r}"
                                   "（无同口径基准 → 确认指标不可得）："
                                   f"「{line.strip()[:60]}」"})
                    break
        for line in _claim_scan_lines(text):
            m = _first_unexempt_claim(line, _ETF_MISPRICE_CLAIM_RE, family="mispricing")
            if m:
                findings.append({
                    "id": "etf-mispricing-claim", "severity": "error",
                    "message": "正文将折溢价断言为错价/高估/低估，但净值时点未知"
                               "（premium_discount_caliber）："
                               f"「{line.strip()[:60]}」"})
                break
        holdings = (payload.get("holdings") or {})
        coverage = holdings.get("coverage") or {}
        if (coverage.get("pct_missing_rows") or coverage.get("gaps")
                or any(r.get("pct") is None for r in holdings.get("rows") or [])):
            for line in _claim_scan_lines(text):
                m = _first_unexempt_claim(line, _ETF_ZERO_WEIGHT_RE, family="zero_weight")
                if m:
                    findings.append({
                        "id": "etf-holdings-zero-weight", "severity": "error",
                        "message": "payload 存在缺权重披露，正文却把缺失按 0/合计 100% 表述"
                                   f"（不得按 0、不得重归一化）：「{line.strip()[:60]}」"})
                    break
        ie = payload.get("index_exposure") or {}
        pa = ie.get("period_alignment") or {}
        if ie and ie.get("status") == "ok" and pa.get("joinable") is False:
            for line in _claim_scan_lines(text):
                m = _first_unexempt_claim(line, _ETF_STALE_MIX_CLAIM_RE, family="stale_mix")
                if m:
                    findings.append({
                        "id": "etf-stale-composition-claim", "severity": "error",
                        "message": "成分/权重文件日期不同（错月），正文却拼作「当期完整组合」："
                                   f"「{line.strip()[:60]}」"})
                    break
        identity = (payload.get("profile") or {}).get("identity") or {}
        ver = (identity.get("verification") or {}).get("status")
        if ver != "verified_original":
            for line in _claim_scan_lines(text):
                m = _first_unexempt_claim(line, _ETF_IDENTITY_VERIFIED_RE, family="identity")
                if m:
                    findings.append({
                        "id": "etf-identity-claim", "severity": "error",
                        "message": f"payload 身份核验状态={ver!r}，正文却称身份已核/原文核验："
                                   f"「{line.strip()[:60]}」"})
                    break
        # 同代数值一致性 / 独立复算 / 期间与极值排名 / 估值排除断言
        # （2026-10-10 A8；全部离线只读同代 payload，失败反例见
        #  tests/test_b06_qc_record.py 的 TestETFValueCrossChecks 各项）
        findings.extend(_etf_boll_findings(payload, text))
        findings.extend(_etf_derived_payload_findings(payload, text))
        findings.extend(_etf_peers_table_findings(payload, text))
        findings.extend(_etf_share_window_findings(payload, text))
        findings.extend(_etf_rank_findings(payload, text))
        findings.extend(_etf_valuation_excluded_findings(payload, text))
        if "research" in payload:
            # ETF 领域合同只在实际研究块出现时加载；兼容旧代与独立包布局。
            # 模块缺失/加载失败降级为该 finding（review #4：原先的 spec.loader
            # 判空是死分支，缺失会冒成通用 etf-generation-check-error 硬阻断）。
            research_mod, load_reason = _load_etf_lib_module("etf_research")
            if research_mod is None:
                findings.append({"id": "etf-operating-research", "severity": "error",
                                 "message": f"经营证据链合同模块不可用：{load_reason}"})
            else:
                findings.extend(research_mod.research_findings(payload, text))
                if html_path.is_file():
                    findings.extend(research_mod.research_html_findings(payload, html_text, text))
    # QC 记录侧车（产物哈希绑定 + 分层签认）
    rec = verify_qc_record(report_path)
    if record_refresh:
        # 本次将覆盖重写侧车：记录现状如实标注为 info（不阻断、不冒充 PASS），
        # 内容/封存/HTML 检查已在上文独立执行
        findings.append({
            "id": "etf-qc-record-refresh", "severity": "info",
            "message": f"本次 --record 将覆盖重写 QC 记录（现状={rec['status']}；"
                       "旧签认不继承，新记录绑定本次产物 sha256 与本次机器结论）"})
    elif rec["status"] == "none":
        findings.append({
            "id": "etf-qc-record-missing", "severity": "warning",
            "message": "无 QC 记录侧车（产物 sha256/分层签认未记录）："
                       "机器身份/文本 PASS 不能代替人工/原文核验；"
                       "交付前应 report_qc.py <md> --record 记录本代产物"})
    elif rec["status"] in ("stale", "invalid"):
        findings.append({
            "id": "etf-qc-record-stale", "severity": "error",
            "message": "QC 记录与产物不一致（" + "；".join(rec["issues"]) + "）"})
    elif rec["status"] == "valid":
        unexecuted = [k for k, v in (rec.get("signoffs") or {}).items()
                      if (v or {}).get("status") == "not_executed"]
        if unexecuted:
            findings.append({
                "id": "etf-signoffs-unexecuted", "severity": "info",
                "message": "QC 记录有效，但以下签认层未执行（≠通过）："
                           + "、".join(unexecuted)})
    return findings


def _check_etf_generation(report_path: Path, *,
                          record_refresh: bool = False) -> LayerResult:
    """ETF 域层：同代 payload/产物哈希绑定 + 断言对照（B06）。"""
    layer = LayerResult(layer="etf-generation", status="pass")
    try:
        findings = etf_generation_findings(report_path,
                                           record_refresh=record_refresh)
    except Exception as exc:  # 不可静默 skip（fail-open 防护）
        layer.status = "fail"
        layer.findings_count = 1
        layer.details.append({"id": "etf-generation-check-error", "severity": "error",
                              "message": f"ETF 同代检查异常: {exc}"})
        return layer
    layer.details = findings
    layer.findings_count = len([f for f in findings if f["severity"] != "info"])
    if any(f["severity"] == "error" for f in findings):
        layer.status = "fail"
    elif any(f["severity"] in ("warning", "warn") for f in findings):
        layer.status = "warn"
    return layer


def _run_verify_layers(report_path: Path, report_type: str) -> list[LayerResult]:
    """--verify-data 模式：audit / quality / rigor（仅 stock）。"""
    layers: list[LayerResult] = []
    symbol = _extract_symbol(report_path)

    # ── audit：抽取数据点 + 偏差判定 ──
    audit = LayerResult(layer="audit", status="skip")
    if report_type == "etf":
        # B06：ETF 数据核验走同代 payload/QC 记录（离线只读，另见
        # etf-generation 层）；stock 式 audit 不适用 → 如实 skip，
        # 且 **不**因此置 network_used（未发起任何联网）。
        audit.details.append({
            "id": "audit-not-applicable", "severity": "info",
            "message": "ETF 数据核验=同代 payload/QC 记录（etf-generation 层，离线）；"
                       "未执行联网重采集（network_used=false）",
        })
    if report_type == "stock":
        try:
            report_audit = _load_stock_module("report_audit")
            extract_report = report_audit.extract_report
            verdict_report = report_audit.verdict_report

            extract_report(report_path)
            v = verdict_report(report_path)
            verdict = v.get("verdict", "FAIL")
            audit.findings_count = v.get("failed", 0)
            audit.details.append({
                "id": "audit-verdict",
                "severity": "info",
                "message": f"verdict={verdict} verified={v.get('verified', 0)} "
                           f"failed={v.get('failed', 0)} pending={v.get('pending', 0)}",
            })
            audit.status = {
                "PASS": "pass",
                "FAIL": "fail",
                "REVISIONS_NEEDED": "warn",
            }.get(verdict, "warn")
        except Exception as exc:  # pragma: no cover
            audit.status = "skip"
            audit.details.append({"id": "audit-unavailable", "severity": "info",
                                  "message": f"audit 不可用: {exc}"})
    layers.append(audit)

    # ── quality + rigor：需要先采集数据 ──
    # 注意：三层各自独立 try——rigor 异常不得被 quality 的 except 吞掉
    # （此前共用 try 导致 rigor 抛异常时被替换为重复 quality-skip 层，
    # _compute_overall 过滤 skip 后假 PASS）。异常一律 fail 不静默
    # （遵循 _run_lint_layer "不可静默 skip" 原则）。
    if report_type == "stock" and symbol:
        try:
            collector = _load_stock_module("collector")
            financial_rigor = _load_stock_module("financial_rigor")
            quality_check = _load_stock_module("quality_check")
            run_rigor = financial_rigor.run_rigor
            run_quality_check = quality_check.run_quality_check

            result = collector.collect_all(symbol, ["basic_info", "financials",
                                                    "quote", "valuation", "kline"])
            # B06：network_used 只反映**实际发起**的联网采集（collector），
            # 不再等同于 --verify-data 标志本身（旧实现 flag=True 即置 True，
            # ETF/无采集路径下「未执行」被记成用了网络）。
        except Exception as exc:  # pragma: no cover
            # 采集失败：两层都无法执行 → 两层都 fail
            for layer_name in ("quality", "rigor"):
                layers.append(LayerResult(
                    layer=layer_name, status="fail",
                    details=[{"id": f"{layer_name}-unavailable", "severity": "error",
                              "message": f"collect_all 失败，{layer_name} 未执行: {exc}"}]))
            return layers

        # quality 层
        try:
            qc = run_quality_check(result)
            q_overall = (qc.get("summary") or {}).get("overall", "pass")
            quality = LayerResult(layer="quality", status="pass")
            quality.network_used = True  # collect_all 已实际联网（上一步成功）
            quality.details = [
                {"id": m.get("id", m.get("name", "")), "severity": "info",
                 "message": f"{m.get('label', m.get('name', ''))}: {m.get('status', '')}"}
                for m in qc.get("metrics", [])
                if m.get("status") in ("fail", "warn")
            ]
            quality.findings_count = len(quality.details)
            if q_overall == "fail":
                quality.status = "fail"
            elif q_overall == "warn":
                quality.status = "warn"
        except Exception as exc:  # pragma: no cover
            quality = LayerResult(layer="quality", status="fail",
                                  details=[{"id": "quality-unavailable", "severity": "error",
                                            "message": f"quality 层异常: {exc}"}])
        layers.append(quality)

        # rigor 层（quality 异常不压制 rigor 运行）
        try:
            reports = run_rigor(result)
            rigor = LayerResult(layer="rigor", status="pass")
            rigor.network_used = True  # collect_all 已实际联网（上一步成功）
            rigor.details = [
                {"id": r.command, "severity": "info",
                 "message": f"[{r.command}] {r.field}: {r.detail} (偏差 {r.deviation_pct:.1f}%)"}
                for r in reports
                if r.status in ("fail", "warn")
            ]
            rigor.findings_count = len(rigor.details)
            if any(r.status == "fail" for r in reports):
                rigor.status = "fail"
            elif any(r.status == "warn" for r in reports):
                rigor.status = "warn"
        except Exception as exc:  # pragma: no cover
            rigor = LayerResult(layer="rigor", status="fail",
                                details=[{"id": "rigor-unavailable", "severity": "error",
                                          "message": f"rigor 层异常: {exc}"}])
        layers.append(rigor)
    return layers


def _compute_overall(layers: list[LayerResult]) -> str:
    """统一判定：FAIL > WARN > PASS（skip 不参与）。"""
    statuses = [l.status for l in layers if l.status != "skip"]
    if "fail" in statuses:
        return "FAIL"
    if "warn" in statuses:
        return "WARN"
    return "PASS"


def qc_file(
    report_path: Path,
    *,
    profile: str = "precommit",
    fail_on: str = "warning",
    verify_data: bool = False,
    record_refresh: bool = False,
) -> QCResult:
    """单文件 QC。report_path 不存在时返回 FAIL（含原因）。

    ``record_refresh=True``：调用方本次会 `--record` 覆盖重写侧车 →
    记录自状态（missing/stale/invalid）不计为内容失败（避免陈旧产物必须
    连跑两次）；实质检查照常。"""
    path = Path(report_path)
    if not path.exists():
        return QCResult(
            report_path=str(path),
            report_type="unknown",
            overall="FAIL",
            layers=[LayerResult(layer="lint", status="fail", findings_count=1,
                                details=[{"id": "file-missing", "severity": "error",
                                          "message": f"文件不存在: {path}"}])],
        )

    report_type = detect_report_type(path)
    text = path.read_text(encoding="utf-8")

    layers = [_run_lint_layer(path, profile, fail_on), _check_structure(text, report_type)]
    if report_type == "stock":
        layers.append(_check_stock_completion(path, text))
        layers.append(_check_insight_contract(path, text))
    if report_type != "pulse":
        # T6-2/T6-3（v0.3.0 R1）：F2 派生表述来源 / F4 §N 引用存在性——通用文本规则。
        # unknown 类型同样挂载（R1 审查 F13：event-calendar 等附属技能
        # 等新技能的产出一律 type=unknown，若跳过则「必跑」的准出对它们形同虚设）
        layers.append(_check_sourcing(text))
    if report_type in {"stock", "etf"}:
        # v0.3.0 A3：R-A1 可读性指标组**仅挂研究备忘录类型**——日历/扫描产物挂
        # 长句密度会批量制造无意义 WARN。
        layers.append(_check_readability(text))
        # v0.3.0 全量重审 F-U7-5：LAW 6a 实质要件此前零实现（仅全文免责存在性）。
        # 合规机制靠「三情景区间 + 用户决策」，故该要件须有机器把关点。
        # 三情景估值是研究备忘录概念，同类门控。
        layers.append(_check_law6a_scenarios(text))
    # v0.3.0 A3 补（2026-09-18 review #12）：R-A2 结论段证据 / R-A6 [事实]→[分析]
    # 对偶是**通用文本规则**，与产物类型无关——被删除的 stock 旧版
    # `run_report_qc` 对**任意**文件跑这三项，故 A3 移植时把它们一并门控是**净移除**
    # 了 journal/pulse/gap_scan/unknown 经 `invest.py qc-report` 的结论段门禁。
    # 拆分依据：源代码注释只论证了 R-A1（长句密度噪音），未论证 R-A2/R-A6。
    # R2 二轮：同代侧车存在时，[事实: F…] 引用须引用真实编号（F999999 非证据）。
    layers.append(_check_conclusion_evidence(
        text, report_type, fact_ids=_load_analysis_fact_ids(path)))
    if report_type == "etf":
        layers.append(_check_etf_derived(text))
        # B06：ETF 域同代消费层（payload 封存 / 产物哈希绑定 / QC 记录 /
        # 报告断言 × payload 实况）。离线只读；未执行项以 warn 呈现（未执行≠PASS）。
        layers.append(_check_etf_generation(path, record_refresh=record_refresh))
    elif report_type == "stock":
        # v0.2.7 E1：stock 报告引用 derived 字段（板块同步性 6 字段等）时同样校验。
        # 未引用时层为 skip → 不挂载（保持既有「stock 无 derived 层」行为，
        # test_not_etf_report_skip 语义不变）。
        derived_layer = _check_etf_derived(text)
        if derived_layer.status != "skip":
            layers.append(derived_layer)
    if verify_data:
        layers.extend(_run_verify_layers(path, report_type))

    return QCResult(
        report_path=str(path),
        report_type=report_type,
        overall=_compute_overall(layers),
        layers=layers,
        # B06：network_used 只反映**实际发起**的联网层（各层运行时自报），
        # 不等于 --verify-data 标志（旧实现 flag 即置 True：ETF 等未采集
        # 路径会被误记为已联网）。
        network_used=any(getattr(l, "network_used", False) for l in layers),
    )


def qc_directory(
    directory: Path,
    *,
    profile: str = "precommit",
    fail_on: str = "warning",
    verify_data: bool = False,
    record_refresh: bool = False,
) -> list[QCResult]:
    """批量检查目录下所有 .md（递归）。"""
    root = Path(directory)
    if not root.is_dir():
        return []
    results = []
    for path in sorted(root.rglob("*.md")):
        if ".audit_checklist" in path.name:
            continue
        results.append(qc_file(path, profile=profile, fail_on=fail_on,
                               verify_data=verify_data,
                               record_refresh=record_refresh))
    return results


def qc_latest(
    reports_dir: Path = Path("reports"),
    *,
    profile: str = "precommit",
    fail_on: str = "warning",
    verify_data: bool = False,
    record_refresh: bool = False,
) -> QCResult | None:
    """检查 reports/ 下最新修改的 .md。找不到返回 None。"""
    root = Path(reports_dir)
    if not root.is_dir():
        return None
    # 复盘纪要与审计清单**都不是研报**：混进来会让闸门在错的文档上给 PASS
    # （纪要与报告同目录且 mtime 最新）
    candidates = [p for p in root.rglob("*.md")
                  if ".audit_checklist" not in p.name
                  and not _REVIEW_MEMO_RE.match(p.name)]
    if not candidates:
        return None
    # mtime 相同（同秒写入/粗粒度文件系统）时按文件名取新，避免 max 平局由
    # rglob 迭代序决定（跨环境不确定，CI 曾取到旧报告）
    latest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
    return qc_file(latest, profile=profile, fail_on=fail_on, verify_data=verify_data,
                   record_refresh=record_refresh)


# ── 输出格式化 ────────────────────────────────────────────────────────────

_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}


def format_qc_result(result: QCResult, *, verbose: bool = False) -> str:
    """人类可读输出。"""
    lines = [
        f"{_ICON.get(result.overall.lower(), '❓')} {result.overall}  "
        f"{result.report_path}  (type={result.report_type})"
    ]
    for layer in result.layers:
        lines.append(f"   {_ICON.get(layer.status, '❓')} {layer.layer}: {layer.status}"
                     f" ({layer.findings_count})")
        if verbose and layer.details:
            for d in layer.details:
                sev = d.get("severity", "")
                # severity 词表权威定义见 invest-a-stock lib/lint.py:34 =
                # error/warning/info。v0.3.0 A5 前本文件产出侧混用 "warn"/"warning"
                # 两种拼写，而这里只认 "warn" → lint 层（发 "warning"）的全部
                # warning 级 finding 被渲染成 ℹ️，与 info 无法区分，delivery-qc.md
                # 要求的「逐条复核 sourcing warning」被静默跳过。产出侧已统一，
                # 此处兼容两种拼写以防未来漂移再次静默降级为 info 外观。
                icon = "❌" if sev == "error" else ("⚠️" if sev in ("warning", "warn") else "ℹ️")
                lines.append(f"      {icon} [{d.get('id', '')}] {d.get('message', '')}")
    return "\n".join(lines)


def _print_summary(results: list[QCResult], file=None, *, verbose: bool = False) -> int:
    """打印多个结果，返回退出码（0=PASS 1=WARN 2=FAIL）。"""
    if file is None:
        # def-time file=sys.stdout 会在 capsys 捕获期绑定临时流（lint.py 同族
        # 缺陷，2026-08-23 code-review #14）——调用时解析避免写已关闭流
        file = sys.stdout
    for r in results:
        print(format_qc_result(r, verbose=verbose), file=file)
    worst = max((r.overall for r in results), default="PASS",
                key=lambda o: {"PASS": 0, "WARN": 1, "FAIL": 2}.get(o, 0))
    if len(results) > 1:
        counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for r in results:
            counts[r.overall] = counts.get(r.overall, 0) + 1
        print(f"\n汇总: {len(results)} 份报告 | "
              f"✅ PASS {counts['PASS']} | ⚠️ WARN {counts['WARN']} | ❌ FAIL {counts['FAIL']}",
              file=file)
    return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(worst, 0)


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_qc",
        description="统一研报质量检查器（lint + 结构 + derived；--verify-data 联网验数据）",
    )
    parser.add_argument("target", nargs="*", help="报告文件路径（可多个）")
    parser.add_argument("--latest", action="store_true", help="检查 reports/ 下最新 .md")
    parser.add_argument("--dir", default="", help="批量检查目录下所有 .md")
    # 默认 claude：delivery-qc.md §2 第 0 层「机器准出（必跑）」就是本 CLI 不带 --profile
    # 的形式，故**默认值即合规门禁**。历史默认 precommit 对齐旧 check_report.sh
    # 的阻断项，会跳过全部 law6-* / known-violation*（14 条 error 级），使 v0.3.0
    # 注入报告首屏的模型撰写正文失去机器拦截。库函数默认值不动（保持对下游
    # 程序化调用与 pre-commit hook 的兼容，hook 显式传 --profile precommit）。
    parser.add_argument("--profile", choices=["claude", "precommit", "engine"],
                        default="claude",
                        help="规则档位（默认 claude：全量规则，含 LAW 6 等红线）")
    parser.add_argument("--fail-on", choices=["error", "warning", "info"],
                        default="warning",
                        help="lint 违规阈值：达到该级别即 FAIL（默认 warning）")
    parser.add_argument("--verify-data", action="store_true",
                        help="联网重采集，执行 audit + quality + rigor（仅 stock）")
    parser.add_argument("--record", action="store_true",
                        help="B06: 落盘 QC 记录侧车 <md ts>.qc.json（本代产物 sha256 "
                             "+ 本次机器结论 + 分层签认占位）；产物变更后旧记录自动失效")
    parser.add_argument("--signoff", action="append", default=None, metavar="LAYER=STATUS[:NOTE]",
                        help="B06: 记录分层签认（可重复）。层: "
                             + "/".join(QC_SIGNOFF_LAYERS)
                             + "；状态: " + "/".join(QC_SIGNOFF_STATES)
                             + "（默认全部 not_executed；机器 PASS 不代签）")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    # B06：解析分层签认（fail-loud：非法层/状态直接退出 2）
    signoffs: dict = {}
    for spec in args.signoff or []:
        layer, _, rest = spec.partition("=")
        if not layer or not rest:
            print(f"❌ --signoff 需形如 LAYER=STATUS[:NOTE]，收到 {spec!r}", file=sys.stderr)
            return 2
        status, _, note = rest.partition(":")
        try:
            parsed = _normalize_signoffs({layer: {"status": status, "note": note or None}})
        except ValueError as exc:
            print(f"❌ --signoff 非法: {exc}", file=sys.stderr)
            return 2
        signoffs[layer] = parsed[layer]

    results: list[QCResult] = []
    # --record 绑定：本次将覆盖重写侧车 → 记录自状态不计为内容失败（qc_file
    # 的 record_refresh），使陈旧产物一次重跑即可完成重核验（R2）。
    if args.latest:
        r = qc_latest(profile=args.profile, fail_on=args.fail_on,
                      verify_data=args.verify_data, record_refresh=args.record)
        if r:
            results.append(r)
        else:
            print("❌ reports/ 下未找到任何 .md 报告", file=sys.stderr)
            return 2
    elif args.dir:
        results = qc_directory(args.dir, profile=args.profile, fail_on=args.fail_on,
                               verify_data=args.verify_data,
                               record_refresh=args.record)
        if not results:
            print(f"❌ 目录中未找到 .md 报告: {args.dir}", file=sys.stderr)
            return 2
    elif args.target:
        for t in args.target:
            results.append(qc_file(t, profile=args.profile, fail_on=args.fail_on,
                                   verify_data=args.verify_data,
                                   record_refresh=args.record))
    else:
        parser.print_help()
        return 2

    if args.json:
        # stdout 只出 JSON；--record 的落盘与提示一律走 stderr（R2：组合 flag
        # 必须真实写记录——旧实现的 JSON 分支提前 return 会静默忽略 record）
        if args.record:
            _record_results(results, signoffs, stream=sys.stderr)
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        worst = max((r.overall for r in results), default="PASS",
                    key=lambda o: {"PASS": 0, "WARN": 1, "FAIL": 2}.get(o, 0))
        _trace_qc(results)
        return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(worst, 0)
    _trace_qc(results)
    rc = _print_summary(results, verbose=args.verbose)
    if args.record:
        _record_results(results, signoffs, stream=sys.stdout)
    return rc


def _record_results(results: list[QCResult], signoffs: dict,
                    *, stream) -> list[Path]:
    """把本次机器结论 + 分层签认落盘为侧车（默认签认=全 not_executed）。

    机器 FAIL 也照实落盘（record 记录真实结论，不得为刷新记录伪造成 PASS）；
    旧记录存在时在侧车内记录 supersedes（旧签认不继承）。
    """
    written: list[Path] = []
    for r in results:
        rp = Path(r.report_path)
        if not rp.is_file():
            print(f"⚠️ 跳过记录（文件不存在）: {rp}", file=sys.stderr)
            continue
        path, rec = write_qc_record(rp, r, signoffs=signoffs)
        written.append(path)
        print(f"QC 记录已落盘: {path}"
              f"（machine={rec['machine_qc']['overall']}；产物 sha256 已绑定；"
              "人工/原文/数据签认按 --signoff 记录，未记录=未执行）", file=stream)
    return written


def _trace_qc(results: list[QCResult]) -> None:
    """可选 run trace 钩子（仅 `INVEST_TRACE_FILE` 设置时生效；失败一律吞掉）。

    第 0 层机器准出是**独立进程**（本文件自带 CLI），`invest.py` 侧的计时覆盖不到
    它；宿主按标准链调用时可借此把最终 QC 的耗时与结论并入同一条 time line。
    未设置环境变量时行为与本函数不存在完全一致。
    """
    path = os.environ.get("INVEST_TRACE_FILE")
    if not path or not results:
        return
    try:
        row = {
            "run_id": os.environ.get("INVEST_RUN_ID") or "",
            "stage": "report_qc",
            "event": "end",
            "wall_time": datetime.now().astimezone().isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "targets": [Path(r.report_path).name for r in results],
            "overall": max((r.overall for r in results),
                           key=lambda o: {"PASS": 0, "WARN": 1, "FAIL": 2}.get(o, 0)),
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    sys.exit(main())
