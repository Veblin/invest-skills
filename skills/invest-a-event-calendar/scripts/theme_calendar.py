#!/usr/bin/env python3
"""题材日历记账（R-D01）——**只记账，不预测扩散路径**。

源文档裁决（hypothesis-registry C9/C10）：
- C9：扩散**存在性**部分支持；**扩散路径不可预测**；「提前数周布局」无直接证据
  → 引擎只登记阶段，不做扩散路径预测。
- C10：已炒作方向不追高（拥挤→反转，部分支持）；「复合概念个股退潮时有另一逻辑托底」
  **无支持** → 多概念归属记作**拥挤度加总**，**不得**写成「托底」。

硬约束（写进代码 + 测试）：
1. **证实锚点 = 官方/权威来源确认**；「题材热度见顶」与非官方渠道**均不等于证实**
   （见 `_UNRELIABLE_WORDS`）。
2. 阶段字段固定四值：首波 / 扩散 / 延伸 / 兑现；无「下一轮扩散方向」类字段。
3. **消息方向语义不预设**（分类器验证前，见 C8）——记录里不含方向判断字段。
4. 「证实」事件日入日历，供 journal / 复盘对照预期差。

状态文件与解禁模块**共用** `event_calendar_state.json`（不同顶级键：`themes` vs `symbols`），
故本模块读写时**保留其他键**，绝不整份覆盖。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _ensure_lib_on_path() -> None:
    """共享库引导（照 unlock_calendar._ensure_lib_on_path）：
    单体仓库取 skills/lib；包内运行取 <pkg>/scripts/lib。"""
    here = pathlib.Path(__file__).resolve()
    for cand in (here.parents[2] / "lib", here.parents[1] / "scripts" / "lib"):
        if cand.is_dir():
            sys.path.insert(0, str(cand))
            return


_ensure_lib_on_path()

STAGES: tuple[str, ...] = ("首波", "扩散", "延伸", "兑现")

_STATE_DEFAULT = pathlib.Path.home() / ".local" / "share" / "investment" / "event_calendar_state.json"

# 市场热度词——用作 `anchor_source` 时**不构成证实**（热度见顶 ≠ 证实，C9 裁决）
# 不合格来源两类：① 市场热度描述（热度见顶 ≠ 证实）；
# ② **非官方/不可溯源渠道**（传言类）——黑名单单独存在会放行「股吧传言」「微博热搜」
# 「某自媒体爆料」这类来源，使「官方/权威来源」闸门形同虚设（R4 评审实跑复现）
_UNRELIABLE_WORDS = ("热度", "涨停家数", "涨幅榜", "龙虎榜", "成交额榜", "情绪", "人气", "关注度",
                     "传言", "传闻", "据传", "据说", "爆料", "热搜", "自媒体", "股吧", "微博",
                     "微信", "小道消息", "网传", "消息人士")

# 记录字段白名单（结构上杜绝「扩散路径预测」类字段混入）
_RECORD_KEYS = ("theme", "stage", "anchor", "anchor_source", "confirmed_date",
                "concepts", "note", "recorded_at", "demand_window", "hype_window")

# --- R-D02 需求窗口 vs 炒作窗口 -------------------------------------------------
# 动机（C9）：**基本面季节性有据**，而「股价提前数周布局」无直接证据——两者必须分字段。
_HYPE_LABEL_PREFIX = "从业者惯例，非学术验证"
# 炒作窗口**不得**带收益预期类表述（R-D02 验收：无「预期收益」类描述）
_HYPE_BANNED = ("预期收益", "涨幅预期", "收益预测", "目标位", "目标价", "预期涨幅", "收益率预期")

# --- R-D03 可跟踪度（替代「小作文干扰小」）-------------------------------------
# 覆盖充分度 = 三类覆盖指标的**归一化均值**（归一化上限为工程近似，随 C12 预注册裁决）
_COVERAGE_FULL_ANALYSTS = 10.0     # 研报覆盖数达到此值视为充分
_COVERAGE_FULL_ANNOUNCE = 6.0      # 公告频率（次/月）
_COVERAGE_FULL_MEDIA = 20.0        # 媒体提及条数
_LOW_COVERAGE = 0.3                # 低于此值 → 低覆盖警示（对称风险提示）


def _today() -> str:
    from dates import shanghai_today

    t = shanghai_today()
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}"


def _read_raw(path: pathlib.Path) -> dict:
    """读整份状态（保留其他键）；**损坏时不静默清空**而是 fail loud。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"状态文件损坏（{path}）：{type(exc).__name__}——"
                         f"拒绝写入以免清空既有记录（请先修复或移除该文件）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"状态文件结构异常（{path}）：顶层不是对象——拒绝写入")
    return data


