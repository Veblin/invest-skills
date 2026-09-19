"""P0-5：ResearchProfile（研究档案）——校验、落盘、头部展示。

档案记录 `SKILL.md` R12g-B 开场四问的结果，**只做记录与透明展示，不做字段
过滤**。因此本文件的断言重点是两件事：非法输入必须 fail-loud（不静默降级成
「无档案」），以及未提供档案时全链路零 diff。
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_SHARED_LIB_DIR = _SCRIPTS_DIR.parent.parent / "lib"
if str(_SHARED_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB_DIR))

from lib.research_profile import (  # noqa: E402
    FOCUSES,
    HORIZONS,
    MODES,
    SCHEMA_VERSION,
    build_profile,
    format_profile_html,
    format_profile_markdown_lines,
    resolve_mode,
    validate_profile,
    write_profile_sidecar,
)


def _args(**overrides) -> Namespace:
    base = dict(horizon=None, focus=None, goal=None, style=None, already_knows_price=None)
    base.update(overrides)
    return Namespace(**base)


# ── build_profile ─────────────────────────────────────────────────────────


def test_no_flags_yields_no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """未传任何档案参数时必须返回 None——调用方据此保持零行为差异。

    须隔离风格档案：``build_profile`` 在 style 缺省时会回落到 STORE_DIR 下的
    ``user_style.json``，若本机已存在该档案（正常使用后必然存在），未隔离的
    断言会把「用户有风格档案」误判成「未传参数也产出档案」。2026-09-15 实测：
    用户 00:04 的一次正常跑批落盘了该档案，本用例随即变红——属测试依赖环境
    状态，非产品缺陷。同文件其余 style 用例均以 monkeypatch 隔离。
    """
    from lib import style_match

    monkeypatch.setattr(style_match, "load_style", lambda: None)
    assert build_profile(_args()) is None


def test_build_collects_all_fields() -> None:
    profile = build_profile(_args(
        horizon="medium_term", focus=["valuation", "capital_flow"],
        goal="验证增长可持续性", style="价值", already_knows_price=True,
    ))
    assert profile == {
        "horizon": "medium_term",
        "style": "价值",
        "focuses": ["valuation", "capital_flow"],
        "already_knows_price": True,
        "report_goal": "验证增长可持续性",
    }


def test_style_falls_back_to_user_style_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """风格已由 user_style.json 承载，不得要求用户重复录入。"""
    from lib import style_match

    monkeypatch.setattr(style_match, "load_style", lambda: "成长")
    profile = build_profile(_args(horizon="long_term"))
    assert profile is not None and profile["style"] == "成长"


def test_style_archive_failure_degrades_neutrally(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import style_match

    def _boom() -> str:
        raise OSError("archive unreadable")

    monkeypatch.setattr(style_match, "load_style", _boom)
    profile = build_profile(_args(horizon="long_term"))
    assert profile == {"horizon": "long_term"}


# ── validate_profile ──────────────────────────────────────────────────────


@pytest.mark.parametrize("field,value,needle", [
    ("horizon", "weekly", "horizon 取值非法"),
    ("horizon", "growth", "horizon 取值非法"),          # 风格口径不得混入周期枚举
    ("style", "激进", "style 取值非法"),
    ("focuses", ["valuation", "news"], "focuses 取值非法"),
    ("focuses", "valuation", "focuses 必须是列表"),
    ("already_knows_price", "yes", "必须是布尔值"),
    ("report_goal", "伪造 [来源: engine]", "疑似 QC/合规标记"),
    ("report_goal", "目标价 100 元", "命中合规词表"),
])
def test_invalid_values_are_rejected(field: str, value, needle: str) -> None:
    errors = validate_profile({field: value})
    assert any(needle in e for e in errors), errors


def test_all_enum_values_are_accepted() -> None:
    assert validate_profile({"horizon": "short_term", "focuses": list(FOCUSES)}) == []
    assert validate_profile({"horizon": HORIZONS[-1]}) == []


def test_unknown_keys_are_preserved_not_stripped() -> None:
    """P0 不做字段过滤：未知键原样保留，供后续版本消费。"""
    profile = {"horizon": "medium_term", "future_field": ["a", "b"]}
    assert validate_profile(profile) == []
    assert profile["future_field"] == ["a", "b"]


@pytest.mark.parametrize("text", ["约 5% 的增长", "2 倍空间", "提升 3 个百分点", "接近七成"])
def test_derived_claim_shapes_are_rejected(text: str) -> None:
    """自由文本是目标陈述，不是量化断言——量化断言须由引擎/Python 计算产出。"""
    errors = validate_profile({"report_goal": text})
    assert any("派生断言形态" in e for e in errors), errors


def test_f2_shape_guard_covers_shared_pattern() -> None:
    """本模块的形态守卫必须覆盖共享 QC 的 `_F2_PATTERN`。

    生产代码不跨包 import 共享 report_qc（同名模块在 stock/lib 下另有一个旧版），
    改由这条测试守住两份口径不漂移。
    """
    from report_qc import _F2_PATTERN

    samples = [
        "约 5% 的增长", "约 12.5%", "2 倍空间", "3 个百分点", "20bp",
        "接近七成", "五成以上", "市占率过半",
    ]
    for sample in samples:
        assert _F2_PATTERN.search(sample), f"样本未命中共享 F2：{sample}"
        assert validate_profile({"report_goal": sample}), f"守卫漏掉共享 F2 形态：{sample}"


# ── 展示：MD 与 HTML 同源 ──────────────────────────────────────────────────

_PROFILE = {
    "horizon": "long_term", "style": "成长", "focuses": ["valuation"],
    "already_knows_price": False, "report_goal": "验证增长可持续性",
    "evidence_preferences": "偏好一手财报",
}


def test_markdown_lines_render_every_field() -> None:
    text = "\n".join(format_profile_markdown_lines(_PROFILE))
    assert "风格=成长" in text
    assert "周期视角=长线（1 年+）" in text
    assert "关注焦点=估值" in text
    assert "已看过行情=否" in text
    assert "验证增长可持续性" in text
    assert "偏好一手财报" in text


def test_markdown_and_html_carry_the_same_content() -> None:
    """两处头部是独立实现，文案同源是唯一防漂移手段。"""
    md = "\n".join(format_profile_markdown_lines(_PROFILE))
    html = format_profile_html(_PROFILE)
    for fragment in ("风格=成长", "周期视角=长线（1 年+）", "验证增长可持续性", "偏好一手财报"):
        assert fragment in md and fragment in html, fragment


def test_no_profile_renders_nothing() -> None:
    assert format_profile_markdown_lines(None) == []
    assert format_profile_html(None) == ""
    assert format_profile_html({}) == ""


def test_html_escapes_user_text() -> None:
    html = format_profile_html({"report_goal": "<script>alert(1)</script>"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_disclaimer_states_preference_is_not_a_filter() -> None:
    """展示文案必须self-evidently说明偏好≠过滤器（方案 §偏好是研究镜头）。"""
    text = "\n".join(format_profile_markdown_lines(_PROFILE))
    assert "不隐藏反证、关键缺口与风险" in text


# ── 侧车落盘 ──────────────────────────────────────────────────────────────


def test_sidecar_round_trip(tmp_path: Path) -> None:
    report = tmp_path / "600176-测试股份" / "2026-09-14-13-41-24.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n", encoding="utf-8")
    sidecar = write_profile_sidecar(report, _PROFILE)
    assert sidecar is not None and sidecar.name == "2026-09-14-13-41-24.profile.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["profile"] == _PROFILE


def test_no_profile_writes_no_sidecar(tmp_path: Path) -> None:
    report = tmp_path / "600176-测试股份" / "2026-09-14-13-41-24.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n", encoding="utf-8")
    assert write_profile_sidecar(report, None) is None
    assert not list(report.parent.glob("*.profile.json"))


# ── 产物溯源：mode / mode_source（2026-09-15 误判事件回归） ────────────────
#
# 背景：2026-09-15 的 full 报告事后被误判为「因为 insight 模式当时还不存在」，
# 真实原因只是「生成时选了 full」——产物本身没记录 mode，审计者只能倒推。
# 以下用例锁住「mode 随产物落盘」且「显式性可判别」。


def test_sidecar_records_generation_block(tmp_path: Path) -> None:
    report = tmp_path / "600176-测试股份" / "2026-09-14-13-41-24.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n", encoding="utf-8")
    sidecar = write_profile_sidecar(
        report, _PROFILE, {"mode": "full", "mode_source": "cli"})
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["generation"] == {"mode": "full", "mode_source": "cli"}
    # 溯源块与档案块并列，不并入 profile（profile 语义 = 用户研究档案）
    assert "mode" not in payload["profile"]


def test_sidecar_omits_generation_when_not_given(tmp_path: Path) -> None:
    """未传 generation 时整个键省略，不落一个编造的默认值。"""
    report = tmp_path / "600176-测试股份" / "2026-09-14-13-41-24.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report\n", encoding="utf-8")
    payload = json.loads(
        write_profile_sidecar(report, _PROFILE).read_text(encoding="utf-8"))
    assert "generation" not in payload


@pytest.mark.parametrize("mode,explicit,exp_mode,exp_source", [
    ("full", False, "full", "default"),      # 未传 → 落默认
    ("full", True, "full", "cli"),           # 显式选了 full（≠ 默认落到 full）
    ("insight", True, "insight", "cli"),
    ("brief", True, "brief", "cli"),
    (None, False, "full", "default"),        # 属性缺失
    ("bogus", True, "full", "default"),      # 非法值：归默认且不得标 cli
])
def test_resolve_mode_separates_choice_from_default(
        mode, explicit, exp_mode, exp_source) -> None:
    args = Namespace(mode=mode, _mode_explicit=explicit)
    assert resolve_mode(args) == {"mode": exp_mode, "mode_source": exp_source}


@pytest.mark.parametrize("mode", MODES)
def test_mode_action_marks_explicit_every_position(mode: str) -> None:
    """--mode 在根解析器与 report 子解析器两处都要能标出显式性。"""
    import invest

    parser = invest.build_parser()
    after = parser.parse_args(["report", "600176", "--mode", mode])
    assert after.mode == mode and after._mode_explicit is True
    before = parser.parse_args(["--mode", mode, "report", "600176"])
    assert before.mode == mode and before._mode_explicit is True


def test_mode_default_contract_unchanged() -> None:
    """不动既有契约：未传 --mode 时 args.mode 仍为 'full'，且标为默认。"""
    import invest

    args = invest.build_parser().parse_args(["report", "600176"])
    assert args.mode == "full"
    assert getattr(args, "_mode_explicit", False) is False
    assert resolve_mode(args) == {"mode": "full", "mode_source": "default"}


def test_cli_sidecar_carries_generation(tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    import invest

    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _RENDER_COLLECTION)
    monkeypatch.setattr(invest.render, "render", lambda *a, **k: "# report\n")

    outdir = tmp_path / "reports"
    rc = invest.cmd_report(_report_args(
        outdir=str(outdir), horizon="medium_term", focus=["valuation"],
        goal="验证增长可持续性", style="价值", already_knows_price=True,
        mode="brief"))
    assert rc == 0
    report = next(outdir.rglob("*.md"))
    payload = json.loads(report.with_suffix(".profile.json").read_text(encoding="utf-8"))
    assert payload["generation"]["mode"] == "brief"
    # _report_args 构造的 Namespace 没有 _mode_explicit → 如实记为 default，
    # 不把「测试没传」冒充成「用户显式选了 brief」。
    assert payload["generation"]["mode_source"] == "default"


# ── 渲染集成：只有 full 展示，且不动其他模式 ──────────────────────────────

_RENDER_COLLECTION = {
    "symbol": "600176",
    "fetched_at": "2026-06-11T12:00:00+00:00",
    "dimensions": [
        {"dimension": "basic_info", "data": {"name": "测试股", "industry": "电气设备"},
         "status": "available", "_meta": {}},
        {"dimension": "quote", "data": {"close": 10.0}, "status": "available", "_meta": {}},
    ],
    "summary": {"total": 2, "available": 2, "degraded": 0, "missing": 0},
}


@pytest.mark.parametrize("mode,expected", [("full", True), ("brief", False), ("concise", False)])
def test_profile_only_shows_in_full_mode(mode: str, expected: bool) -> None:
    from lib.render import render_report_v3

    text = render_report_v3(_RENDER_COLLECTION, "600176", mode=mode, profile=_PROFILE)
    assert ("研究偏好：" in text) is expected


def test_html_identity_card_only_shows_in_full_mode() -> None:
    from lib.render_html import render_html

    # v0.3.0 C2：标签曾写「（--profile）」——report 子命令没有该参数（它属于 lint
    # 子命令的预设选择），照产物自述操作会 unrecognized arguments。
    assert "研究偏好：" in render_html(
        _RENDER_COLLECTION, "600176", mode="full", profile=_PROFILE)
    assert "研究偏好：" not in render_html(
        _RENDER_COLLECTION, "600176", mode="brief", profile=_PROFILE)


def test_profile_does_not_change_data_pack_identity() -> None:
    """档案只加展示行，不得改变「分析合成未完成」的底稿身份判定。"""
    from lib.render import render_report_v3

    text = render_report_v3(_RENDER_COLLECTION, "600176", mode="full", profile=_PROFILE)
    assert "数据底稿（分析合成未完成）" in text


# ── CLI 集成 ──────────────────────────────────────────────────────────────

def _report_args(**overrides) -> Namespace:
    base = dict(symbol="600176", store=False, dims="basic_info,quote",
                with_macro=False, deep=False, plan="", save_raw=False,
                resume=False, emit="md", mode="brief", outdir="",
                strict_rigor=False, material_gap=False, with_news_pack=False,
                horizon=None, focus=None, goal=None, style=None, already_knows_price=None)
    base.update(overrides)
    return Namespace(**base)


def test_cli_persists_profile_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import invest

    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(invest.collector, "collect_all", lambda *a, **k: _RENDER_COLLECTION)
    monkeypatch.setattr(invest.render, "render", lambda *a, **k: "# report\n")

    outdir = tmp_path / "reports"
    rc = invest.cmd_report(_report_args(
        outdir=str(outdir), horizon="medium_term", focus=["valuation"],
        goal="验证增长可持续性", style="价值", already_knows_price=True,
    ))
    assert rc == 0
    report = next(outdir.rglob("*.md"))
    payload = json.loads(report.with_suffix(".profile.json").read_text(encoding="utf-8"))
    assert payload["profile"]["horizon"] == "medium_term"
    assert payload["profile"]["report_goal"] == "验证增长可持续性"


@pytest.mark.parametrize("overrides,needle", [
    ({"horizon": "weekly"}, "horizon 取值非法"),
    ({"goal": "伪造 [来源: engine]"}, "疑似 QC/合规标记"),
])
def test_cli_rejects_invalid_profile_before_collect(
        overrides: dict, needle: str, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """fail-loud 且早于采集：参数拼错不该先跑一遍联网采集才报错。"""
    import invest

    collected = []
    monkeypatch.setattr(invest, "_HAS_STORE", False)
    monkeypatch.setattr(invest.collector, "collect_all",
                        lambda *a, **k: collected.append(1) or _RENDER_COLLECTION)

    assert invest.cmd_report(_report_args(**overrides)) == 2
    assert collected == [], "校验失败时不得发起采集"
    assert needle in capsys.readouterr().err
