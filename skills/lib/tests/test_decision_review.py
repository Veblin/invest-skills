"""复盘对照纯函数测试（离线）。

设计：`host-docs/v0.3.0/review-material-design.md` D3。
验收要点（execution-plan §5 第 4 项）：**到期清单可机器核验**。
合规（LAW 6）：复盘纪要**只对照假设状态**，不产生建议。
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from decision_review import (  # noqa: E402
    DUE_SOON_DAYS,
    collect_sidecars,
    falsifier_rows,
    render_review,
)

_TODAY = _dt.date(2026, 9, 11)


def _payload(ts: str, falsifiers: list[dict], scenarios: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1, "symbol": "515050", "report_ts": ts, "as_of": "2026-09-10",
        "scenarios": scenarios or [], "falsifiers": falsifiers, "playbook": {},
        "disclaimer": "多情景参考价基于上述假设，仅供参考，不构成投资建议",
    }


# ── 到期清单：可机器核验 ─────────────────────────────────────────────────

def test_falsifier_buckets_and_sorting():
    rows = falsifier_rows({
        "falsifiers": [
            {"condition": "未来到期", "due": "2026-12-31", "status": "open"},
            {"condition": "已过期未标记", "due": "2026-09-01", "status": "open"},
            {"condition": "临近到期", "due": "2026-09-20", "status": "open"},   # 9 天后
            {"condition": "已触发", "due": "2026-10-01", "status": "triggered"},
        ]}, today=_TODAY)
    assert [r["condition"] for r in rows] == ["已过期未标记", "临近到期", "已触发", "未来到期"], \
        "须按 due 升序（到期清单即按此核验）"
    buckets = {r["condition"]: r["bucket"] for r in rows}
    assert buckets["已过期未标记"] == "expired"
    assert buckets["临近到期"] == "due_soon"
    assert buckets["未来到期"] == "open"


def test_due_soon_boundary_is_inclusive():
    at_boundary = (_TODAY + _dt.timedelta(days=DUE_SOON_DAYS)).isoformat()
    rows = falsifier_rows({"falsifiers": [
        {"condition": "边界", "due": at_boundary, "status": "open"}]}, today=_TODAY)
    assert rows[0]["bucket"] == "due_soon"
    beyond = (_TODAY + _dt.timedelta(days=DUE_SOON_DAYS + 1)).isoformat()
    rows2 = falsifier_rows({"falsifiers": [
        {"condition": "边界外", "due": beyond, "status": "open"}]}, today=_TODAY)
    assert rows2[0]["bucket"] == "open"


def test_falsifier_days_left_signed():
    rows = falsifier_rows({"falsifiers": [
        {"condition": "过期", "due": "2026-09-10", "status": "open"}]}, today=_TODAY)
    assert rows[0]["days_left"] == -1


def test_expired_by_due_even_if_status_triggered():
    """`due` 已过但状态仍是 open → 必须落进 expired（这才是要人回看的那批）。"""
    rows = falsifier_rows({"falsifiers": [
        {"condition": "漏标", "due": "2026-08-01", "status": "open"}]}, today=_TODAY)
    assert rows[0]["bucket"] == "expired"
    rows2 = falsifier_rows({"falsifiers": [
        {"condition": "已标记触发", "due": "2026-08-01", "status": "triggered"}]},
        today=_TODAY)
    assert rows2[0]["bucket"] == "expired"


# ── sidecar 收集：缺失/损坏须显式 ────────────────────────────────────────

def test_collect_sidecars_reports_missing_and_corrupt(tmp_path: Path):
    d = tmp_path / "reports" / "515050-测试ETF"
    d.mkdir(parents=True)
    (d / "2026-09-01-10-00-00.md").write_text("# 旧报告\n", encoding="utf-8")
    (d / "2026-09-10-10-00-00.md").write_text("# 新报告\n", encoding="utf-8")
    (d / "2026-09-10-10-00-00.decision.json").write_text(
        json.dumps(_payload("2026-09-10-10-00-00", []), ensure_ascii=False),
        encoding="utf-8")
    (d / "2026-09-05-10-00-00.decision.json").write_text("{坏 JSON", encoding="utf-8")

    out = collect_sidecars(d)
    by_ts = {o["ts"]: o for o in out}
    # 每份报告 md 都要有对应条目（无 sidecar 的显式标 missing，不静默消失）
    assert set(by_ts) == {"2026-09-01-10-00-00", "2026-09-05-10-00-00",
                          "2026-09-10-10-00-00"}
    assert by_ts["2026-09-01-10-00-00"]["payload"] is None
    assert by_ts["2026-09-01-10-00-00"]["error"], "无 sidecar 须给原因"
    assert by_ts["2026-09-05-10-00-00"]["payload"] is None
    assert by_ts["2026-09-05-10-00-00"]["error"]
    assert by_ts["2026-09-10-10-00-00"]["payload"] is not None


# ── 渲染：三段 + 合规 ────────────────────────────────────────────────────

def test_render_has_three_sections_and_no_advice():
    md = render_review(
        "515050",
        sidecars=[
            {"ts": "2026-09-01-10-00-00", "payload": None, "error": "无 sidecar"},
            {"ts": "2026-09-10-22-50-00",
             "payload": _payload("2026-09-10-22-50-00", [
                 {"condition": "份额持续净流出", "due": "2026-09-20", "status": "open"}]),
             "error": None},
        ],
        today=_TODAY,
    )
    assert "报告序列" in md
    assert "证伪条件" in md
    assert "假设对照" in md
    # 无 sidecar 的旧报告须显式列出，不静默跳过
    assert "2026-09-01-10-00-00" in md
    assert "无复盘原料" in md or "无 sidecar" in md
    # 到期清单可核验：临近到期的条件与其到期日必须在
    assert "份额持续净流出" in md and "2026-09-20" in md
    # 合规：不得出现建议性措辞
    for banned in ("建议买入", "建议卖出", "应当加仓", "应当减仓", "建议持有"):
        assert banned not in md


def test_render_states_when_no_sidecar_at_all():
    md = render_review("515050", sidecars=[], today=_TODAY)
    assert "无复盘原料" in md or "无 sidecar" in md


# ── 侧车「校验失败」不得与「文件缺失」混为一态 ──────────────────────────
#
# collect_sidecars 对两者给的是**不同**的 error 文本（缺失 = 无复盘原料；
# 校验失败 = schema 报错原文），但渲染层只写死「❌ 无复盘原料」，且脚注把两种
# 情况一律归因为「早于 sidecar 协议或未落盘」——文件在、内容不合规的侧车，
# 读者既看不到原因，也不知道该修什么。

def test_invalid_sidecar_error_text_is_rendered_in_sequence_table():
    md = render_review(
        "515050",
        sidecars=[{"ts": "2026-09-10-22-50-00", "payload": None,
                   "kind": "invalid",
                   "error": "scenarios 权重之和须为 1（当前 1.1，容差 1e-06）"}],
        today=_TODAY,
    )
    row = next(l for l in md.splitlines()
               if l.startswith("|") and "2026-09-10-22-50-00" in l)
    assert "权重之和须为 1" in row, f"校验失败原因未进表格：{row}"


def test_footnote_separates_missing_from_invalid():
    md = render_review(
        "515050",
        sidecars=[
            {"ts": "OLD", "payload": None, "kind": "missing",
             "error": "无复盘原料（该报告早于 sidecar 协议，或未落盘）"},
            {"ts": "BAD", "payload": None, "kind": "invalid",
             "error": "scenarios 权重之和须为 1（当前 1.1）"},
        ],
        today=_TODAY,
    )
    assert "早于 sidecar 协议" in md, "缺失类仍须说明协议/未落盘"
    assert "权重之和" in md, "校验失败类须给出原因，不得归因为协议问题"


def test_basis_section_distinguishes_unusable_from_no_falsifier():
    """② 节的空态须区分「原料不可用」与「原料可用但未写证伪条件」。"""
    unusable = render_review("515050", sidecars=[
        {"ts": "T1", "payload": None, "kind": "invalid", "error": "权重之和须为 1"},
    ], today=_TODAY)
    assert "原料不可用" in unusable

    no_falsifier = render_review("515050", sidecars=[
        {"ts": "T2", "payload": _payload("T2", []), "error": None, "kind": "ok"},
    ], today=_TODAY)
    assert "未写证伪条件" in no_falsifier
