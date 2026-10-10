"""B05 同代输入封存：seal/verify、报告目录配对、HTML 元信息（离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etf_generation import (
    load_etf_payload,
    render_generation_line,
    seal_etf_payload,
    verify_etf_payload,
    verify_report_generation,
)
from etf_html import render_etf_html


def _payload(symbol="515050"):
    return {"skill": "invest-a-etf", "generated_at": "2026-10-08T12:00:00+08:00",
            "symbol": symbol, "profile": {}, "quote": {}, "kline": {},
            "share_history": {}, "history": None, "events": {},
            "playbook": None, "disclaimer": "占位"}


class TestSeal:
    def test_seal_roundtrip(self):
        p = _payload()
        h = seal_etf_payload(p)
        assert h == p["_meta"]["payload_hash"]
        assert verify_etf_payload(p) == []

    def test_tamper_detected(self):
        p = _payload()
        seal_etf_payload(p)
        p["quote"]["price"] = 999.0
        errs = verify_etf_payload(p)
        assert any("哈希不一致" in e for e in errs)

    def test_missing_meta(self):
        assert verify_etf_payload(_payload())

    def test_wrong_version(self):
        p = _payload()
        seal_etf_payload(p)
        p["_meta"]["payload_version"] = 99
        errs = verify_etf_payload(p)
        assert any("版本" in e or "哈希不一致" in e for e in errs)

    def test_seal_requires_symbol(self):
        with pytest.raises(ValueError):
            seal_etf_payload({"generated_at": "x"})

    def test_load_corrupt_json(self, tmp_path):
        f = tmp_path / "x.payload.json"
        f.write_text("{broken", encoding="utf-8")
        payload, errs = load_etf_payload(f)
        assert payload is None and "JSON" in errs[0]

    def test_load_missing_file(self, tmp_path):
        payload, errs = load_etf_payload(tmp_path / "nope.json")
        assert payload is None and "不存在" in errs[0]


class TestVerifyReportGeneration:
    def _mk(self, tmp_path, ts="2026-10-08-12-00-00", symbol="515050",
            with_payload=True, with_html=True, with_decision=False,
            html_hash=None, decision_ts=None):
        d = tmp_path / f"{symbol}-通信ETF"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{ts}.md").write_text("# 报告\n", encoding="utf-8")
        h = None
        if with_payload:
            p = _payload(symbol)
            h = seal_etf_payload(p)
            (d / f"{ts}.payload.json").write_text(
                json.dumps(p, ensure_ascii=False), encoding="utf-8")
        if with_html:
            embed = html_hash if html_hash is not None else (h or "deadbeef")
            (d / f"{ts}.html").write_text(
                f'<meta name="invest-generation" content="{embed}">', encoding="utf-8")
        if with_decision:
            (d / f"{ts}.decision.json").write_text(json.dumps(
                {"report_ts": decision_ts or ts}), encoding="utf-8")
        return d

    def test_paired(self, tmp_path):
        d = self._mk(tmp_path, with_decision=True)
        out = verify_report_generation(d)
        assert out["generations"][0]["status"] == "paired"
        assert out["generations"][0]["payload_hash"]

    @pytest.mark.parametrize("record", ["{broken", "[]", "null", '{"ts":"other"}'])
    def test_damaged_qc_record_is_mismatch(self, tmp_path, record):
        d = self._mk(tmp_path)
        (d / "2026-10-08-12-00-00.qc.json").write_text(record, encoding="utf-8")
        generation = verify_report_generation(d)["generations"][0]
        assert generation["qc_record"]["status"] == "invalid"
        assert generation["status"] == "mismatch"
        assert any("QC 记录非法" in issue for issue in generation["issues"])

    def test_legacy_no_payload(self, tmp_path):
        d = self._mk(tmp_path, with_payload=False)
        out = verify_report_generation(d)
        assert out["generations"][0]["status"] == "legacy_no_payload"

    def test_html_hash_mismatch_invalidates_old_signature(self, tmp_path):
        d = self._mk(tmp_path, with_decision=True, html_hash="deadbeefdeadbeef")
        out = verify_report_generation(d)
        assert out["generations"][0]["status"] == "mismatch"
        assert any("html" in i for i in out["generations"][0]["issues"])

    def test_decision_ts_mismatch(self, tmp_path):
        d = self._mk(tmp_path, with_decision=True, decision_ts="2026-10-01-09-00-00")
        out = verify_report_generation(d)
        assert out["generations"][0]["status"] == "mismatch"
        assert any("report_ts" in i for i in out["generations"][0]["issues"])

    def test_symbol_mismatch(self, tmp_path):
        d = self._mk(tmp_path, symbol="515050")
        p = _payload("510300")
        seal_etf_payload(p)
        (d / "2026-10-08-12-00-00.payload.json").write_text(
            json.dumps(p, ensure_ascii=False), encoding="utf-8")
        out = verify_report_generation(d)
        assert out["generations"][0]["status"] == "mismatch"

    def test_read_only_no_file_changes(self, tmp_path):
        d = self._mk(tmp_path, with_decision=True)
        before = sorted(p.name for p in d.iterdir())
        verify_report_generation(d)
        after = sorted(p.name for p in d.iterdir())
        assert before == after

    def test_generation_line_states(self):
        line = render_generation_line({"same_generation": True,
                                       "payload_hash": "abc123def456",
                                       "frozen_at": "2026-10-08T12:01:00+08:00",
                                       "generated_at": "2026-10-08T12:00:00+08:00"})
        assert "abc123def456"[:12] in line and "同代输入" in line
        fallback = render_generation_line(None)
        assert "回采" in fallback


class TestArtifactBindingB06:
    """负责人定点补充：产物实际内容 sha256 绑定；改 MD/HTML 后旧签认失效。"""

    def _mk(self, tmp_path, ts="2026-10-08-12-00-00", symbol="515050"):
        d = tmp_path / f"{symbol}-通信ETF"
        d.mkdir(parents=True, exist_ok=True)
        md = d / f"{ts}.md"
        md.write_text("# 报告\n", encoding="utf-8")
        p = _payload(symbol)
        h = seal_etf_payload(p)
        (d / f"{ts}.payload.json").write_text(
            json.dumps(p, ensure_ascii=False), encoding="utf-8")
        (d / f"{ts}.html").write_text(
            f'<meta name="invest-generation" content="{h}">净值 1.2345', encoding="utf-8")
        return d, md

    def test_artifacts_reported_and_record_none_without_record(self, tmp_path):
        d, _ = self._mk(tmp_path)
        gen = verify_report_generation(d)["generations"][0]
        assert gen["artifacts"]["md"]["sha256"]
        assert gen["artifacts"]["html"]["sha256"]
        assert gen["artifacts"]["payload"]["sha256"]
        assert gen["qc_record"]["status"] == "none"
        # 无记录 → 提示级（未执行 ≠ PASS），但输入配对本身仍为 paired
        assert gen["status"] == "paired"
        assert any("QC 记录" in i for i in gen["issues"])

    def test_qc_record_valid_then_md_edit_is_mismatch(self, tmp_path):
        from report_qc import qc_file, write_qc_record
        d, md = self._mk(tmp_path)
        write_qc_record(md, qc_file(md, fail_on="error"))
        gen = verify_report_generation(d)["generations"][0]
        assert gen["qc_record"]["status"] == "valid" and gen["status"] == "paired"
        md.write_text("# 报告（改了一行）\n", encoding="utf-8")
        gen2 = verify_report_generation(d)["generations"][0]
        assert gen2["status"] == "mismatch"
        assert gen2["qc_record"]["status"] == "stale"

    def test_html_number_edit_keeping_meta_is_mismatch(self, tmp_path):
        from report_qc import qc_file, write_qc_record
        d, md = self._mk(tmp_path)
        write_qc_record(md, qc_file(md, fail_on="error"))
        html = d / f"{md.stem}.html"
        html.write_text(html.read_text(encoding="utf-8").replace("1.2345", "9.9999"),
                        encoding="utf-8")
        gen = verify_report_generation(d)["generations"][0]
        assert gen["status"] == "mismatch"
        assert any("html" in c for c in gen["qc_record"]["changed"])

    def test_read_only_still_holds_with_record(self, tmp_path):
        from report_qc import qc_file, write_qc_record
        d, md = self._mk(tmp_path)
        write_qc_record(md, qc_file(md, fail_on="error"))
        before = sorted(p.name for p in d.iterdir())
        verify_report_generation(d)
        assert sorted(p.name for p in d.iterdir()) == before


class TestHtmlGenerationMeta:
    def _payload_for_render(self):
        p = _payload()
        p["profile"] = {"category": {"label": "通信ETF"}}
        p["holdings"] = {"available": False, "note": "无"}
        p["peers"] = {"available": False, "notes": []}
        p["sector_flow"] = {"available": False, "notes": []}
        return p

    def test_html_embeds_hash_and_is_byte_stable(self):
        p = self._payload_for_render()
        h = seal_etf_payload(p)
        gen = {"same_generation": True, "payload_hash": h,
               "frozen_at": p["_meta"]["frozen_at"], "generated_at": p["generated_at"]}
        html1 = render_etf_html(p, md_text="# 测试\n", generation=gen)
        html2 = render_etf_html(p, md_text="# 测试\n", generation=dict(gen))
        assert html1 == html2  # 同输入两次渲染逐字节一致
        assert f'<meta name="invest-generation" content="{h}">' in html1
        assert "同代输入" in html1


class TestCliSealIntegration:
    def test_report_payload_out_then_html_roundtrip(self, tmp_path, monkeypatch, capsys):
        """report --payload-out 冻结 → html --payload 只读渲染（模拟 CLI 装配）。"""
        import importlib.util
        import sys as _sys

        etf_py = Path(__file__).resolve().parent.parent / "scripts" / "etf.py"
        name = "etf_cli_generation_under_test"
        _sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(name, etf_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        report_dir = tmp_path / "reports" / "515050-通信ETF"
        report_dir.mkdir(parents=True)
        md = report_dir / "2026-10-08-12-00-00.md"
        md.write_text("# 报告\n", encoding="utf-8")

        monkeypatch.setattr(mod, "_collect_report_payload",
                            lambda symbol, **kw: _payload(symbol))
        monkeypatch.setattr("etf_html.render_etf_html",
                            lambda payload, md_text=None, generation=None: "HTML_OK")
        payload_path = report_dir / "2026-10-08-12-00-00.payload.json"
        assert mod.cmd_report("515050", as_json=True, with_nav=False,
                              payload_out=str(payload_path)) == 0
        assert payload_path.exists()
        assert mod.cmd_html("515050", md_path=str(md),
                            payload_path=str(payload_path), no_open=True) == 0
        out = capsys.readouterr().out
        assert "同代输入" in out
