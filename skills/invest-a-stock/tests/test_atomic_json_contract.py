"""A05-1 R2：原子 JSON 写入契约（底层收敛 + 三处调用方逐字节一致）。

对应用户批准卡反例：
- 应通过：三侧车写入成功后内容与旧实现逐字节一致；
- 应失败：os.replace 抛错 → 旧文件保留 + 临时文件清理（三处分别断言），
  不留半截 JSON。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib import insight_model, json_util, research_profile
from lib.insight_model import _atomic_json
from lib.json_util import atomic_write_json, atomic_write_text
from lib.research_profile import write_profile_sidecar


def _boom_replace(*a, **k):
    raise OSError("replace failed (test)")


def _tmp_leftovers(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]


def test_atomic_write_text_bytes(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    atomic_write_text(target, "world\n")  # 覆盖写
    assert target.read_text(encoding="utf-8") == "world\n"
    assert _tmp_leftovers(tmp_path) == []


def test_atomic_write_json_matches_legacy_bytes(tmp_path: Path) -> None:
    payload = [{"b": 1, "中文": "值"}, {"a": None}]
    target = tmp_path / "x.json"
    atomic_write_json(target, payload)
    expected = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    assert target.read_text(encoding="utf-8") == expected


def test_atomic_write_failure_preserves_old_and_cleans_temp(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "x.json"
    atomic_write_json(target, {"v": 1})
    before = target.read_bytes()
    monkeypatch.setattr(os, "replace", _boom_replace)
    with pytest.raises(OSError):
        atomic_write_json(target, {"v": 2})
    assert target.read_bytes() == before, "写入失败不得覆盖旧文件"
    assert _tmp_leftovers(tmp_path) == [], "失败须清理临时文件（不留半截 JSON）"


def test_analysis_sidecar_bytes_and_failure(tmp_path: Path, monkeypatch) -> None:
    import invest

    report = tmp_path / "2026-10-08-00-00-00.md"
    report.write_text("body", encoding="utf-8")
    payload = [{"section": "结论", "text": "分析"}]
    sidecar = invest._write_analysis_sidecar(report, payload)
    assert sidecar == report.with_suffix(".analysis.json")
    expected = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    assert sidecar.read_text(encoding="utf-8") == expected

    monkeypatch.setattr(os, "replace", _boom_replace)
    with pytest.raises(OSError):
        invest._write_analysis_sidecar(report, [{"section": "结论", "text": "改"}])
    assert sidecar.read_text(encoding="utf-8") == expected
    assert _tmp_leftovers(tmp_path) == []


def test_profile_sidecar_bytes_and_failure(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "2026-10-08-00-00-00.md"
    report.write_text("body", encoding="utf-8")
    profile = {"style": "价值", "horizon": "long_term"}
    sidecar = write_profile_sidecar(report, profile)
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == research_profile.SCHEMA_VERSION
    assert data["source"] == "cli"
    assert data["profile"] == profile
    before = sidecar.read_bytes()

    monkeypatch.setattr(os, "replace", _boom_replace)
    with pytest.raises(OSError):
        write_profile_sidecar(report, {"style": "成长"})
    assert sidecar.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_insight_atomic_json_bytes_and_failure(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "sub" / "facts.json"  # 需 mkdir（本函数历史行为）
    payload = {"facts": [1, 2], "gaps": []}
    _atomic_json(target, payload)
    expected = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    assert target.read_text(encoding="utf-8") == expected

    monkeypatch.setattr(os, "replace", _boom_replace)
    with pytest.raises(OSError):
        _atomic_json(target, {"facts": []})
    assert target.read_text(encoding="utf-8") == expected
    assert _tmp_leftovers(target.parent) == []


def test_no_duplicate_atomic_implementations() -> None:
    """三处调用方不再各自持有 mkstemp 原子写实现（收敛检查）。"""
    stock = Path(__file__).resolve().parent.parent / "scripts"
    for rel in ("invest.py", "lib/research_profile.py", "lib/insight_model.py"):
        text = (stock / rel).read_text(encoding="utf-8")
        assert "tempfile.mkstemp" not in text, f"{rel} 仍持有第二份原子写实现"
    assert callable(json_util.atomic_write_text)
    assert callable(insight_model._atomic_json)
