"""B03-1 折溢价口径 / 盘口缺口 / 对冲映射核验状态 / diagnose 一致性。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import etf_data
from etf_data import _lookup_hedge, _spot_row_to_quote


class TestPremiumAndSpread:
    def test_quote_carries_caliber_and_spread_gap(self):
        q = _spot_row_to_quote("515050", {"最新价": 1.0, "涨跌幅": 1.0,
                                          "成交量": 1, "成交额": 2, "基金折价率": 0.1})
        assert q["premium_discount"] == -0.1
        cal = q["premium_discount_caliber"]
        assert cal["nav_time_point"].startswith("未知")
        assert "不得用隔夜 NAV 确认当日错价" in cal["note"]
        spread = q["bid_ask_spread"]
        assert spread["status"] == "not_available"
        assert "日成交额不得推导价差" in spread["note"]


class TestHedgeVerification:
    def test_588000_futures_claim_marked_unverified(self):
        hc = _lookup_hedge("588000")
        ver = hc["verification"]
        assert ver["status"] == "unverified"
        assert ver["checked_at"] == "2026-10-08"
        assert "cffex.com.cn" in " ".join(ver["attempted_sources"])
        assert "待核" in ver["note"] or "未" in ver["note"]

    def test_regular_entry_static_map_only(self):
        ver = _lookup_hedge("510300")["verification"]
        assert ver["status"] == "static_map_only"

    def test_unknown_code_static_map_only_unknown_coverage(self):
        hc = _lookup_hedge("999999")
        assert hc["coverage"] == "unknown"
        assert hc["verification"]["status"] == "static_map_only"


_ETF_PY = Path(__file__).resolve().parent.parent / "scripts" / "etf.py"


def _load_etf_module():
    name = "etf_cli_tradability_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _ETF_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDiagnoseHedgeConsistency:
    def test_repo_doc_consistent_rc0(self, capsys):
        mod = _load_etf_module()
        assert mod._check_hedge_map_consistency() == 0
        out = capsys.readouterr().out
        assert "code=39 md=39" in out

    def test_tampered_doc_missing_row_rc1(self, tmp_path, capsys, monkeypatch):
        orig = Path(etf_data.__file__).resolve().parent.parent.parent / "references" / "etf-hedge-map.md"
        text = orig.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines()
                 if not ln.startswith("| 510300 ")]
        tampered = tmp_path / "etf-hedge-map.md"
        tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")
        mod = _load_etf_module()
        monkeypatch.setattr("skill_paths.skill_relpath", lambda *a, **k: str(tampered))
        # B06：读文件用 default_reference（绝对路径，CWD 无关）——注入点随之迁移
        monkeypatch.setattr("skill_paths.default_reference", lambda *a, **k: str(tampered))
        assert mod._check_hedge_map_consistency() == 1
        assert "代码有文档无" in capsys.readouterr().out

    def test_coverage_mismatch_rc1(self, tmp_path, capsys, monkeypatch):
        orig = Path(etf_data.__file__).resolve().parent.parent.parent / "references" / "etf-hedge-map.md"
        text = orig.read_text(encoding="utf-8").replace(
            "| 510300 | 沪深300 ETF | 沪深300 | IF | ✅ | **high** | |",
            "| 510300 | 沪深300 ETF | 沪深300 | IF | ✅ | **none** | |")
        tampered = tmp_path / "etf-hedge-map.md"
        tampered.write_text(text, encoding="utf-8")
        mod = _load_etf_module()
        monkeypatch.setattr("skill_paths.skill_relpath", lambda *a, **k: str(tampered))
        # B06：读文件用 default_reference（绝对路径，CWD 无关）——注入点随之迁移
        monkeypatch.setattr("skill_paths.default_reference", lambda *a, **k: str(tampered))
        assert mod._check_hedge_map_consistency() == 1
        assert "覆盖度不一致" in capsys.readouterr().out

    def test_missing_doc_rc1(self, tmp_path, capsys, monkeypatch):
        mod = _load_etf_module()
        monkeypatch.setattr("skill_paths.skill_relpath",
                            lambda *a, **k: str(tmp_path / "nope.md"))
        monkeypatch.setattr("skill_paths.default_reference",
                            lambda *a, **k: str(tmp_path / "nope.md"))
        assert mod._check_hedge_map_consistency() == 1
        assert "对冲表文档缺失" in capsys.readouterr().out
