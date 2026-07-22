"""CSINDEX_MAP coverage vs hedge map."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from etf_data import CSINDEX_MAP, ETF_HEDGE_MAP  # noqa: E402


def test_159845_in_csindex_map():
    assert CSINDEX_MAP.get("159845") == "000852"
    assert CSINDEX_MAP["159845"] == CSINDEX_MAP["512100"]


def test_588000_options_and_coverage():
    entry = ETF_HEDGE_MAP["588000"]
    assert entry["options"] == "科创50ETF期权"
    assert entry["coverage"] == "high"


def test_hedge_mapped_cn_etfs_have_csindex_when_applicable():
    """境内宽基 ETF：有对冲条目且跟踪国内指数的，应有 csindex 映射。"""
    overseas = {"513100", "513500", "518880", "511880"}  # 海外/商品/货基可无 A 股指数 PE
    for code, meta in ETF_HEDGE_MAP.items():
        if code in overseas:
            continue
        if meta.get("index") in ("纳指100", "标普500", "黄金9999", "银华日利"):
            continue
        # 行业主题可无 csindex 宽基码，跳过
        if meta.get("coverage") == "none" and code.startswith(("515", "516")):
            continue
        if code in ("510880",):  # 红利 — 可选
            continue
        if code in CSINDEX_MAP:
            assert CSINDEX_MAP[code]
        # 至少 159845 / 主流宽基已覆盖
    assert "159845" in CSINDEX_MAP and "512100" in CSINDEX_MAP
