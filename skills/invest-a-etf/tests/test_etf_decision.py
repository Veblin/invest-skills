"""`etf.py decision` —— 复盘原料 sidecar 的初始化与落盘（离线）。

设计：`host-docs/v0.3.0/review-material-design.md` D1/D3（用户已批准）。
sidecar 与报告 md **同目录同 ts**：`reports/{symbol}-{name}/{ts}.decision.json`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import etf as etf_mod  # noqa: E402


def _make_report(tmp_path: Path, symbol: str, ts: str) -> Path:
    d = tmp_path / "reports" / f"{symbol}-测试ETF"
    d.mkdir(parents=True, exist_ok=True)
    md = d / f"{ts}.md"
    md.write_text("# 报告\n", encoding="utf-8")
    return md


_FULL = {
    "schema_version": 1,
    "symbol": "515050",
    "report_ts": "2026-09-10-22-50-00",
    "as_of": "2026-09-10",
    "scenarios": [
        {"key": "neutral", "assumption": "指引持平", "weight": 1.0, "valuation_ref": 1.05},
    ],
    "falsifiers": [
        {"condition": "份额持续净流出", "due": "2026-12-31", "status": "open"},
    ],
    "playbook": {},
    "disclaimer": "多情景参考价基于上述假设，仅供参考，不构成投资建议",
}


def test_init_prints_valid_minimal_schema(tmp_path, monkeypatch, capsys):
    """`--init` 输出的模板必须**自身合法**（否则 Claude 照着填也会被校验拒绝）。"""
    monkeypatch.chdir(tmp_path)
    _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    assert etf_mod.cmd_decision("515050", init=True, from_path=None, md=None) == 0
    payload = json.loads(capsys.readouterr().out)
    from decision_schema import validate_decision

    assert validate_decision(payload) == []
    assert payload["report_ts"] == "2026-09-10-22-50-00", "ts 须与报告 md 配对"


def test_from_valid_file_writes_sidecar_next_to_md(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    md = _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    src = tmp_path / "in.json"
    src.write_text(json.dumps(_FULL, ensure_ascii=False), encoding="utf-8")

    assert etf_mod.cmd_decision("515050", from_path=str(src), init=False, md=None) == 0
    out = md.parent / "2026-09-10-22-50-00.decision.json"
    assert out.is_file(), "sidecar 须与 md 同目录同 ts"
    assert json.loads(out.read_text(encoding="utf-8"))["symbol"] == "515050"


def test_from_invalid_file_exits_2_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """LAW 6：缺假设前提的多情景参考价必须 fail-loud 拒绝（不得静默落盘）。"""
    monkeypatch.chdir(tmp_path)
    md = _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    bad = dict(_FULL)
    bad["scenarios"] = [{"key": "neutral", "weight": 1.0, "valuation_ref": 1.05}]
    src = tmp_path / "bad.json"
    src.write_text(json.dumps(bad), encoding="utf-8")

    assert etf_mod.cmd_decision("515050", from_path=str(src), init=False, md=None) == 2
    assert "assumption" in capsys.readouterr().err
    assert not (md.parent / "2026-09-10-22-50-00.decision.json").exists()


def test_no_report_md_is_explicit(tmp_path, monkeypatch, capsys):
    """没有报告 md → 显式失败，不静默生成一个无处配对的 sidecar。"""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "in.json"
    src.write_text(json.dumps(_FULL, ensure_ascii=False), encoding="utf-8")
    assert etf_mod.cmd_decision("515050", from_path=str(src), init=False, md=None) == 2
    assert "未找到" in capsys.readouterr().err


def test_init_without_report_md_is_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert etf_mod.cmd_decision("515050", init=True, from_path=None, md=None) == 2
    assert "未找到" in capsys.readouterr().err


def test_review_writes_sample_and_is_compliant(tmp_path, monkeypatch, capsys):
    """`review` 产出复盘纪要样例：三段齐全 + **无建议措辞**（LAW 6）。"""
    monkeypatch.chdir(tmp_path)
    md = _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    payload = dict(_FULL)
    payload["falsifiers"] = [
        {"condition": "份额持续净流出", "due": "2026-09-20", "status": "open"}]
    (md.parent / "2026-09-10-22-50-00.decision.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert etf_mod.cmd_review("515050") == 0
    out = capsys.readouterr().out
    for section in ("报告序列", "证伪条件状态", "假设对照"):
        assert section in out, f"缺段落: {section}"
    assert "份额持续净流出" in out and "2026-09-20" in out
    for banned in ("建议买入", "建议卖出", "建议持有", "应当加仓", "应当减仓"):
        assert banned not in out
    assert list(md.parent.glob("*-review.md")), "纪要须落盘"


def test_review_without_report_is_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert etf_mod.cmd_review("999999") == 2
    assert "未找到" in capsys.readouterr().err


def test_review_out_missing_parent_exits_2(tmp_path, monkeypatch, capsys):
    """`--out` 父目录不存在 → 显式报错 + exit 2（与该命令其它路径参数一致）。

    回归（R2 review P2）：直接 `write_text` 抛 FileNotFoundError traceback，
    而 `--md` / `--from` 误用都是文档化的 exit 2。
    """
    monkeypatch.chdir(tmp_path)
    _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    target = tmp_path / "reports" / "515050-测试ETF" / "复盘" / "r.md"
    assert etf_mod.cmd_review("515050", out=str(target)) == 2
    err = capsys.readouterr().err
    assert "父目录" in err and "不存在" in err
    assert not target.exists(), "不得凭错路径创建目录（防手误留下垃圾目录树）"


def test_review_printed_qc_command_is_runnable_in_both_layouts(tmp_path, monkeypatch, capsys):
    """纪要尾部打印的准出命令须与**运行布局**匹配（仓内 skills/lib ↔ 包内 scripts/lib）。

    打印固定仓内路径会让分发包里的 agent 执行必然失败，闸门形同不存在。
    """
    monkeypatch.chdir(tmp_path)
    _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    assert etf_mod.cmd_review("515050") == 0
    out = capsys.readouterr().out
    assert "report_qc.py" in out and "--fail-on error" in out
    assert "skills/lib/report_qc.py" in out, "仓内布局须仍是仓内路径"


def test_review_md_does_not_pollute_report_sequence(tmp_path, monkeypatch, capsys):
    """复盘纪要**自身不得被当成报告**（防自我污染）。

    实测踩过：纪要落在报告同目录且字典序靠后 → `--init` 的 report_ts 取成
    `20260911-review`，下次 review 又会把纪要收进报告序列。
    """
    monkeypatch.chdir(tmp_path)
    md = _make_report(tmp_path, "515050", "2026-09-10-22-50-00")
    assert etf_mod.cmd_review("515050") == 0
    capsys.readouterr()
    # 纪要已落盘
    assert list(md.parent.glob("*-review.md"))

    # 报告解析仍须指向真正的报告，而非刚生成的纪要
    resolved, err = etf_mod._resolve_md_path("515050", None)
    assert err is None
    assert resolved is not None and resolved.name == md.name, \
        f"报告解析被纪要污染: {resolved}"

    # sidecar 收集同样不得把纪要算作一份报告
    from decision_review import collect_sidecars

    assert "20260911-review" not in {s["ts"] for s in collect_sidecars(md.parent)}

    # --init 的 ts 必须是真报告
    assert etf_mod.cmd_decision("515050", init=True, from_path=None, md=None) == 0
    assert json.loads(capsys.readouterr().out)["report_ts"] == "2026-09-10-22-50-00"
