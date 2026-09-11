"""复盘原料 sidecar（decision.json）schema 校验器测试（离线）。

设计依据：`host-docs/v0.3.0/review-material-design.md` D1/D2（2026-09-10 用户批准）。
核心约束（LAW 6）：多情景参考价**必须**带假设前提 + 概率权重 + 免责声明；
不允许无假设的单一目标价。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from decision_schema import (  # noqa: E402
    DecisionSchemaError,
    load_decision_json,
    minimal_decision,
    validate_decision,
)


def _full() -> dict:
    return {
        "schema_version": 1,
        "symbol": "515050",
        "report_ts": "2026-09-10-22-50-00",
        "as_of": "2026-09-10",
        "scenarios": [
            {"key": "optimistic", "assumption": "AI 资本开支指引不下修",
             "weight": 0.3, "valuation_ref": 1.23},
            {"key": "neutral", "assumption": "指引持平", "weight": 0.5,
             "valuation_ref": 1.05},
            {"key": "pessimistic", "assumption": "指引下修", "weight": 0.2,
             "valuation_ref": 0.88},
        ],
        "falsifiers": [
            {"condition": "季度份额流转为持续净流出", "observe_from": "2026-10-01",
             "due": "2026-12-31", "status": "open", "evidence_ref": None},
        ],
        "playbook": {"drawdown_levels": [], "checklist": []},
        "disclaimer": "多情景参考价基于上述假设，仅供参考，不构成投资建议",
    }


# ── 合法路径 ─────────────────────────────────────────────────────────────

def test_full_payload_passes():
    assert validate_decision(_full()) == []


def test_minimal_payload_passes():
    """无假设/预案时只落最小 schema（缺省段为空）——否则「有/无 sidecar」不可机器区分。"""
    minimal = minimal_decision(symbol="515050", report_ts="2026-09-10-22-50-00",
                               as_of="2026-09-10")
    assert validate_decision(minimal) == []
    assert minimal["scenarios"] == [] and minimal["falsifiers"] == []
    assert minimal["disclaimer"]


# ── LAW 6：多情景三要素缺一不可 ──────────────────────────────────────────

def test_scenario_without_assumption_rejected():
    """多情景参考价必须带**假设前提**（不允许无假设的单一目标价）。"""
    bad = _full()
    del bad["scenarios"][0]["assumption"]
    assert any("assumption" in e for e in validate_decision(bad))


def test_scenario_with_blank_assumption_rejected():
    bad = _full()
    bad["scenarios"][0]["assumption"] = "   "
    assert any("assumption" in e for e in validate_decision(bad))


def test_scenario_without_weight_rejected():
    bad = _full()
    del bad["scenarios"][0]["weight"]
    assert any("weight" in e for e in validate_decision(bad))


def test_scenario_without_valuation_ref_rejected():
    bad = _full()
    del bad["scenarios"][1]["valuation_ref"]
    assert any("valuation_ref" in e for e in validate_decision(bad))


def test_unknown_scenario_key_rejected():
    bad = _full()
    bad["scenarios"][0]["key"] = "target_price"
    assert any("key" in e for e in validate_decision(bad))


def test_weight_out_of_range_rejected():
    bad = _full()
    bad["scenarios"][0]["weight"] = 1.5
    assert any("weight" in e for e in validate_decision(bad))


def test_disclaimer_required():
    bad = _full()
    del bad["disclaimer"]
    assert any("disclaimer" in e for e in validate_decision(bad))


# ── 证伪条件：到期清单须可机器核验 ───────────────────────────────────────

def test_falsifier_without_due_rejected():
    """`due` 是 review「到期清单可机器核验」的基石，不得缺。"""
    bad = _full()
    del bad["falsifiers"][0]["due"]
    assert any("due" in e for e in validate_decision(bad))


def test_falsifier_bad_due_format_rejected():
    bad = _full()
    bad["falsifiers"][0]["due"] = "2026/12/31"
    assert any("due" in e for e in validate_decision(bad))


def test_falsifier_unknown_status_rejected():
    bad = _full()
    bad["falsifiers"][0]["status"] = "maybe"
    assert any("status" in e for e in validate_decision(bad))


def test_falsifier_without_condition_rejected():
    bad = _full()
    bad["falsifiers"][0]["condition"] = ""
    assert any("condition" in e for e in validate_decision(bad))


# ── 顶层结构 ─────────────────────────────────────────────────────────────

def test_missing_required_toplevel_keys_rejected():
    for key in ("schema_version", "symbol", "report_ts", "as_of"):
        bad = _full()
        del bad[key]
        assert any(key in e for e in validate_decision(bad)), f"缺 {key} 未报错"


def test_wrong_type_rejected():
    assert any("顶层" in e for e in validate_decision(["not", "a", "dict"]))
    bad = _full()
    bad["scenarios"] = {"not": "a list"}
    assert any("scenarios" in e for e in validate_decision(bad))


def test_validate_raises_helper_is_fail_loud(tmp_path: Path):
    """load_decision_json 须 fail-loud（对齐 analysis.json 的校验语义）。"""
    p = tmp_path / "bad.decision.json"
    p.write_text(json.dumps({"symbol": "515050"}), encoding="utf-8")
    with pytest.raises(DecisionSchemaError):
        load_decision_json(p)


def test_load_decision_json_ok(tmp_path: Path):
    p = tmp_path / "ok.decision.json"
    p.write_text(json.dumps(_full(), ensure_ascii=False), encoding="utf-8")
    assert load_decision_json(p)["symbol"] == "515050"


def test_load_decision_json_missing_file_is_fail_loud(tmp_path: Path):
    with pytest.raises(DecisionSchemaError):
        load_decision_json(tmp_path / "nope.json")
