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

用法：
    uv run python skills/lib/report_qc.py <file>
    uv run python skills/lib/report_qc.py --latest
    uv run python skills/lib/report_qc.py --dir reports/
    uv run python skills/lib/report_qc.py <file> --verify-data --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from invest_path import ensure_invest_a_scripts_on_path  # noqa: E402


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
        ("structure-fact", r"\[事实\]", "warn", "报告应包含 [事实] 块引用数据来源（SOP-QC）"),
        ("structure-analysis", r"\[分析\]", "warn", "报告应包含 [分析] 块（基于事实的逻辑推演）"),
        ("structure-evidence", r"\[证据强度", "warn", "报告应包含 [证据强度:] 四维标注（SOP-EV）"),
        ("structure-source", r"\[来源:", "warn", "报告应标注 [来源:] 数据来源"),
        ("structure-risk-statement", r"不构成投资建议", "warn", "报告应包含风险声明（不构成投资建议）"),
    ],
    "etf": [
        ("structure-fact", r"\[事实\]", "warn", "报告应包含 [事实] 块引用数据来源（SOP-QC）"),
        ("structure-analysis", r"\[分析\]", "warn", "报告应包含 [分析] 块（基于事实的逻辑推演）"),
        ("structure-evidence", r"\[证据强度", "warn", "报告应包含 [证据强度:] 四维标注（SOP-EV）"),
        ("structure-risk-statement", r"不构成投资建议", "warn", "报告应包含风险声明（不构成投资建议）"),
    ],
    "journal": [
        # 买入路径四维（逻辑完整性/数据盲点/仓位匹配/风险收益比）与
        # 卖出路径四维（一致性/情绪化检测/参考点独立性/机会成本，v0.2.5 D2）双支持
        ("journal-logic", r"逻辑完整性|一致性", "warn", "journal 应包含评估维度（逻辑完整性或一致性）"),
        ("journal-blindspot", r"数据盲点|情绪化检测|情绪检测", "warn", "journal 应包含评估维度（数据盲点或情绪化检测）"),
        ("journal-position", r"仓位匹配|参考点独立性", "warn", "journal 应包含评估维度（仓位匹配或参考点独立性）"),
        ("journal-rr", r"风险收益比|机会成本", "warn", "journal 应包含评估维度（风险收益比或机会成本）"),
    ],
    "gap_scan": [
        ("gap-title", r"跳空缺口", "warn", "gap-scan 报告应包含'跳空缺口'标题"),
        ("gap-summary", r"(扫描摘要|统计|命中)", "warn", "gap-scan 报告应包含扫描摘要/命中统计"),
    ],
    "pulse": [],
    # 复盘纪要（R2/T8-3）：**按设计不含** [事实]/[分析]/[证据强度]——它明确不做
    # 推演，只对照当时写下的假设与证伪条件的当前状态。故只要求风险声明；
    # 若套用 etf/stock 的结构检查会稳定产出 4 条误报。
    "review": [
        ("structure-risk-statement", r"不构成投资建议", "warn",
         "复盘纪要应包含风险声明（不构成投资建议）"),
    ],
    "unknown": [],
}


def _check_structure(text: str, report_type: str) -> LayerResult:
    """结构层：按报告类型检查必备章节/标签存在性。"""
    layer = LayerResult(layer="structure", status="pass")
    for rule_id, pattern, severity, message in _STRUCTURE_REQUIREMENTS.get(report_type, []):
        if re.search(pattern, text):
            continue
        layer.findings_count += 1
        layer.details.append({"id": rule_id, "severity": severity, "message": message})
    if layer.findings_count:
        layer.status = "warn"
    return layer


# ── 股票报告交付完成度 ────────────────────────────────────────────────────

# v0.2.8 起，标准的自动化股票报告使用「研究快照」标题；它不是最终研究成品，
# 必须由同代 analysis.json 完成可追溯的分析合成。这里同时要求风险提示中的
# 「自动化引擎生成」字样，避免把用户手写的研究备忘录误判为待合成快照。
_AUTOMATED_STOCK_SNAPSHOT_RE = re.compile(
    r"^#\s+\d{6}\s+.+?\s+研究快照\s*$", re.M
)
_AUTOMATED_ENGINE_NOTICE_RE = re.compile(r"本报告由自动化引擎生成")

