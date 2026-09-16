"""ResearchProfile — 用户研究镜头（P0-5）。

记录 `SKILL.md` R12g-B 开场四问的结果（持有周期 / 风格 / 关注焦点 / 已看行情），
落同代侧车并在 full 模式报告头部透明展示。

**P0 阶段只做「记录 + 展示」，不做字段过滤**：偏好改变的是阅读顺序与补证优先级，
不得删除反证、关键缺口或风险。展示文案须self-evidently传达这一点，避免被读成
「只展示这些数据」的过滤器。

`horizon` 与 `style` 是两个独立维度。把「成长」（风格口径）与「中期/短期」
（持有周期口径）并列进同一枚举，会与 R12g-B 的分流（短/中线→趋势路径，
长线→价值路径）冲突；两者**禁止互相推断**——风格猜错会静默改变整条分析主轴。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from html import escape as _html_escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

HORIZONS: tuple[str, ...] = ("short_term", "medium_term", "long_term")
FOCUSES: tuple[str, ...] = ("valuation", "event_catalyst", "capital_flow", "comprehensive")
STYLES: tuple[str, ...] = ("价值", "成长", "趋势", "事件驱动", "混合")
MODES: tuple[str, ...] = ("brief", "full", "concise", "insight")
DEFAULT_MODE = "full"

_HORIZON_LABELS = {
    "short_term": "短线（1-2 周）",
    "medium_term": "中线（1-6 月）",
    "long_term": "长线（1 年+）",
}
_FOCUS_LABELS = {
    "valuation": "估值",
    "event_catalyst": "事件催化",
    "capital_flow": "资金行为",
    "comprehensive": "全面",
}

_FREE_TEXT_MAX = 120

# 伪造 QC / 合规标记的注入向量：自由文本最终进报告正文，含这些片段即可伪造
# 来源标注，或让完成度门禁与 lint 的判定失真。
_MARKER_INJECTION_RE = re.compile(r"\[\s*(?:来源|证据|证据强度|待|分析提示|Evidence)", re.I)

# 派生断言形态（与 skills/lib/report_qc.py 的 `_F2_PATTERN` 对应）。
# 「研究目标」「证据偏好」是目标陈述，不是量化断言；量化断言必须由引擎或
# Python 计算产出并带合法来源标注，用户输入无法提供。两者的一致性由
# tests/test_research_profile.py::test_f2_shape_guard_covers_shared_pattern 守住。
_F2_SHAPE_RE = re.compile(
    r"[+-]?\d+(?:\.\d+)?\s*(?:倍|个百分点|个点|bp)"
    r"|近?(?:六成|七成|八成|九成)|五成以上|过半"
    r"|约\s*[+-]?\d+(?:\.\d+)?\s*%"
)


class ProfileSchemaError(ValueError):
    """ResearchProfile 校验失败。"""


def _clean_free_text(value: Any) -> str:
    """自由文本归一化：单行化、压缩连续空白、截断。"""
    return re.sub(r"\s+", " ", str(value)).strip()[:_FREE_TEXT_MAX]


def _banned_compliance_word(text: str) -> str | None:
    """命中合规词表 error 级逐行规则时返回该规则 id。

    复用 `lib/lint.py` 的既有词表（references/compliance_rules.yaml），
    不另造一套禁词。词表不可加载时 fail-closed——放行一份无法校验的自由文本，
    比拒绝一次输入更危险。
    """
    from lib.lint import RulesLoadError, load_rules
    try:
        rules = load_rules()
    except RulesLoadError as exc:  # pragma: no cover - 分发包缺 yaml 时
        raise ProfileSchemaError(f"合规词表不可用（{exc}）") from exc
    for rule in rules:
        if rule.get("scope", "line") != "line" or rule.get("severity") != "error":
            continue
        pattern = rule.get("pattern")
        if not pattern:
            continue
        try:
            if re.search(pattern, text):
                return str(rule.get("id", "unknown"))
        except re.error:  # pragma: no cover - 词表自带正则不应坏
            continue
    return None


def build_profile(args: Any) -> dict[str, Any] | None:
    """从 CLI 参数组装 ResearchProfile；未提供任何相关参数时返回 None。

    返回 None 表示「本次未使用研究档案」——调用方应完全不落盘、不渲染，
    与既有行为零差异。
    """
    horizon = getattr(args, "horizon", None)
    focuses = [f for f in (getattr(args, "focus", None) or []) if f]
    goal = getattr(args, "goal", None)
    already_knows_price = getattr(args, "already_knows_price", None)
    style = getattr(args, "style", None)

    if not horizon:
        horizon = None
    if not style:
        # 风格已由 user_style.json 承载（SKILL.md R12g-B Q_风格 的持久化），
        # 默认读档案而非要求用户重复录入。
        try:
            from lib.style_match import load_style
            style = load_style()
        except Exception:  # noqa: BLE001 - 档案缺失/损坏一律中性降级
            style = None

    if horizon is None and not focuses and not goal \
            and already_knows_price is None and not style:
        return None

    profile: dict[str, Any] = {}
    if horizon:
        profile["horizon"] = horizon
    if style:
        profile["style"] = style
    if focuses:
        profile["focuses"] = focuses
    if already_knows_price is not None:
        profile["already_knows_price"] = bool(already_knows_price)
    if goal:
        profile["report_goal"] = _clean_free_text(goal)
    return profile


def resolve_mode(args: Any) -> dict[str, str]:
    """产物溯源：本次报告用哪个 ``--mode``，以及它是显式传入还是落到默认值。

    动机（2026-09-15 实测）：full 报告事后被误判为「因为 insight 模式当时
    还不存在」，真实原因只是「生成时选了 full」——产物本身没记录 mode，
    审计者只能倒推。把 mode 与显式性随产物落盘，这类误判即不可能发生。

    ``args.mode`` 因根解析器 default='full' **恒存在**，``hasattr`` 无法判别
    显式性；显式性由 ``invest.py`` 的 ``_ModeAction`` 记录到私有属性
    ``_mode_explicit``（不改变任何既有默认值契约）。
    """
    raw = getattr(args, "mode", None)
    # 非法值一律归默认，且来源记 "default"——否则会出现「mode=full 但标注
    # 显式传入」这种自相矛盾的溯源（显式传的是别的值，不等于显式传了 full）。
    explicit = raw in MODES and bool(getattr(args, "_mode_explicit", False))
    return {
        "mode": raw if raw in MODES else DEFAULT_MODE,
        "mode_source": "cli" if explicit else "default",
    }


def validate_profile(profile: dict[str, Any]) -> list[str]:
    """返回错误列表（空 = 通过）。未知键原样保留，不做字段过滤。"""
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["ResearchProfile 顶层必须是对象"]

    horizon = profile.get("horizon")
    if horizon is not None and horizon not in HORIZONS:
        errors.append(f"horizon 取值非法：{horizon!r}（允许 {', '.join(HORIZONS)}）")

    style = profile.get("style")
    if style is not None and style not in STYLES:
        errors.append(f"style 取值非法：{style!r}（允许 {', '.join(STYLES)}）")

    focuses = profile.get("focuses")
    if focuses is not None:
        if not isinstance(focuses, list):
            errors.append("focuses 必须是列表")
        else:
            bad = [f for f in focuses if f not in FOCUSES]
            if bad:
                errors.append(
                    f"focuses 取值非法：{', '.join(map(repr, bad))}"
                    f"（允许 {', '.join(FOCUSES)}）"
                )

    akp = profile.get("already_knows_price")
    if akp is not None and not isinstance(akp, bool):
        errors.append(f"already_knows_price 必须是布尔值，得到 {type(akp).__name__}")

    for key in ("report_goal", "evidence_preferences"):
        value = profile.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"{key} 必须是字符串")
            continue
        if _MARKER_INJECTION_RE.search(value):
            errors.append(f"{key} 含疑似 QC/合规标记片段，可能伪造来源标注")
            continue
        if _F2_SHAPE_RE.search(value):
            errors.append(
                f"{key} 含派生断言形态（约 X% / N 倍 / N 个百分点 / 成数）；"
                "研究目标与证据偏好应为目标陈述，量化断言须由引擎或 Python 计算产出"
            )
            continue
        banned = _banned_compliance_word(value)
        if banned:
            errors.append(f"{key} 命中合规词表规则 {banned}（LAW 6 等措辞红线）")
    return errors


def _format_horizon(profile: dict[str, Any]) -> str | None:
    horizon = profile.get("horizon")
    if not horizon:
        return None
    # 字段名避开「持有」二字：law6-hold-standalone 词规（error 级）会命中
    # 「持有周期」，使任何带研究档案的报告无法通过第 0 层门禁——引擎自有固定
    # 文案不得触发红线词规（改文案，而非放宽规则）。
    return f"周期视角={_HORIZON_LABELS.get(horizon, horizon)}"


def _format_focus(profile: dict[str, Any]) -> str | None:
    focuses = profile.get("focuses") or []
    if not focuses:
        return None
    return "关注焦点=" + "、".join(_FOCUS_LABELS.get(f, f) for f in focuses)


def _format_summary(profile: dict[str, Any]) -> str:
    """档案单行摘要（MD 与 HTML 共用，避免两套措辞漂移）。"""
    parts: list[str] = []
    style = profile.get("style")
    if style:
        parts.append(f"风格={style}")
    for item in (_format_horizon(profile), _format_focus(profile)):
        if item:
            parts.append(item)
    if profile.get("already_knows_price") is not None:
        parts.append(f"已看过行情={'是' if profile['already_knows_price'] else '否'}")
    return "；".join(parts)


_DISCLAIMER = "该档案只影响阅读顺序与补证优先级，不隐藏反证、关键缺口与风险。"


def _profile_segments(profile: dict[str, Any]) -> list[str]:
    """档案的展示段落（MD 与 HTML 共用同一来源，避免两处措辞漂移）。"""
    segments: list[str] = []
    summary = _format_summary(profile)
    if summary:
        segments.append(f"**研究档案（--profile）：** {summary}。{_DISCLAIMER}")
    goal = profile.get("report_goal")
    if goal:
        segments.append(f"**研究目标：** {goal}")
    prefs = profile.get("evidence_preferences")
    if prefs:
        segments.append(f"**证据偏好：** {prefs}")
    return segments


def format_profile_markdown_lines(profile: dict[str, Any] | None) -> list[str]:
    """full 模式头部展示行；未提供档案时返回空列表（零 diff）。"""
    if not profile:
        return []
    return [f"> {segment}" for segment in _profile_segments(profile)]


def format_profile_html(profile: dict[str, Any] | None) -> str:
    """HTML 状态卡内的档案段；未提供档案时返回空串（零 diff）。

    自由文本来自用户输入，必须转义后再入 HTML。
    """
    if not profile:
        return ""
    segments = _profile_segments(profile)
    if not segments:
        return ""
    body = "<br>".join(
        # `**…**` 是 MD 强调标记，HTML 侧转成 <strong>（先转义再替换，避免
        # 用户文本里的 `<` 被当成标签）。
        _html_escape(segment).replace("**", "<strong>", 1).replace("**", "</strong>", 1)
        for segment in segments
    )
    return (
        f"<p style=\"margin:8px 0 0;color:var(--tx-m);font-size:var(--text-sm)\">{body}</p>"
    )


def write_profile_sidecar(
    report_path: Path,
    profile: dict[str, Any] | None,
    generation: dict[str, str] | None = None,
) -> Path | None:
    """原子写同代侧车 ``<report>.profile.json``；无档案时返回 None。

    ``generation``（``resolve_mode`` 的产物）作为**独立顶层块**落盘，不并入
    ``profile``：``profile`` 的语义是用户研究档案（风格/周期/焦点/目标），
    生成参数属于产物溯源，混入会污染该字段的读法。未提供时整个键省略——
    caller 没传就不写，避免落一个编造的默认值。

    与 `invest.py:_write_analysis_sidecar` 同款：临时文件与目标同目录，
    ``replace`` 在同一文件系统内为原子替换，中断时不留半截 JSON。
    """
    if not profile:
        return None
    sidecar = report_path.with_suffix(".profile.json")
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": "cli:--profile",
        "recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    }
    if generation:
        body["generation"] = generation
    body["profile"] = profile
    payload = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{sidecar.name}.", suffix=".tmp", dir=str(sidecar.parent), text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(sidecar)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return sidecar