def _write_raw(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_official_source(source: str) -> bool:
    """证实锚点须为**官方/权威来源**；市场热度描述不算证实。"""
    s = str(source or "").strip()
    if not s:
        return False
    return not any(w in s for w in _UNRELIABLE_WORDS)


def make_demand_window(text: str, *, source: str, lit_note: str = "") -> dict:
    """需求窗口（R-D02）：**基本面日期硬编码 + 来源 + 商品基本面文献注记**。

    需求侧季节性是有据的基本面事实（与「股价提前布局」是两回事），故须带来源。
    """
    if not str(text or "").strip():
        raise ValueError("需求窗口文本不能为空")
    if not str(source or "").strip():
        raise ValueError("需求窗口须带来源（基本面日期为硬编码事实，无来源即不可核）")
    return {"text": str(text).strip(), "source": str(source).strip(),
            "lit_note": str(lit_note or "").strip()}


def make_hype_window(text: str, *, source: str = "") -> dict:
    """炒作窗口（R-D02）：须带**「从业者惯例，非学术验证」**标注，且**不带收益预期权重**。

    动机（C9）：「股价提前数周布局」**无直接证据**——炒作窗口只作从业者惯例的记录，
    不得与需求窗口同权重，更不得承载任何收益预期。
    """
    t = str(text or "").strip()
    if not t:
        raise ValueError("炒作窗口文本不能为空")
    hit = [w for w in _HYPE_BANNED if w in t]
    if hit:
        raise ValueError(
            f"炒作窗口**不得带收益预期**（命中 {hit}）——R-D02 验收要求「无预期收益类描述」；"
            f"预期类表述请移出本字段（本字段只记录从业者惯例的时间窗）")
    src = str(source or "").strip() or "出处不可考"
    return {"text": t, "source": src, "label": f"{_HYPE_LABEL_PREFIX}：{src}"}


def tractability_field(*, hard_catalysts: int, total_catalysts: int,
                       analyst_coverage: float | None,
                       announcement_freq: float | None,
                       media_mentions: float | None) -> dict:
    """可跟踪度（R-D03）= **硬催化占比 × 覆盖充分度**，替代「小作文干扰小」。

    动机（C12）：机制层有据（信息环境决定融入速度），而「小作文少可跟踪」**无直接检验**
    → 改为可计算字段。**低覆盖给对称风险提示**——低覆盖意味着**坏消息消化可能更慢**
    （Hou 2007 lead-lag 的反向警示），**不是**「干扰小所以好跟踪」。
    """
    total = int(total_catalysts or 0)
    hard = int(hard_catalysts or 0)
    if total <= 0:
        raise ValueError("total_catalysts 须 > 0（空分母不得静默产出比率——D5）")
    if hard < 0 or hard > total:
        raise ValueError(f"hard_catalysts({hard}) 须在 [0, total_catalysts({total})] 内")

    ratio = hard / total
    norms: list[float] = []
    for v, full in ((analyst_coverage, _COVERAGE_FULL_ANALYSTS),
                    (announcement_freq, _COVERAGE_FULL_ANNOUNCE),
                    (media_mentions, _COVERAGE_FULL_MEDIA)):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:          # NaN
            continue
        norms.append(min(1.0, max(0.0, f / full)))

    missing: list[str] = []
    if not norms:
        missing.append("覆盖充分度三类输入（研报覆盖数/公告频率/媒体提及）全部缺失")
    coverage = (sum(norms) / len(norms)) if norms else None
    tract = (ratio * coverage) if coverage is not None else None
    low = (coverage is not None and coverage < _LOW_COVERAGE)
    return {
        "hard_catalyst_ratio": ratio,
        "coverage_sufficiency": coverage,
        "tractability": tract,
        "available": coverage is not None,
        "low_coverage": low,
        # 措辞刻意**不复现**被替代词（「干扰小」等）——禁令引用禁词会让「无主观措辞」
        # 的扫描把免责句判成违规（本会话第三次同类）
        "warning": ("⚠️ 覆盖不充分：坏消息消化可能**更慢**（信息环境决定融入速度；"
                    "Hou 2007 lead-lag 的反向警示）——这是**对称**风险提示，"
                    "不得读作「覆盖低 ⇒ 波动小」") if low else None,
        "missing": missing,
        "norms_used": len(norms),
        "note": ("可跟踪度 = 硬催化占比 × 覆盖充分度（R-D03）；覆盖充分度为三类指标的"
                 "归一化均值，归一化上限为**工程近似**，随 C12 预注册裁决（见 "
                 "backtest_prereg/C12_预注册.md）"),
    }


def load_themes(*, state_file: str | pathlib.Path | None = None) -> list[dict]:
    """已登记的题材事件（按登记时间升序）。"""
    path = pathlib.Path(state_file) if state_file else _STATE_DEFAULT
    data = _read_raw(path)
    themes = data.get("themes")
    return list(themes) if isinstance(themes, list) else []


def register_theme(theme: str, *, stage: str, anchor: str, anchor_source: str,
                   concepts: list[str] | None = None, confirmed_date: str | None = None,
                   note: str = "", demand_window: dict | None = None,
                   hype_window: dict | None = None,
                   state_file: str | pathlib.Path | None = None) -> dict:
    """登记一条题材事件（阶段 + 证实锚点 + 概念归属）。

    校验：阶段须属 ``STAGES``；``anchor_source`` 须为官方/权威来源
    （热度类词被拒——**热度见顶 ≠ 证实**）；``stage == "兑现"`` 须给
    ``confirmed_date``（证实日入日历）。
    """
    if stage not in STAGES:
        raise ValueError(f"阶段非法：{stage!r}（须为 {STAGES} 之一）")
    if not str(anchor or "").strip():
        raise ValueError("证实锚点不能为空（题材必须能指向一个可核验的事件）")
    if not is_official_source(anchor_source):
        raise ValueError(
            f"证实锚点来源非法：{anchor_source!r}——须为可溯源的官方/权威来源"
            f"（部委/交易所/公司公告等）；市场热度描述（热度见顶 ≠ 证实）"
            f"与非官方渠道（传言/热搜/自媒体）均不构成证实")
    if stage == "兑现" and not confirmed_date:
        raise ValueError("阶段「兑现」须给 confirmed_date（证实日入日历，供复盘对照预期差）")

    concepts = [str(c).strip() for c in (concepts or []) if str(c).strip()]
    rec = {"theme": str(theme).strip(), "stage": stage, "anchor": str(anchor).strip(),
           "anchor_source": str(anchor_source).strip(),
           "confirmed_date": confirmed_date, "concepts": concepts,
           "note": str(note or "").strip(), "recorded_at": _today(),
           "demand_window": demand_window, "hype_window": hype_window}
    assert set(rec) == set(_RECORD_KEYS), "记录字段集与白名单不符"

    path = pathlib.Path(state_file) if state_file else _STATE_DEFAULT
    data = _read_raw(path)
    themes = data.get("themes")
    if not isinstance(themes, list):
        themes = []
    themes.append(rec)
    data["themes"] = themes
    data["updated"] = _today()
    _write_raw(path, data)
    return rec


def replay_theme(theme: str, *, state_file: str | pathlib.Path | None = None) -> list[dict]:
    """回放某题材的登记序列（教学 / 复盘用；**不推断后续阶段**）。"""
    return [t for t in load_themes(state_file=state_file) if t.get("theme") == str(theme).strip()]


def crowding_field(concepts: list[str], all_themes: list[dict]) -> dict:
    """多概念归属 → **拥挤度加总**字段。

    C10 裁决：可确认的仅是多概念 = 多重暴露 / 拥挤上升；
    「复合概念退潮时有另一逻辑托底」**无支持** → 本字段只做加总，
    **不输出任何「托底/更稳」语义**。
    """
    wanted = {str(c).strip() for c in (concepts or []) if str(c).strip()}
    total = 0
    detail: list[str] = []
    for t in all_themes or []:
        hit = wanted & {str(c).strip() for c in (t.get("concepts") or [])}
        if hit:
            total += len(hit)
            detail.append(f"{t.get('theme')}（{t.get('stage')}）")
    return {"concepts": sorted(wanted), "crowding_sum": total,
            "matched_themes": detail,
            "note": "多概念 = 多重暴露/拥挤度**加总**；不加权、不推断方向、不含「托底」语义（C10）"}


def confirmed_event_days(*, state_file: str | pathlib.Path | None = None) -> list[str]:
    """「证实」事件日（阶段=兑现 且有证实日）→ 日历，供复盘对照预期差。"""
    out = []
    for t in load_themes(state_file=state_file):
        d = t.get("confirmed_date")
        if t.get("stage") == "兑现" and d and is_official_source(t.get("anchor_source")):
            out.append(str(d))
    return sorted(set(out))


def render_themes(*, state_file: str | pathlib.Path | None = None) -> str:
    """题材台账渲染（描述性；无方向/预测语义）。"""
    themes = load_themes(state_file=state_file)
    lines = ["## 题材日历台账（只记账，不预测扩散路径）", ""]
    if not themes:
        lines.append("- （尚无登记；用 `--theme <题材> --stage <阶段> --anchor <锚点> "
                     "--anchor-source <来源>` 登记）")
    else:
        lines.append("| 题材 | 阶段 | 证实锚点 | 来源 | 证实日 | 概念 | 登记日 |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in themes:
            lines.append(f"| {t.get('theme')} | {t.get('stage')} | {t.get('anchor')} | "
                         f"{t.get('anchor_source')} | {t.get('confirmed_date') or '—'} | "
                         f"{'、'.join(t.get('concepts') or []) or '—'} | {t.get('recorded_at')} |")
    # R-D02：需求窗口与炒作窗口**禁止合并输出**——分列 + 显式声明
    win = [t for t in themes if t.get("demand_window") or t.get("hype_window")]
    if win:
        lines.append("")
        lines.append("### 时间窗口（需求窗口与炒作窗口**禁止合并**——两者证据等级不同）")
        lines.append("")
        lines.append("| 题材 | 窗口类型 | 窗口 | 来源 / 标注 |")
        lines.append("|---|---|---|---|")
        for t_ in win:
            dw, hw = t_.get("demand_window"), t_.get("hype_window")
            if dw:
                lit = f"；文献注记：{dw['lit_note']}" if dw.get("lit_note") else ""
                lines.append(f"| {t_.get('theme')} | **需求窗口**（基本面季节性） | "
                             f"{dw['text']} | {dw['source']}{lit} |")
            if hw:
                lines.append(f"| {t_.get('theme')} | 炒作窗口（**从业者惯例**） | "
                             f"{hw['text']} | {hw['label']} |")
        lines.append("")
        lines.append("> ⚠️ **两类窗口不得合并**：需求窗口是**基本面事实**（须带来源）；"
                     "炒作窗口是**从业者惯例**（标注「非学术验证」，"
                     "**不带收益预期权重**）。合并输出等于让惯例借用事实的证据等级（C9）。")
    # R-B03：多题材拥挤度**反向字段**——消费 crowding_field（C10：多概念 = 多重暴露/拥挤加总，
    # **不提供「托底」语义**）。此前该函数已实现但无消费方
    if themes:
        rows = []
        for t_ in themes:
            cf = crowding_field(t_.get("concepts") or [], themes)
            rows.append((t_.get("theme"), cf))
        if any(r[1]["crowding_sum"] for r in rows):
            lines.append("")
            lines.append("### 多题材拥挤度（加总口径，**不含托底语义**）")
            lines.append("")
            lines.append("| 题材 | 概念数 | 拥挤度加总 | 共享概念的其他题材 |")
            lines.append("|---|---|---|---|")
            for name, cf in rows:
                others = [d for d in cf["matched_themes"] if not d.startswith(f"{name}（")]
                lines.append(f"| {name} | {len(cf['concepts'])} | {cf['crowding_sum']} | "
                             f"{'、'.join(others) or '—'} |")
            lines.append("")
            lines.append("> 口径：多概念 = **多重暴露 / 拥挤度加总**（C10）——只做加总，"
                         "不加权、不推断方向；**不提供「托底/更稳」标签**"
                         "（该说法无支持，见 hypothesis-registry C10）")

    lines.append("")
    conf = confirmed_event_days(state_file=state_file)
    lines.append(f"- 已证实事件日：{'、'.join(conf) if conf else '—（无）'}")
    lines.append("- 口径：需求窗口与炒作窗口**分字段、禁合并**（R-D02）；"
                 "可跟踪度 = 硬催化占比 × 覆盖充分度，低覆盖时给**对称**风险提示（R-D03）；"
                 "证实锚点 = **官方/权威来源**确认；**题材热度见顶 ≠ 证实**；"
                 "引擎**只记账不预测扩散路径**；多概念归属记为**拥挤度加总**（不含「托底」语义）。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="题材日历记账（R-D01；只记账不预测）")
    ap.add_argument("--theme", required=True, help="题材名（教学示例请勿用真实个股）")
    ap.add_argument("--stage", required=True, choices=list(STAGES))
    ap.add_argument("--anchor", required=True, help="证实锚点（可核验的事件）")
    ap.add_argument("--anchor-source", required=True, help="锚点来源（须官方/权威）")
    ap.add_argument("--concepts", default="", help="概念（逗号分隔）")
    ap.add_argument("--confirmed-date", default=None)
    ap.add_argument("--note", default="")
    # R-D02：需求窗口（须来源）与炒作窗口（强制惯例标注、禁收益预期）——**分列输出**
    ap.add_argument("--demand-window", default="", help="需求窗口文本（基本面季节性）")
    ap.add_argument("--demand-source", default="", help="需求窗口来源（缺失即拒绝）")
    ap.add_argument("--demand-lit-note", default="", help="商品基本面文献注记（可选）")
    ap.add_argument("--hype-window", default="", help="炒作窗口文本（从业者惯例）")
    ap.add_argument("--hype-source", default="", help="炒作窗口出处（不可考写「出处不可考」）")
    ap.add_argument("--state-file", default=str(_STATE_DEFAULT))
    ap.add_argument("--no-out", action="store_true")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args(argv)

    concepts = [c for c in (args.concepts or "").split(",") if c.strip()]
    try:
        dw = (make_demand_window(args.demand_window, source=args.demand_source,
                                 lit_note=args.demand_lit_note)
              if args.demand_window else None)
        hw = (make_hype_window(args.hype_window, source=args.hype_source)
              if args.hype_window else None)
        rec = register_theme(args.theme, stage=args.stage, anchor=args.anchor,
                             anchor_source=args.anchor_source, concepts=concepts,
                             confirmed_date=args.confirmed_date, note=args.note,
                             demand_window=dw, hype_window=hw,
                             state_file=args.state_file)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    print(f"✅ 已登记：{rec['theme']} / {rec['stage']}（来源 {rec['anchor_source']}）")
    print(render_themes(state_file=args.state_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