# 仅捕捉明确表示「尚待模型填写」的模板残留。不能把「待独立验证」「数据不可得」
# 这类有意保留的不确定性误作未完成报告。
_TEMPLATE_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\[\s*(?:待\s*(?:Claude|AI|LLM)(?:\s+report)?(?:\s+阶段)?\s*"
        r"(?:填充|填写|补充|验证)?|分析提示|待(?:填|填写|填充)|TODO|TBD|FIXME)\s*\]",
        re.I,
    ),
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
    r"(?:逻辑链|支撑依据|依据|证据|基础)|(?:左|右)侧参考指标数据不足",
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

    is_automated_snapshot = bool(
        _AUTOMATED_STOCK_SNAPSHOT_RE.search(text)
        and _AUTOMATED_ENGINE_NOTICE_RE.search(text)
    )
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

    if layer.findings_count:
        layer.status = "fail"
    elif is_automated_snapshot or any(
        _basis_section_kind(match.group(2))
        for line in lines if (match := _MARKDOWN_HEADING_RE.match(line))
    ):
        layer.status = "pass"
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
            "severity": "warn",
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
                    "severity": "warn",
                    "message": f"字段 {field_name} 值 '{raw}' 无法解析为数值",
                })
                continue
            lo, hi = _ETF_DERIVED_RANGES.get(field_name, (-1e9, 1e9))
            if not (lo <= value <= hi):
                layer.findings_count += 1
                layer.details.append({
                    "id": f"derived-{field_name}",
                    "severity": "warn",
                    "message": f"字段 {field_name} 值 {value} 超出合理范围 [{lo}, {hi}]",
                })
            elif abs(round(value, 2) - value) > 1e-6:
                layer.findings_count += 1
                layer.details.append({
                    "id": f"derived-{field_name}",
                    "severity": "info",
                    "message": f"字段 {field_name} 值 {value} 未保留两位小数（引擎输出 round(…, 2)）",
                })
        # 仅 warn 级发现（超范围/无法解析/漂移）翻转状态；info 级（位数）不阻塞
        if any(d["severity"] == "warn" for d in layer.details):
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
            "severity": "warn",
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
            "severity": "warn",
            "message": f"正文引用 §{ref} 但报告无对应标题节",
        })
    if layer.findings_count:
        layer.status = "warn"
    return layer


# ── 主检查流程 ────────────────────────────────────────────────────────────


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
    spec = importlib.util.spec_from_file_location(
        "_invest_lib", init_path, submodule_search_locations=[str(lib_dir)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 lib 包: {lib_dir}")
    mod = importlib.util.module_from_spec(spec)
    # 必须先把模块注册进 sys.modules，否则模块内 @dataclass / 相对导入
    # 会因查不到模块而失败（AttributeError: 'NoneType'）
    sys.modules[mod.__name__] = mod
    spec.loader.exec_module(mod)
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


def _run_verify_layers(report_path: Path, report_type: str) -> list[LayerResult]:
    """--verify-data 模式：audit / quality / rigor（仅 stock）。"""
    layers: list[LayerResult] = []
    symbol = _extract_symbol(report_path)

    # ── audit：抽取数据点 + 偏差判定 ──
    audit = LayerResult(layer="audit", status="skip")
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
) -> QCResult:
    """单文件 QC。report_path 不存在时返回 FAIL（含原因）。"""
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
    if report_type != "pulse":
        # T6-2/T6-3（v0.3.0 R1）：F2 派生表述来源 / F4 §N 引用存在性——通用文本规则。
        # unknown 类型同样挂载（R1 审查 F13：event-calendar 等附属技能
        # 等新技能的产出一律 type=unknown，若跳过则「必跑」的准出对它们形同虚设）
        layers.append(_check_sourcing(text))
    if report_type == "etf":
        layers.append(_check_etf_derived(text))
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
        network_used=verify_data,
    )


def qc_directory(
    directory: Path,
    *,
    profile: str = "precommit",
    fail_on: str = "warning",
    verify_data: bool = False,
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
                               verify_data=verify_data))
    return results


def qc_latest(
    reports_dir: Path = Path("reports"),
    *,
    profile: str = "precommit",
    fail_on: str = "warning",
    verify_data: bool = False,
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
    return qc_file(latest, profile=profile, fail_on=fail_on, verify_data=verify_data)


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
                icon = "❌" if sev == "error" else ("⚠️" if sev == "warn" else "ℹ️")
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
    parser.add_argument("--profile", choices=["claude", "precommit", "engine"],
                        default="precommit")
    parser.add_argument("--fail-on", choices=["error", "warning", "info"],
                        default="warning",
                        help="lint 违规阈值：达到该级别即 FAIL（默认 warning）")
    parser.add_argument("--verify-data", action="store_true",
                        help="联网重采集，执行 audit + quality + rigor（仅 stock）")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    results: list[QCResult] = []
    if args.latest:
        r = qc_latest(profile=args.profile, fail_on=args.fail_on,
                      verify_data=args.verify_data)
        if r:
            results.append(r)
        else:
            print("❌ reports/ 下未找到任何 .md 报告", file=sys.stderr)
            return 2
    elif args.dir:
        results = qc_directory(args.dir, profile=args.profile, fail_on=args.fail_on,
                               verify_data=args.verify_data)
        if not results:
            print(f"❌ 目录中未找到 .md 报告: {args.dir}", file=sys.stderr)
            return 2
    elif args.target:
        for t in args.target:
            results.append(qc_file(t, profile=args.profile, fail_on=args.fail_on,
                                   verify_data=args.verify_data))
    else:
        parser.print_help()
        return 2

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        worst = max((r.overall for r in results), default="PASS",
                    key=lambda o: {"PASS": 0, "WARN": 1, "FAIL": 2}.get(o, 0))
        return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(worst, 0)
    return _print_summary(results, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
