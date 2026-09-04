"""W2/M2+M3：record_hits 幂等/schema + eval_forward 统计数学（synthetic 无网络）。"""

from __future__ import annotations

import json
from pathlib import Path


def _sample_json(n: int = 3) -> dict:
    return {"hits": [
        {"ts_code": f"60{i}001.SH", "name": f"标的{i}",
         "gap": {"gap_date": "20260710", "gap_pct": 2.5 + i},
         "current_price": 10.0 + i, "ma60": 9.0 + i,
         "pct_from_ma60": 5.0 + i, "pct_from_gap_high": 3.0 + i,
         "vol_ratio": 1.5 + i, "avg_amount_20d": 1e8}
        for i in range(n)]}


def test_record_hits_schema_fields(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from record_hits import SCHEMA_FIELDS, record

    state = tmp_path / "hits.jsonl"
    added = record(_sample_json(3), "20260902", state)
    assert added == 3
    rec = json.loads(state.read_text(encoding="utf-8").splitlines()[0])
    for f in SCHEMA_FIELDS:
        assert f in rec, f"缺 schema 字段: {f}"
    assert rec["scan_date"] == "20260902" and rec["gap_date"] == "20260710"


def test_record_hits_idempotent(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from record_hits import record

    state = tmp_path / "hits.jsonl"
    assert record(_sample_json(3), "20260902", state) == 3
    assert record(_sample_json(3), "20260902", state) == 0   # 幂等
    assert record(_sample_json(3), "20260903", state) == 3   # 新日期新增
    lines = state.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6


def test_eval_forward_stats_math():
    """synthetic 超额 → 手算对照（n=4：3 正 1 负）。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from eval_forward import stats_report

    items = [{"excess": 8.0}, {"excess": 5.0}, {"excess": 2.0}, {"excess": -3.0}]
    st = stats_report(items)
    assert st["n"] == 4 and st["beat"] == 3
    # 双侧符号检验 p（n=4, k=3）: P(X>=3)*2 = 2*(4*0.0625+0.0625)=0.625
    assert st["p_two_sided"] == 0.625
    assert st["mean_excess"] == 3.0
    assert st["median_excess"] == 3.5


def test_eval_forward_n_lt_30_disclaimer():
    """n<30 → 报告含方向性声明硬红线。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from eval_forward import render_report

    items = [{"code": "a", "name": "x", "scan_date": "20260901",
              "sessions": 10, "ret": 1.0, "bench": 0.0, "excess": 1.0}]
    st = {"n": 1, "beat": 1, "p_two_sided": 1.0, "mean_excess": 1.0,
          "t": None, "median_excess": 1.0,
          "uncorrected_note": "t 未做截面相关校正"}
    report = render_report(items, st, [], "扩张", "2026-09-03")
    assert "样本不足（n<30）：结论为方向性" in report
    assert "仅供研究，不构成投资建议" in report
